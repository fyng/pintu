"""GitHub Copilot as a Chat Completions endpoint: device-flow login, auth headers, models (SPEC §9).

Endpoints, headers and the device-flow polling follow opencode's Copilot provider
(packages/opencode/src/plugin/github-copilot/copilot.ts, and the older token-exchange
plugin opencode-copilot-auth), both MIT, Copyright (c) 2025 opencode.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from . import __version__

API = "https://api.githubcopilot.com"
GITHUB = "https://github.com"
TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
API_VERSION = "2026-06-01"
AUTH_FILE = Path("~/.config/pintu/copilot.json")
AUTH_ENV = "PINTU_COPILOT_AUTH"
CLIENT_ID_ENV = "PINTU_COPILOT_CLIENT_ID"
USER_AGENT = f"pintu/{__version__}"
POLL_MARGIN = 3.0  # seconds added to each poll interval, as opencode does
EXPIRY_MARGIN = 300.0  # refresh an exchanged token this many seconds before it expires

_SECRETS: set[str] = set()
_TOKEN_RE = re.compile(r"\b(?:gh[opsur]_|github_pat_)[A-Za-z0-9_]{16,}|\btid=[^\s\"',]+")
_HEADER_RE = re.compile(r"\b(Bearer|token)\s+[A-Za-z0-9._~+/=:;-]{8,}", re.I)


class CopilotError(RuntimeError):
    """A failed login, token exchange or Copilot request."""


def redact(text: str) -> str:
    """The text with GitHub and Copilot tokens replaced by ``[redacted]``."""
    for s in _SECRETS:
        text = text.replace(s, "[redacted]")
    text = _TOKEN_RE.sub("[redacted]", text)
    return _HEADER_RE.sub(lambda m: m.group(1) + " [redacted]", text)


def _secret(s: str) -> str:
    _SECRETS.add(s)
    return s


def _http(method: str, url: str, headers: dict, body: Optional[dict] = None,
          timeout: float = 30.0) -> tuple[int, Any]:
    """One JSON request; returns (status, parsed body). Tests replace this."""
    data = urllib.parse.urlencode(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except OSError as e:
        raise CopilotError(redact(f"{method} {url} failed: {e}")) from None
    try:
        return status, json.loads(raw or b"null")
    except json.JSONDecodeError:
        return status, raw.decode("utf-8", "replace")


def auth_path() -> Path:
    """The GitHub token file: ``$PINTU_COPILOT_AUTH``, else ``~/.config/pintu/copilot.json``."""
    return Path(os.environ.get(AUTH_ENV) or AUTH_FILE).expanduser()


def save_auth(data: dict, path: Optional[Path] = None) -> Path:
    """Writes the token file with mode 0600 (its folder 0700 if new)."""
    path = path or auth_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.chmod(path, 0o600)
    return path


def load_auth(path: Optional[Path] = None) -> dict:
    """Reads the token file.

    Raises:
        CopilotError: No login yet.
    """
    path = path or auth_path()
    if not path.is_file():
        raise CopilotError(f"not logged in to GitHub Copilot ({path} missing); run: pintu login copilot")
    data = json.loads(path.read_text(encoding="utf-8"))
    _secret(data["github_token"])
    return data


def login(client_id: str, out: Callable[[str], None] = print, sleep: Callable[[float], None] = time.sleep,
          path: Optional[Path] = None) -> Path:
    """GitHub device flow (RFC 8628): prints the URL and code, polls, saves the token.

    Args:
        client_id: Client id of a GitHub OAuth app with device flow enabled.
        out: Where the instructions go.
        sleep: Wait between polls.
        path: Token file; defaults to ``auth_path()``.

    Returns:
        The token file path.
    """
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    status, dev = _http("POST", f"{GITHUB}/login/device/code", headers, {"client_id": client_id, "scope": "read:user"})
    if status != 200 or not isinstance(dev, dict) or "device_code" not in dev:
        raise CopilotError(redact(f"device code request failed ({status}): {dev}"))
    out(f"Open {dev['verification_uri']} and enter the code {dev['user_code']}")
    interval = float(dev.get("interval", 5))
    deadline = time.monotonic() + float(dev.get("expires_in", 900))
    while time.monotonic() < deadline:
        sleep(interval + POLL_MARGIN)
        status, tok = _http("POST", f"{GITHUB}/login/oauth/access_token", headers, {
            "client_id": client_id, "device_code": dev["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        if not isinstance(tok, dict):
            raise CopilotError(redact(f"token request failed ({status}): {tok}"))
        if tok.get("access_token"):
            _secret(tok["access_token"])
            return save_auth({"github_token": tok["access_token"], "client_id": client_id,
                              "created": int(time.time())}, path)
        err = tok.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":  # RFC 8628 §3.5: add 5 s, or take the server's interval
            interval = float(tok.get("interval") or interval + 5)
            continue
        raise CopilotError(redact(f"login failed: {err or status}: {tok.get('error_description', '')}"))
    raise CopilotError("login timed out; the device code expired")


class CopilotAuth:
    """Per-request auth and Copilot headers for one GitHub login.

    Args:
        exchange: Trade the GitHub token for a short-lived Copilot token
            (``copilot_internal/v2/token``) instead of sending it directly.
        path: Token file; defaults to ``auth_path()``.
    """

    def __init__(self, exchange: bool = False, path: Optional[Path] = None):
        self.exchange = exchange
        self.github_token = load_auth(path)["github_token"]
        self.interaction = str(uuid.uuid4())
        self._token: Optional[str] = None
        self._expires = 0.0

    def token(self) -> str:
        """The bearer token; exchanges or re-exchanges when needed."""
        if not self.exchange:
            return self.github_token
        if self._token and time.time() < self._expires - EXPIRY_MARGIN:
            return self._token
        status, data = _http("GET", TOKEN_URL, {"Accept": "application/json", "User-Agent": USER_AGENT,
                                                "Authorization": f"token {self.github_token}"})
        if status != 200 or not isinstance(data, dict) or "token" not in data:
            hint = "; run: pintu login copilot" if status in (401, 403, 404) else ""
            raise CopilotError(redact(f"Copilot token exchange failed ({status}): {data}{hint}"))
        self._token, self._expires = _secret(data["token"]), float(data["expires_at"])
        return self._token

    def refresh(self) -> bool:
        """Drops the cached Copilot token after a 401; False if there is nothing to refresh."""
        if not self.exchange:
            return False
        self._token, self._expires = None, 0.0
        return True

    def base_headers(self) -> dict:
        """Auth and version headers for any Copilot API call."""
        return {"Authorization": f"Bearer {self.token()}", "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": API_VERSION}

    def headers(self, messages: list[dict]) -> dict:
        """Headers for one chat request.

        ``x-initiator`` is ``user`` only for the first request of a run (no assistant
        message yet); later steps are agent-initiated. ``Copilot-Vision-Request`` is
        set when any message holds an image part.
        """
        h = self.base_headers()
        h["Openai-Intent"] = "conversation-edits"
        h["X-Interaction-Id"] = self.interaction
        h["x-initiator"] = "agent" if any(m.get("role") == "assistant" for m in messages) else "user"
        if any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"])
               for m in messages):
            h["Copilot-Vision-Request"] = "true"
        return h

    async def aheaders(self, messages: list[dict]) -> dict:
        """``headers`` without blocking the event loop on a token exchange."""
        return await asyncio.to_thread(self.headers, messages)


def list_models(auth: CopilotAuth, base_url: str = API) -> list[dict]:
    """Chat models the login may use, as ``{id, name, vendor, tools, vision, endpoints, picker}``."""
    status, data = _http("GET", base_url.rstrip("/") + "/models", auth.base_headers())
    if status != 200 or not isinstance(data, dict):
        raise CopilotError(redact(f"model list failed ({status}): {data}"))
    out = []
    for m in data.get("data", []):
        caps = m.get("capabilities") or {}
        sup = caps.get("supports") or {}
        if (m.get("policy") or {}).get("state") == "disabled" or caps.get("type", "chat") != "chat":
            continue
        media = ((caps.get("limits") or {}).get("vision") or {}).get("supported_media_types") or []
        out.append({"id": m["id"], "name": m.get("name", ""), "vendor": m.get("vendor", ""),
                    "tools": bool(sup.get("tool_calls")),
                    "vision": bool(sup.get("vision")) or any(t.startswith("image/") for t in media),
                    "endpoints": m.get("supported_endpoints") or [],
                    "picker": bool(m.get("model_picker_enabled"))})
    return out

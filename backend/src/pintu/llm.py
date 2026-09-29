"""OpenAI-compatible chat client with native or JSON-in-text tool calls (SPEC §9)."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

CONFIG = Path("~/.config/pintu/llm.toml")
CONFIG_ENV = "PINTU_LLM_CONFIG"
PROFILE_ENV = "PINTU_LLM_PROFILE"

TEXT_PROTOCOL = """\
## Calling tools

This endpoint has no native tool calling. To call a tool, write a fenced code block
tagged tool_call_json that holds one JSON object, and nothing after the last block:

```tool_call_json
{"name": "read_file", "arguments": {"path": "recipes/plot.py"}}
```

You may put several blocks in one reply; they run in order. Results come back in the
next user message. When you are done, reply with a short summary and no tool_call_json
block.

Tools (JSON Schema of the arguments):
"""


class LLMError(RuntimeError):
    """A bad profile or model reply."""


@dataclass(frozen=True)
class Profile:
    """One endpoint from llm.toml.

    Attributes:
        name: Profile name.
        base_url: OpenAI-compatible base URL, ending in ``/v1``.
        model: Model name sent to the endpoint.
        api_key_env: Environment variable holding the API key.
        vision: Whether the model accepts image content parts.
        tools: Native tool calling; False uses JSON in the text.
        timeout: Seconds per request.
    """

    name: str
    base_url: str
    model: str
    api_key_env: str = "OPENAI_API_KEY"
    vision: bool = False
    tools: bool = True
    timeout: float = 1800.0


def load_profile(name: Optional[str] = None, path: Optional[str | Path] = None) -> Profile:
    """Reads a profile from llm.toml.

    Args:
        name: Profile name; defaults to ``$PINTU_LLM_PROFILE``, else the only or
            first profile.
        path: Config file; defaults to ``$PINTU_LLM_CONFIG``, else
            ``~/.config/pintu/llm.toml``.

    Raises:
        LLMError: The file or profile is missing.
    """
    path = Path(path or os.environ.get(CONFIG_ENV) or CONFIG).expanduser()
    if not path.is_file():
        raise LLMError(f"no LLM config at {path}")
    profiles = tomllib.loads(path.read_text(encoding="utf-8")).get("profiles", {})
    if not profiles:
        raise LLMError(f"{path} has no [profiles.<name>] table")
    name = name or os.environ.get(PROFILE_ENV) or next(iter(profiles))
    if name not in profiles:
        raise LLMError(f"no profile {name!r} in {path}; have {', '.join(profiles)}")
    p = profiles[name]
    known = {k: p[k] for k in ("api_key_env", "vision", "tools", "timeout") if k in p}
    return Profile(name=name, base_url=p["base_url"], model=p["model"], **known)


@dataclass
class ToolCall:
    """One tool call from the model."""

    id: str
    name: str
    arguments: dict
    error: Optional[str] = None  # set when the arguments did not parse


@dataclass
class Reply:
    """One model reply.

    Attributes:
        content: Visible text, with reasoning removed.
        tool_calls: Parsed tool calls.
        reasoning: Separate reasoning text, if the server returned any.
        usage: Token counts.
        seconds: Wall time of the request.
        message: The assistant message to append to the history.
    """

    content: str
    tool_calls: list[ToolCall]
    reasoning: Optional[str] = None
    usage: dict = field(default_factory=dict)
    seconds: float = 0.0
    message: dict = field(default_factory=dict)


_THINK = re.compile(r"<think>.*?</think>", re.S)
_BLOCK = re.compile(r"<tool_call>(.*?)(?:</tool_call>|$)", re.S)
_FENCE = re.compile(r"```\w*\s*(.*?)```", re.S)
_NAME = re.compile(r'"(?:name|tool)"\s*:\s*"(\w+)"')
_GLM_ARG = re.compile(r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>", re.S)


def _objects(text: str) -> list[Any]:
    """Every top-level JSON value that starts with '{' or '[' in the text."""
    dec, out, i = json.JSONDecoder(), [], 0
    while True:
        starts = [j for j in (text.find("{", i), text.find("[", i)) if j >= 0]
        if not starts:
            return out
        i = min(starts)
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        out.append(obj)
        i = end


def _as_calls(obj: Any) -> list[dict]:
    if isinstance(obj, list):
        return [c for o in obj for c in _as_calls(o)]
    if not isinstance(obj, dict):
        return []
    if isinstance(obj.get("tool_calls"), list):
        return _as_calls(obj["tool_calls"])
    if isinstance(obj.get("function"), dict):
        return _as_calls(obj["function"])
    name = obj.get("name") or obj.get("tool")
    if isinstance(name, str):
        return [{"name": name, "arguments": obj.get("arguments", obj.get("args", obj.get("parameters", {})))}]
    return []


def _args(raw: Any) -> tuple[dict, Optional[str]]:
    if isinstance(raw, dict):
        return raw, None
    if raw in (None, ""):
        return {}, None
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
        except json.JSONDecodeError as e:
            return {}, f"arguments are not valid JSON: {e}"
        if isinstance(v, dict):
            return v, None
    return {}, "arguments must be a JSON object"


def _glm_call(chunk: str) -> list[dict]:
    """GLM's native ``name<arg_key>k</arg_key><arg_value>v</arg_value>`` call body."""
    name = chunk.split("<arg_key>", 1)[0].strip()
    if not name.isidentifier():
        return []
    args = {}
    for k, v in _GLM_ARG.findall(chunk):
        try:
            args[k.strip()] = json.loads(v)
        except json.JSONDecodeError:
            args[k.strip()] = v
    return [{"name": name, "arguments": args}]


def _repair(chunk: str) -> Optional[Any]:
    """The chunk as JSON after appending missing closers at the end, or None."""
    stack, in_str, esc = [], False, False
    for ch in chunk:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack.pop() != ch:
                return None
    if in_str or not stack:
        return None
    try:
        return json.loads(chunk + "".join(reversed(stack)))
    except json.JSONDecodeError:
        return None


def _chunk_calls(chunk: str) -> list[dict]:
    """Calls in one chunk; a broken JSON call comes back as ``{"name", "error"}``."""
    calls = [c for obj in _objects(chunk) for c in _as_calls(obj)] or _glm_call(chunk)
    body = chunk.strip()
    if calls or not body.startswith("{") or not _NAME.search(body):
        return calls
    calls = _as_calls(_repair(body))
    if calls:
        return calls
    try:
        json.loads(body)
        return []
    except json.JSONDecodeError as e:
        name = _NAME.search(body).group(1)
        return [{"name": name, "arguments": {}, "error":
                 f"your {name} call is not valid JSON ({e.msg} at line {e.lineno} column {e.colno}, "
                 f"char {e.pos} of {len(body)}); nothing was run. Resend the call as valid JSON, "
                 "with every brace and quote closed and newlines in strings escaped as \\n."}]


def parse_text_calls(text: str) -> list[ToolCall]:
    """Tool calls written as JSON in the text.

    Accepts ``<tool_call>`` blocks, then fenced code blocks (the protocol asks for
    ``tool_call_json`` fences; some servers strip ``<tool_call>``), then bare JSON; each
    holding ``{"name", "arguments"}`` (or ``tool``/``args``), a list of those, or
    ``{"tool_calls": [...]}``. A ``<tool_call>`` block may also hold GLM's native
    ``name<arg_key>..</arg_key><arg_value>..</arg_value>`` form. A call missing only its
    final closers is repaired; any other broken JSON call comes back with ``error`` set.
    """
    text = _THINK.sub("", text)
    chunks = _BLOCK.findall(text) if "<tool_call>" in text else (_FENCE.findall(text) or [text])
    calls = [c for chunk in chunks for c in _chunk_calls(chunk)]
    out = []
    for k, c in enumerate(calls):
        args, err = _args(c["arguments"])
        out.append(ToolCall(id=f"call_{k}", name=c["name"], arguments=args, error=c.get("error") or err))
    return out


def text_protocol(tools: list[dict]) -> str:
    """System prompt section for JSON-in-text tool calls."""
    lines = [TEXT_PROTOCOL]
    for t in tools:
        f = t["function"]
        lines.append(f"- {f['name']}: {f['description']}\n  {json.dumps(f['parameters'])}")
    return "\n".join(lines)


class LLM:
    """Chat Completions client for one profile.

    Args:
        profile: The endpoint.
        client: An ``openai.AsyncOpenAI``-like object; built from the profile if None.
    """

    def __init__(self, profile: Profile, client: Any = None):
        self.profile = profile
        if client is None:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(base_url=profile.base_url, timeout=profile.timeout, max_retries=1,
                                 api_key=os.environ.get(profile.api_key_env) or "none")
        self.client = client

    async def chat(self, messages: list[dict], tools: list[dict]) -> Reply:
        """Sends the history; returns the parsed reply.

        In text mode, pass ``text_protocol(tools)`` in the system prompt yourself.
        """
        kw: dict[str, Any] = {"model": self.profile.model, "messages": messages}
        if self.profile.tools and tools:
            kw["tools"] = tools
        t0 = time.perf_counter()
        resp = await self.client.chat.completions.create(**kw)
        seconds = time.perf_counter() - t0
        if not resp.choices:
            raise LLMError("model returned no choices")
        msg = resp.choices[0].message
        reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
        content = msg.content or ""
        if "</think>" in content:  # reasoning inlined in the content
            head, _, content = content.rpartition("</think>")
            reasoning = reasoning or head.replace("<think>", "").strip()
        content = content.strip()
        usage = {}
        if getattr(resp, "usage", None) is not None:
            u = resp.usage
            usage = {"prompt": u.prompt_tokens, "completion": u.completion_tokens, "total": u.total_tokens}
        calls = []
        for tc in getattr(msg, "tool_calls", None) or []:  # text mode too: vLLM may parse them out
            args, err = _args(tc.function.arguments)
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args, error=err))
        if self.profile.tools:
            message = {"role": "assistant", "content": content or None}
            if calls:
                message["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in calls]
        else:
            if calls:  # show the server-parsed calls in the protocol's own form
                content = "\n\n".join([content] + [
                    "```tool_call_json\n" + json.dumps({"name": c.name, "arguments": c.arguments}) + "\n```"
                    for c in calls]).strip()
            else:
                calls = parse_text_calls(content)
            message = {"role": "assistant", "content": content}
        return Reply(content=content, tool_calls=calls, reasoning=reasoning, usage=usage,
                     seconds=seconds, message=message)

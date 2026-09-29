import asyncio
import json
import stat
from types import SimpleNamespace

import pytest

from pintu import cli, copilot
from pintu.llm import LLM, LLMError, Profile, load_profile

GH = "gho_" + "a" * 36
CP = "tid=abc123;exp=1999999999;sku=free;8kp=1:" + "f" * 64


class FakeHTTP:
    """Scripted stand-in for ``copilot._http``; records each call."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, method, url, headers, body=None, timeout=30.0):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return self.replies.pop(0)


@pytest.fixture
def auth_file(tmp_path, monkeypatch):
    path = tmp_path / "cfg" / "copilot.json"
    monkeypatch.setenv(copilot.AUTH_ENV, str(path))
    return path


def test_login_device_flow(auth_file, monkeypatch):
    http = FakeHTTP([
        (200, {"device_code": "DC", "user_code": "ABCD-1234", "verification_uri": "https://github.com/login/device",
               "interval": 5, "expires_in": 900}),
        (200, {"error": "authorization_pending"}),
        (200, {"error": "slow_down", "interval": 10}),
        (200, {"access_token": GH, "token_type": "bearer", "scope": "read:user"}),
    ])
    monkeypatch.setattr(copilot, "_http", http)
    said, waits = [], []
    path = copilot.login("Iv-test", out=said.append, sleep=waits.append)
    assert path == auth_file
    assert said == ["Open https://github.com/login/device and enter the code ABCD-1234"]
    assert waits == [8.0, 8.0, 13.0]  # interval + margin; slow_down takes the server's interval
    assert http.calls[0]["url"] == "https://github.com/login/device/code"
    assert http.calls[0]["body"] == {"client_id": "Iv-test", "scope": "read:user"}
    assert http.calls[1]["body"]["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
    assert stat.S_IMODE(auth_file.stat().st_mode) == 0o600
    assert json.loads(auth_file.read_text())["github_token"] == GH


def test_login_denied(auth_file, monkeypatch):
    monkeypatch.setattr(copilot, "_http", FakeHTTP([
        (200, {"device_code": "DC", "user_code": "U", "verification_uri": "V", "interval": 0}),
        (200, {"error": "access_denied", "error_description": "user said no"})]))
    with pytest.raises(copilot.CopilotError, match="access_denied"):
        copilot.login("x", out=lambda s: None, sleep=lambda s: None)
    assert not auth_file.exists()


def test_not_logged_in(auth_file):
    with pytest.raises(copilot.CopilotError, match="pintu login copilot"):
        copilot.CopilotAuth()


def test_direct_headers(auth_file):
    copilot.save_auth({"github_token": GH})
    auth = copilot.CopilotAuth()
    first = auth.headers([{"role": "system", "content": "s"}, {"role": "user", "content": "go"}])
    assert first["Authorization"] == f"Bearer {GH}"
    assert first["X-GitHub-Api-Version"] == copilot.API_VERSION
    assert first["Openai-Intent"] == "conversation-edits"
    assert first["x-initiator"] == "user" and "Copilot-Vision-Request" not in first
    img = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}
    later = auth.headers([{"role": "user", "content": "go"}, {"role": "assistant", "content": "ok"},
                          {"role": "user", "content": [{"type": "text", "text": "r"}, img]}])
    assert later["x-initiator"] == "agent" and later["Copilot-Vision-Request"] == "true"
    assert later["X-Interaction-Id"] == first["X-Interaction-Id"]
    assert auth.refresh() is False


def test_exchange_caches_and_refreshes(auth_file, monkeypatch):
    copilot.save_auth({"github_token": GH})
    now = 1_000_000.0
    monkeypatch.setattr(copilot.time, "time", lambda: now)
    http = FakeHTTP([(200, {"token": CP, "expires_at": now + 1800, "refresh_in": 1500}),
                     (200, {"token": CP + "2", "expires_at": now + 3600}),
                     (200, {"token": CP + "3", "expires_at": now + 9999})])
    monkeypatch.setattr(copilot, "_http", http)
    auth = copilot.CopilotAuth(exchange=True)
    assert auth.token() == CP and auth.token() == CP  # cached
    assert http.calls[0]["url"] == copilot.TOKEN_URL
    assert http.calls[0]["headers"]["Authorization"] == f"token {GH}"
    now += 1600  # inside the expiry margin
    assert auth.token() == CP + "2"
    assert auth.refresh() is True and auth.token() == CP + "3"
    assert len(http.calls) == 3


def test_exchange_failure_redacted(auth_file, monkeypatch):
    copilot.save_auth({"github_token": GH})
    monkeypatch.setattr(copilot, "_http", FakeHTTP([(401, {"message": f"bad credentials {GH}"})]))
    with pytest.raises(copilot.CopilotError) as e:
        copilot.CopilotAuth(exchange=True).token()
    assert GH not in str(e.value) and "[redacted]" in str(e.value) and "pintu login copilot" in str(e.value)


def test_redact():
    text = f"Authorization: Bearer {CP} token {GH} github_pat_{'b' * 30} key=sk-short"
    out = copilot.redact(text)
    assert GH not in out and CP not in out and "github_pat_" not in out
    assert "key=sk-short" in out


def test_list_models(auth_file, monkeypatch):
    copilot.save_auth({"github_token": GH})
    http = FakeHTTP([(200, {"data": [
        {"id": "gpt-5.4", "name": "GPT-5.4", "vendor": "OpenAI", "model_picker_enabled": True,
         "supported_endpoints": ["/chat/completions", "/responses"],
         "capabilities": {"type": "chat", "supports": {"tool_calls": True, "vision": True}}},
        {"id": "claude-sonnet-4.6", "model_picker_enabled": True, "supported_endpoints": ["/chat/completions", "/v1/messages"],
         "capabilities": {"type": "chat", "supports": {"tool_calls": True},
                          "limits": {"vision": {"supported_media_types": ["image/png"]}}}},
        {"id": "old", "policy": {"state": "disabled"}, "capabilities": {"type": "chat", "supports": {}}},
        {"id": "text-embedding-3-small", "capabilities": {"type": "embeddings", "supports": {}}},
    ]})])
    monkeypatch.setattr(copilot, "_http", http)
    models = copilot.list_models(copilot.CopilotAuth())
    assert [(m["id"], m["tools"], m["vision"]) for m in models] == [("gpt-5.4", True, True),
                                                                     ("claude-sonnet-4.6", True, True)]
    assert http.calls[0]["url"] == "https://api.githubcopilot.com/models"
    assert http.calls[0]["headers"]["Authorization"] == f"Bearer {GH}"


def test_cli_models(auth_file, monkeypatch, capsys):
    copilot.save_auth({"github_token": GH})
    monkeypatch.setattr(copilot, "_http", FakeHTTP([(500, {"error": f"boom {GH}"})]))
    with pytest.raises(SystemExit) as e:
        cli.main(["models", "copilot"])
    assert e.value.code == 1 and GH not in capsys.readouterr().err


def test_load_copilot_profile(tmp_path):
    cfg = tmp_path / "llm.toml"
    cfg.write_text('[profiles.copilot]\nprovider = "copilot"\nmodel = "gpt-5.4"\nvision = true\n'
                   'headers = {"Copilot-Integration-Id" = "x"}\n\n[profiles.bad]\nprovider = "nope"\nmodel = "m"\n')
    p = load_profile("copilot", cfg)
    assert (p.provider, p.base_url, p.token_exchange, p.headers) == (
        "copilot", "https://api.githubcopilot.com", False, {"Copilot-Integration-Id": "x"})
    with pytest.raises(LLMError, match="provider"):
        load_profile("bad", cfg)


class Status401(Exception):
    status_code = 401


class ScriptedClient:
    """Stand-in for openai.AsyncOpenAI: raises or returns scripted results."""

    def __init__(self, results):
        self.results = list(results)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.requests.append(kw)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _resp(*messages):
    return SimpleNamespace(choices=[SimpleNamespace(message=m) for m in messages], usage=None)


def _call(i, name):
    return SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=name, arguments="{}"))


def test_chat_retries_401_once(auth_file, monkeypatch):
    copilot.save_auth({"github_token": GH})
    monkeypatch.setattr(copilot, "_http", FakeHTTP([(200, {"token": CP, "expires_at": 9e9}),
                                                    (200, {"token": CP + "2", "expires_at": 9e9})]))
    ok = SimpleNamespace(content="hi", tool_calls=None)
    client = ScriptedClient([Status401("expired"), _resp(ok)])
    llm = LLM(Profile("c", copilot.API, "gpt-5.4", provider="copilot", token_exchange=True), client=client)
    reply = asyncio.run(llm.chat([{"role": "user", "content": "go"}], []))
    assert reply.content == "hi"
    assert [r["extra_headers"]["Authorization"] for r in client.requests] == [f"Bearer {CP}", f"Bearer {CP}2"]


def test_chat_401_direct_is_redacted_error(auth_file):
    copilot.save_auth({"github_token": GH})
    client = ScriptedClient([Status401(f"Unauthorized for Bearer {GH}")])
    llm = LLM(Profile("c", copilot.API, "m", provider="copilot"), client=client)
    with pytest.raises(LLMError) as e:
        asyncio.run(llm.chat([{"role": "user", "content": "go"}], []))
    assert GH not in str(e.value) and "pintu login copilot" in str(e.value)
    assert len(client.requests) == 1


def test_chat_copilot_reply_shape(auth_file):
    copilot.save_auth({"github_token": GH})
    first = SimpleNamespace(content="Reading.", tool_calls=None, reasoning_text="think", reasoning_opaque="OPQ")
    second = SimpleNamespace(content=None, tool_calls=[_call(0, "read_file")])
    client = ScriptedClient([_resp(first, second)])
    llm = LLM(Profile("c", copilot.API, "claude-sonnet-4.6", provider="copilot"), client=client)
    tools = [{"type": "function", "function": {"name": "read_file", "description": "", "parameters": {}}}]
    reply = asyncio.run(llm.chat([{"role": "user", "content": "go"}], tools))
    assert reply.content == "Reading." and reply.reasoning == "think"
    assert [c.name for c in reply.tool_calls] == ["read_file"]
    assert reply.message["reasoning_opaque"] == "OPQ" and reply.message["reasoning_text"] == "think"
    assert client.requests[0]["tools"] == tools

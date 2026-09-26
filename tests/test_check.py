import json

import httpx
from fastapi.testclient import TestClient

import app as appmod

SSE = (
    'data: {"choices":[{"delta":{"content":"Hello"}}]}\n'
    'data: {"choices":[{"delta":{"content":" world"}}],'
    '"usage":{"prompt_tokens":2,"completion_tokens":3}}\n'
    "data: [DONE]\n"
)


def check(monkeypatch, tmp_path, handler, **extra):
    real = httpx.AsyncClient
    monkeypatch.setattr(appmod.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(appmod, "LOG_FILE", tmp_path / "checker.log")
    body = {"endpoint": "http://up", "models": ["m1"], "prompt": "hi", **extra}
    with TestClient(appmod.app) as c:
        text = c.post("/api/check", json=body).text
    events = [json.loads(l[5:]) for l in text.splitlines() if l.startswith("data:")]
    return [e for e in events if e["type"] == "result"]


def test_check_streams_ttft_and_runs(monkeypatch, tmp_path):
    (r,) = check(monkeypatch, tmp_path, lambda req: httpx.Response(200, content=SSE), runs=2)
    assert r["status"] == "active"
    assert (r["runs_ok"], r["runs_total"], r["flaky"]) == (2, 2, False)
    assert r["ttft_ms"] is not None
    assert r["completion_tokens"] == 3 and r["tokens_per_sec"] > 0
    assert r["response"] == "Hello world"


def test_check_flaky_when_some_runs_fail(monkeypatch, tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, content=SSE) if len(calls) == 1 else httpx.Response(500, text="boom")
    (r,) = check(monkeypatch, tmp_path, handler, runs=2)
    assert r["status"] == "active" and r["flaky"] is True and r["runs_ok"] == 1


def test_check_inactive_and_capabilities(monkeypatch, tmp_path):
    (r,) = check(monkeypatch, tmp_path, lambda req: httpx.Response(500, text="boom"),
                 capabilities=["vision"])
    assert r["status"] == "inactive" and "boom" in r["error"]
    assert r["capabilities"] == {"vision": False}

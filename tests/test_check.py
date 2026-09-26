import json

import httpx
from fastapi.testclient import TestClient

import app as appmod

REAL_CLIENT = httpx.AsyncClient  # captured before any monkeypatching

SSE = (
    'data: {"choices":[{"delta":{"content":"Hello"}}]}\n'
    'data: {"choices":[{"delta":{"content":" world"}}],'
    '"usage":{"prompt_tokens":2,"completion_tokens":3}}\n'
    "data: [DONE]\n"
)


def check(monkeypatch, tmp_path, handler, **extra):
    monkeypatch.setattr(appmod.httpx, "AsyncClient",
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler)))
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


def test_check_survives_hostile_usage_and_bad_runs(monkeypatch, tmp_path):
    hostile = ('data: {"choices":[{"delta":{"content":"ok"}}],"usage":'
               '{"prompt_tokens":"<img src=x onerror=alert(1)>","completion_tokens":"abc"}}\n'
               "data: [DONE]\n")
    rs = check(monkeypatch, tmp_path, lambda req: httpx.Response(200, content=hostile),
               models=["m1", "m2"], runs="abc")
    assert [r["model"] for r in rs] == ["m1", "m2"] or {r["model"] for r in rs} == {"m1", "m2"}
    for r in rs:
        assert r["status"] == "active" and r["runs_total"] == 1
        assert isinstance(r["prompt_tokens"], int) and isinstance(r["completion_tokens"], int)


def test_check_reports_raw_error_body_and_plain_json_content(monkeypatch, tmp_path):
    (r,) = check(monkeypatch, tmp_path, lambda req: httpx.Response(500, text="upstream exploded"))
    assert r["status"] == "inactive" and r["response"] == "upstream exploded"
    body = {"choices": [{"message": {"content": "plain hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2}}
    (r,) = check(monkeypatch, tmp_path, lambda req: httpx.Response(200, json=body))
    assert r["response"] == "plain hi" and r["tokens_per_sec"] is not None


def test_check_uses_finite_timeouts(monkeypatch, tmp_path):
    seen = []
    real = httpx.AsyncClient

    def factory(**kw):
        seen.append(kw.get("timeout"))
        return real(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=SSE)))
    monkeypatch.setattr(appmod.httpx, "AsyncClient", factory)
    monkeypatch.setattr(appmod, "LOG_FILE", tmp_path / "checker.log")
    with TestClient(appmod.app) as c:
        c.post("/api/check", json={"endpoint": "http://up", "models": ["m1"], "capabilities": ["vision"]})
    assert seen and all(t is not None for t in seen)


def test_check_returns_probe_details_and_logs_probe_calls(monkeypatch, tmp_path):
    def handler(req):
        if b"image_url" in req.content:
            return httpx.Response(400, text="no images please")
        return httpx.Response(200, content=SSE)
    (r,) = check(monkeypatch, tmp_path, handler, capabilities=["vision"])
    assert r["capabilities"] == {"vision": False}
    assert "no images please" in r["probe_details"]["vision"]["reason"]
    lines = [json.loads(l) for l in (tmp_path / "checker.log").read_text().splitlines()]
    probe = [l for l in lines if l["model"] == "m1 [probe:vision]"]
    assert len(probe) == 1 and "no images please" in probe[0]["response"] and "no images please" in probe[0]["error"]


def test_probe_log_omits_image_bytes(monkeypatch, tmp_path):
    def handler(req):
        if b"image_url" in req.content:
            return httpx.Response(200, json={"choices": [{"message": {"content": "A cartoon avatar of a young man with spiky black hair."}}]})
        return httpx.Response(200, content=SSE)
    (r,) = check(monkeypatch, tmp_path, handler, capabilities=["vision"])
    assert r["capabilities"] == {"vision": True}
    log = (tmp_path / "checker.log").read_text()
    assert "base64," not in log and "image omitted" in log

# ── proxy fallback replies count as failures ────────────────────────────
from tests.test_stream import FB_SSE  # noqa: E402

def test_check_treats_fallback_reply_as_inactive(monkeypatch, tmp_path):
    (r,) = check(monkeypatch, tmp_path, lambda req: httpx.Response(200, content=FB_SSE))
    assert r["status"] == "inactive"
    assert "served by fallback model mimo" in r["error"]
    assert "fallback model" in r["response"]  # full reply text for Details

def test_check_flaky_when_one_run_falls_back(monkeypatch, tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, content=FB_SSE) if len(calls) == 1 else httpx.Response(200, content=SSE)
    (r,) = check(monkeypatch, tmp_path, handler, runs=2)
    assert r["status"] == "active" and r["flaky"] is True and r["runs_ok"] == 1

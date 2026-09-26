import asyncio
import json

import httpx

import app as appmod

SSE = (
    'data: {"choices":[{"delta":{"content":"Hello"}}]}\n'
    'data: {"choices":[{"delta":{"content":" world"}}],'
    '"usage":{"prompt_tokens":2,"completion_tokens":3}}\n'
    "data: [DONE]\n"
)
URL = "http://up/v1/chat/completions"
PAYLOAD = {"model": "m", "stream": True}


def run_with(handler):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await appmod.stream_completion(c, URL, {}, PAYLOAD)
    return asyncio.run(run())


def test_stream_ttft_and_usage():
    r = run_with(lambda request: httpx.Response(200, content=SSE))
    assert r["ok"] is True
    assert r["streamed"] is True
    assert r["content"] == "Hello world"
    assert r["usage"]["completion_tokens"] == 3
    assert r["ttft_ms"] is not None


def test_plain_json_fallback():
    body = {"choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    r = run_with(lambda request: httpx.Response(200, json=body))
    assert r["ok"] is True
    assert r["streamed"] is False
    assert r["ttft_ms"] == r["latency_ms"]
    assert r["usage"]["completion_tokens"] == 1


def test_http_error_retry_hint():
    r = run_with(lambda request: httpx.Response(400, text="stream is not supported"))
    assert r["ok"] is False
    assert r["retry_stream"] is True


def test_plain_json_content_is_extracted():
    body = {"choices": [{"message": {"content": "hi"}}]}
    assert run_with(lambda request: httpx.Response(200, json=body))["content"] == "hi"
    anth = {"content": [{"type": "text", "text": "yo"}]}
    assert run_with(lambda request: httpx.Response(200, json=anth))["content"] == "yo"


def test_http_200_with_error_body_is_not_ok():
    r = run_with(lambda request: httpx.Response(200, json={"error": {"message": "bad key"}}))
    assert r["ok"] is False and "bad key" in r["error"]
    sse = 'data: {"error": {"message": "overloaded"}}\n'
    r = run_with(lambda request: httpx.Response(200, content=sse))
    assert r["ok"] is False and "overloaded" in r["error"]


def test_error_status_keeps_body():
    r = run_with(lambda request: httpx.Response(500, text="upstream exploded"))
    assert r["ok"] is False and r["body"] == "upstream exploded"


def test_compute_tps_guards_against_zero_generation_window():
    assert appmod.compute_tps(10, 800, 800, False) == 12.5    # non-stream: whole latency
    assert appmod.compute_tps(10, 800, 799, True) == 12.5     # burst-buffered stream: below floor
    assert appmod.compute_tps(10, 1000, 200, True) == 12.5    # real generation window 800 ms
    assert appmod.compute_tps(None, 800, 200, True) is None


def test_stream_completion_tolerates_glued_done_marker():
    from tests.test_parse import GLUED
    r = run_with(lambda request: httpx.Response(200, content=GLUED))
    assert r["ok"] is True and r["content"] == "hi" and r["usage"]["completion_tokens"] == 2

# ── proxy fallback notices ──────────────────────────────────────────────
FB = ("⚠️ Model `Kimi-K2.6` encountered an error (HTTP 400 Bad Request). "
      "This response was generated using the fallback model `mimo`.\n\nyes")
FB_SSE = 'data: {"choices":[{"delta":{"content":' + json.dumps(FB) + '}}]}\ndata: [DONE]\n'

def test_fallback_sse_reply_is_not_ok():
    r = run_with(lambda request: httpx.Response(200, content=FB_SSE))
    assert r["ok"] is False
    assert "fallback model mimo" in r["error"]
    assert r["status"] == 200 and r["retry_stream"] is False
    assert "fallback model" in r["body"]

def test_fallback_plain_json_reply_is_not_ok():
    body = {"choices": [{"message": {"content": FB}}]}
    r = run_with(lambda request: httpx.Response(200, json=body))
    assert r["ok"] is False and "fallback model mimo" in r["error"]

import asyncio

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

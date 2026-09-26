import asyncio

import httpx

import app as appmod


def test_judge_tools_openai_and_anthropic():
    oa = {"choices": [{"message": {"tool_calls": [{"id": "1"}]}}]}
    an = {"content": [{"type": "tool_use", "name": "get_weather"}]}
    assert appmod._judge_capability("tools", 200, oa, None) is True
    assert appmod._judge_capability("tools", 200, an, None) is True
    assert appmod._judge_capability("tools", 200, {"choices": [{"message": {}}]}, "hi") is False


def test_judge_json_and_errors():
    assert appmod._judge_capability("json", 200, {"x": 1}, '{"ok": true}') is True
    assert appmod._judge_capability("json", 200, {"x": 1}, "not json") is False
    assert appmod._judge_capability("vision", 400, {"x": 1}, "x") is False
    assert appmod._judge_capability("vision", 200, {"x": 1}, "dot") is True


def test_run_probes_end_to_end():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {
            "content": '{"ok": true}', "tool_calls": [{"id": "1"}]}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await appmod.run_probes(
                c, "http://up/v1/chat/completions", {}, "m", ["tools", "json", "vision", "bogus"])
    out = asyncio.run(run())
    assert out == {"tools": True, "json": True, "vision": True}

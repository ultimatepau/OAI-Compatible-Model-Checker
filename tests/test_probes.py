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


def probe_details(handler, kinds):
    details = {}

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            out = await appmod.run_probes(c, "http://up/v1/chat/completions", {}, "m", kinds, details)
            return out
    return asyncio.run(run()), details


def test_failed_probe_explains_why():
    out, d = probe_details(lambda req: httpx.Response(400, text="model does not support images"), ["vision"])
    assert out == {"vision": False}
    assert d["vision"]["status"] == 400
    assert "HTTP 400" in d["vision"]["reason"] and "does not support images" in d["vision"]["reason"]
    assert "does not support images" in d["vision"]["body"]


def test_probe_reasons_per_kind_and_exception():
    plain = httpx.Response(200, json={"choices": [{"message": {"content": "just text"}}]})
    out, d = probe_details(lambda req: plain, ["tools", "json"])
    assert out == {"tools": False, "json": False}
    assert "tool call" in d["tools"]["reason"] and "not valid JSON" in d["json"]["reason"]

    def boom(req):
        raise httpx.ConnectTimeout("timed out")
    out, d = probe_details(boom, ["vision"])
    assert out == {"vision": False} and "timed out" in d["vision"]["reason"]


def test_passing_probe_has_no_reason():
    out, d = probe_details(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "dot"}}]}), ["vision"])
    assert out == {"vision": True} and d["vision"]["reason"] is None


def test_probe_tolerates_json_with_glued_done_marker():
    from tests.test_parse import GLUED
    out, d = probe_details(lambda req: httpx.Response(200, content=GLUED), ["vision"])
    assert out == {"vision": True} and d["vision"]["reason"] is None

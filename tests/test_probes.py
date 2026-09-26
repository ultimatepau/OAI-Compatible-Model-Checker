import asyncio
import base64
import json
from pathlib import Path

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
    j = lambda text, data={"x": 1}: appmod._judge_capability("vision", 200, data, text)
    assert j("A cartoon avatar of a young man with spiky black hair.") is True
    assert j("This is an illustration of a person wearing a hoodie.") is True
    assert j("Yes.") is False                                   # no description of the image
    assert j("Không nhận được ảnh") is False
    assert j("No image received. I cannot describe it.") is False
    assert j("I can't see any person in this image.") is False  # mentions 'person' but refuses
    plain = {"choices": [{"message": {"content": "A man's face."}}]}
    assert j(None, plain) is True                               # plain-JSON body, no SSE text


def test_run_probes_end_to_end():
    def handler(request):
        text = "A cartoon avatar of a young man with spiky black hair." if b"image_url" in request.content else '{"ok": true}'
        return httpx.Response(200, json={"choices": [{"message": {
            "content": text, "tool_calls": [{"id": "1"}]}}]})

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
    out, d = probe_details(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "A cartoon avatar of a young man with spiky black hair."}}]}), ["vision"])
    assert out == {"vision": True} and d["vision"]["reason"] is None


def test_probe_tolerates_json_with_glued_done_marker():
    glued = '{"choices":[{"message":{"content":"A cartoon avatar of a young man with spiky black hair."}}]}data: [DONE]\n'
    out, d = probe_details(lambda req: httpx.Response(200, content=glued), ["vision"])
    assert out == {"vision": True} and d["vision"]["reason"] is None


def test_vision_probe_sends_the_real_test_image_and_question():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "A cartoon avatar of a young man with spiky black hair."}}]})
    out, _ = probe_details(handler, ["vision"])
    parts = seen[0]["messages"][0]["content"]
    png = (Path(appmod.__file__).parent / "test_image.png").read_bytes()
    assert parts[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(png).decode()
    q = parts[0]["text"].lower()
    assert "about" in q and "yes or no" not in q and seen[0]["max_tokens"] >= 200
    assert out == {"vision": True}


def test_vision_probe_fails_when_model_did_not_see_the_image():
    reply = "Không nhận được ảnh"
    out, d = probe_details(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": reply}}]}), ["vision"])
    assert out == {"vision": False}
    assert "does not describe" in d["vision"]["reason"] and reply in d["vision"]["reason"]


def test_vision_probe_reports_missing_image_file(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, "VISION_IMAGE_PATH", tmp_path / "nope.png")
    out, d = probe_details(lambda req: (_ for _ in ()).throw(AssertionError("must not call upstream")), ["vision"])
    assert out == {"vision": False} and "not found" in d["vision"]["reason"]

from app import parse_completion_response
def test_parse_openai_sse():
    raw = "data: {\"choices\":[{\"delta\":{\"content\":\"Hi\"}}]}\ndata: {\"choices\":[{\"delta\":{\"content\":\" there\"}}],\"usage\":{\"prompt_tokens\":2,\"completion_tokens\":3}}\n"
    data, content = parse_completion_response(raw)
    assert content == "Hi there"
    assert data["usage"]["completion_tokens"] == 3
def test_parse_plain_json():
    raw = "{\"choices\":[{\"message\":{\"content\":\"hi\"}}],\"usage\":{\"prompt_tokens\":1,\"completion_tokens\":1}}"
    data, content = parse_completion_response(raw)
    assert content is None
    assert data["usage"]["prompt_tokens"] == 1

def test_parse_garbage():
    assert parse_completion_response("<html>err</html>") == (None, None)

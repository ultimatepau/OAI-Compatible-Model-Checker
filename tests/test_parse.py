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


GLUED = '{"choices":[{"message":{"content":"hi"}}],"usage":{"completion_tokens":2}}data: [DONE]\n'


def test_plain_json_with_glued_done_marker_is_parsed():
    data, content = parse_completion_response(GLUED)
    assert data["usage"]["completion_tokens"] == 2 and content is None
    assert parse_completion_response("garbage{")[0] is None

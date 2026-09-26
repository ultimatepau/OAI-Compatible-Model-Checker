import httpx
from fastapi.testclient import TestClient

import app as appmod
import db

RESULTS = [
    {"model": "a", "status": "active", "latency_ms": 200, "ttft_ms": 50,
     "tokens_per_sec": 12.5, "prompt_tokens": 2, "completion_tokens": 3,
     "capabilities": {"vision": True}, "error": None},
    {"model": "b", "status": "inactive", "latency_ms": 100, "error": "boom"},
]


def test_record_list_get_delete_roundtrip():
    rid = db.record_run("http://up", "hi", 10, RESULTS)
    (run,) = db.list_runs()
    assert (run["id"], run["total"], run["active"]) == (rid, 2, 1)
    got, rows = db.get_run(rid)
    assert [r["model"] for r in rows] == ["b", "a"]  # ordered by latency
    assert rows[1]["capabilities"] == '{"vision": true}'
    db.delete_run(rid)
    assert db.list_runs() == [] and db.get_run(rid) == (None, None)


def test_check_persists_and_history_api(monkeypatch, tmp_path):
    sse = 'data: {"choices":[{"delta":{"content":"hi"}}]}\ndata: [DONE]\n'
    real = httpx.AsyncClient
    monkeypatch.setattr(appmod.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(
                            lambda req: httpx.Response(200, content=sse))))
    monkeypatch.setattr(appmod, "LOG_FILE", tmp_path / "checker.log")
    with TestClient(appmod.app) as c:
        c.post("/api/check", json={"endpoint": "http://up", "models": ["m1"]})
        runs = c.get("/api/history").json()["runs"]
        assert len(runs) == 1 and runs[0]["total"] == 1
        detail = c.get(f"/api/history/{runs[0]['id']}").json()
        assert detail["results"][0]["model"] == "m1"
        assert c.get("/api/history/999").json()["ok"] is False
        c.delete(f"/api/history/{runs[0]['id']}")
        assert c.get("/api/history").json()["runs"] == []

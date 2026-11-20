from fastapi.testclient import TestClient

from agent.http import app


def test_http_scaffold():
    client = TestClient(app)
    assert client.get("/agent/health").json() == {"status": "ok"}
    assert client.post("/agent/v1/answers").status_code == 503

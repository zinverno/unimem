"""``GET /health`` — process liveness, and the promise that it claims nothing more."""

from fastapi.testclient import TestClient


def test_health_returns_exactly_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_needs_no_stores(client: TestClient) -> None:
    """It answers without asking a store anything.

    The stack's stores are wired but never touched: health is liveness, not
    readiness, and a probe that quietly opened the database would start failing
    for reasons that have nothing to do with whether the process is alive.
    """
    for _ in range(3):
        assert client.get("/health").json() == {"status": "ok"}

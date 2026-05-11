"""VC-021: single end-to-end acceptance test for the full public surface."""

from fastapi.testclient import TestClient

from langgraph_runnable_server import create_app


def test_full_public_surface():
    default_app = create_app()
    prefixed_app = create_app(prefix="/api")

    default_id_before = default_app.state["instance_id"]
    prefixed_id_before = prefixed_app.state["instance_id"]

    # 1. Non-empty strings
    assert isinstance(default_id_before, str) and len(default_id_before) > 0
    assert isinstance(prefixed_id_before, str) and len(prefixed_id_before) > 0

    # 2. Distinct
    assert default_id_before != prefixed_id_before

    # 3. Default app endpoints + lifespan
    with TestClient(default_app) as client:
        h = client.get("/health")
        m = client.get("/metrics")
        assert h.status_code == 200
        assert h.content == b"ok"
        assert h.headers["content-type"].startswith("text/plain")
        assert m.status_code == 200
        assert m.content == b""
        assert default_app.state["instance_id"] == default_id_before

    # 4. Prefixed app + un-prefixed paths are 404
    with TestClient(prefixed_app) as client:
        h = client.get("/api/health")
        m = client.get("/api/metrics")
        assert h.status_code == 200
        assert h.content == b"ok"
        assert m.status_code == 200
        assert m.content == b""
        assert client.get("/health").status_code == 404
        assert client.get("/metrics").status_code == 404

    # 5. Instance ID stable after all requests
    assert default_app.state["instance_id"] == default_id_before
    assert prefixed_app.state["instance_id"] == prefixed_id_before

    # 6. __all__ discipline
    import langgraph_runnable_server as p

    assert p.__all__ == ["create_app", "create_runnable_app"]

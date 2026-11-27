from collections.abc import Iterator

from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from app.config import Settings
from app.main import create_app


def test_security_headers_for_health_stream_and_peer_denial() -> None:
    app = create_app(Settings(_env_file=None, providers=[]))

    @app.get("/header-test-stream")
    def stream() -> StreamingResponse:
        def body() -> Iterator[bytes]:
            yield b"data: first\n\n"
            yield b"data: second\n\n"

        return StreamingResponse(
            body(), media_type="text/event-stream", headers={"Referrer-Policy": "same-origin"}
        )

    local = TestClient(app, client=("127.0.0.1", 12345))
    remote = TestClient(app, client=("192.0.2.1", 12345))
    health = local.get("/api/health")
    output = local.get("/header-test-stream")
    denial = remote.get("/api/health")
    for response in (health, output, denial):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"] == (
            "default-src 'none'; frame-ancestors 'none'"
        )
    assert health.headers["referrer-policy"] == denial.headers["referrer-policy"] == "no-referrer"
    assert denial.status_code == 403
    assert output.headers["referrer-policy"] == "same-origin"
    assert output.text == "data: first\n\ndata: second\n\n"


def test_documentation_html_can_load_its_scripts() -> None:
    local = TestClient(create_app(Settings(_env_file=None, providers=[])), client=("127.0.0.1", 1))
    for path in ("/docs", "/redoc"):
        response = local.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "content-security-policy" not in response.headers
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"

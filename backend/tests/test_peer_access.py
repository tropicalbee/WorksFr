import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from app.config import Settings
from app.main import RequireLocalPeer


@pytest.mark.parametrize(
    ("peer", "token", "authorization", "expected"),
    [
        ("127.0.0.1", "", "", 200),
        ("127.0.0.2", "", "", 200),
        ("::1", "", "", 200),
        ("::ffff:127.0.0.1", "", "", 200),
        ("127.0.0.1", "token", "Bearer wrong", 200),
        ("192.0.2.2", "", "", 403),
        ("192.0.2.2", "token", "Bearer token", 200),
        ("192.0.2.2", "token", "Bearer wrong", 403),
        ("192.0.2.2", "token", "", 403),
        ("192.0.2.2", "token", "Basic token", 403),
        ("invalid-peer", "", "", 403),
    ],
)
def test_peer_access(peer: str, token: str, authorization: str, expected: int) -> None:
    app = FastAPI()
    app.add_middleware(RequireLocalPeer, token=token)

    @app.get("/events")
    def events() -> StreamingResponse:
        return StreamingResponse(iter([b"data: ready\n\n"]), media_type="text/event-stream")

    response = TestClient(app, client=(peer, 12345)).get(
        "/events", headers={"Authorization": authorization}
    )
    assert response.status_code == expected
    if expected == 200:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.text == "data: ready\n\n"
    else:
        assert response.json() == {"detail": "Requests from other machines need the API token"}


def test_settings_repr_hides_api_token() -> None:
    assert "credential-test-value" not in repr(
        Settings(_env_file=None, api_token="credential-test-value")
    )

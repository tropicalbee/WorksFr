import asyncio
import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from app import mcp_server

REPORT_DONE = {
    "id": "abc",
    "source_kind": "url",
    "source": "https://example.com/tutorial",
    "title": "Pandas tutorial",
    "status": "done",
    "created_at": "2026-11-24T10:00:00",
    "score": {"works": 1, "broken": 1, "fixed": 1},
    "versions": {"pandas": "2.2.0"},
    "failed_packages": ["oldlib"],
    "steps": [
        {
            "idx": 1,
            "kind": "python",
            "original_code": "print(1)",
            "final_code": "print(1)",
            "label": "works",
            "reason": "Ran with no error.",
        },
        {
            "idx": 2,
            "kind": "python",
            "original_code": "df.append(x)",
            "final_code": "pd.concat([df, x])",
            "label": "broken",
            "reason": "DataFrame.append was removed.",
            "verdict": "fixed",
            "explanation": "Use concat.",
        },
    ],
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(mcp_server, "_sleep", instant)


def use_backend(monkeypatch, handler):
    monkeypatch.setattr(mcp_server, "_transport", httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def test_check_tutorial_polls_until_done(monkeypatch):
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert request.url.path == "/api/checks"
            assert json.loads(request.content) == {"url": "https://example.com/tutorial"}
            return httpx.Response(200, json={"check_id": "abc"})
        polls["n"] += 1
        if polls["n"] < 3:
            return httpx.Response(200, json={**REPORT_DONE, "status": "running", "steps": []})
        return httpx.Response(200, json=REPORT_DONE)

    use_backend(monkeypatch, handler)
    summary = run(mcp_server.check_tutorial("https://example.com/tutorial"))
    assert polls["n"] == 3
    assert summary["status"] == "done"
    assert summary["title"] == "Pandas tutorial"
    assert summary["library_versions"] == {"pandas": "2.2.0"}
    assert summary["failed_packages"] == ["oldlib"]


def test_summary_has_fixed_code_and_no_score(monkeypatch):
    use_backend(monkeypatch, lambda request: httpx.Response(200, json=REPORT_DONE))
    summary = run(mcp_server.get_report("abc"))
    text = json.dumps(summary).lower()
    assert "score" not in text
    assert "grade" not in text
    works, broken = summary["steps"]
    assert works["label"] == "works"
    assert "replacement" not in works
    assert broken["step"] == 3
    assert broken["verdict"] == "fixed"
    assert broken["replacement"] == "pd.concat([df, x])"
    assert broken["reason"] == "DataFrame.append was removed."


def test_failed_check_returns_message(monkeypatch):
    failed = {**REPORT_DONE, "status": "failed", "error": "Page could not be fetched", "steps": []}
    use_backend(monkeypatch, lambda request: httpx.Response(200, json=failed))
    summary = run(mcp_server.get_report("abc"))
    assert summary["status"] == "failed"
    assert summary["error"] == "Page could not be fetched"


def test_check_code_posts_code_and_title(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"check_id": "abc"})
        return httpx.Response(200, json=REPORT_DONE)

    use_backend(monkeypatch, handler)
    run(mcp_server.check_code("import pandas", title="My snippet"))
    assert seen["path"] == "/api/checks/code"
    assert seen["body"] == {"code": "import pandas", "title": "My snippet"}


def test_unreachable_backend_gives_clear_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    use_backend(monkeypatch, handler)
    with pytest.raises(mcp_server.WorksFrUnavailable) as info:
        run(mcp_server.recent_checks())
    assert "isn't reachable at" in str(info.value)
    assert "uvicorn app.main:app" in str(info.value)


def test_http_error_surfaces_detail(monkeypatch):
    use_backend(
        monkeypatch, lambda request: httpx.Response(422, json={"detail": "That is not a URL"})
    )
    with pytest.raises(mcp_server.WorksFrUnavailable) as info:
        run(mcp_server.check_tutorial("nope"))
    assert "That is not a URL" in str(info.value)


def test_recent_checks_is_compact(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=[REPORT_DONE, {**REPORT_DONE, "id": "def"}])

    use_backend(monkeypatch, handler)
    rows = run(mcp_server.recent_checks(limit=1))
    assert seen["path"] == "/api/checks"
    assert rows == [
        {
            "id": "abc",
            "title": "Pandas tutorial",
            "status": "done",
            "created_at": "2026-11-24T10:00:00",
        }
    ]


def test_tools_are_registered():
    names = {tool.name for tool in run(mcp_server.mcp.list_tools())}
    assert names == {"check_tutorial", "check_code", "get_report", "recent_checks"}


def test_unreachable_backend_message_reaches_the_client(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    use_backend(monkeypatch, handler)
    with pytest.raises(ToolError) as info:
        run(mcp_server.mcp.call_tool("recent_checks", {}))
    assert not isinstance(info.value, UnexpectedToolError)
    assert "isn't reachable" in str(info.value)

import json
from pathlib import Path

import httpx
import pytest

from scripts.export_showcase import export_reports


def report(check_id: str, *, day: int, status: str = "done") -> dict[str, object]:
    return {
        "id": check_id,
        "title": check_id,
        "status": status,
        "source_kind": "paste",
        "source": "Pasted code",
        "created_at": f"2026-11-{day:02d}T12:00:00+00:00",
        "steps": [{"idx": 0, "kind": "python", "original_code": "x = 1", "final_code": "x = 1"}],
    }


def test_exports_finished_reports_and_indexes_all_saved_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checks = tmp_path / "checks"
    checks.mkdir()
    saved = {**report("saved", day=23), "showcase_label": "Saved example"}
    (checks / "saved.json").write_text(json.dumps(saved), encoding="utf-8")
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        check_id = request.url.path.rsplit("/", 1)[-1]
        data = {
            "older": report("older", day=22),
            "newer": report("newer", day=25),
            "busy": report("busy", day=24, status="running"),
        }
        return httpx.Response(200, json=data[check_id]) if check_id in data else httpx.Response(404)

    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(handle)) as client:
        export_reports(["older", "newer", "busy", "missing", "../unsafe"], client, tmp_path)

    assert requests == [
        f"/api/checks/{check_id}" for check_id in ("older", "newer", "busy", "missing")
    ]
    assert json.loads((checks / "newer.json").read_text(encoding="utf-8"))["steps"]
    assert not (checks / "busy.json").exists()
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert [entry["id"] for entry in index] == ["newer", "saved", "older"]
    assert all(entry["steps"] == [] for entry in index)
    assert index[1]["showcase_label"] == "Saved example"
    assert "running" in capsys.readouterr().out

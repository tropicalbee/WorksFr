"""Export chosen finished reports from a running WorksFr backend for the static showcase."""

import argparse
import json
import re
from pathlib import Path
from typing import Any

import httpx

from app.models import CheckReport, CheckStatus

OUTPUT_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public" / "showcase"
DEFAULT_API = "http://127.0.0.1:8000"
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")


def _finished_report(data: Any, check_id: str) -> dict[str, Any] | None:
    report = CheckReport.model_validate(data)
    if report.id != check_id:
        raise ValueError("Report ID does not match the requested check")
    if report.status != CheckStatus.DONE:
        print(f"Skipping {check_id}: status is {report.status.value}.")
        return None
    payload = report.model_dump(mode="json")
    if isinstance(data.get("showcase_label"), str):
        payload["showcase_label"] = data["showcase_label"]
    return payload


def export_reports(check_ids: list[str], client: httpx.Client, output_dir: Path) -> None:
    checks_dir = output_dir / "checks"
    checks_dir.mkdir(parents=True, exist_ok=True)
    for check_id in dict.fromkeys(check_ids):
        if not _SAFE_ID.fullmatch(check_id):
            print("Skipping an invalid check ID.")
            continue
        try:
            response = client.get(f"/api/checks/{check_id}", follow_redirects=False)
            response.raise_for_status()
            report = _finished_report(response.json(), check_id)
        except httpx.HTTPStatusError as exc:
            print(f"Skipping {check_id}: HTTP {exc.response.status_code}.")
            continue
        except (httpx.RequestError, ValueError) as exc:
            print(f"Skipping {check_id}: {type(exc).__name__}.")
            continue
        if report is not None:
            path = checks_dir / f"{check_id}.json"
            path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"Exported {check_id}.")

    index: list[dict[str, Any]] = []
    for path in sorted(checks_dir.glob("*.json")):
        try:
            report = _finished_report(json.loads(path.read_text(encoding="utf-8")), path.stem)
        except (OSError, ValueError) as exc:
            print(f"Skipping a saved report: {type(exc).__name__}.")
            continue
        if report is not None:
            index.append({**report, "steps": []})
    index.sort(key=lambda report: report["created_at"], reverse=True)
    (output_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check_ids", nargs="+", help="Finished check IDs to make public")
    parser.add_argument("--api", default=DEFAULT_API, help="Base URL of the running backend")
    args = parser.parse_args(argv)
    with httpx.Client(base_url=args.api, timeout=20) as client:
        export_reports(args.check_ids, client, OUTPUT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

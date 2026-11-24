"""Run one saved tutorial through the real sandbox without live HTTP or LLM calls."""

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx

from app.config import Settings
from app.events import CheckEvent
from app.extract.routing import route_url
from app.llm import LLMUnavailableError
from app.models import RepairProposal
from app.pipeline import PipelineDeps, run_check
from app.sandbox.docker_sandbox import DockerSandbox

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "backend" / "tests" / "fixtures"


@dataclass(frozen=True)
class Fixture:
    filename: str
    url: str


def load_fixtures() -> dict[str, Fixture]:
    fixtures = {}
    for line in (FIXTURES / "SOURCES.md").read_text(encoding="utf-8").splitlines():
        columns = [column.strip() for column in line.split("|")]
        if len(columns) >= 5 and columns[3].startswith("https://"):
            fixtures[columns[1]] = Fixture(columns[2], columns[3])
    return fixtures


class NoPatches:
    def propose(self, messages: list[dict[str, str]]) -> tuple[RepairProposal, str]:
        raise LLMUnavailableError(["Fixture review disables LLM repairs"])


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, library: str, version: str | None, source: str, content: str) -> None:
        self.values[key] = content


class Sink:
    def __init__(self) -> None:
        self.events: list[CheckEvent] = []

    def emit(self, event: CheckEvent) -> None:
        self.events.append(event)


def isolated_settings() -> Settings:
    fields = {name.upper() for name in Settings.model_fields}
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() not in fields and not name.upper().startswith("LLM_")
    }
    with patch.dict(os.environ, environment, clear=True):
        return Settings(_env_file=None)


def print_summary(events: list[CheckEvent], elapsed: float, misses: Counter[str]) -> None:
    reasons = {event.idx: event.reason for event in events if event.type == "step_result"}
    print(f"{'Step':>4}  {'Kind':<8}  {'Label':<12}  Reason")
    for event in events:
        if event.type == "step_final":
            step = event.step
            label = step.label.value if step.label else "skipped"
            reason = " ".join(reasons.get(step.idx, step.reason).split())
            if len(reason) > 100:
                reason = reason[:97] + "..."
            print(f"{step.idx + 1:>4}  {step.kind.value:<8}  {label:<12}  {reason}")
        elif event.type == "failed":
            print(f"Run failed: {event.error}")
    environment = next((event for event in events if event.type == "environment"), None)
    print("\nEnvironment:")
    print(json.dumps(environment.versions if environment else {}, indent=2))
    print("Failed packages:", json.dumps(environment.failed_packages if environment else []))
    print(
        "Install pins:",
        json.dumps(
            [pin.model_dump(mode="json") for pin in environment.install_pins] if environment else []
        ),
    )
    done = next((event for event in events if event.type == "done"), None)
    print("Score:", done.score.model_dump_json() if done else "unavailable")
    print(f"Total run time: {elapsed:.2f}s")
    print(f"Mock HTTP 404 requests: {sum(misses.values())}")
    for url, count in sorted(misses.items()):
        print(f"  {count} x {url}")


def run_fixture(name: str, fixture: Fixture) -> int:
    started = time.monotonic()
    data = (FIXTURES / fixture.filename).read_bytes()
    allowed = {httpx.URL(fixture.url)}
    route = route_url(fixture.url)
    if route.kind == "stackoverflow" and route.answer_id is not None:
        allowed.add(
            httpx.URL(
                f"https://api.stackexchange.com/2.3/answers/{route.answer_id}",
                params={"site": "stackoverflow", "filter": "withbody"},
            )
        )
    misses: Counter[str] = Counter()

    def serve(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url in allowed:
            return httpx.Response(200, content=data)
        misses[str(request.url)] += 1
        return httpx.Response(404)

    settings = isolated_settings()
    sink = Sink()
    source = (fixture.filename, data) if fixture.filename.endswith(".ipynb") else fixture.url
    # URL validation normally resolves DNS before transport dispatch. Keep that
    # validation offline too; MockTransport is the only HTTP path in this script.
    with (
        httpx.Client(transport=httpx.MockTransport(serve)) as client,
        patch("app.extract.fetch.resolve_host", return_value=["1.1.1.1"]),
    ):
        deps = PipelineDeps(
            settings=settings,
            sandbox_factory=lambda check_id: DockerSandbox(settings, check_id),
            proposer=NoPatches(),
            http_client=client,
            doc_cache=MemoryCache(),
        )
        run_check(f"fixture-{uuid4().hex}", source, deps, sink)
    output = ROOT / ".scratch" / "fixture-runs" / f"{name}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps([event.model_dump(mode="json") for event in sink.events], indent=2) + "\n",
        encoding="utf-8",
    )
    print_summary(sink.events, time.monotonic() - started, misses)
    print(f"Events: {output}")
    return 0 if sink.events and sink.events[-1].type == "done" else 1


def main() -> int:
    fixtures = load_fixtures()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", nargs="?", choices=list(fixtures))
    parser.add_argument("--list", action="store_true", help="List saved fixture names")
    args = parser.parse_args()
    if args.list:
        print("\n".join(fixtures))
        return 0
    if args.name is None:
        parser.error("provide one fixture name or --list")
    try:
        return run_fixture(args.name, fixtures[args.name])
    except (OSError, ValueError) as exc:
        print(f"Fixture run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

from collections.abc import Callable
from pathlib import Path

import httpx
import nbformat
from support.local_sandbox import LocalRunnerSandbox

from app.config import Settings
from app.events import CheckEvent
from app.llm.client import LLMResult
from app.models import RepairProposal
from app.pipeline import PipelineDeps, run_check
from app.repair.loop import LLMProposer

ROOT = Path(__file__).resolve().parents[2]


class Proposer:
    def propose(self, messages: list[dict[str, str]]) -> tuple[RepairProposal, str]:
        return RepairProposal(patched_code="int('42')", explanation="valid integer"), "test"


class Cache:
    def get(self, key: str) -> str | None:
        return None

    def put(self, key: str, library: str, version: str | None, source: str, content: str) -> None:
        pass


class Sink:
    def __init__(self) -> None:
        self.events: list[CheckEvent] = []

    def emit(self, event: CheckEvent) -> None:
        self.events.append(event)


def notebook() -> bytes:
    return nbformat.writes(
        nbformat.v4.new_notebook(
            cells=[
                nbformat.v4.new_code_cell("x = 1"),
                nbformat.v4.new_code_cell(
                    "import warnings\nwarnings.warn('old', DeprecationWarning)"
                ),
                nbformat.v4.new_code_cell("int('bad')"),
                nbformat.v4.new_code_cell("input()"),
                nbformat.v4.new_code_cell("!pip install missing-example"),
            ]
        )
    ).encode()


def test_pipeline_exact_sequence_and_score() -> None:
    settings = Settings(_env_file=None, providers=[])
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        deps = PipelineDeps(
            settings=settings,
            sandbox_factory=lambda check_id: LocalRunnerSandbox(
                settings, runner_path=ROOT / "sandbox/runner.py"
            ),
            proposer=Proposer(),
            http_client=http,
            doc_cache=Cache(),
        )
        sink = Sink()
        run_check("c1", ("test.ipynb", notebook()), deps, sink)
    assert [event.type for event in sink.events] == [
        "extracted",
        "environment",
        "step_started",
        "step_result",
        "step_final",
        "step_started",
        "step_result",
        "step_final",
        "step_started",
        "step_result",
        "repair_attempt",
        "step_final",
        "step_started",
        "step_result",
        "step_final",
        "step_started",
        "step_final",
        "done",
    ]
    score = sink.events[-1].score
    assert (score.works, score.deprecated, score.broken, score.cant_verify, score.fixed) == (
        1,
        1,
        1,
        1,
        1,
    )


def test_pipeline_failure_events() -> None:
    settings = Settings(_env_file=None, providers=[])

    def broken_factory(check_id: str):
        from app.sandbox.interface import SandboxError

        raise SandboxError("sandbox start failed")

    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        deps = PipelineDeps(settings, broken_factory, Proposer(), http, Cache())
        sink = Sink()
        run_check("c1", ("bad.ipynb", b"invalid"), deps, sink)
        assert sink.events[-1].type == "failed"
        sink = Sink()
        run_check("c2", ("test.ipynb", notebook()), deps, sink)
        assert [event.type for event in sink.events] == ["extracted", "failed"]


def test_pipeline_wait_notice_reaches_current_check_in_order() -> None:
    settings = Settings(_env_file=None, providers=[])
    callbacks: list[Callable[[str, float], None]] = []

    class WaitingProposer(Proposer):
        def __init__(self, on_wait: Callable[[str, float], None]) -> None:
            self.on_wait = on_wait

        def propose(self, messages: list[dict[str, str]]) -> tuple[RepairProposal, str]:
            self.on_wait("test", 3.0)
            return super().propose(messages)

    def factory(on_wait: Callable[[str, float], None]) -> Proposer:
        callbacks.append(on_wait)
        return WaitingProposer(on_wait)

    source = nbformat.writes(
        nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("int('bad')")])
    ).encode()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        deps = PipelineDeps(
            settings=settings,
            sandbox_factory=lambda check_id: LocalRunnerSandbox(
                settings, runner_path=ROOT / "sandbox/runner.py"
            ),
            proposer=Proposer(),
            http_client=http,
            doc_cache=Cache(),
            proposer_factory=factory,
        )
        sinks = [Sink(), Sink()]
        for index, sink in enumerate(sinks):
            run_check(f"wait-{index}", ("test.ipynb", source), deps, sink)
    assert len(callbacks) == 2
    assert callbacks[0] is not callbacks[1]
    for sink in sinks:
        assert [event.type for event in sink.events] == [
            "extracted",
            "environment",
            "step_started",
            "step_result",
            "rate_limit_wait",
            "repair_attempt",
            "step_final",
            "done",
        ]
        assert sink.events[4].provider == "test"
        assert sink.events[4].seconds == 3.0


def test_llm_proposer_forwards_per_check_wait_callback() -> None:
    waits: list[tuple[str, float]] = []

    def on_wait(provider: str, seconds: float) -> None:
        waits.append((provider, seconds))

    class Client:
        def complete_json(
            self,
            messages: list[dict[str, str]],
            schema: type[RepairProposal],
            *,
            on_wait: Callable[[str, float], None] | None = None,
        ) -> LLMResult[RepairProposal]:
            assert schema is RepairProposal
            assert on_wait is not None
            on_wait("test", 2.0)
            return LLMResult(
                value=RepairProposal(patched_code="x = 1", explanation="new value"), provider="test"
            )

    proposal, provider = LLMProposer(Client(), on_wait=on_wait).propose([])
    assert proposal.patched_code == "x = 1"
    assert provider == "test"
    assert waits == [("test", 2.0)]

from collections import deque
from pathlib import Path

import httpx
import pytest
from support.local_sandbox import LocalRunnerSandbox

from app.config import Settings
from app.llm import LLMUnavailableError
from app.models import Block, PatchVerdict, RepairProposal, StepKind
from app.repair.loop import RepairContext, repair_step
from app.session import CheckSession
from app.step import Step

ROOT = Path(__file__).resolve().parents[2]


class Proposer:
    def __init__(self, codes: list[str]) -> None:
        self.codes = deque(codes)
        self.messages: list[list[dict[str, str]]] = []

    def propose(self, messages: list[dict[str, str]]) -> tuple[RepairProposal, str]:
        self.messages.append(messages)
        if not self.codes:
            raise LLMUnavailableError(["test provider unavailable"])
        return RepairProposal(
            patched_code=self.codes.popleft(), explanation="test repair", apis_changed=["old"]
        ), "scripted"


class Cache:
    def get(self, key: str) -> str | None:
        return None

    def put(self, key: str, library: str, version: str | None, source: str, content: str) -> None:
        pass


@pytest.fixture
def resources():
    settings = Settings(_env_file=None, step_timeout_seconds=2)
    sandbox = LocalRunnerSandbox(settings, runner_path=ROOT / "sandbox/runner.py")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        with CheckSession(sandbox, settings) as session:
            session.start([])
            context = RepairContext(
                title="Test",
                earlier_code="",
                versions={},
                failed_packages=[],
                settings=settings,
                http_client=http,
                doc_cache=Cache(),
            )
            yield session, sandbox, context


def make_step(idx: int, code: str) -> Step:
    return Step(Block(idx=idx, kind=StepKind.PYTHON, code=code, runnable_code=code))


def test_fixed_on_first_attempt(resources) -> None:
    session, sandbox, context = resources
    step = make_step(0, "int('bad')")
    session.run(step)
    callbacks = []
    outcome = repair_step(
        session,
        step,
        context,
        Proposer(["int('42')"]),
        on_attempt=lambda item: callbacks.append(item.attempt),
    )
    assert outcome.verdict == PatchVerdict.FIXED
    assert step.accepted.provider == "scripted"
    assert callbacks == [1]


def test_second_attempt_accumulates_error_and_search(
    resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    session, sandbox, context = resources
    searches = []
    import app.repair.loop as loop

    original = loop.gather

    def gather(*args, **kwargs):
        searches.append(kwargs.get("attempt", args[4] if len(args) > 4 else None))
        return original(*args, **kwargs)

    monkeypatch.setattr(loop, "gather", gather)
    step = make_step(0, "int('bad')")
    session.run(step)
    proposer = Proposer(["int('still bad')", "int('42')"])
    assert repair_step(session, step, context, proposer).verdict == PatchVerdict.FIXED
    assert "still bad" in proposer.messages[1][-1]["content"]
    assert searches == [1, 2]


@pytest.mark.parametrize(
    "patch",
    [
        "",
        "# only comments",
        "invalid(",
        "int('bad')",
        "x = 1",
        "try:\n    int('bad')\nexcept Exception:\n    pass",
    ],
)
def test_sanity_rejects_without_running(resources, patch: str) -> None:
    session, sandbox, context = resources
    step = make_step(0, "int('bad')")
    session.run(step)
    outcome = repair_step(session, step, context, Proposer([patch]), max_attempts=1)
    assert outcome.verdict == PatchVerdict.FAILED
    assert step.attempts[0].result is None
    assert not any(request["id"].endswith("-a1") for request in sandbox.requests)


def test_all_fail_then_reset(resources) -> None:
    session, sandbox, context = resources
    step = make_step(0, "x = 1\nint('bad')")
    session.run(step)
    repair_step(session, step, context, Proposer(["x = 99\nint('oops')"]), max_attempts=1)
    later = make_step(1, "x")
    session.run(later)
    assert later.result.result == "1"


def test_library_failure_is_unverified(resources) -> None:
    session, sandbox, context = resources
    session.run(make_step(0, "from pathlib import Path"))
    context.earlier_code = "from pathlib import Path"
    step = make_step(1, "Path('missing.csv').removed()")
    session.run(step)
    outcome = repair_step(
        session, step, context, Proposer(["Path('missing.csv').read_text()"]), max_attempts=1
    )
    assert outcome.verdict == PatchVerdict.FIXED_UNVERIFIED


def test_failing_names_cannot_be_dropped(resources) -> None:
    session, sandbox, context = resources
    session.run(make_step(0, "df = []\nrow = 1"))
    context.earlier_code = "df = []\nrow = 1"
    step = make_step(1, "df.removed(row)")
    session.run(step)
    outcome = repair_step(
        session,
        step,
        context,
        Proposer(["import pathlib\npathlib.Path('missing.csv').read_text()"]),
        max_attempts=1,
    )
    assert outcome.verdict == PatchVerdict.FAILED
    assert step.attempts[0].result is None


def test_unavailable_and_skipped_steps(resources) -> None:
    session, sandbox, context = resources
    good = make_step(0, "42")
    session.run(good)
    proposer = Proposer([])
    assert repair_step(session, good, context, proposer).verdict is None
    assert proposer.messages == []
    broken = make_step(1, "x = missing()")
    session.run(broken)
    assert repair_step(session, broken, context, proposer).llm_unavailable
    cascade = make_step(2, "print(x)")
    session.run(cascade)
    count = len(proposer.messages)
    assert repair_step(session, cascade, context, proposer).verdict is None
    assert len(proposer.messages) == count


def test_no_restart_when_no_patch_ran(resources, monkeypatch: pytest.MonkeyPatch) -> None:
    session, sandbox, context = resources
    step = make_step(0, "int('bad')")
    session.run(step)
    restarts: list[list[str]] = []
    original_restart = sandbox.restart

    def counting_restart(replay: list[str]) -> None:
        restarts.append(replay)
        original_restart(replay)

    monkeypatch.setattr(sandbox, "restart", counting_restart)
    # Every proposal is rejected by the sanity checks, then the LLM becomes unavailable.
    outcome = repair_step(session, step, context, Proposer(["", "int('bad')"]))
    assert outcome.verdict == PatchVerdict.FAILED
    assert outcome.llm_unavailable
    assert restarts == []

from pathlib import Path

import pytest
from support.local_sandbox import LocalRunnerSandbox

from app.config import Settings
from app.models import Block, ErrorInfo, ExecResult, Label, RepairAttempt, StepKind
from app.sandbox.interface import SandboxError
from app.session import CheckSession, CheckTimeoutError
from app.step import Step

ROOT = Path(__file__).resolve().parents[2]


def step(idx: int, code: str) -> Step:
    return Step(Block(idx=idx, kind=StepKind.PYTHON, code=code, runnable_code=code))


@pytest.fixture
def session() -> CheckSession:
    settings = Settings(_env_file=None, step_timeout_seconds=1)
    sandbox = LocalRunnerSandbox(settings, runner_path=ROOT / "sandbox/runner.py")
    with CheckSession(sandbox, settings) as session:
        session.start([])
        yield session


def test_persistence_and_precheck(session: CheckSession) -> None:
    assert session.run(step(0, "x = 42")).label == Label.WORKS
    later = step(1, "x")
    session.run(later)
    assert later.result.result == "42"
    assert session.run(step(2, "input()")).label == Label.CANT_VERIFY
    assert len(session.ledger) == 2


def test_timeout_restarts_and_replays(session: CheckSession) -> None:
    session.run(step(0, "x = 42"))
    assert session.run(step(1, "import time\ntime.sleep(10)")).label == Label.CANT_VERIFY
    later = step(2, "x")
    session.run(later)
    assert later.result.result == "42"


def test_clean_patch_accept_and_reset(session: CheckSession) -> None:
    session.run(step(0, "values = []"))
    broken = step(1, "values.append(1)\nraise ValueError('broken')")
    session.run(broken)
    result, decision, verdict = session.try_patch(broken, "assert values == []\nx = 42", 1)
    proposal = RepairAttempt(
        attempt=1,
        patched_code="assert values == []\nx = 42",
        explanation="fixed",
        result=result,
        decision=decision,
        verdict=verdict,
    )
    broken.record_attempt(proposal)
    session.accept(broken, proposal)
    session.reset()
    later = step(2, "x")
    session.run(later)
    assert later.result.result == "42"


def test_failed_patch_reset_and_cascade(session: CheckSession) -> None:
    broken = step(0, "x = missing()")
    session.run(broken)
    session.try_patch(broken, "leaked = 1\nraise ValueError('bad')", 1)
    session.reset()
    check = step(1, "'leaked' in globals()")
    session.run(check)
    assert check.result.result == "False"
    assert session.run(step(2, "print(x)")).reason == "depends on step 1, which is still broken"


def test_deadline_and_protocol_failure() -> None:
    class Fake:
        def start(self, packages):
            from app.models import Environment

            return Environment()

        def exec(self, step_id, code):
            return ExecResult(
                id=step_id, status="error", error=ErrorInfo(type="ProtocolError", message="bad")
            )

        def close(self):
            pass

    now = [0.0]
    with CheckSession(Fake(), Settings(_env_file=None), clock=lambda: now[0]) as session:
        session.start([])
        with pytest.raises(SandboxError):
            session.run(step(0, "42"))
        now[0] = 1000
        with pytest.raises(CheckTimeoutError):
            session.run(step(1, "42"))
        assert session.probe("json", "dump") is None

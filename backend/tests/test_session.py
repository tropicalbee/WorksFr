from pathlib import Path

import pytest
from support.local_sandbox import LocalRunnerSandbox

from app.config import Settings
from app.models import Block, Environment, ErrorInfo, ExecResult, Label, RepairAttempt, StepKind
from app.sandbox.interface import SandboxError, StepTimeoutError
from app.session import CheckSession, CheckTimeoutError
from app.step import Step

ROOT = Path(__file__).resolve().parents[2]


def step(idx: int, code: str, kind: StepKind = StepKind.PYTHON) -> Step:
    return Step(Block(idx=idx, kind=kind, code=code, runnable_code=code))


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
        def start(self, packages: list[str], npm_packages: list[str] | None = None) -> Environment:
            return Environment()

        def exec(self, step_id: str, code: str, *, lang: str = "python") -> ExecResult:
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


def test_replay_carries_kinds_and_prechecks_do_not_run() -> None:
    class MixedSandbox:
        requests: list[tuple[str, str, str]] = []
        replays: list[list[tuple[StepKind, str]]] = []

        def start(self, packages: list[str], npm_packages: list[str] | None = None) -> Environment:
            assert packages == ["pandas"]
            assert npm_packages == ["example"]
            return Environment(failed_npm_packages=["example"])

        def exec(self, step_id: str, code: str, *, lang: str = "python") -> ExecResult:
            self.requests.append((step_id, code, lang))
            if step_id == "s6":
                raise StepTimeoutError("timed out")
            if step_id == "s3":
                return ExecResult(
                    id=step_id,
                    status="error",
                    error=ErrorInfo(type="Error", message="Cannot find module 'example'"),
                )
            if step_id == "s5":
                return ExecResult(
                    id=step_id, status="error", error=ErrorInfo(type="ExitStatus", message="exit 1")
                )
            return ExecResult(id=step_id, status="ok")

        def restart(self, replay: list[tuple[StepKind, str]]) -> None:
            self.replays.append(replay)

        def close(self) -> None:
            pass

    sandbox = MixedSandbox()
    with CheckSession(
        sandbox, Settings(_env_file=None), c_system_headers=["curl/curl.h"]
    ) as session:
        session.start(["pandas"], ["example"])
        assert session.run(step(0, "const x = 1;", StepKind.JAVASCRIPT)).label == Label.WORKS
        assert session.run(step(1, "x = 2", StepKind.REPL)).label == Label.WORKS
        header = session.run(step(2, "#include <curl/curl.h>", StepKind.C))
        assert header.reason == "needs the system library curl/curl.h"
        missing = session.run(step(3, "require('example')", StepKind.JAVASCRIPT))
        assert missing.reason == "example didn't install in the sandbox, so this step couldn't run."
        assert session.run(step(4, "int f(void) { return 1; }", StepKind.C)).label == Label.WORKS
        assert session.run(step(5, "int main() { return f(); }", StepKind.C)).label == Label.BROKEN
        timeout = session.run(step(6, "while (true) {}", StepKind.TYPESCRIPT))
        assert timeout.label == Label.CANT_VERIFY
    assert sandbox.requests == [
        ("s0", "const x = 1;", "javascript"),
        ("s1", "x = 2", "python"),
        ("s3", "require('example')", "javascript"),
        ("s4", "int f(void) { return 1; }", "c"),
        ("s5", "int main() { return f(); }", "c"),
        ("s6", "while (true) {}", "typescript"),
    ]
    assert sandbox.replays == [
        [
            (StepKind.JAVASCRIPT, "const x = 1;"),
            (StepKind.REPL, "x = 2"),
            (StepKind.C, "int f(void) { return 1; }"),
        ]
    ]


def test_cascade_only_runs_for_python_kinds() -> None:
    class FailingSandbox:
        def start(self, packages: list[str], npm_packages: list[str] | None = None) -> Environment:
            return Environment()

        def exec(self, step_id: str, code: str, *, lang: str = "python") -> ExecResult:
            return ExecResult(
                id=step_id,
                status="error",
                error=ErrorInfo(type="NameError", message="name 'value' is not defined"),
            )

        def close(self) -> None:
            pass

    with CheckSession(FailingSandbox(), Settings(_env_file=None)) as session:
        session.start([])
        session.run(step(0, "value = missing()", StepKind.JAVASCRIPT))
        decision = session.run(step(1, "print(value)"))
        assert decision.reason == "NameError: name 'value' is not defined"
        session.run(step(2, "value = missing()"))
        decision = session.run(step(3, "value", StepKind.JAVASCRIPT))
        assert decision.reason == "NameError: name 'value' is not defined"
        assert session.run(step(4, "value")).reason == "depends on step 3, which is still broken"


@pytest.mark.parametrize(
    "source",
    [
        "train_data = open('missing-cascade-data.csv').read()",
        "train_data = input()",
        "import time\ntime.sleep(10)\ntrain_data = 1",
    ],
)
def test_unverified_source_and_transitive_dependents(session: CheckSession, source: str) -> None:
    assert session.run(step(0, source)).label == Label.CANT_VERIFY
    dependent = session.run(step(1, "processed = train_data.copy()"))
    assert dependent.label == Label.CANT_VERIFY
    assert dependent.reason == "depends on step 1, which couldn't be verified"
    transitive = session.run(step(2, "print(processed)"))
    assert transitive.label == Label.CANT_VERIFY
    assert transitive.reason == "depends on step 2, which couldn't be verified"

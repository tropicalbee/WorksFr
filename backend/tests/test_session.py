from pathlib import Path
from unittest.mock import Mock

import pytest
from support.local_sandbox import LocalRunnerSandbox

from app.config import Settings
from app.models import Block, Environment, ErrorInfo, ExecResult, Label, RepairAttempt, StepKind
from app.sandbox.interface import Sandbox, SandboxError, StepTimeoutError
from app.session import CheckSession, CheckTimeoutError
from app.step import Step

ROOT = Path(__file__).resolve().parents[2]


def step(idx: int, code: str, kind: StepKind = StepKind.PYTHON) -> Step:
    return Step(Block(idx=idx, kind=kind, code=code, runnable_code=code))


@pytest.fixture
def session() -> CheckSession:
    settings = Settings(_env_file=None, step_timeout_seconds=5)
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
    assert session.run(step(1, "import time\ntime.sleep(15)")).label == Label.CANT_VERIFY
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


def test_python_cascade_stays_isolated_from_other_languages() -> None:
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
    ("kind", "source", "dependent", "error", "source_label", "reason"),
    [
        (
            StepKind.JAVASCRIPT,
            "const value = missing();",
            "console.log(value);",
            ErrorInfo(type="ReferenceError", message="value is not defined"),
            Label.BROKEN,
            "is still broken",
        ),
        (
            StepKind.TYPESCRIPT,
            "const value: string = prompt();",
            "console.log(value);",
            ErrorInfo(type="ReferenceError", message="value is not defined"),
            Label.CANT_VERIFY,
            "couldn't be verified",
        ),
        (
            StepKind.C,
            "static int value = missing;",
            "int main(void) { return value; }",
            ErrorInfo(type="CompileError", message="step.c:2:3: error: ‘value’ undeclared"),
            Label.BROKEN,
            "is still broken",
        ),
        (
            StepKind.CPP,
            "int value() { return missing; }",
            "int main() { return value(); }",
            ErrorInfo(type="CompileError", message="error: 'value' was not declared in this scope"),
            Label.BROKEN,
            "is still broken",
        ),
    ],
)
def test_non_python_cascade_labels_dependents_unverifiable(
    kind: StepKind,
    source: str,
    dependent: str,
    error: ErrorInfo,
    source_label: Label,
    reason: str,
) -> None:
    sandbox = Mock(spec=Sandbox)
    sandbox.start.return_value = Environment()
    sandbox.exec.side_effect = [
        *(
            [ExecResult(id="s0", status="error", error=ErrorInfo(type="Error", message="oops"))]
            if source_label == Label.BROKEN
            else []
        ),
        ExecResult(id="s1", status="error", error=error),
    ]
    with CheckSession(sandbox, Settings(_env_file=None)) as session:
        session.start([])
        assert session.run(step(0, source, kind)).label == source_label
        decision = session.run(step(1, dependent, kind))
    assert decision.label == Label.CANT_VERIFY
    assert decision.reason == f"depends on step 1, which {reason}"


@pytest.mark.parametrize(
    ("source", "dependent_kind", "error_type"),
    [
        ("const value = missing();", StepKind.TYPESCRIPT, "ReferenceError"),
        ("const other = missing();", StepKind.JAVASCRIPT, "ReferenceError"),
        ("/* const value = 1; */ missing();", StepKind.JAVASCRIPT, "ReferenceError"),
        ("'const value = 1'; missing();", StepKind.JAVASCRIPT, "ReferenceError"),
        ("const value = missing();", StepKind.JAVASCRIPT, "TypeError"),
    ],
)
def test_non_python_cascade_requires_a_matching_error_and_declaration(
    source: str, dependent_kind: StepKind, error_type: str
) -> None:
    sandbox = Mock(spec=Sandbox)
    sandbox.start.return_value = Environment()
    sandbox.exec.side_effect = [
        ExecResult(id="s0", status="error", error=ErrorInfo(type="Error", message="oops")),
        ExecResult(
            id="s1",
            status="error",
            error=ErrorInfo(type=error_type, message="value is not defined"),
        ),
    ]
    with CheckSession(sandbox, Settings(_env_file=None)) as session:
        session.start([])
        assert session.run(step(0, source, StepKind.JAVASCRIPT)).label == Label.BROKEN
        decision = session.run(step(1, "console.log(value);", dependent_kind))
    assert decision.label == Label.BROKEN
    assert decision.reason == f"{error_type}: value is not defined"


def test_compiled_native_step_clears_missing_binding_and_stays_in_ledger() -> None:
    source = "int value = 1;"
    kind = StepKind.C
    failure = ErrorInfo(type="CompileError", message="error: 'value' undeclared")
    sandbox = Mock(spec=Sandbox)
    sandbox.start.return_value = Environment()
    sandbox.exec.side_effect = [
        ExecResult(id="s0", status="error", error=failure),
        ExecResult(id="s1", status="ok", result="compiled"),
        ExecResult(id="s2", status="error", error=failure),
    ]
    with CheckSession(sandbox, Settings(_env_file=None)) as session:
        session.start([])
        assert session.run(step(0, "int value = missing;", kind)).label == Label.BROKEN
        session.run(step(1, source, kind))
        assert session.ledger == [(1, kind, source)]
        decision = session.run(step(2, "int main(void) { return value; }", kind))
    assert decision.label == Label.BROKEN
    assert "undeclared" in decision.reason


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


@pytest.mark.parametrize("kind", [StepKind.C, StepKind.CPP])
def test_compile_only_step_is_cant_verify_but_stays_in_ledger(kind: StepKind) -> None:
    source = "int value(void) { return 42; }"
    sandbox = Mock(spec=Sandbox)
    sandbox.start.return_value = Environment()
    sandbox.exec.side_effect = [
        ExecResult(id="s0", status="ok", result="compiled"),
        ExecResult(id="s1", status="ok"),
    ]
    with CheckSession(sandbox, Settings(_env_file=None)) as session:
        session.start([])
        decision = session.run(step(0, source, kind))
        assert decision.label == Label.CANT_VERIFY
        assert decision.reason == "Compiled without errors, but it has no main(), so nothing ran."
        assert session.ledger == [(0, kind, source)]
        later = session.run(step(1, "int main(void) { return value() == 42 ? 0 : 1; }", kind))
    assert later.label == Label.WORKS


def test_non_python_cascade_is_limited_to_names_a_failed_step_declares() -> None:
    sandbox = Mock(spec=Sandbox)
    sandbox.start.return_value = Environment()
    reference = ErrorInfo(type="ReferenceError", message="total is not defined")
    sandbox.exec.side_effect = [
        ExecResult(id="s0", status="error", error=ErrorInfo(type="Error", message="oops")),
        ExecResult(id="s1", status="ok"),
        ExecResult(id="s2", status="error", error=reference),
        ExecResult(id="s3", status="error", error=reference),
    ]
    with CheckSession(sandbox, Settings(_env_file=None)) as session:
        session.start([])
        kind = StepKind.JAVASCRIPT
        assert session.run(step(0, "const total = missing();", kind)).label == Label.BROKEN
        # A later successful step that declares the name again ends the dependency.
        assert session.run(step(1, "const total = 1;", kind)).label == Label.WORKS
        assert session.run(step(2, "console.log(total);", kind)).label == Label.BROKEN
        # Without a failed step that declares the name there is no cascade at all.
        assert session.run(step(3, "console.log(total);", kind)).label == Label.BROKEN

from app.models import (
    Block,
    ExecResult,
    Label,
    LabelDecision,
    PatchVerdict,
    RepairAttempt,
    StepKind,
)
from app.step import Step


def attempt(code: str = "x = 2", verdict: PatchVerdict = PatchVerdict.FIXED) -> RepairAttempt:
    return RepairAttempt(
        attempt=1,
        patched_code=code,
        explanation="fixed",
        provider="test",
        result=ExecResult(id="s0-a1", status="ok", stdout="out", stderr="err"),
        decision=LabelDecision(label=Label.WORKS),
        verdict=verdict,
    )


def test_display_and_report() -> None:
    step = Step(
        Block(
            idx=0,
            kind=StepKind.PYTHON,
            code="!pip install x\n%inline\nx = 1",
            runnable_code="x = 1",
        )
    )
    assert step.display_code == step.block.code
    step.record_run(ExecResult(id="s0", status="error"), LabelDecision(label=Label.BROKEN))
    fixed = attempt()
    step.record_attempt(fixed)
    step.accept(fixed)
    assert step.executed_code == "x = 2"
    assert step.display_code == "!pip install x\n%inline\nx = 2"
    report = step.to_report()
    assert report.label == Label.BROKEN
    assert report.verdict == PatchVerdict.FIXED
    assert report.output == "out\n\nerr"
    assert report.error is None
    assert report.repair_attempts == [fixed]


def test_repl_failed_and_unrepaired_reports() -> None:
    step = Step(Block(idx=1, kind=StepKind.REPL, code=">>> x = 1", runnable_code="x = 1"))
    assert step.to_report().verdict is None
    failed = attempt(verdict=PatchVerdict.FAILED)
    step.record_attempt(failed)
    assert step.to_report().verdict == PatchVerdict.FAILED
    fixed = attempt()
    step.record_attempt(fixed)
    step.accept(fixed)
    assert step.display_code == "x = 2"


def test_bound_names_only_module_scope() -> None:
    code = (
        "a, (b, c) = (1, (2, 3))\nx: int = 1\nx += 1\nimport os.path\n"
        "from json import dumps as dump\ndef f():\n    hidden = 1\n"
        "class C:\n    other = 1\nfor item in []:\n    inside = 1\n"
        "with open('x') as handle:\n    via_with = 1\n"
    )
    step = Step(Block(idx=0, kind=StepKind.PYTHON, code=code, runnable_code=code))
    assert step.bound_names() == {
        "a",
        "b",
        "c",
        "x",
        "os",
        "dump",
        "f",
        "C",
        "item",
        "inside",
        "handle",
        "via_with",
    }
    step.block.runnable_code = "invalid("
    assert step.bound_names() == set()


def test_bound_names_ignore_other_languages() -> None:
    for kind in (StepKind.JAVASCRIPT, StepKind.TYPESCRIPT, StepKind.C, StepKind.CPP):
        code = "value = 1"
        step = Step(Block(idx=0, kind=kind, code=code, runnable_code=code))
        assert step.bound_names() == set()

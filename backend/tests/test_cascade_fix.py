from pathlib import Path

import httpx
import pytest
from test_pipeline import Cache
from test_pipeline_video import FakeSandbox
from test_session import step

from app.config import Settings
from app.extract.blocks import build_blocks
from app.extract.html_page import parse_html_blocks
from app.models import ErrorInfo, ExecResult, Label, LabelDecision, PatchVerdict, RepairAttempt
from app.repair.loop import RepairContext, repair_step
from app.session import CheckSession
from app.step import Step

PATCH = "features = [1, 2]\ntarget = [3, 4]"


@pytest.mark.parametrize("patch_works", [False, True])
def test_boston_replaced_names_cascade(patch_works: bool) -> None:
    html = Path(__file__).parent / "fixtures/sklearn_boston.html"
    blocks = build_blocks(parse_html_blocks(html.read_bytes()))
    first = next(block for block in blocks if "load_boston()" in (block.runnable_code or ""))
    first.idx = 0
    original = Step(first)
    assert {"boston", "X", "y"} <= original.bound_names()

    class Sandbox(FakeSandbox):
        def restart(self, replay: list) -> None:
            pass

        def exec(self, step_id: str, code: str, *, lang: str = "python") -> ExecResult:
            error = {
                "s0": ErrorInfo(type="ImportError", message="load_boston was removed"),
                "s1": ErrorInfo(type="NameError", message="name 'X' is not defined"),
                "s2": ErrorInfo(type="ValueError", message="different error"),
            }.get(step_id)
            if step_id == "s1-a1" and not patch_works:
                error = ErrorInfo(type="ValueError", message="patch failed")
            return ExecResult(id=step_id, status="error" if error else "ok", error=error)

    class Proposer:
        calls = 0

        def propose(self, messages: list[dict[str, str]]):
            from app.models import RepairProposal

            self.calls += 1
            assert "Step 1's fix replaced these names" in messages[-1]["content"]
            assert "features = [1, 2]" in messages[-1]["content"]
            return RepairProposal(
                patched_code="print(features)", explanation="Use replacement"
            ), "test"

    settings = Settings(_env_file=None, providers=[])
    with (
        CheckSession(Sandbox(), settings) as session,
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as client,
    ):
        session.start([])
        assert session.run(original).label == Label.BROKEN
        keep_names = PATCH + "\n" + "\n".join(f"{name} = None" for name in original.bound_names())
        result, decision, verdict = session.try_patch(original, PATCH, 1)
        session.accept(
            original,
            RepairAttempt(
                attempt=1,
                patched_code=PATCH,
                explanation="Swap data",
                result=result,
                decision=decision,
                verdict=verdict,
            ),
        )
        dependent = step(1, "print(X)")
        assert session.run(dependent) == LabelDecision(
            label=Label.CANT_VERIFY, reason="depends on step 1, whose fix replaced X"
        )
        proposer = Proposer()
        context = RepairContext(None, PATCH, {}, [], settings, client, Cache())
        outcome = repair_step(session, dependent, context, proposer)
        assert proposer.calls == 1
        if patch_works:
            assert outcome.verdict == PatchVerdict.FIXED
            assert dependent.to_report().label == Label.BROKEN and dependent.to_report().fixed
        else:
            assert dependent.to_report().label == Label.CANT_VERIFY
        assert session.run(step(2, "print(X)")).label == Label.BROKEN
        assert session.run(step(3, "print(features)")).label == Label.WORKS

    with CheckSession(Sandbox(), settings) as session:
        session.start([])
        original = Step(first)
        session.run(original)
        result, decision, verdict = session.try_patch(original, keep_names, 1)
        session.accept(
            original,
            RepairAttempt(
                attempt=1,
                patched_code=keep_names,
                explanation="Keep names",
                result=result,
                decision=decision,
                verdict=verdict,
            ),
        )
        assert session.run(step(1, "print(X)")).label == Label.BROKEN


@pytest.mark.parametrize(
    ("code", "attribute", "label"),
    [
        ("boston.feature_names", "feature_names", Label.CANT_VERIFY),
        ("df.data", "data", Label.BROKEN),
        ("boston.feature_names )", "feature_names", Label.BROKEN),
    ],
)
def test_attribute_error_identifies_the_base_name(code: str, attribute: str, label: Label) -> None:
    class Sandbox(FakeSandbox):
        def restart(self, replay: list) -> None:
            pass

        def exec(self, step_id: str, code: str, *, lang: str = "python") -> ExecResult:
            error = None
            if step_id == "s0":
                error = ErrorInfo(type="ImportError", message="Old API")
            if step_id == "s1":
                error = ErrorInfo(
                    type="AttributeError",
                    message=f"'DataFrame' object has no attribute '{attribute}'",
                    traceback='  File "<step-s1>", line 1, in <module>',
                )
            return ExecResult(id=step_id, status="error" if error else "ok", error=error)

    with CheckSession(Sandbox(), Settings(_env_file=None)) as session:
        session.start([])
        original = step(0, "boston = None\ndata = None")
        session.run(original)
        result, decision, verdict = session.try_patch(original, PATCH, 1)
        session.accept(
            original,
            RepairAttempt(
                attempt=1,
                patched_code=PATCH,
                explanation="Swap data",
                result=result,
                decision=decision,
                verdict=verdict,
            ),
        )
        dependent = session.run(step(1, code))
        assert dependent.label == label
        if label == Label.CANT_VERIFY:
            assert dependent.reason == "depends on step 1, whose fix replaced boston"

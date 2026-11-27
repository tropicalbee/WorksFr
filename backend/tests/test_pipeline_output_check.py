import httpx
from test_pipeline import Cache, Proposer, Sink
from test_pipeline_video import FakeSandbox

from app.config import Settings
from app.events import StepFinalEvent
from app.extract import PastedCode
from app.models import ErrorInfo, ExecResult, Label, OutputMatch, StepKind
from app.pipeline import PipelineDeps, run_check


class OutputSandbox(FakeSandbox):
    def restart(self, replay: list[tuple[StepKind, str]]) -> None:
        pass

    def exec(self, step_id: str, code: str, *, lang: str = "python") -> ExecResult:
        if code == "int('bad')":
            return ExecResult(
                id=step_id,
                status="error",
                stdout="old",
                error=ErrorInfo(type="ValueError", message="invalid integer"),
            )
        if code == "int('42')":
            return ExecResult(id=step_id, status="ok", result="42")
        return ExecResult(id=step_id, status="ok", stdout="1", result="3")


def check_paste(text: str) -> Sink:
    sink = Sink()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as client:
        deps = PipelineDeps(
            Settings(_env_file=None, providers=[]),
            lambda _: OutputSandbox(),
            Proposer(),
            client,
            Cache(),
        )
        run_check("output-test", PastedCode(code=text), deps, sink)
    assert sink.events[-1].type == "done"
    return sink


def test_output_checks_reach_final_events_and_score() -> None:
    sink = check_paste(
        "```python\nprint(1)\n```\n```output\n1\n```\n"
        "```python\nprint(1)\n```\n```output\n2\n```\n```python\nx = 1\n```"
    )
    steps = [event.step for event in sink.events if isinstance(event, StepFinalEvent)]
    assert steps[0].output_check.status == OutputMatch.MATCHES
    assert steps[2].output_check.status == OutputMatch.DIFFERS
    assert steps[4].output_check is None
    score = sink.events[-1].score
    assert (score.output_compared, score.output_differs) == (2, 1)
    assert (score.ratio, score.still_run, score.checked) == (1.0, 3, 3)


def test_accepted_patch_is_compared_without_changing_label() -> None:
    sink = check_paste("```python\nint('bad')\n```\n```output\n42\n```")
    final = next(event.step for event in sink.events if isinstance(event, StepFinalEvent))
    assert final.label == Label.BROKEN and final.fixed
    assert final.output_check.status == OutputMatch.MATCHES
    assert final.output_check.actual == "42"


def test_repl_with_multiple_values_is_not_compared() -> None:
    sink = check_paste("```pycon\n>>> 2\n2\n>>> print(1)\n1\n>>> 3\n3\n```")
    final = next(event.step for event in sink.events if isinstance(event, StepFinalEvent))
    assert final.output_check.status == OutputMatch.NOT_COMPARED
    assert final.output_check.reason == (
        "This REPL block shows several values, but only the last one is captured."
    )
    assert sink.events[-1].score.output_compared == 0

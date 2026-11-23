from app.models import ErrorInfo, Label, Score, StepKind, StepReport
from app.score import compute_score


def step(idx: int, label: Label | None, fixed: bool = False) -> StepReport:
    return StepReport(
        idx=idx,
        kind=StepKind.PYTHON,
        original_code="x",
        final_code="x",
        label=label,
        fixed=fixed,
        error=ErrorInfo(type="E", message="m") if label == Label.BROKEN else None,
    )


def test_empty():
    score = compute_score([])
    assert score == Score()
    assert score.checked == 0
    assert score.still_run == 0


def test_counts_by_label():
    steps = [
        step(0, Label.WORKS),
        step(1, Label.WORKS),
        step(2, Label.DEPRECATED),
        step(3, Label.BROKEN),
        step(4, Label.CANT_VERIFY),
    ]
    score = compute_score(steps)
    assert (score.works, score.deprecated, score.broken, score.cant_verify) == (2, 1, 1, 1)
    assert score.fixed == 0
    assert score.still_run == 3
    assert score.checked == 4


def test_none_labels_skipped():
    score = compute_score([step(0, None), step(1, None, fixed=True), step(2, Label.WORKS)])
    assert score == Score(works=1)


def test_fixed_counts_only_broken_and_stays_in_broken():
    steps = [
        step(0, Label.BROKEN, fixed=True),
        step(1, Label.BROKEN, fixed=False),
        step(2, Label.BROKEN, fixed=True),
        step(3, Label.WORKS, fixed=True),
        step(4, Label.CANT_VERIFY, fixed=True),
    ]
    score = compute_score(steps)
    assert score.broken == 3
    assert score.fixed == 2
    assert score.works == 1
    assert score.cant_verify == 1

import pytest

from app.models import ExecResult
from app.output_check import OutputMatch, check_output


def result(stdout: str, value: str | None = None) -> ExecResult:
    return ExecResult(id="1", status="ok", stdout=stdout, result=value)


def test_absent_shown_output() -> None:
    assert check_output(None, result("anything"), "") is None


def test_exact_match_normalizes_newlines_and_blank_lines() -> None:
    comparison = check_output("\r\nA  \r\n\r\n\r\nB\r\n", result("A\n\nB"), "")
    assert comparison is not None
    assert comparison.status == OutputMatch.MATCHES
    assert comparison.ignored == []


@pytest.mark.parametrize(
    ("shown", "actual", "label"),
    [
        ("<History at 0xa1b2>", "<History at 0xc3d4>", "memory addresses"),
        ("2026-11-27 at 12:05", "2026-11-28 at 14:10:06.25", "dates and times"),
        ("completed in 138ms", "completed in 2.5s", "durations"),
        ("Done", "100%|##########| 10/10\nDone", "progress bars"),
        ("Done", "10/10 \u2501\u2501\u2501 6ms/step\nDone", "progress bars"),
        ("Done", "10/10 [=========>] 6ms/step\nDone", "progress bars"),
        ("col  value\n1  42", "col\tvalue\n1 42", "spacing"),
        ("  row", "\trow", "spacing"),
        ("time 7:05", "time 10:15", "dates and times"),
        ("0.67", "0.6712", "float digits"),
        ("[1.92695, 0.72945]", "[1.9269507873731737, 0.72945462740384615]", "float digits"),
    ],
)
def test_loose_rule(shown: str, actual: str, label: str) -> None:
    comparison = check_output(shown, result(actual), "")
    assert comparison is not None
    assert comparison.status == OutputMatch.MATCHES_IGNORING
    assert label in comparison.ignored


@pytest.mark.parametrize(
    ("shown", "actual"),
    [("value 5", "value 6"), ("name  0.67", "value 0.6712"), ("value 0.67", "value 0.6812")],
)
def test_real_difference(shown: str, actual: str) -> None:
    comparison = check_output(shown, result(actual), "")
    assert comparison is not None
    assert comparison.status == OutputMatch.DIFFERS
    assert "float digits" not in comparison.ignored


def test_difference_has_no_ignored_rules() -> None:
    comparison = check_output("0xaaaa value 5", result("0xbbbb value 6"), "")
    assert comparison is not None
    assert comparison.status == OutputMatch.DIFFERS
    assert comparison.ignored == []


@pytest.mark.parametrize(
    ("execution", "code", "reason", "actual"),
    [
        (None, "", "no run", ""),
        (ExecResult(id="1", status="error", stdout="partial"), "", "failed", "partial"),
        (result("partial\n[truncated]"), "", "truncated", "partial\n[truncated]"),
        (result("draw"), "np.random.rand()", "seed", "draw"),
    ],
)
def test_not_compared(execution: ExecResult | None, code: str, reason: str, actual: str) -> None:
    comparison = check_output("shown", execution, code)
    assert comparison is not None
    assert comparison.status == OutputMatch.NOT_COMPARED
    assert reason in comparison.reason.lower()
    assert comparison.actual == actual


@pytest.mark.parametrize(
    ("code", "earlier"),
    [
        ("random.random()", "random.seed(42)"),
        ("random.seed(42)\nrandom.random()", ""),
        ("np.random.default_rng(42).random()", ""),
    ],
)
def test_seeded_random_code_is_comparable(code: str, earlier: str) -> None:
    comparison = check_output("draw", result("draw"), code, earlier)
    assert comparison is not None
    assert comparison.status == OutputMatch.MATCHES


def test_stdout_and_expression_result_variant() -> None:
    stdout = "Found 2048 images belonging to 2 classes.\n"
    value = "[1.9269507873731737, 0.72945462740384615]"
    comparison = check_output(stdout + value, result(stdout, value), "")
    assert comparison is not None
    assert comparison.status == OutputMatch.MATCHES
    assert comparison.actual == stdout + value


def test_stdout_exact_match_wins_over_loose_expression_variant() -> None:
    comparison = check_output("A", result("A", "12:00"), "")
    assert comparison is not None
    assert comparison.status == OutputMatch.MATCHES
    assert comparison.actual == "A"


def test_loose_rules_report_labels_in_order() -> None:
    shown = "0xaaaa  2026-11-27 42ms 0.67"
    actual = "100%|######| 6/6\n0xbbbb 2026-11-28 3s 0.6712"
    comparison = check_output(shown, result(actual), "")
    assert comparison is not None
    assert comparison.status == OutputMatch.MATCHES_IGNORING
    assert comparison.ignored == [
        "memory addresses",
        "dates and times",
        "durations",
        "progress bars",
        "spacing",
        "float digits",
    ]

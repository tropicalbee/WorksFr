"""Freshness score: counts of step labels. The ratios live on `Score` itself."""

from app.models import Label, OutputMatch, PatchVerdict, Score, StepReport


def compute_score(steps: list[StepReport]) -> Score:
    """Count steps by label. Steps without a label (blocks that never run) are skipped."""
    counts = dict.fromkeys(Label, 0)
    fixed = 0
    fixed_unverified = 0
    output_compared = 0
    output_differs = 0
    for step in steps:
        if step.output_check and step.output_check.status != OutputMatch.NOT_COMPARED:
            output_compared += 1
            output_differs += step.output_check.status == OutputMatch.DIFFERS
        if step.label is None:
            continue
        counts[step.label] += 1
        if step.label == Label.BROKEN and step.verdict == PatchVerdict.FIXED:
            fixed += 1
        if step.label == Label.BROKEN and step.verdict == PatchVerdict.FIXED_UNVERIFIED:
            fixed_unverified += 1
    return Score(
        works=counts[Label.WORKS],
        deprecated=counts[Label.DEPRECATED],
        broken=counts[Label.BROKEN],
        cant_verify=counts[Label.CANT_VERIFY],
        fixed=fixed,
        fixed_unverified=fixed_unverified,
        output_compared=output_compared,
        output_differs=output_differs,
    )

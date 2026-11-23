"""Downloadable reports preserve labels and accepted code in notebook and script form."""

import nbformat

from app.models import CheckReport, StepKind, StepReport


def _header(report: CheckReport) -> str:
    score = report.score
    freshness = f"{score.ratio:.0%}" if score and score.ratio is not None else "unavailable"
    versions = (
        "\n".join(f"- {name}: {version}" for name, version in report.versions.items())
        or "- None recorded"
    )
    failed = ", ".join(report.failed_packages) or "None"
    return (
        f"# {report.title or 'WorksFr report'}\n\nFreshness: {freshness}"
        f"\n\nVersions:\n{versions}\n\nFailed packages: {failed}"
    )


def _step_header(step: StepReport) -> str:
    label = step.label.value if step.label else "skipped"
    verdict = f"; {step.verdict.value}" if step.verdict else ""
    return f"Step {step.idx + 1}: {label}{verdict}\n{step.explanation or step.reason}"


def notebook_bytes(report: CheckReport) -> bytes:
    cells = [nbformat.v4.new_markdown_cell(_header(report))]
    for step in report.steps:
        cells.append(nbformat.v4.new_markdown_cell(_step_header(step)))
        cells.append(nbformat.v4.new_code_cell(step.final_code))
    return nbformat.writes(nbformat.v4.new_notebook(cells=cells)).encode("utf-8")


def _comments(text: str) -> str:
    return "\n".join("# " + line for line in text.splitlines())


def python_text(report: CheckReport) -> str:
    parts = [_comments(_header(report))]
    for step in report.steps:
        parts.append(_comments(_step_header(step)))
        if step.kind in (StepKind.SHELL, StepKind.OUTPUT, StepKind.OTHER):
            parts.append(_comments(step.final_code))
        else:
            parts.append(
                "\n".join(
                    "# " + line if line.lstrip().startswith(("!", "%")) else line
                    for line in step.final_code.splitlines()
                )
            )
    return "\n\n".join(parts) + "\n"

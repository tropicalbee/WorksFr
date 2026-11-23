import ast

import nbformat

from app.downloads import notebook_bytes, python_text
from app.models import CheckReport, CheckStatus, Label, Score, SourceKind, StepKind, StepReport


def test_downloads_are_valid_and_keep_metadata() -> None:
    report = CheckReport(
        id="c1",
        source_kind=SourceKind.NOTEBOOK,
        source="test.ipynb",
        status=CheckStatus.DONE,
        score=Score(works=1),
        versions={"example": "1"},
        failed_packages=["missing"],
        steps=[
            StepReport(
                idx=0,
                kind=StepKind.PYTHON,
                original_code="!pip install example\nx = 1",
                final_code="!pip install example\nx = 1",
                label=Label.WORKS,
            )
        ],
    )
    notebook = nbformat.reads(notebook_bytes(report).decode(), as_version=4)
    nbformat.validate(notebook)
    assert "missing" in notebook.cells[0].source
    assert "example: 1" in notebook.cells[0].source
    assert notebook.cells[-1].source == report.steps[0].final_code
    script = python_text(report)
    ast.parse(script)
    assert "# !pip install example" in script
    assert "x = 1" in script

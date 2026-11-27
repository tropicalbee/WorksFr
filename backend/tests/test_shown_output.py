from pathlib import Path

import nbformat
import pytest

from app.extract import extract_notebook, extract_paste
from app.extract.blocks import build_blocks, classify
from app.extract.html_page import parse_html_blocks
from app.extract.markdown_doc import parse_markdown
from app.models import StepKind

FIXTURES = Path(__file__).parent / "fixtures"


def test_keras_notebook_shown_output() -> None:
    fixture = FIXTURES / "keras_notebook.ipynb"
    extraction = extract_notebook(fixture.name, fixture.read_bytes())
    shown = [block.shown_output for block in extraction.blocks if block.shown_output is not None]
    assert len(shown) == 13
    assert "[1.9269507873731737, 0.72945462740384615]" in shown
    assert "Found 2048 images belonging to 2 classes.\n" in shown


def test_notebook_error_stderr_and_display_data_are_ignored() -> None:
    v4 = nbformat.v4
    stdout = v4.new_output("stream", name="stdout", text="before error\n")
    notebook = v4.new_notebook(
        cells=[
            v4.new_code_cell(
                "x = 1", outputs=[v4.new_output("stream", name="stderr", text="warning")]
            ),
            v4.new_code_cell("x = 2", outputs=[stdout, v4.new_output("error", ename="ValueError")]),
            v4.new_code_cell(
                "x = 3", outputs=[v4.new_output("display_data", data={"text/plain": "3"})]
            ),
        ]
    )
    result = extract_notebook("example.ipynb", nbformat.writes(notebook).encode())
    assert [block.shown_output for block in result.blocks] == [None, None, None]


def test_repl_keeps_output_and_strips_prompts_from_runnable_code() -> None:
    block = classify(">>> for i in range(2):\n...     print(i)\n...\n0\n1", None)
    assert block.kind == StepKind.REPL
    assert block.runnable_code == "for i in range(2):\n    print(i)"
    assert block.shown_output == "0\n1"


def test_saved_html_output_is_paired_with_previous_step() -> None:
    blocks = build_blocks(parse_html_blocks((FIXTURES / "openai_tds.html").read_bytes()))
    assert [block.kind for block in blocks[:2]] == [StepKind.PYTHON, StepKind.OUTPUT]
    assert blocks[0].shown_output == blocks[1].code
    assert blocks[1].shown_output is None


@pytest.mark.parametrize("markup", ["html", "markdown"])
def test_page_output_pairing_rules(markup: str) -> None:
    texts = [
        ("python", "x = 1"),
        ("python", "print(x)"),
        ("output", "1"),
        ("output", "second output"),
        ("python", "raise ValueError()"),
        ("output", "Traceback (most recent call last):\nValueError"),
    ]
    if markup == "html":
        raw = parse_html_blocks(
            "\n".join(
                f'<pre><code class="language-{hint}">{code}</code></pre>' for hint, code in texts
            )
        )
    else:
        _, raw = parse_markdown("\n".join(f"```{hint}\n{code}\n```" for hint, code in texts))
    blocks = build_blocks(raw)
    assert [block.code for block in blocks] == [code for _, code in texts]
    assert [block.shown_output for block in blocks] == [None, "1", None, None, None, None]


def test_fenced_paste_keeps_repl_output_over_adjacent_output() -> None:
    extraction = extract_paste("```pycon\n>>> print(1)\n1\n```\n```output\n2\n```", None)
    assert [block.shown_output for block in extraction.blocks] == ["1", None]

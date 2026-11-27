from app.extract.blocks import build_blocks, classify
from app.extract.html_page import parse_html_blocks
from app.extract.markdown_doc import parse_markdown
from app.models import StepKind


def test_markdown_code_prefix_and_output() -> None:
    _, raw = parse_markdown("```\nx = 1\nprint(x)\nName  Count\nrow   1\n```")
    (block,) = build_blocks(raw)
    assert block.runnable_code == "x = 1\nprint(x)"
    assert block.shown_output == "Name  Count\nrow   1"


def test_html_marked_lines() -> None:
    html = (
        "<pre><code class='language-python'>print(1)\n<em>1</em>\nprint(2)\n<em>2</em></code></pre>"
    )
    (block,) = build_blocks(parse_html_blocks(html))
    assert block.runnable_code == "print(1)\n\nprint(2)"
    assert block.shown_output == "1\n2"


def test_partial_line_span_is_not_compared() -> None:
    (block,) = build_blocks(parse_html_blocks("<pre>print(1)<em> 1</em></pre>"))
    assert block.runnable_code == "print(1)"
    assert block.shown_output is None


def test_mixed_repl_in_markdown() -> None:
    _, raw = parse_markdown("```python\nimport math\ny = 2\n>>> x = 1\n>>> x\n1\n```")
    (block,) = build_blocks(raw)
    assert block.kind == StepKind.REPL
    assert block.runnable_code == "import math\ny = 2\nx = 1\nx"
    assert block.shown_output == "1"


def test_invalid_mixed_prefix_keeps_existing_behavior() -> None:
    text = "out: ?\n>>> print(1)\n1"
    block = classify(text, "python")
    assert block.kind == StepKind.PYTHON
    assert block.runnable_code == text
    assert block.shown_output is None


def test_pure_repl_and_plain_code() -> None:
    repl = classify(">>> print(1)\n1", "pycon")
    plain = classify("print(1)", None)
    assert repl.kind == StepKind.REPL and repl.runnable_code == "print(1)"
    assert repl.shown_output == "1"
    assert plain.kind == StepKind.PYTHON and plain.runnable_code == "print(1)"
    assert plain.shown_output is None

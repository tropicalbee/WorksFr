"""Pasted code: Markdown fences, plain code split at blank lines, shell lines, fallback."""

from app.extract.paste import extract_paste
from app.models import SourceFormat, SourceKind, StepKind


def test_fenced_markdown_blocks_use_fence_language_and_ignore_prose() -> None:
    text = (
        "Intro prose that is not code.\n\n"
        "```bash\npip install requests\n```\n\n"
        "More prose.\n\n"
        "```python\nimport requests\nprint(requests.__version__)\n```\n\n"
        "```output\n2.32.0\n```\n"
    )
    result = extract_paste(text, None)
    assert [b.kind for b in result.blocks] == [StepKind.SHELL, StepKind.PYTHON, StepKind.OUTPUT]
    assert [b.idx for b in result.blocks] == [0, 1, 2]
    assert all(b.context == "" for b in result.blocks)
    assert result.blocks[1].runnable_code == "import requests\nprint(requests.__version__)"
    assert result.packages == ["requests"]
    assert result.source_kind == SourceKind.PASTE
    assert result.source_format == SourceFormat.PASTE
    assert result.source == "Pasted code"
    assert result.title == "Pasted code"
    assert result.published is None


def test_given_title_is_used() -> None:
    assert extract_paste("x = 1", "My snippet").title == "My snippet"
    assert extract_paste("x = 1", "   ").title == "Pasted code"


def test_plain_code_splits_at_blank_lines_between_statements() -> None:
    result = extract_paste("import numpy as np\nx = np.arange(3)\n\n\nprint(x)\ny = 2\n", None)
    assert [b.code for b in result.blocks] == [
        "import numpy as np\nx = np.arange(3)",
        "print(x)\ny = 2",
    ]
    assert all(b.kind == StepKind.PYTHON for b in result.blocks)
    assert all(b.runnable_code == b.code for b in result.blocks)
    assert result.packages == ["numpy"]


def test_function_with_internal_blank_lines_stays_whole() -> None:
    code = (
        "def f(a):\n    b = a + 1\n\n    return b\n\n\nclass C:\n    x = 1\n\n    y = 2\n\nf(1)\n"
    )
    result = extract_paste(code, None)
    assert [b.code.split("\n")[0] for b in result.blocks] == ["def f(a):", "class C:", "f(1)"]
    assert "    b = a + 1\n\n    return b" in result.blocks[0].code


def test_comment_before_a_statement_goes_with_it() -> None:
    result = extract_paste("a = 1\n\n# second step\nb = 2\n", None)
    assert [b.code for b in result.blocks] == ["a = 1", "# second step\nb = 2"]


def test_shell_lines_become_shell_blocks() -> None:
    code = (
        "!pip install pandas\n%matplotlib inline\n\nimport pandas\n"
        "$ pip install rich\npip install tqdm\n"
    )
    result = extract_paste(code, None)
    kinds = [b.kind for b in result.blocks]
    assert kinds == [StepKind.SHELL, StepKind.PYTHON, StepKind.SHELL]
    assert result.blocks[0].code == "!pip install pandas\n%matplotlib inline"
    assert result.blocks[1].code == "import pandas"
    assert result.blocks[2].code == "$ pip install rich\npip install tqdm"
    assert "pandas" in result.packages


def test_bang_lines_alone_are_shell_and_percent_lines_alone_are_other() -> None:
    assert extract_paste("!ls\n!pwd", None).blocks[0].kind == StepKind.SHELL
    assert extract_paste("%load_ext x", None).blocks[0].kind == StepKind.OTHER


def test_unparsable_text_is_one_python_step() -> None:
    text = "def broken(:\n    pass\n\nprint 'hello'\n"
    result = extract_paste(text, None)
    assert len(result.blocks) == 1
    assert result.blocks[0].kind == StepKind.PYTHON
    assert result.blocks[0].code == text.strip()
    assert result.blocks[0].runnable_code == text.strip()


def test_empty_text_has_no_blocks() -> None:
    assert extract_paste("  \n\n", None).blocks == []

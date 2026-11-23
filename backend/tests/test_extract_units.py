"""Focused unit tests for extraction helpers: routing, kinds, REPL, notebook magics, packages."""

import json

import httpx
import pytest

from app.extract import ExtractionError, extract_url, route_url
from app.extract.blocks import RawBlock, build_blocks, classify_notebook_cell, strip_repl
from app.extract.html_page import parse_html_blocks
from app.extract.libs import (
    IMPORT_TO_PYPI,
    detect_packages,
    packages_from_imports,
    packages_from_pip_line,
    packages_from_shell,
)
from app.extract.markdown_doc import parse_markdown
from app.models import Block, StepKind


@pytest.fixture(autouse=True)
def public_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda host: ["93.184.216.34"])


# URL routing


@pytest.mark.parametrize(
    ("url", "kind", "fetch_first"),
    [
        (
            "https://github.com/langchain-ai/langchain",
            "github_readme",
            "https://raw.githubusercontent.com/langchain-ai/langchain/HEAD/README.md",
        ),
        (
            "https://github.com/o/r/blob/main/docs/README.md",
            "github_readme",
            "https://raw.githubusercontent.com/o/r/main/docs/README.md",
        ),
        (
            "https://github.com/o/r/tree/dev/sub",
            "github_readme",
            "https://raw.githubusercontent.com/o/r/dev/sub/README.md",
        ),
        (
            "https://raw.githubusercontent.com/o/r/main/README.md",
            "github_readme",
            "https://raw.githubusercontent.com/o/r/main/README.md",
        ),
        (
            "https://github.com/o/r/blob/master/nb/demo.ipynb",
            "notebook",
            "https://raw.githubusercontent.com/o/r/master/nb/demo.ipynb",
        ),
        (
            "https://example.com/files/demo.ipynb",
            "notebook",
            "https://example.com/files/demo.ipynb",
        ),
        ("https://github.com/features/copilot", "html", "https://github.com/features/copilot"),
        ("https://example.com/post", "html", "https://example.com/post"),
    ],
)
def test_route_fetch_urls(url: str, kind: str, fetch_first: str) -> None:
    route = route_url(url)
    assert route.kind == kind
    assert route.fetch_urls[0] == fetch_first


@pytest.mark.parametrize(
    ("url", "question_id", "answer_id"),
    [
        ("https://stackoverflow.com/a/77292230", None, 77292230),
        ("https://stackoverflow.com/a/77292230/10347145", None, 77292230),
        ("https://stackoverflow.com/questions/77280761/some-title/77292230", 77280761, 77292230),
        ("https://stackoverflow.com/questions/77280761/some-title#77292230", 77280761, 77292230),
        ("https://stackoverflow.com/questions/77280761/some-title", 77280761, None),
        ("https://stackoverflow.com/q/77280761", 77280761, None),
        ("https://www.stackoverflow.com/questions/77280761", 77280761, None),
    ],
)
def test_route_stackoverflow(url: str, question_id: int | None, answer_id: int | None) -> None:
    route = route_url(url)
    assert route.kind == "stackoverflow"
    assert (route.question_id, route.answer_id) == (question_id, answer_id)


def test_stackoverflow_non_question_pages_are_html() -> None:
    assert route_url("https://stackoverflow.com/users/1/someone").kind == "html"


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def test_stackoverflow_question_picks_accepted_then_top_voted() -> None:
    def answer(aid: int, score: int, accepted: bool, code: str) -> dict[str, object]:
        return {
            "answer_id": aid,
            "question_id": 5,
            "score": score,
            "is_accepted": accepted,
            "creation_date": 1697274598,
            "body": f"<p>try</p><pre><code>{code}</code></pre>",
        }

    def reply(items: list[dict[str, object]]) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/2.3/questions/5/answers":
                return httpx.Response(200, json={"items": items})
            if request.url.path == "/2.3/questions/5":
                return httpx.Response(200, json={"items": [{"title": "A &amp; B"}]})
            return httpx.Response(404)

        return httpx.MockTransport(handler)

    items = [answer(1, 10, False, "x = 1"), answer(2, 3, True, "y = 2")]
    with _client(reply(items)) as client:
        result = extract_url("https://stackoverflow.com/questions/5/title", client)
    assert result.blocks[0].code == "y = 2"
    assert result.title == "A & B"
    assert str(result.published) == "2023-10-14"

    with _client(reply([answer(1, 10, False, "x = 1"), answer(2, 3, False, "y = 2")])) as client:
        result = extract_url("https://stackoverflow.com/questions/5/title", client)
    assert result.blocks[0].code == "x = 1"


def test_http_errors_become_extraction_errors() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    with _client(transport) as client, pytest.raises(ExtractionError):
        extract_url("https://example.com/post", client)


def test_missing_readme_candidates_raise() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    with _client(transport) as client, pytest.raises(ExtractionError):
        extract_url("https://github.com/o/r", client)


def test_readme_falls_back_to_next_candidate_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/readme.md"):
            return httpx.Response(200, text="# T\n\n```python\nimport numpy\n```\n")
        return httpx.Response(404)

    with _client(httpx.MockTransport(handler)) as client:
        result = extract_url("https://github.com/o/r", client)
    assert result.packages == ["numpy"]
    assert result.title == "T"


# Kinds


def _kinds(raws: list[RawBlock]) -> list[StepKind]:
    return [b.kind for b in build_blocks(raws)]


def test_kind_table() -> None:
    raws = [
        RawBlock("x = 1\nprint(x)"),
        RawBlock(">>> x = 1\n>>> x\n1"),
        RawBlock("pip install pandas==1.3"),
        RawBlock("{'a': 1, 'b': [1, 2]}"),
        RawBlock("Traceback (most recent call last):\n  File 'x', line 1\nKeyError: 'a'"),
        RawBlock("const a = 1;", hint="javascript"),
        RawBlock("key: value", hint="yaml"),
        RawBlock("npm update openai"),
        RawBlock("   \n\n"),
    ]
    assert _kinds(raws) == [
        StepKind.PYTHON,
        StepKind.REPL,
        StepKind.SHELL,
        StepKind.OUTPUT,
        StepKind.OUTPUT,
        StepKind.OTHER,
        StepKind.OTHER,
        StepKind.SHELL,
    ]


def test_runnable_code_is_none_for_non_running_kinds() -> None:
    blocks = build_blocks(
        [RawBlock("pip install x"), RawBlock("{'a': 1}"), RawBlock("a: 1", "yaml")]
    )
    assert [b.runnable_code for b in blocks] == [None, None, None]


def test_markup_decides_when_code_alone_is_ambiguous() -> None:
    # A bare dict literal is output without markup, a step when the page says python.
    assert _kinds([RawBlock("{'a': 1}")]) == [StepKind.OUTPUT]
    assert _kinds([RawBlock("{'a': 1}", hint="python")]) == [StepKind.PYTHON]
    # Pasted output marked with <em> is removed from what runs.
    block = build_blocks([RawBlock("print(1)\n1\n", without_output="print(1)\n")])[0]
    assert block.kind is StepKind.PYTHON
    assert block.runnable_code == "print(1)"
    assert block.code == "print(1)\n1"


def test_unparseable_python_is_still_python() -> None:
    block = build_blocks([RawBlock("import os\nprint 'hello'")])[0]
    assert block.kind is StepKind.PYTHON


def test_code_followed_by_unmarked_output() -> None:
    block = build_blocks([RawBlock("print(arr)\n[1. 2. 3.]")])[0]
    assert block.kind is StepKind.PYTHON
    assert block.runnable_code == "print(arr)"


def test_python_tagged_pip_line_is_shell() -> None:
    assert _kinds([RawBlock("pip install requests", hint="python")]) == [StepKind.SHELL]


def test_repl_strips_prompts_and_output() -> None:
    text = ">>> import math\n>>> for i in range(2):\n...     print(i)\n...\n0\n1\n>>> math.pi\n3.14"
    assert strip_repl(text) == "import math\nfor i in range(2):\n    print(i)\n\nmath.pi"
    block = build_blocks([RawBlock(text)])[0]
    assert block.kind is StepKind.REPL
    assert block.code == text
    assert block.runnable_code == strip_repl(text)


def test_repl_hint_without_prompts_is_python() -> None:
    assert _kinds([RawBlock("x = 1", hint="pycon")]) == [StepKind.PYTHON]


# Notebook magics


def test_notebook_removes_bang_and_percent_lines() -> None:
    block = classify_notebook_cell(
        "%matplotlib inline\n!pip install pillow\nimport numpy as np\n!ls -la \\\n  data\nx = 1\n"
    )
    assert block is not None
    assert block.kind is StepKind.PYTHON
    assert block.runnable_code == "import numpy as np\nx = 1"
    assert block.code.startswith("%matplotlib inline")


def test_notebook_comment_left_over_stays_python() -> None:
    block = classify_notebook_cell("# setup\n!pip install pillow")
    assert block is not None
    assert block.kind is StepKind.PYTHON
    assert block.runnable_code == "# setup"


def test_notebook_cells_with_nothing_left() -> None:
    bang = classify_notebook_cell("!pip install pillow")
    magic = classify_notebook_cell("%matplotlib inline")
    assert bang is not None
    assert magic is not None
    assert (bang.kind, bang.runnable_code) == (StepKind.SHELL, None)
    assert (magic.kind, magic.runnable_code) == (StepKind.OTHER, None)
    assert classify_notebook_cell("  \n\n") is None


def test_notebook_cell_magics() -> None:
    bash = classify_notebook_cell("%%bash\npip install x")
    timed = classify_notebook_cell("%%time\nx = 1")
    assert bash is not None
    assert timed is not None
    assert bash.kind is StepKind.SHELL
    assert (timed.kind, timed.runnable_code) == (StepKind.PYTHON, "x = 1")


def test_notebook_bang_pip_names_reach_the_package_list() -> None:
    cell = classify_notebook_cell("!pip install -q pillow==10.0 tqdm\nimport tqdm")
    assert cell is not None
    assert detect_packages([cell]) == ["pillow", "tqdm"]


# Packages


def test_import_mapping() -> None:
    code = (
        "import sklearn.linear_model\nimport cv2\nfrom PIL import Image\nimport bs4\n"
        "import yaml\nimport numpy as np\nimport os, sys\nfrom . import sibling\n"
        "from sk import my_sk\nfrom __future__ import annotations\n"
    )
    assert sorted(packages_from_imports(code)) == [
        "beautifulsoup4",
        "numpy",
        "opencv-python",
        "pillow",
        "pyyaml",
        "scikit-learn",
        "sk",
    ]


def test_mapping_table_is_a_single_extensible_dict() -> None:
    assert IMPORT_TO_PYPI["sklearn"] == "scikit-learn"
    assert IMPORT_TO_PYPI["cv2"] == "opencv-python"
    assert IMPORT_TO_PYPI["PIL"] == "pillow"
    assert IMPORT_TO_PYPI["bs4"] == "beautifulsoup4"
    assert IMPORT_TO_PYPI["yaml"] == "pyyaml"


def test_dotted_mapping_prefers_longest_prefix() -> None:
    assert packages_from_imports("import google.generativeai as genai") == {"google-generativeai"}
    assert packages_from_imports("from sklearn.datasets import load_iris") == {"scikit-learn"}


def test_imports_found_even_when_code_does_not_parse() -> None:
    assert packages_from_imports("import numpy as np, pandas\nprint 'x'") == {"numpy", "pandas"}


def test_imports_nested_in_functions_and_try_blocks() -> None:
    code = "def f():\n    import requests\ntry:\n    import ujson\nexcept ImportError:\n    pass"
    assert packages_from_imports(code) == {"requests", "ujson"}


@pytest.mark.parametrize(
    ("line", "names"),
    [
        ("pip install pandas", ["pandas"]),
        ("pip install pandas==1.3.5 numpy>=1.20", ["numpy", "pandas"]),
        ("pip install --upgrade openai", ["openai"]),
        ("$ pip3 install -U 'scikit-learn<1.2' requests[security]", ["requests", "scikit-learn"]),
        ("!pip install -q pillow", ["pillow"]),
        ("%pip install tqdm", ["tqdm"]),
        ("python -m pip install --no-cache-dir Flask_Login", ["flask-login"]),
        ("pip install -r requirements.txt", []),
        ("pip install -e .", []),
        ("pip install ./local/pkg git+https://github.com/o/r.git", []),
        ("pip install --index-url https://x.example/simple pkg", ["pkg"]),
        ("pip install pkg ; python run.py", ["pkg"]),
        ("cd app && pip install a b | tee log", ["a", "b"]),
        ("pip install 'pkg ; python_version < \"3.9\"'", ["pkg"]),
        ("pip list", []),
        ("conda install numpy", []),
        ("# pip install commented", []),
        ('pip install "unterminated', ["unterminated"]),
    ],
)
def test_pip_lines(line: str, names: list[str]) -> None:
    assert sorted(packages_from_pip_line(line)) == names


def test_pip_continuation_lines_are_joined() -> None:
    assert packages_from_shell("pip install \\\n  numpy \\\n  pandas") == {"numpy", "pandas"}


def test_detect_packages_is_sorted_and_unique() -> None:
    blocks = [
        Block(idx=0, kind=StepKind.SHELL, code="pip install scipy==1.0"),
        Block(
            idx=1, kind=StepKind.PYTHON, code="import scipy, os", runnable_code="import scipy, os"
        ),
        Block(idx=2, kind=StepKind.REPL, code=">>> import cv2", runnable_code="import cv2"),
        Block(idx=3, kind=StepKind.OUTPUT, code="import zzz", runnable_code=None),
    ]
    assert detect_packages(blocks) == ["opencv-python", "scipy"]


# Page parsing


def test_html_skips_site_chrome_and_reads_code_wrappers() -> None:
    page = """
    <html><body>
    <nav><pre>nav = 1</pre></nav>
    <article>
      <h2>Step one</h2><p>Load the data.</p>
      <div class="highlight"><pre class="language-python"><code>df = load()</code></pre></div>
      <pre><code class="lang-bash">pip install pandas</code></pre>
      <pre><code><span class="line">a = 1</span><span class="line">b = 2</span></code></pre>
      <pre>line one<br>line two</pre>
    </article>
    <div id="comments"><pre>spam = 1</pre></div>
    <footer><pre>foot = 1</pre></footer>
    </body></html>
    """
    raws = parse_html_blocks(page)
    assert [r.text for r in raws] == [
        "df = load()",
        "pip install pandas",
        "a = 1\nb = 2\n",
        "line one\nline two",
    ]
    assert raws[0].hint == "python"
    assert raws[1].hint == "bash"
    assert raws[0].context == "Step one\n\nLoad the data."


def test_markdown_fences_hints_and_title() -> None:
    text = (
        "# \U0001f99c Title *here*\n\nIntro text.\n\n```python title=a.py\nimport os\n```\n\n"
        "- item\n\n  ~~~bash\n  pip install x\n  ~~~\n\n````\nplain ``` inside\n````\n"
    )
    title, raws = parse_markdown(text)
    assert title == "Title here"
    assert [(r.hint, r.text) for r in raws] == [
        ("python", "import os"),
        ("bash", "pip install x"),
        (None, "plain ``` inside"),
    ]
    assert raws[0].context == "# \U0001f99c Title *here*\nIntro text."


def test_extraction_json_roundtrip() -> None:
    blocks = build_blocks([RawBlock("import numpy")])
    dumped = json.loads(blocks[0].model_dump_json())
    assert dumped["kind"] == "python"
    assert dumped["runnable_code"] == "import numpy"

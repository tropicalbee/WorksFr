"""JavaScript, TypeScript, C and C++ tutorials: fences, detection, paste, packages, pins."""

import httpx
import nbformat
import pytest
from support.local_sandbox import LocalRunnerSandbox
from test_pipeline import ROOT, Cache, Proposer, Sink

from app.config import Settings
from app.extract import extract_notebook
from app.extract.blocks import RawBlock, build_blocks
from app.extract.html_page import parse_html_blocks
from app.extract.libs import (
    detect_c_system_headers,
    detect_npm_packages,
    detect_packages,
    npm_packages_from_code,
    npm_packages_from_shell,
    npm_pins_from_line,
)
from app.extract.markdown_doc import parse_markdown
from app.extract.paste import extract_paste
from app.models import Block, InstallPin, StepKind, is_runnable
from app.pins import find_install_pins
from app.pipeline import PipelineDeps, run_check

ALIASES = [
    ("js", StepKind.JAVASCRIPT),
    ("javascript", StepKind.JAVASCRIPT),
    ("jsx", StepKind.JAVASCRIPT),
    ("mjs", StepKind.JAVASCRIPT),
    ("cjs", StepKind.JAVASCRIPT),
    ("node", StepKind.JAVASCRIPT),
    ("ts", StepKind.TYPESCRIPT),
    ("typescript", StepKind.TYPESCRIPT),
    ("tsx", StepKind.TYPESCRIPT),
    ("c", StepKind.C),
    ("h", StepKind.C),
    ("cpp", StepKind.CPP),
    ("c++", StepKind.CPP),
    ("cc", StepKind.CPP),
    ("cxx", StepKind.CPP),
    ("hpp", StepKind.CPP),
]


@pytest.mark.parametrize(("alias", "kind"), ALIASES)
def test_markdown_fence_alias_gives_kind(alias: str, kind: StepKind) -> None:
    _, raws = parse_markdown(f"```{alias}\n!x % y\n```\n")
    [block] = build_blocks(raws)
    assert block.kind == kind
    assert block.runnable_code == "!x % y"


@pytest.mark.parametrize(("alias", "kind"), ALIASES)
def test_html_language_class_gives_kind(alias: str, kind: StepKind) -> None:
    for prefix in ("language-", "lang-"):
        html = f'<pre><code class="{prefix}{alias}">x = 1</code></pre>'
        [block] = build_blocks(parse_html_blocks(html))
        assert block.kind == kind


def test_other_languages_stay_other_and_python_stays_python() -> None:
    assert build_blocks([RawBlock("x", hint="java")])[0].kind == StepKind.OTHER
    assert build_blocks([RawBlock("x = 1", hint="python")])[0].kind == StepKind.PYTHON
    assert build_blocks([RawBlock("npm install x", hint="bash")])[0].kind == StepKind.SHELL


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        ("const fs = require('fs');\nconsole.log(fs.existsSync('a'));", StepKind.JAVASCRIPT),
        ("import axios from 'axios';\naxios.get('/x');", StepKind.JAVASCRIPT),
        ("const add = (a, b) => a + b;\nlet total = add(1, 2);", StepKind.JAVASCRIPT),
        ('#include <stdio.h>\nint main(void) {\n  puts("hi");\n}', StepKind.C),
        ("#include <iostream>\nint main() { std::cout << 1; }", StepKind.CPP),
        ("#include <stdio.h>\nclass A {};\nint main() {}", StepKind.CPP),
    ],
)
def test_unlabelled_blocks_are_detected(code: str, kind: StepKind) -> None:
    [block] = build_blocks([RawBlock(code)])
    assert block.kind == kind
    assert block.runnable_code == code


def test_unlabelled_python_still_python() -> None:
    [block] = build_blocks([RawBlock("import numpy as np\nx = np.arange(3)")])
    assert block.kind == StepKind.PYTHON


def test_runnable_kinds() -> None:
    for kind in (
        StepKind.PYTHON,
        StepKind.REPL,
        StepKind.JAVASCRIPT,
        StepKind.TYPESCRIPT,
        StepKind.C,
        StepKind.CPP,
    ):
        assert is_runnable(kind)
    for kind in (StepKind.SHELL, StepKind.OUTPUT, StepKind.OTHER):
        assert not is_runnable(kind)


# Notebooks


def _notebook(language: str | None, cells: list[str], *, info: bool = False) -> bytes:
    nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(c) for c in cells])
    if language and info:
        nb.metadata["language_info"] = {"name": language}
    elif language:
        nb.metadata["kernelspec"] = {"name": "k", "display_name": "k", "language": language}
    return nbformat.writes(nb).encode()


@pytest.mark.parametrize(
    ("language", "info", "kind"),
    [
        ("javascript", False, StepKind.JAVASCRIPT),
        ("typescript", False, StepKind.TYPESCRIPT),
        ("javascript", True, StepKind.JAVASCRIPT),
        ("c++", False, StepKind.CPP),
        ("python", False, StepKind.PYTHON),
        (None, False, StepKind.PYTHON),
    ],
)
def test_notebook_kernel_language(language: str | None, info: bool, kind: StepKind) -> None:
    cell = "!x = 1\nconst y = 2"
    extraction = extract_notebook("a.ipynb", _notebook(language, [cell], info=info))
    assert extraction.blocks[0].kind == kind
    if kind != StepKind.PYTHON:
        assert extraction.blocks[0].runnable_code == cell


# Paste


def test_paste_auto_python_when_it_parses() -> None:
    result = extract_paste("x = 1\n\nprint(x)\n", None)
    assert [b.kind for b in result.blocks] == [StepKind.PYTHON, StepKind.PYTHON]


def test_paste_auto_c_and_cpp() -> None:
    c = extract_paste('#include <stdio.h>\nint main(void) {\n  puts("a");\n\n  return 0;\n}\n', None)
    assert [b.kind for b in c.blocks] == [StepKind.C]
    cpp = extract_paste("#include <iostream>\nint main() { std::cout << 1; }\n", None)
    assert cpp.blocks[0].kind == StepKind.CPP
    cls = extract_paste("#include <stdio.h>\nclass A { int x; };\n", None)
    assert cls.blocks[0].kind == StepKind.CPP


def test_paste_auto_falls_back_to_javascript() -> None:
    result = extract_paste("function f() { return 1 }\nf();\n", None)
    assert [b.kind for b in result.blocks] == [StepKind.JAVASCRIPT]


def test_paste_splits_at_blank_lines_only_at_brace_depth_zero() -> None:
    code = (
        "const a = 1;\n\n"
        "function f(x) {\n  const y = x;\n\n  return y;\n}\n\n"
        "const o = {\n  a: 1,\n\n  b: 2,\n};\n\n"
        "f(a);\n"
    )
    result = extract_paste(code, None, "javascript")
    assert [b.code.split("\n")[0] for b in result.blocks] == [
        "const a = 1;",
        "function f(x) {",
        "const o = {",
        "f(a);",
    ]
    assert "  const y = x;\n\n  return y;" in result.blocks[1].code
    assert all(b.kind == StepKind.JAVASCRIPT and b.runnable_code == b.code for b in result.blocks)


def test_paste_braces_in_strings_and_comments_do_not_count() -> None:
    code = 'const s = "{";\n\n// } stray\nconst t = 1;\n'
    result = extract_paste(code, None, "javascript")
    assert len(result.blocks) == 2


@pytest.mark.parametrize(
    ("language", "kind"),
    [
        ("javascript", StepKind.JAVASCRIPT),
        ("typescript", StepKind.TYPESCRIPT),
        ("c", StepKind.C),
        ("cpp", StepKind.CPP),
        ("python", StepKind.PYTHON),
    ],
)
def test_paste_explicit_language(language: str, kind: StepKind) -> None:
    result = extract_paste("x\n", None, language)
    assert result.blocks[0].kind == kind


def test_paste_explicit_language_overrides_detection() -> None:
    assert extract_paste("x = 1\n", None, "javascript").blocks[0].kind == StepKind.JAVASCRIPT


def test_paste_python_two_style_text_stays_python_under_auto() -> None:
    result = extract_paste("def broken(:\n    pass\n\nprint 'hello'\n", None)
    assert [b.kind for b in result.blocks] == [StepKind.PYTHON]


def test_paste_npm_install_line_is_shell_and_feeds_packages() -> None:
    code = "npm install lodash@4.17.21\n\nconst _ = require('lodash');\n"
    result = extract_paste(code, None, "javascript")
    assert [b.kind for b in result.blocks] == [StepKind.SHELL, StepKind.JAVASCRIPT]
    assert result.npm_packages == ["lodash"]
    assert result.packages == []


# Packages


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("const _ = require('lodash');", ["lodash"]),
        ("import _ from 'lodash/fp';", ["lodash"]),
        ("import { a } from '@scope/pkg/sub';", ["@scope/pkg"]),
        ('import {\n  a,\n  b,\n} from "express";', ["express"]),
        ("import 'reflect-metadata';", ["reflect-metadata"]),
        ("const m = await import('chalk');", ["chalk"]),
        ("import type { X } from 'zod';", ["zod"]),
        ("const x = require('./local'); import y from '../up'; import z from '/abs';", []),
        ("import fs from 'node:fs'; const p = require('path'); import 'fs/promises';", []),
        ("const os = require('os'); const e = require('express')", ["express"]),
        ("import x from '@scope'; require('http://x');", []),
    ],
)
def test_npm_imports(code: str, expected: list[str]) -> None:
    assert sorted(npm_packages_from_code(code)) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("npm install lodash express", ["express", "lodash"]),
        ("npm i -D typescript@5.4.2 @types/node", ["@types/node", "typescript"]),
        ("yarn add react@^18.0.0", ["react"]),
        ("pnpm add -g left-pad && npm install", ["left-pad"]),
        ("$ npm install --save axios", ["axios"]),
        ("npm install", []),
        ("npm install ./local ../x git+https://x/y.git github:a/b", []),
        ("pip install numpy", []),
    ],
)
def test_npm_packages_from_shell(text: str, expected: list[str]) -> None:
    assert sorted(npm_packages_from_shell(text)) == expected


def test_npm_pins_from_line() -> None:
    line = "npm install lodash@4.17.21 express @scope/p@^1.2.0 react@latest"
    assert npm_pins_from_line(line) == [("lodash", "4.17.21"), ("@scope/p", "^1.2.0")]


def test_detect_npm_packages_uses_js_ts_and_shell_blocks_only() -> None:
    blocks = [
        Block(idx=0, kind=StepKind.SHELL, code="npm install axios"),
        Block(
            idx=1, kind=StepKind.JAVASCRIPT, code="x", runnable_code="const l = require('lodash')"
        ),
        Block(idx=2, kind=StepKind.TYPESCRIPT, code="x", runnable_code="import z from 'zod'"),
        Block(idx=3, kind=StepKind.PYTHON, code="x", runnable_code="import numpy"),
    ]
    assert detect_npm_packages(blocks) == ["axios", "lodash", "zod"]
    assert detect_packages(blocks) == ["numpy"]


def test_c_system_headers() -> None:
    code = (
        "#include <stdio.h>\n#include <vector>\n#include <unistd.h>\n"
        '#  include <sys/socket.h>\n#include "mine.h"\n#include <curl/curl.h>\n'
        "// #include <fake.h>\n"
    )
    other = "#include <x.h>"
    blocks = [
        Block(idx=0, kind=StepKind.C, code=code, runnable_code=code),
        Block(idx=1, kind=StepKind.CPP, code=code, runnable_code="#include <openssl/ssl.h>"),
        Block(idx=2, kind=StepKind.JAVASCRIPT, code=other, runnable_code=other),
    ]
    assert detect_c_system_headers(blocks) == [
        "curl/curl.h",
        "openssl/ssl.h",
        "sys/socket.h",
        "unistd.h",
    ]


def test_extraction_fields_for_markdown_fences() -> None:
    result = extract_paste("```cpp\n#include <curl/curl.h>\n```\n```js\nrequire('chalk')\n```", None)
    assert [b.kind for b in result.blocks] == [StepKind.CPP, StepKind.JAVASCRIPT]
    assert result.c_system_headers == ["curl/curl.h"]
    assert result.npm_packages == ["chalk"]


# Install pins


def test_find_install_pins_reads_npm_install_lines() -> None:
    pins = find_install_pins(
        [(0, "npm install lodash@4.17.21 express\npip install pandas==2.0"), (1, "yarn add a@^1")],
        {"pandas": "2.1.0", "lodash": "4.17.21"},
    )
    assert pins == [
        InstallPin(
            step_idx=0,
            line="npm install lodash@4.17.21 express",
            package="lodash",
            pinned="4.17.21",
            used="4.17.21",
            ecosystem="npm",
        ),
        InstallPin(
            step_idx=0, line="pip install pandas==2.0", package="pandas", pinned="==2.0", used="2.1.0"
        ),
        InstallPin(
            step_idx=1, line="yarn add a@^1", package="a", pinned="^1", used=None, ecosystem="npm"
        ),
    ]
    assert pins[1].ecosystem == "pypi"


# Pipeline


def test_js_only_page_is_not_rejected_as_non_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda _: ["93.184.216.34"])
    settings = Settings(_env_file=None, providers=[])
    sink = Sink()
    body = '<pre class="language-javascript">const x = 1;</pre>'
    started: list[str] = []

    def factory(check_id: str) -> LocalRunnerSandbox:
        started.append(check_id)
        return LocalRunnerSandbox(settings, runner_path=ROOT / "sandbox/runner.py")

    deps = PipelineDeps(settings, factory, Proposer(), httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ), Cache())
    with deps.http_client:
        run_check("js", "https://example.com", deps, sink)
    failed = [e for e in sink.events if e.type == "failed"]
    assert not any("none of it is Python" in getattr(e, "error", "") for e in failed)
    assert started

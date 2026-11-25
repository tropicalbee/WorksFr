from collections.abc import Collection

import pytest

from app.labels import label_step, precheck
from app.models import ErrorInfo, ExecResult, Label, StepKind, WarningInfo


def test_patch_verdict_requires_library_origin() -> None:
    from app.labels import cascade_decision, cascade_source, judge_patch
    from app.models import PatchVerdict

    for filename, expected in (
        ("<step-s1>", PatchVerdict.FAILED),
        ("/lib/pathlib.py", PatchVerdict.FIXED_UNVERIFIED),
    ):
        result = ExecResult(
            id="s1",
            status="error",
            error=ErrorInfo(
                type="FileNotFoundError",
                message="missing.csv",
                traceback=f'File "{filename}", line 1, in read',
            ),
        )
        assert judge_patch(result)[1] == expected
    assert judge_patch(ExecResult(id="s1", status="ok"))[1] == PatchVerdict.FIXED
    assert judge_patch(None, timed_out=True)[1] == PatchVerdict.FAILED
    error = ErrorInfo(type="NameError", message="name 'x' is not defined")
    assert cascade_source(error, {"x": 2}) == 2
    assert cascade_decision(2).reason == "depends on step 3, which is still broken"


def test_failed_distribution_matches_import_alias() -> None:
    result = ExecResult(
        id="s1",
        status="error",
        error=ErrorInfo(
            type="ModuleNotFoundError",
            message="No module named 'dotenv'",
        ),
    )
    assert label_step(result, failed_packages=["Python_Dotenv"]).label == Label.CANT_VERIFY


def ok(*categories: str) -> ExecResult:
    return ExecResult(
        id="s",
        status="ok",
        warnings=[WarningInfo(category=c, message="m") for c in categories],
    )


def err(etype: str, message: str = "") -> ExecResult:
    return ExecResult(id="s", status="error", error=ErrorInfo(type=etype, message=message))


CANT = Label.CANT_VERIFY


@pytest.mark.parametrize(
    ("result", "kwargs", "label", "reason_part"),
    [
        # rule 1: timeout
        (None, {}, CANT, "too slow"),
        (ok(), {"timed_out": True}, CANT, "too slow"),
        (err("ValueError", "x"), {"timed_out": True}, CANT, "too slow"),
        # rule 2: ok
        (ok(), {}, Label.WORKS, ""),
        (ok("UserWarning", "RuntimeWarning"), {}, Label.WORKS, ""),
        (ok("DeprecationWarning"), {}, Label.DEPRECATED, "DeprecationWarning"),
        (ok("PendingDeprecationWarning"), {}, Label.DEPRECATED, ""),
        (ok("FutureWarning"), {}, Label.DEPRECATED, ""),
        (ok("UserWarning", "FutureWarning"), {}, Label.DEPRECATED, ""),
        # rule 3: stdin
        (err("StdinUnavailableError", "no stdin"), {}, CANT, "keyboard"),
        # rule 4: auth
        (err("AuthenticationError", "bad"), {}, CANT, "API key"),
        (err("PermissionDeniedError", "bad"), {}, CANT, "API key"),
        (err("PermissionDenied", "403"), {}, CANT, "API key"),
        (err("Unauthenticated", "x"), {}, CANT, "API key"),
        (err("APIError", "Incorrect API key provided: sk-xxx"), {}, CANT, "API key"),
        (err("ValueError", "invalid api key"), {}, CANT, "API key"),
        (err("BadRequest", "API key not valid. Please pass a valid API key."), {}, CANT, "API key"),
        (err("HTTPError", "401 Client Error: Unauthorized"), {}, CANT, "API key"),
        # rule 5: network
        (err("ConnectionError", "boom"), {}, CANT, "network"),
        (err("ConnectionRefusedError", "boom"), {}, CANT, "network"),
        (err("ConnectionResetError", "boom"), {}, CANT, "network"),
        (err("APIConnectionError", "Connection error."), {}, CANT, "network"),
        (err("gaierror", "x"), {}, CANT, "network"),
        (err("NewConnectionError", "x"), {}, CANT, "network"),
        (err("MaxRetryError", "x"), {}, CANT, "network"),
        (err("ConnectError", "x"), {}, CANT, "network"),
        (err("URLError", "<urlopen error x>"), {}, CANT, "network"),
        (err("OSError", "Temporary failure in name resolution"), {}, CANT, "network"),
        (err("OSError", "[Errno -2] Name or service not known"), {}, CANT, "network"),
        (err("OSError", "Network is unreachable"), {}, CANT, "network"),
        # rule 6: GPU
        (err("RuntimeError", "No CUDA GPUs are available"), {}, CANT, "GPU"),
        (err("AssertionError", "Torch not compiled with CUDA enabled"), {}, CANT, "GPU"),
        (err("RuntimeError", "CUDA error: out of memory"), {}, CANT, "GPU"),
        # rule 7: file not found
        (
            err("FileNotFoundError", "[Errno 2] No such file or directory: 'data.csv'"),
            {},
            CANT,
            "data file",
        ),
        (
            err("FileNotFoundError", "[Errno 2] No such file or directory: './a/b.txt'"),
            {},
            CANT,
            "data file",
        ),
        (err("FileNotFoundError", "no path info"), {}, CANT, "data file"),
        (
            err("FileNotFoundError", "[Errno 2] No such file or directory: '/etc/nope'"),
            {},
            Label.BROKEN,
            "FileNotFoundError",
        ),
        # rule 8: local module
        (
            err("ModuleNotFoundError", "No module named 'mylocal'"),
            {"failed_packages": ["mylocal"]},
            CANT,
            "didn't install",
        ),
        (
            err("ModuleNotFoundError", "No module named 'My_Local.sub'"),
            {"failed_packages": ["my-local"]},
            CANT,
            "didn't install",
        ),
        (
            err("ModuleNotFoundError", "No module named 'mylocal'"),
            {},
            Label.BROKEN,
            "ModuleNotFoundError",
        ),
        (
            err("ModuleNotFoundError", "No module named 'other'"),
            {"failed_packages": ["mylocal"]},
            Label.BROKEN,
            "",
        ),
        # rule 9: broken
        (
            err("AttributeError", "module 'x' has no attribute 'y'"),
            {},
            Label.BROKEN,
            "AttributeError: module",
        ),
        (
            err("TypeError", "unexpected keyword argument 'z'"),
            {},
            Label.BROKEN,
            "unexpected keyword",
        ),
        (ExecResult(id="s", status="error"), {}, Label.BROKEN, ""),
    ],
)
def test_label_step(result, kwargs, label, reason_part):
    decision = label_step(result, **kwargs)
    assert decision.label == label
    assert reason_part.lower() in decision.reason.lower()
    if label == CANT:
        assert decision.reason


@pytest.mark.parametrize(
    ("result", "label", "reason_part"),
    [
        # auth beats network
        (
            err("AuthenticationError", "connection error while checking api key 401"),
            CANT,
            "API key",
        ),
        (err("APIError", "Incorrect API key; Network is unreachable"), CANT, "API key"),
        # stdin beats auth/network/cuda
        (err("StdinUnavailableError", "401 CUDA connection"), CANT, "keyboard"),
        # network beats cuda
        (err("ConnectionError", "CUDA download failed"), CANT, "network"),
        # cuda beats file not found
        (err("FileNotFoundError", "CUDA: 'libcuda.so'"), CANT, "GPU"),
        # ok beats everything: status ok never consults error
        (ok(), Label.WORKS, ""),
    ],
)
def test_rule_order(result, label, reason_part):
    decision = label_step(result)
    assert decision.label == label
    assert reason_part.lower() in decision.reason.lower()


def test_401_needs_word_boundary():
    assert label_step(err("ValueError", "value 14010 is bad")).label == Label.BROKEN


def test_bare_401_without_http_context_is_broken():
    assert label_step(err("IndexError", "index 401 is out of bounds")).label == Label.BROKEN


def test_openai_style_401_is_auth():
    message = "Error code: 401 - {'error': {'message': 'bad key'}}"
    assert label_step(err("APIStatusError", message)).label == Label.CANT_VERIFY


def test_failed_packages_accepts_any_collection():
    packages: Collection[str] = {"mylocal"}
    result = err("ModuleNotFoundError", "No module named 'mylocal'")
    assert label_step(result, failed_packages=packages).label == CANT


@pytest.mark.parametrize(
    ("code", "reason_part"),
    [
        ("model = Net()\nmodel.cuda()", "GPU"),
        ("x = t.cuda(0)", "GPU"),
        ("m = Net().cuda()", "GPU"),
        ("t = torch.zeros(1, device='cuda')", "GPU"),
        ('t = torch.zeros(1, device="cuda:0")', "GPU"),
        ("d = torch.device('cuda')", "GPU"),
        ("d = torch.device('cuda:1')", "GPU"),
        ("x = t.to('cuda')", "GPU"),
        ("name = input('Your name: ')", "keyboard"),
        ("x = input()", "keyboard"),
        ("import getpass\npw = getpass.getpass()", "keyboard"),
        ("from getpass import getpass\npw = getpass('pw')", "keyboard"),
    ],
)
def test_precheck_positive(code, reason_part):
    decision = precheck(code)
    assert decision is not None
    assert decision.label == CANT
    assert reason_part.lower() in decision.reason.lower()


@pytest.mark.parametrize(
    "code",
    [
        "# model.cuda()\nx = 1",
        "s = 'call model.cuda() later'",
        "s = \"device='cuda'\"",
        '"""docstring with input() and .cuda()"""\nx = 1',
        "d = torch.device('cpu')",
        "t = torch.zeros(1, device='cpu')",
        "cuda = 1\nprint(cuda)",
        "x = torch.cuda.is_available()",
        "def input_data():\n    return 1\ninput_data()",
        "print(input_value)",
        "",
    ],
)
def test_precheck_negative(code):
    assert precheck(code) is None


@pytest.mark.parametrize(
    "code", ["def broken(:\n  pass", "%matplotlib inline\nx.cuda()", "x = input("]
)
def test_precheck_unparseable_returns_none(code):
    assert precheck(code) is None


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        (StepKind.C, 'scanf("%d", &n);'),
        (StepKind.C, "getchar();"),
        (StepKind.C, "gets(buffer);"),
        (StepKind.C, "fgets(buffer, sizeof(buffer), stdin);"),
        (StepKind.CPP, "std::cin >> value;"),
        (StepKind.C, 'fscanf(stdin, "%d", &n);'),
        (StepKind.C, "int c = fgetc(stdin);"),
        (StepKind.C, "int c = getc( stdin );"),
        (StepKind.C, "ssize_t n = getline(&line, &size, stdin);"),
        (StepKind.C, "fread(buffer, 1, sizeof(buffer), stdin);"),
        (StepKind.CPP, "std::getline(std::cin, line);"),
        (StepKind.CPP, "char c = std::cin.get();"),
        (StepKind.CPP, "std::cin.getline(buffer, 80);"),
        (StepKind.JAVASCRIPT, 'const rl = require("node:readline/promises");'),
        (StepKind.JAVASCRIPT, "import { createInterface } from 'readline';"),
        (StepKind.TYPESCRIPT, "readline.createInterface(options);"),
        (StepKind.TYPESCRIPT, "process /* input */ . stdin.on('data', read);"),
        (StepKind.JAVASCRIPT, 'prompt("Name?");'),
    ],
)
def test_non_python_input_prechecks(kind: StepKind, code: str) -> None:
    decision = precheck(code, kind=kind)
    assert decision is not None
    assert decision.label == CANT
    assert decision.reason == "needs keyboard input"


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        (StepKind.C, 'puts("scanf( and getchar( and gets(");'),
        (StepKind.C, "my_scanf(); has_getchar(); fgets(buffer, sizeof(buffer), file);"),
        (StepKind.C, "/* scanf(); */ puts(text); // getchar()"),
        (StepKind.CPP, "other_cin >> value;"),
        (StepKind.C, 'fscanf(file, "%d", &n); sscanf(text, "%d", &n);'),
        (StepKind.C, "int c = fgetc(file); getline(&line, &size, file);"),
        (StepKind.CPP, "std::getline(file, line); std::cin.tie(nullptr);"),
        (StepKind.CPP, "const auto mask = 1 << 3; // std::cin >> mask"),
        (StepKind.JAVASCRIPT, 'console.log("readline", "process.stdin", "prompt(");'),
        (StepKind.TYPESCRIPT, "custom_readline(); $prompt(); process.stdinBackup;"),
        (StepKind.TYPESCRIPT, "console.log(`prompt()`); /* process.stdin */"),
        (StepKind.TYPESCRIPT, 'import something from "readline-extra";'),
    ],
)
def test_non_python_input_lookalikes_are_runnable(kind: StepKind, code: str) -> None:
    assert precheck(code, kind=kind) is None


@pytest.mark.parametrize("kind", [StepKind.C, StepKind.CPP])
def test_c_system_header_precheck(kind: StepKind) -> None:
    headers = {"curl/curl.h"}
    decision = precheck("  # include <curl/curl.h>\n", kind=kind, c_system_headers=headers)
    assert decision is not None
    assert decision.label == CANT
    assert decision.reason == "needs the system library curl/curl.h"
    assert precheck("#include <stdio.h>", kind=kind, c_system_headers=headers) is None
    assert precheck("/*\n#include <curl/curl.h>\n*/", kind=kind, c_system_headers=headers) is None


@pytest.mark.parametrize("name", ["document", "window", "navigator", "localStorage", "alert"])
def test_browser_error_requires_node_reference_to_missing_global(name: str) -> None:
    result = err("ReferenceError", f"{name} is not defined")
    assert label_step(result, kind=StepKind.JAVASCRIPT).reason == "runs in a web browser"
    assert label_step(result).label == Label.BROKEN
    assert label_step(err("TypeError", result.error.message), kind=StepKind.JAVASCRIPT).label == (
        Label.BROKEN
    )
    assert (
        label_step(
            err("ReferenceError", f"my_{name} is not defined"), kind=StepKind.TYPESCRIPT
        ).label
        == Label.BROKEN
    )


@pytest.mark.parametrize(
    "message", ["ENOTFOUND example.com", "connect ECONNREFUSED", "EAI_AGAIN", "fetch failed"]
)
def test_node_network_errors(message: str) -> None:
    decision = label_step(err("TypeError", message), kind=StepKind.TYPESCRIPT)
    assert decision.label == CANT
    assert decision.reason == "needs network access"
    near_match = label_step(err("TypeError", "fetch failedFlag"), kind=StepKind.TYPESCRIPT)
    assert near_match.label == Label.BROKEN


@pytest.mark.parametrize(
    ("etype", "message", "package"),
    [
        ("Error", "Cannot find module 'example/subpath'", "example"),
        (
            "ERR_MODULE_NOT_FOUND",
            "Cannot find package '@scope/example' imported from file",
            "@scope/example",
        ),
    ],
)
def test_node_missing_package_requires_failed_install(
    etype: str, message: str, package: str
) -> None:
    result = err(etype, message)
    decision = label_step(result, kind=StepKind.JAVASCRIPT, failed_npm_packages=[package])
    assert decision.label == CANT
    assert decision.reason == f"{package} didn't install in the sandbox, so this step couldn't run."
    assert (
        label_step(result, kind=StepKind.JAVASCRIPT, failed_packages=[package]).label
        == Label.BROKEN
    )
    local = err("Error", f"Cannot find module './{package}'")
    decision = label_step(local, kind=StepKind.JAVASCRIPT, failed_npm_packages=[package])
    assert decision.label == Label.BROKEN


def test_native_compilation_error_does_not_use_python_labels() -> None:
    result = err("CompileError", "'cuda' was not declared in this scope")
    assert label_step(result, kind=StepKind.CPP).label == Label.BROKEN
    assert label_step(ok("DeprecationWarning"), kind=StepKind.C).label == Label.DEPRECATED


@pytest.mark.parametrize(
    "label,reason",
    [
        (Label.BROKEN, "depends on step 3, which is still broken"),
        (Label.CANT_VERIFY, "depends on step 3, which couldn't be verified"),
    ],
)
def test_cascade_keeps_source_label(label: Label, reason: str) -> None:
    from app.labels import cascade_decision, is_cascade

    decision = cascade_decision(2, label)
    assert decision.label == label
    assert decision.reason == reason
    assert is_cascade(decision)


@pytest.mark.parametrize("kind", [StepKind.C, StepKind.CPP])
def test_quoted_local_include_is_cant_verify(kind: StepKind) -> None:
    decision = precheck('#include <stdio.h>\n  # include "foo.h"\nint x;', kind=kind)
    assert decision is not None
    assert decision.label == CANT
    assert decision.reason == "includes a local file, foo.h, that the tutorial doesn't show"


@pytest.mark.parametrize(
    "code",
    [
        '#include "stdio.h"\nint x;',
        '#include "vector"\nint x;',
        '/* #include "foo.h" */\nint x;',
        '// #include "foo.h"\nint x;',
        'const char *text = "#include \\"foo.h\\"";',
        "#include <foo.h>\nint x;",
    ],
)
def test_quoted_include_lookalikes_do_not_hit_the_local_file_rule(code: str) -> None:
    decision = precheck(code, kind=StepKind.CPP)
    assert decision is None or "local file" not in decision.reason


@pytest.mark.parametrize("kind", [StepKind.C, StepKind.CPP])
def test_compile_only_result_is_cant_verify(kind: StepKind) -> None:
    decision = label_step(ExecResult(id="s1", status="ok", result="compiled"), kind=kind)
    assert decision.label == CANT
    assert decision.reason == "Compiled without errors, but it has no main(), so nothing ran."
    assert label_step(ExecResult(id="s1", status="ok"), kind=kind).label == Label.WORKS
    python = label_step(ExecResult(id="s1", status="ok", result="compiled"))
    assert python.label == Label.WORKS


@pytest.mark.parametrize(
    ("kind", "code", "expected"),
    [
        (StepKind.JAVASCRIPT, "const a = 1, b = 2;\nlet c;\nvar d;", {"a", "c", "d"}),
        (StepKind.JAVASCRIPT, "async function load() {}\nclass Box {}", {"load", "Box"}),
        (StepKind.JAVASCRIPT, "const { a, b: renamed, c = 3 } = obj;", {"a", "renamed", "c"}),
        (StepKind.TYPESCRIPT, "const [first, second] = pair;", {"first", "second"}),
        (
            StepKind.JAVASCRIPT,
            "import fs, { readFile as read, stat } from 'node:fs';",
            {"fs", "read", "stat"},
        ),
        (StepKind.JAVASCRIPT, "import * as path from 'node:path';", {"path"}),
        (StepKind.JAVASCRIPT, "/* const hidden = 1 */ const shown = '/*';", {"shown"}),
        (StepKind.JAVASCRIPT, "console.log('const quoted = 1'); result.const = 1;", set()),
        (StepKind.C, "static int value = 1;\nconst char *name = 0;", {"value", "name"}),
        (StepKind.C, "int add(void) { int total = 0; return total; }", {"add", "total"}),
        (StepKind.C, "return value;\nelse other;", set()),
        (StepKind.CPP, "struct Point { int x; };\nclass Shape : Base {};", {"Point", "x", "Shape"}),
        (StepKind.C, "#define LIMIT 10\n// int hidden = 1;", {"LIMIT"}),
    ],
)
def test_declared_names(kind: StepKind, code: str, expected: set[str]) -> None:
    from app.labels import declared_names

    assert declared_names(code, kind) == expected


@pytest.mark.parametrize(
    ("kind", "etype", "message", "expected"),
    [
        (StepKind.JAVASCRIPT, "ReferenceError", "value is not defined", "value"),
        (StepKind.TYPESCRIPT, "ReferenceError", "$x is not defined", "$x"),
        (StepKind.JAVASCRIPT, "TypeError", "value is not defined", None),
        (
            StepKind.JAVASCRIPT,
            "ReferenceError",
            "Cannot access 'value' before initialization",
            None,
        ),
        (StepKind.C, "CompileError", "a.c:2:3: error: \u2018value\u2019 undeclared", "value"),
        (StepKind.CPP, "CompileError", "error: 'value' was not declared in this scope", "value"),
        (StepKind.CPP, "CompileError", "error: \u2018Shape\u2019 does not name a type", "Shape"),
        (StepKind.C, "CompileError", "error: unknown type name \u2018Point\u2019", "Point"),
        (StepKind.C, "CompileError", "error: expected ';' before '}' token", None),
        (StepKind.C, "ExitStatus", "\u2018value\u2019 undeclared", None),
    ],
)
def test_missing_name(kind: StepKind, etype: str, message: str, expected: str | None) -> None:
    from app.labels import missing_name

    assert missing_name(err(etype, message).error, kind) == expected
    assert missing_name(None, kind) is None

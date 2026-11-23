from collections.abc import Collection

import pytest

from app.labels import label_step, precheck
from app.models import ErrorInfo, ExecResult, Label, WarningInfo


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

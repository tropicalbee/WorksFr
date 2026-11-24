import pytest

from app.labels import REASON_LOCAL_MODULE, label_step
from app.models import ErrorInfo, ExecResult, Label
from app.packages import (
    dist_for_import,
    guessed_distributions,
    imports_for_dist,
    is_stdlib,
    normalize,
    same_dist,
)


@pytest.mark.parametrize(
    "module,dist",
    [
        ("sklearn", "scikit-learn"),
        ("dotenv", "python-dotenv"),
        ("Crypto", "pycryptodome"),
        ("docx", "python-docx"),
        ("OpenSSL", "pyopenssl"),
        ("fitz", "pymupdf"),
    ],
)
def test_package_identity(module: str, dist: str) -> None:
    assert dist_for_import(module + ".submodule") == dist
    assert module in imports_for_dist(dist)
    assert same_dist(dist, dist.upper().replace("-", "_"))
    assert normalize("A...B__c") == "a-b-c"
    assert is_stdlib("os.path")
    assert not is_stdlib(module)


def test_guessed_distributions_exclude_mappings_stdlib_and_explicit_installs() -> None:
    assert guessed_distributions(
        {"sk", "sklearn.datasets", "google.generativeai", "google", "os.path", "custom_lib"},
        {"CUSTOM-LIB"},
    ) == {"sk", "google"}


@pytest.mark.parametrize(
    "module,explicit,label",
    [("sk", (), Label.CANT_VERIFY), ("sklearn", (), Label.BROKEN), ("sk", ("SK",), Label.BROKEN)],
)
def test_import_error_from_guessed_distribution_is_local(
    module: str, explicit: tuple[str, ...], label: Label
) -> None:
    result = ExecResult(
        id="s1",
        status="error",
        error=ErrorInfo(
            type="ImportError", message=f"cannot import name 'missing' from '{module}'"
        ),
    )
    decision = label_step(result, guessed_packages=guessed_distributions([module], explicit))
    assert decision.label == label
    if label == Label.CANT_VERIFY:
        assert decision.reason == REASON_LOCAL_MODULE


def test_unmatched_import_error_is_still_broken() -> None:
    result = ExecResult(
        id="s1", status="error", error=ErrorInfo(type="ImportError", message="unrelated failure")
    )
    assert label_step(result, guessed_packages={"sk"}).label == Label.BROKEN

import pytest

from app.packages import dist_for_import, imports_for_dist, is_stdlib, normalize, same_dist


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

import re
from pathlib import Path


def test_showcase_security_headers() -> None:
    blueprint = (Path(__file__).resolve().parents[2] / "render.yaml").read_text(encoding="utf-8")
    service = blueprint.split("    name: worksfr-showcase\n", 1)[1].split("\n  - ", 1)[0]
    headers = service.split("    headers:\n", 1)[1]
    entries = dict(
        re.findall(
            r"^      - path: /\*\n        name: (.+)\n        value: (.+)$", headers, re.MULTILINE
        )
    )
    assert entries == {
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self' data:; "
            "img-src 'self' data:; connect-src 'self'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'"
        ),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }

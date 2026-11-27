import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_security_audit_script() -> None:
    script = (ROOT / "scripts/security-check.ps1").read_text(encoding="utf-8")
    for command in ("pip_audit", "npm audit", "aquasec/trivy", "zricethezav/gitleaks"):
        assert command in script
    images = re.findall(r"(?:aquasec/trivy|zricethezav/gitleaks)[^'\s]*", script)
    assert len(images) == 2
    for image in images:
        assert re.fullmatch(r"[^:]+:v?\d+\.\d+\.\d+@sha256:[a-f0-9]{64}", image)


def test_pre_commit_hook() -> None:
    hook = (ROOT / ".githooks/pre-commit").read_bytes()
    assert hook.startswith(b"#!/bin/sh\n")
    assert b"\r" not in hook
    image = re.search(rb"zricethezav/gitleaks:v\d+\.\d+\.\d+@sha256:[a-f0-9]{64}", hook)
    assert image is not None
    assert image[0] in (ROOT / "scripts/security-check.ps1").read_bytes()


def test_security_dev_dependencies_are_pinned() -> None:
    requirements = (ROOT / "backend/requirements-dev.txt").read_text(encoding="utf-8")
    for package in ("pip-audit", "pip-tools"):
        assert re.search(rf"^{package}==\d+\.\d+\.\d+$", requirements, re.MULTILINE)


def test_security_scanners_use_read_only_mounts_without_the_docker_socket() -> None:
    script = (ROOT / "scripts/security-check.ps1").read_text(encoding="utf-8")
    hook = (ROOT / ".githooks/pre-commit").read_text(encoding="utf-8")
    for source in (script, hook):
        assert "docker.sock" not in source.casefold()
        assert re.findall(r":/repo(?::[a-z]+)?", source) == [":/repo:ro"]
    assert "docker save " in script
    assert re.findall(r":/scan(?::[a-z]+)?", script) == [":/scan:ro"]
    assert '--input "/scan/${Image}.tar"' in script

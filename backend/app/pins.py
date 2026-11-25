"""Tutorial install constraints and pip output alongside sandbox versions."""

import re
from collections.abc import Iterable

from packaging.version import InvalidVersion, Version

from app.extract.libs import npm_pins_from_line, packages_from_shell, pins_from_pip_line
from app.models import InstallPin
from app.packages import normalize

_PACKAGE_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_SATISFIED = re.compile(
    r"Requirement already satisfied: (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r" in \S.*[ \t]+\((?P<version>[^()\s]+)\)"
)


def _valid_version(version: str) -> bool:
    try:
        Version(version)
    except InvalidVersion:
        return False
    return True


def _output_versions(line: str) -> list[tuple[str, str]]:
    satisfied = _SATISFIED.fullmatch(line)
    if (
        satisfied
        and _PACKAGE_NAME.fullmatch(satisfied["name"])
        and _valid_version(satisfied["version"])
    ):
        return [(normalize(satisfied["name"]), satisfied["version"])]
    if not line.startswith("Successfully installed "):
        return []
    items = line.removeprefix("Successfully installed ").split()
    versions: list[tuple[str, str]] = []
    for item in items:
        separators = list(re.finditer(r"-(?=\d)", item))
        if not separators:
            return []
        position = separators[-1].start()
        name, version = item[:position], item[position + 1 :]
        if not _PACKAGE_NAME.fullmatch(name) or not _valid_version(version):
            return []
        versions.append((normalize(name), version))
    return versions


def find_install_pins(
    blocks: Iterable[tuple[int, str]],
    versions: dict[str, str],
    *,
    npm_versions: dict[str, str] | None = None,
    output_blocks: Iterable[tuple[int, str]] = (),
    packages: Iterable[str] = (),
) -> list[InstallPin]:
    """Read pip output only from explicitly supplied output blocks and tutorial packages."""
    installed = {normalize(name): version for name, version in versions.items()}
    installed_npm = npm_versions or {}
    tutorial_packages = {normalize(package) for package in packages}
    pins: list[InstallPin] = []
    for idx, code in sorted(blocks, key=lambda block: block[0]):
        tutorial_packages.update(packages_from_shell(code))
        for line in re.sub(r"\\[ \t]*\n", " ", code).split("\n"):
            for package, specifier in pins_from_pip_line(line):
                pins.append(
                    InstallPin(
                        step_idx=idx,
                        line=line.strip(),
                        package=package,
                        pinned=specifier,
                        used=installed.get(package),
                    )
                )
            for package, specifier in npm_pins_from_line(line):
                pins.append(
                    InstallPin(
                        step_idx=idx,
                        line=line.strip(),
                        package=package,
                        pinned=specifier,
                        used=installed_npm.get(package),
                        ecosystem="npm",
                    )
                )
    seen: set[str] = set()
    for idx, code in sorted(output_blocks, key=lambda block: block[0]):
        for raw_line in code.splitlines():
            line = raw_line.strip()
            for package, version in _output_versions(line):
                if package not in tutorial_packages or package in seen:
                    continue
                seen.add(package)
                pins.append(
                    InstallPin(
                        step_idx=idx,
                        line=line,
                        package=package,
                        pinned=f"=={version}",
                        used=installed.get(package),
                        origin="pip_output",
                    )
                )
    return pins

"""Install-line constraints alongside the versions the sandbox actually used."""

import re
from collections.abc import Iterable

from app.extract.libs import pins_from_pip_line
from app.models import InstallPin
from app.packages import normalize


def find_install_pins(
    blocks: Iterable[tuple[int, str]], versions: dict[str, str]
) -> list[InstallPin]:
    installed = {normalize(name): version for name, version in versions.items()}
    pins: list[InstallPin] = []
    for idx, code in sorted(blocks, key=lambda block: block[0]):
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
    return pins

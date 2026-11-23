"""The interface the rest of the backend uses to talk to a check's sandbox.

`DockerSandbox` (task D1.7) implements it against a real container. Unit tests for
labeling and repair use a fake that implements the same methods.
"""

from typing import Protocol

from app.models import Environment, ExecResult, ProbeResult


class StepTimeoutError(Exception):
    """A step ran past the step timeout. The container has been killed; call `restart`."""


class SandboxError(Exception):
    """The container could not be started, prepared or reached."""


class Sandbox(Protocol):
    def start(self, packages: list[str]) -> Environment:
        """Start the container, install `packages`, cut the network, start the runner.

        A package pip can't install goes in `failed_packages`; the check carries on
        without it. Raises SandboxError when the container itself can't be prepared.
        """
        ...

    def exec(self, step_id: str, code: str) -> ExecResult:
        """Run one step in the session namespace. Raises StepTimeoutError."""
        ...

    def probe(self, probe_id: str, target: str, name: str) -> ProbeResult: ...

    def restart(self, replay: list[str]) -> None:
        """Start a fresh container and rerun `replay` (earlier steps' final code) in order."""
        ...

    def close(self) -> None:
        """Stop and remove the container. Safe to call more than once."""
        ...

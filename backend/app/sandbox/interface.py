"""The interface the rest of the backend uses to talk to a check's sandbox.

`DockerSandbox` (task D1.7) implements it against a real container. Unit tests for
labeling and repair use a fake that implements the same methods.
"""

from typing import Protocol

from app.models import Environment, ExecResult, ProbeResult, StepKind


class StepTimeoutError(Exception):
    """A step ran past the step timeout. The container has been killed; call `restart`."""


class SandboxError(Exception):
    """The container could not be started, prepared or reached."""


class SandboxTimeLimit(SandboxError):
    """The whole-check time limit ran out while the sandbox was working."""


class Sandbox(Protocol):
    def start(self, packages: list[str], npm_packages: list[str] | None = None) -> Environment:
        """Install pip and npm packages, cut the network, and start the runner.

        Record install failures in the environment and carry on without those packages.
        Raises SandboxError when the container itself can't be prepared.
        """
        ...

    def exec(self, step_id: str, code: str, *, lang: str = "python") -> ExecResult:
        """Run one step in the session namespace. Raises StepTimeoutError."""
        ...

    def probe(self, probe_id: str, target: str, name: str) -> ProbeResult: ...

    def restart(self, replay: list[tuple[StepKind, str]]) -> None:
        """Start a fresh container and rerun earlier steps by kind, in order."""
        ...

    def close(self) -> None:
        """Stop and remove the container. Safe to call more than once."""
        ...

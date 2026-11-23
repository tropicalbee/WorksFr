"""Run and replay steps while keeping sandbox mechanics out of the pipeline."""

import time
from collections.abc import Callable
from types import TracebackType
from typing import Self

from app.config import Settings
from app.labels import (
    cascade_decision,
    cascade_source,
    judge_patch,
    label_step,
    precheck,
)
from app.models import (
    Environment,
    ExecResult,
    Label,
    LabelDecision,
    PatchVerdict,
    ProbeResult,
    RepairAttempt,
)
from app.sandbox.interface import Sandbox, SandboxError, StepTimeoutError
from app.step import Step


class CheckTimeoutError(Exception):
    """The whole-check time budget has expired."""


class CheckSession:
    def __init__(
        self, sandbox: Sandbox, settings: Settings, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.sandbox, self.settings, self._clock = sandbox, settings, clock
        self._environment: Environment | None = None
        self._deadline: float | None = None
        self._ledger: list[tuple[int, str]] = []
        self._unbound: dict[str, int] = {}
        self._probe_id = 0
        self._closed = False

    @property
    def environment(self) -> Environment:
        if self._environment is None:
            raise SandboxError("Session has not started")
        return self._environment

    @property
    def ledger(self) -> list[tuple[int, str]]:
        return list(self._ledger)

    def _check_deadline(self) -> None:
        if self._deadline is not None and self._clock() >= self._deadline:
            raise CheckTimeoutError("The check exceeded its time limit")

    def start(self, packages: list[str]) -> Environment:
        self._check_deadline()
        self._deadline = self._clock() + self.settings.check_timeout_seconds
        self._environment = self.sandbox.start(packages)
        self._check_deadline()
        return self.environment

    def _restart(self, ledger: list[tuple[int, str]]) -> None:
        self._check_deadline()
        self.sandbox.restart([code for _, code in ledger])
        self._check_deadline()

    def _require_latest(self, step: Step) -> None:
        # Repairs happen right after a step runs, so its entry is the last in the ledger.
        # A real check, not an assert, so it survives `python -O`.
        if not self._ledger or self._ledger[-1][0] != step.idx:
            raise RuntimeError("Only the most recently run step can be repaired")

    @staticmethod
    def _check_protocol(result: ExecResult | ProbeResult) -> None:
        if result.error is not None and result.error.type == "ProtocolError":
            raise SandboxError("Runner protocol error")

    def run(self, step: Step) -> LabelDecision:
        self._check_deadline()
        if not step.runnable:
            raise ValueError("Step is not runnable")
        code = step.executed_code
        if code is None:
            raise ValueError("Step has no runnable code")
        decision = precheck(code)
        if decision is not None:
            step.record_run(None, decision)
            return decision
        try:
            result = self.sandbox.exec(f"s{step.idx}", code)
        except StepTimeoutError:
            self._restart(self._ledger)
            decision = label_step(None, timed_out=True)
            step.record_run(None, decision)
            return decision
        self._check_deadline()
        self._check_protocol(result)
        source = cascade_source(result.error, self._unbound)
        decision = (
            cascade_decision(source)
            if source is not None
            else label_step(
                result,
                failed_packages=self.environment.failed_packages,
            )
        )
        self._ledger.append((step.idx, code))
        step.record_run(result, decision)
        if decision.label == Label.BROKEN:
            for name in step.bound_names():
                self._unbound.setdefault(name, step.idx)
        return decision

    def try_patch(
        self, step: Step, code: str, attempt: int
    ) -> tuple[ExecResult | None, LabelDecision, PatchVerdict]:
        self._check_deadline()
        self._require_latest(step)
        baseline = self._ledger[:-1]
        self._restart(baseline)
        try:
            result = self.sandbox.exec(f"s{step.idx}-a{attempt}", code)
        except StepTimeoutError:
            self._restart(baseline)
            decision, verdict = judge_patch(None, timed_out=True)
            return None, decision, verdict
        self._check_deadline()
        self._check_protocol(result)
        decision, verdict = judge_patch(result, failed_packages=self.environment.failed_packages)
        return result, decision, verdict

    def accept(self, step: Step, attempt: RepairAttempt) -> None:
        self._check_deadline()
        self._require_latest(step)
        names = step.bound_names()
        step.accept(attempt)
        self._ledger[-1] = (step.idx, attempt.patched_code)
        for name in names:
            if self._unbound.get(name) == step.idx:
                self._unbound.pop(name)

    def reset(self) -> None:
        self._check_deadline()
        self._restart(self._ledger)

    def probe(self, target: str, name: str) -> ProbeResult | None:
        try:
            self._check_deadline()
            self._probe_id += 1
            result = self.sandbox.probe(f"p{self._probe_id}", target, name)
            self._check_deadline()
            self._check_protocol(result)
            return result
        except (SandboxError, CheckTimeoutError):
            return None

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.sandbox.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

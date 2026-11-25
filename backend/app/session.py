"""Run and replay steps while keeping sandbox mechanics out of the pipeline."""

import time
from collections.abc import Callable, Collection
from types import TracebackType
from typing import Self

from app.config import Settings
from app.labels import (
    cascade_decision,
    cascade_source,
    declared_names,
    judge_patch,
    label_step,
    missing_name,
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
    StepKind,
)
from app.sandbox.interface import Sandbox, SandboxError, StepTimeoutError
from app.step import Step


class CheckTimeoutError(Exception):
    """The whole-check time budget has expired."""


class CheckSession:
    def __init__(
        self,
        sandbox: Sandbox,
        settings: Settings,
        *,
        clock: Callable[[], float] = time.monotonic,
        guessed_packages: Collection[str] = (),
        c_system_headers: Collection[str] = (),
    ) -> None:
        self.sandbox, self.settings, self._clock = sandbox, settings, clock
        self._guessed_packages = tuple(guessed_packages)
        self._c_system_headers = frozenset(c_system_headers)
        self._environment: Environment | None = None
        self._deadline: float | None = None
        self._ledger: list[tuple[int, StepKind, str]] = []
        self._unbound: dict[str, int] = {}
        self._other_unbound: dict[tuple[StepKind, str], int] = {}
        self._unverified_sources: set[int] = set()
        self._probe_id = 0
        self._closed = False

    @property
    def environment(self) -> Environment:
        if self._environment is None:
            raise SandboxError("Session has not started")
        return self._environment

    @property
    def ledger(self) -> list[tuple[int, StepKind, str]]:
        return list(self._ledger)

    def _check_deadline(self) -> None:
        if self._deadline is not None and self._clock() >= self._deadline:
            raise CheckTimeoutError("The check exceeded its time limit")

    def seconds_left(self) -> float | None:
        """Seconds until the check deadline, or None before the session has started."""
        if self._deadline is None:
            return None
        return self._deadline - self._clock()

    def start(self, packages: list[str], npm_packages: list[str] | None = None) -> Environment:
        self._check_deadline()
        self._deadline = self._clock() + self.settings.check_timeout_seconds
        self._environment = self.sandbox.start(packages, npm_packages=npm_packages)
        self._check_deadline()
        return self.environment

    def _restart(self, ledger: list[tuple[int, StepKind, str]]) -> None:
        self._check_deadline()
        self.sandbox.restart([(kind, code) for _, kind, code in ledger])
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

    def _record_unbound(
        self, step: Step, decision: LabelDecision, result: ExecResult | None = None
    ) -> None:
        if not step.is_python:
            self._record_other_unbound(step, decision, result)
            return
        if decision.label not in (Label.BROKEN, Label.CANT_VERIFY):
            return
        for name in step.bound_names():
            self._unbound.setdefault(name, step.idx)
        if decision.label == Label.CANT_VERIFY:
            self._unverified_sources.add(step.idx)

    def _record_other_unbound(
        self, step: Step, decision: LabelDecision, result: ExecResult | None
    ) -> None:
        kind = step.block.kind
        names = declared_names(step.executed_code or "", kind)
        if result is not None and result.status == "ok":
            # The code ran or compiled, so its names exist again for later steps.
            for name in names:
                self._other_unbound.pop((kind, name), None)
            return
        if decision.label not in (Label.BROKEN, Label.CANT_VERIFY):
            return
        for name in names:
            self._other_unbound.setdefault((kind, name), step.idx)
        if decision.label == Label.CANT_VERIFY:
            self._unverified_sources.add(step.idx)

    def _cascade(self, step: Step, result: ExecResult) -> LabelDecision | None:
        if step.is_python:
            source = cascade_source(result.error, self._unbound)
        else:
            name = missing_name(result.error, step.block.kind)
            source = self._other_unbound.get((step.block.kind, name)) if name else None
        if source is None:
            return None
        return cascade_decision(
            source,
            Label.CANT_VERIFY if source in self._unverified_sources else Label.BROKEN,
            always_unverified=not step.is_python,
        )

    def run(self, step: Step) -> LabelDecision:
        self._check_deadline()
        if not step.runnable:
            raise ValueError("Step is not runnable")
        code = step.executed_code
        if code is None:
            raise ValueError("Step has no runnable code")
        kind = step.block.kind
        decision = precheck(code, kind=kind, c_system_headers=self._c_system_headers)
        if decision is not None:
            step.record_run(None, decision)
            self._record_unbound(step, decision)
            return decision
        try:
            lang = StepKind.PYTHON.value if step.is_python else kind.value
            result = self.sandbox.exec(f"s{step.idx}", code, lang=lang)
        except StepTimeoutError:
            self._restart(self._ledger)
            decision = label_step(None, timed_out=True)
            step.record_run(None, decision)
            self._record_unbound(step, decision)
            return decision
        self._check_deadline()
        self._check_protocol(result)
        decision = self._cascade(step, result) or label_step(
            result,
            kind=kind,
            failed_packages=self.environment.failed_packages,
            guessed_packages=self._guessed_packages,
            failed_npm_packages=self.environment.failed_npm_packages,
        )
        # Python can leave bindings behind on error. The other runners keep successful code.
        if step.is_python or result.status == "ok":
            self._ledger.append((step.idx, kind, code))
        step.record_run(result, decision)
        self._record_unbound(step, decision, result)
        return decision

    def try_patch(
        self, step: Step, code: str, attempt: int
    ) -> tuple[ExecResult | None, LabelDecision, PatchVerdict]:
        self._check_deadline()
        self._require_latest(step)
        baseline = self._ledger[:-1]
        self._restart(baseline)
        try:
            result = self.sandbox.exec(f"s{step.idx}-a{attempt}", code, lang=StepKind.PYTHON.value)
        except StepTimeoutError:
            self._restart(baseline)
            decision, verdict = judge_patch(None, timed_out=True)
            return None, decision, verdict
        self._check_deadline()
        self._check_protocol(result)
        decision, verdict = judge_patch(
            result,
            failed_packages=self.environment.failed_packages,
            guessed_packages=self._guessed_packages,
        )
        return result, decision, verdict

    def accept(self, step: Step, attempt: RepairAttempt) -> None:
        self._check_deadline()
        self._require_latest(step)
        names = step.bound_names()
        step.accept(attempt)
        self._ledger[-1] = (step.idx, step.block.kind, attempt.patched_code)
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

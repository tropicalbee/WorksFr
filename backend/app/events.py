"""Typed check events shared by storage, live streams and the OpenAPI schema."""

from datetime import date
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, Field

from app.models import (
    DateSource,
    ErrorInfo,
    InstallPin,
    Label,
    RepairAttempt,
    Score,
    StepKind,
    StepReport,
    WarningInfo,
)


class EventBlock(BaseModel):
    idx: int
    kind: StepKind
    code: str


class ExtractedEvent(BaseModel):
    type: Literal["extracted"] = "extracted"
    blocks: list[EventBlock]
    packages: list[str]
    title: str | None = None
    published: date | None = None
    date_source: DateSource = DateSource.UNKNOWN


class EnvironmentEvent(BaseModel):
    type: Literal["environment"] = "environment"
    versions: dict[str, str]
    failed_packages: list[str]
    install_pins: list[InstallPin] = Field(default_factory=list)


class StepStartedEvent(BaseModel):
    type: Literal["step_started"] = "step_started"
    idx: int


class StepResultEvent(BaseModel):
    type: Literal["step_result"] = "step_result"
    idx: int
    label: Label
    reason: str
    output: str
    error: ErrorInfo | None = None
    warnings: list[WarningInfo] = Field(default_factory=list)


class RepairAttemptEvent(BaseModel):
    type: Literal["repair_attempt"] = "repair_attempt"
    idx: int
    attempt: RepairAttempt


class StepFinalEvent(BaseModel):
    type: Literal["step_final"] = "step_final"
    step: StepReport


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    score: Score


class FailedEvent(BaseModel):
    type: Literal["failed"] = "failed"
    error: str


CheckEvent = Annotated[
    ExtractedEvent
    | EnvironmentEvent
    | StepStartedEvent
    | StepResultEvent
    | RepairAttemptEvent
    | StepFinalEvent
    | DoneEvent
    | FailedEvent,
    Field(discriminator="type"),
]


class StoredEvent(BaseModel):
    seq: int
    check_id: str
    event: CheckEvent


class EventSink(Protocol):
    def emit(self, event: CheckEvent) -> None: ...

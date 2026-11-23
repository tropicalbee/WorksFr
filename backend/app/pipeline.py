"""One check: extract, run, repair and emit a single ordered event history."""

import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.events import (
    DoneEvent,
    EnvironmentEvent,
    EventBlock,
    EventSink,
    ExtractedEvent,
    FailedEvent,
    RepairAttemptEvent,
    StepFinalEvent,
    StepResultEvent,
    StepStartedEvent,
)
from app.extract import ExtractionError, extract_notebook, extract_url
from app.models import Label, RepairAttempt, SourceFormat, StepKind
from app.pins import find_install_pins
from app.repair.evidence import DocCache
from app.repair.loop import Proposer, RepairContext, repair_step
from app.sandbox.interface import Sandbox
from app.score import compute_score
from app.session import CheckSession
from app.step import build_steps

logger = logging.getLogger(__name__)


@dataclass
class PipelineDeps:
    settings: Settings
    sandbox_factory: Callable[[str], Sandbox]
    proposer: Proposer
    http_client: httpx.Client
    doc_cache: DocCache


def _redact(text: str, settings: Settings) -> str:
    keys = [provider.api_key.get_secret_value() for provider in settings.providers]
    keys.append(settings.tavily_api_key.get_secret_value())
    for key in sorted(filter(None, keys), key=len, reverse=True):
        text = text.replace(key, "[redacted]")
    return text


def run_check(
    check_id: str, source: str | tuple[str, bytes], deps: PipelineDeps, sink: EventSink
) -> None:
    try:
        extraction = (
            extract_url(source, deps.http_client)
            if isinstance(source, str)
            else extract_notebook(*source)
        )
        if not extraction.blocks:
            raise ExtractionError("This page has no code blocks to check.")
        runnable = sum(
            block.kind in (StepKind.PYTHON, StepKind.REPL) for block in extraction.blocks
        )
        if not runnable:
            raise ExtractionError(
                "This page has code, but none of it is Python that WorksFr can run."
            )
        if extraction.source_format == SourceFormat.NOTEBOOK and runnable > deps.settings.max_steps:
            raise ExtractionError(
                f"This notebook has {runnable} runnable cells. "
                f"WorksFr checks at most {deps.settings.max_steps}."
            )
        sink.emit(
            ExtractedEvent(
                blocks=[
                    EventBlock(idx=block.idx, kind=block.kind, code=block.code)
                    for block in extraction.blocks
                ],
                packages=extraction.packages,
                title=extraction.title,
                published=extraction.published,
                date_source=extraction.date_source,
            )
        )
        steps = build_steps(extraction)
        unavailable = False
        with CheckSession(deps.sandbox_factory(check_id), deps.settings) as session:
            environment = session.start(extraction.packages)
            sink.emit(
                EnvironmentEvent(
                    versions=environment.versions,
                    failed_packages=environment.failed_packages,
                    install_pins=find_install_pins(
                        ((block.idx, block.code) for block in extraction.blocks),
                        environment.versions,
                    ),
                )
            )
            for step in steps:
                sink.emit(StepStartedEvent(idx=step.idx))
                if step.runnable:
                    decision = session.run(step)
                    report = step.to_report()
                    sink.emit(
                        StepResultEvent(
                            idx=step.idx,
                            label=decision.label,
                            reason=decision.reason,
                            output=report.output,
                            error=report.error,
                            warnings=report.warnings,
                        )
                    )
                    if not unavailable:
                        context = RepairContext(
                            title=extraction.title,
                            earlier_code="\n".join(
                                code for idx, code in session.ledger if idx != step.idx
                            ),
                            versions=environment.versions,
                            failed_packages=environment.failed_packages,
                            settings=deps.settings,
                            http_client=deps.http_client,
                            doc_cache=deps.doc_cache,
                        )

                        def on_attempt(attempt: RepairAttempt, idx: int = step.idx) -> None:
                            sink.emit(RepairAttemptEvent(idx=idx, attempt=attempt))

                        outcome = repair_step(
                            session, step, context, deps.proposer, on_attempt=on_attempt
                        )
                        unavailable = outcome.llm_unavailable
                    if unavailable and decision.label == Label.BROKEN:
                        decision.reason = "No repair was tried: every LLM provider was unavailable."
                sink.emit(StepFinalEvent(step=step.to_report()))
        sink.emit(DoneEvent(score=compute_score([step.to_report() for step in steps])))
    except ExtractionError as exc:
        sink.emit(FailedEvent(error=_redact(str(exc), deps.settings)))
    except Exception:
        logger.error(
            "Check %s failed:\n%s", check_id, _redact(traceback.format_exc(), deps.settings)
        )
        sink.emit(FailedEvent(error="The check stopped because of an internal error."))

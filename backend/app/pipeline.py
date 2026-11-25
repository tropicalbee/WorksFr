"""One check: extract, run, repair and emit a single ordered event history."""

import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import quote, quote_plus

import httpx

from app.config import Settings
from app.events import (
    DoneEvent,
    EnvironmentEvent,
    EventBlock,
    EventSink,
    ExtractedEvent,
    FailedEvent,
    RateLimitWaitEvent,
    RepairAttemptEvent,
    StepFinalEvent,
    StepResultEvent,
    StepStartedEvent,
)
from app.extract import (
    ExtractionError,
    PastedCode,
    extract_notebook,
    extract_paste,
    extract_url,
)
from app.extract.libs import imported_modules, packages_from_shell
from app.extract.routing import route_url
from app.extract.video import VideoReader
from app.extract.youtube_links import LOGIN_NOTE, extract_youtube_links
from app.models import (
    Block,
    Label,
    RepairAttempt,
    SourceFormat,
    StepKind,
    StepReport,
    is_runnable,
)
from app.packages import guessed_distributions
from app.pins import find_install_pins
from app.repair.evidence import DocCache
from app.repair.loop import Proposer, RepairContext, repair_step
from app.sandbox.interface import Sandbox, SandboxTimeLimit
from app.score import compute_score
from app.session import CheckSession, CheckTimeoutError
from app.step import Step, build_steps

logger = logging.getLogger(__name__)


@dataclass
class PipelineDeps:
    settings: Settings
    sandbox_factory: Callable[[str], Sandbox]
    proposer: Proposer
    http_client: httpx.Client
    doc_cache: DocCache
    proposer_factory: Callable[[Callable[[str, float], None]], Proposer] | None = None
    clock: Callable[[], float] = time.monotonic
    video_reader: VideoReader = field(init=False)

    def __post_init__(self) -> None:
        self.video_reader = VideoReader(self.settings, self.http_client)


class _PerCheckDocCache:
    """Keep evidence and code-derived search keys private to one video check."""

    def __init__(self) -> None:
        self._contents: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._contents.get(key)

    def put(self, key: str, library: str, version: str | None, source: str, content: str) -> None:
        self._contents[key] = content


def _redact(text: str, settings: Settings) -> str:
    keys = [provider.api_key.get_secret_value() for provider in settings.providers]
    keys.append(settings.tavily_api_key.get_secret_value())
    keys.append(settings.video_reader_api_key.get_secret_value())
    keys.append(settings.firecrawl_api_key.get_secret_value())
    keys.append(settings.jina_api_key.get_secret_value())
    youtube_key = settings.youtube_api_key.get_secret_value()
    keys.extend((youtube_key, quote(youtube_key, safe=""), quote_plus(youtube_key)))
    for key in sorted(filter(None, keys), key=len, reverse=True):
        text = text.replace(key, "[redacted]")
    return text


def _guessed_packages(blocks: list[Block]) -> set[str]:
    imports: set[str] = set()
    installs: set[str] = set()
    for block in blocks:
        if block.kind in (StepKind.PYTHON, StepKind.REPL) and block.runnable_code:
            imports.update(imported_modules(block.runnable_code))
        if block.kind == StepKind.SHELL:
            installs.update(packages_from_shell(block.code))
        elif block.kind == StepKind.PYTHON:
            commands = "\n".join(
                line for line in block.code.splitlines() if line.lstrip().startswith(("!", "%"))
            )
            installs.update(packages_from_shell(commands))
    return guessed_distributions(imports, installs)


def _step_report(step: Step, reads: int = 1) -> StepReport:
    report = step.to_report()
    report.video_timestamp = step.block.video_timestamp
    report.low_confidence = step.block.low_confidence
    report.read_agreement = step.block.read_agreement
    if report.low_confidence and report.label == Label.BROKEN:
        note = "This step may have been misread from the video."
        agreement = report.read_agreement
        if agreement is not None and agreement < reads:
            note = (
                "This step may have been misread from the video "
                f"(only {agreement} of {reads} reads saw it)."
            )
        report.reason = " ".join(filter(None, (report.reason, note)))
    return report


def run_check(
    check_id: str, source: str | tuple[str, bytes] | PastedCode, deps: PipelineDeps, sink: EventSink
) -> None:
    try:

        def on_wait(provider: str, seconds: float) -> None:
            sink.emit(RateLimitWaitEvent(provider=provider, seconds=seconds))

        if isinstance(source, str):
            route = route_url(source)
            if route.kind == "video":
                linked = extract_youtube_links(route.fetch_urls[0], deps.settings, deps.http_client)
                if linked.login_blocked:
                    from app.events import CheckEvent
                    from app.models import WarningInfo

                    original_sink = sink

                    class SourceNoticeSink:
                        noted = False

                        def emit(self, event: CheckEvent) -> None:
                            if isinstance(event, StepFinalEvent) and not self.noted:
                                event.step.warnings.append(
                                    WarningInfo(category="SourceWarning", message=LOGIN_NOTE)
                                )
                                self.noted = True
                            elif isinstance(event, FailedEvent):
                                event = event.model_copy(
                                    update={"error": f"{event.error} {LOGIN_NOTE}"}
                                )
                            original_sink.emit(event)

                    sink = SourceNoticeSink()
                extraction = linked.extraction or deps.video_reader.extract(
                    route.fetch_urls[0], on_wait=on_wait
                )
                if linked.extraction is None:
                    for block in extraction.blocks:
                        block.code = _redact(block.code, deps.settings)
                        block.context = _redact(block.context, deps.settings)
                        if block.runnable_code is not None:
                            block.runnable_code = _redact(block.runnable_code, deps.settings)
            else:
                extraction = extract_url(source, deps.http_client, deps.settings)
        elif isinstance(source, PastedCode):
            extraction = extract_paste(source.code, source.title, source.language)
        else:
            extraction = extract_notebook(*source)
        if not extraction.blocks:
            raise ExtractionError("This page has no code blocks to check.")
        runnable = sum(is_runnable(block.kind) for block in extraction.blocks)
        if not runnable:
            raise ExtractionError(
                "This page has code, but none of it is code that WorksFr can run."
            )
        if extraction.source_format == SourceFormat.NOTEBOOK and runnable > deps.settings.max_steps:
            raise ExtractionError(
                f"This notebook has {runnable} runnable cells. "
                f"WorksFr checks at most {deps.settings.max_steps}."
            )
        sink.emit(
            ExtractedEvent(
                blocks=[
                    EventBlock(
                        idx=block.idx,
                        kind=block.kind,
                        code=block.code,
                        video_timestamp=block.video_timestamp,
                        low_confidence=block.low_confidence,
                        read_agreement=block.read_agreement,
                    )
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
        out_of_time = False
        current: Step | None = None
        doc_cache = (
            _PerCheckDocCache()
            if extraction.source_format == SourceFormat.VIDEO
            else deps.doc_cache
        )

        proposer = (
            deps.proposer_factory(on_wait) if deps.proposer_factory is not None else deps.proposer
        )
        with CheckSession(
            deps.sandbox_factory(check_id),
            deps.settings,
            guessed_packages=_guessed_packages(extraction.blocks),
            c_system_headers=extraction.c_system_headers,
            clock=deps.clock,
        ) as session:
            environment = session.start(extraction.packages, npm_packages=extraction.npm_packages)
            pypi_names = environment.versions.keys() | set(environment.failed_packages)

            def npm_report_name(package: str) -> str:
                return f"npm:{package}" if package in pypi_names else package

            sink.emit(
                EnvironmentEvent(
                    versions=environment.versions
                    | {
                        npm_report_name(package): version
                        for package, version in environment.npm_versions.items()
                    },
                    failed_packages=[
                        *environment.failed_packages,
                        *(npm_report_name(package) for package in environment.failed_npm_packages),
                    ],
                    install_pins=find_install_pins(
                        ((block.idx, block.code) for block in extraction.blocks),
                        environment.versions,
                        npm_versions=environment.npm_versions,
                        output_blocks=(
                            (block.idx, block.code)
                            for block in extraction.blocks
                            if block.kind == StepKind.OUTPUT
                        ),
                        packages=extraction.packages,
                    ),
                )
            )
            for step in steps:
                current = step
                sink.emit(StepStartedEvent(idx=step.idx))
                if step.runnable:
                    decision = session.run(step)
                    report = _step_report(step, deps.settings.video_reads)
                    sink.emit(
                        StepResultEvent(
                            idx=step.idx,
                            label=decision.label,
                            reason=report.reason,
                            output=report.output,
                            error=report.error,
                            warnings=report.warnings,
                        )
                    )
                    if step.is_python and not unavailable and not out_of_time:
                        context = RepairContext(
                            title=extraction.title,
                            earlier_code="\n".join(
                                code
                                for idx, kind, code in session.ledger
                                if idx != step.idx and kind in (StepKind.PYTHON, StepKind.REPL)
                            ),
                            versions=environment.versions,
                            failed_packages=environment.failed_packages,
                            settings=deps.settings,
                            http_client=deps.http_client,
                            doc_cache=doc_cache,
                        )

                        def on_attempt(attempt: RepairAttempt, idx: int = step.idx) -> None:
                            sink.emit(RepairAttemptEvent(idx=idx, attempt=attempt))

                        outcome = repair_step(
                            session, step, context, proposer, on_attempt=on_attempt
                        )
                        unavailable = outcome.llm_unavailable
                        out_of_time = outcome.out_of_time
                    skipped_reason = None
                    if unavailable:
                        skipped_reason = "No repair was tried: every LLM provider was unavailable."
                    elif out_of_time and not step.attempts:
                        skipped_reason = "No repair was tried: the check was running out of time."
                    if skipped_reason and step.is_python and decision.label == Label.BROKEN:
                        decision.reason = (
                            " ".join(filter(None, (decision.reason, skipped_reason)))
                            if step.block.low_confidence
                            else skipped_reason
                        )
                sink.emit(StepFinalEvent(step=_step_report(step, deps.settings.video_reads)))
        sink.emit(
            DoneEvent(
                score=compute_score(
                    [_step_report(step, deps.settings.video_reads) for step in steps]
                )
            )
        )
    except ExtractionError as exc:
        sink.emit(FailedEvent(error=_redact(str(exc), deps.settings)))
    except (SandboxTimeLimit, CheckTimeoutError):
        minutes = deps.settings.check_timeout_seconds // 60
        where = f"at step {current.idx + 1}" if current is not None else "before the first step"
        sink.emit(
            FailedEvent(
                error=f"This check reached its {minutes}-minute limit {where}. "
                "The steps before it are shown."
            )
        )
    except Exception:
        logger.error(
            "Check %s failed:\n%s", check_id, _redact(traceback.format_exc(), deps.settings)
        )
        sink.emit(FailedEvent(error="The check stopped because of an internal error."))

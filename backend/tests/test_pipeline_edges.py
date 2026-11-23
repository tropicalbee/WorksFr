import logging

import httpx
import nbformat
import pytest
from support.local_sandbox import LocalRunnerSandbox
from test_pipeline import ROOT, Cache, Proposer, Sink

from app.config import ProviderConfig, Settings
from app.labels import judge_patch, label_step
from app.llm import LLMUnavailableError
from app.models import Environment, ErrorInfo, ExecResult, Label, PatchVerdict
from app.pipeline import PipelineDeps, run_check


def notebook_cells(codes: list[str]) -> bytes:
    return nbformat.writes(
        nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(code) for code in codes])
    ).encode()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("", "This page has no code blocks to check."),
        ("<article><p>Just prose.</p></article>", "This page has no code blocks to check."),
        (
            '<pre class="language-shell">pip install pandas</pre>'
            '<pre class="language-javascript">const x = 1;</pre>'
            '<pre>{"x": 1}</pre>',
            "This page has code, but none of it is Python that WorksFr can run.",
        ),
    ],
)
def test_empty_and_non_python_pages(
    body: str, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda _: ["93.184.216.34"])
    settings = Settings(_env_file=None, providers=[])
    sink = Sink()

    def never_start(_: str) -> None:
        pytest.fail("Non-runnable pages must not start a sandbox")

    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as http:
        run_check(
            "empty",
            "https://example.com",
            PipelineDeps(settings, never_start, Proposer(), http, Cache()),
            sink,
        )
    assert sink.events[-1].type == "failed"
    assert sink.events[-1].error == message


def test_notebook_step_limit_before_sandbox() -> None:
    settings = Settings(_env_file=None, providers=[])
    sink = Sink()

    def never_start(_: str) -> None:
        pytest.fail("Oversized notebooks must not start a sandbox")

    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        run_check(
            "large",
            ("large.ipynb", notebook_cells(["x = 1"] * 151)),
            PipelineDeps(settings, never_start, Proposer(), http, Cache()),
            sink,
        )
    assert sink.events[-1].error == (
        "This notebook has 151 runnable cells. WorksFr checks at most 150."
    )


def test_limit_counts_only_runnable_cells() -> None:
    settings = Settings(_env_file=None, providers=[], max_steps=1)
    sink = Sink()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        deps = PipelineDeps(
            settings,
            lambda _: LocalRunnerSandbox(settings, runner_path=ROOT / "sandbox/runner.py"),
            Proposer(),
            http,
            Cache(),
        )
        run_check(
            "small",
            (
                "small.ipynb",
                notebook_cells(
                    [
                        "!pip install missing-example",
                        "x = 1",
                        "%matplotlib inline",
                    ]
                ),
            ),
            deps,
            sink,
        )
    assert sink.events[-1].type == "done"


@pytest.mark.parametrize(("module", "package"), [("pandas", "pandas"), ("sklearn", "scikit-learn")])
def test_failed_install_reason_and_patch_verdict(module: str, package: str) -> None:
    result = ExecResult(
        id="s1",
        status="error",
        error=ErrorInfo(
            type="ModuleNotFoundError",
            message=f"No module named '{module}'",
            traceback='File "/library/module.py", line 1, in load',
        ),
    )
    decision = label_step(result, failed_packages=[package])
    assert decision.label == Label.CANT_VERIFY
    assert decision.reason == f"{package} didn't install in the sandbox, so this step couldn't run."
    assert judge_patch(result, failed_packages=[package])[1] == PatchVerdict.FIXED_UNVERIFIED


def test_failed_install_reason_reaches_final_report() -> None:
    class MissingPackageSandbox:
        def start(self, packages: list[str]) -> Environment:
            return Environment(failed_packages=["pandas"])

        def exec(self, step_id: str, code: str) -> ExecResult:
            return ExecResult(
                id=step_id,
                status="error",
                error=ErrorInfo(type="ModuleNotFoundError", message="No module named 'pandas'"),
            )

        def close(self) -> None:
            pass

    settings = Settings(_env_file=None, providers=[])
    sink = Sink()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        run_check(
            "missing",
            ("missing.ipynb", notebook_cells(["import pandas"])),
            PipelineDeps(settings, lambda _: MissingPackageSandbox(), Proposer(), http, Cache()),
            sink,
        )
    final = next(event.step for event in sink.events if event.type == "step_final")
    assert final.reason == "pandas didn't install in the sandbox, so this step couldn't run."
    assert sink.events[-1].type == "done"


def test_unavailable_providers_only_called_once() -> None:
    class UnavailableProposer:
        calls = 0

        def propose(self, messages: list[dict[str, str]]) -> None:
            self.calls += 1
            raise LLMUnavailableError(["provider rate limited"])

    settings = Settings(_env_file=None, providers=[])
    proposer, sink = UnavailableProposer(), Sink()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        deps = PipelineDeps(
            settings,
            lambda _: LocalRunnerSandbox(settings, runner_path=ROOT / "sandbox/runner.py"),
            proposer,
            http,
            Cache(),
        )
        run_check(
            "unavailable",
            (
                "broken.ipynb",
                notebook_cells(
                    [
                        "int('bad')",
                        "float('bad')",
                        "x = 1",
                    ]
                ),
            ),
            deps,
            sink,
        )
    final = [event.step for event in sink.events if event.type == "step_final"]
    assert proposer.calls == 1
    assert [step.label for step in final] == [Label.BROKEN, Label.BROKEN, Label.WORKS]
    assert all(
        step.reason == "No repair was tried: every LLM provider was unavailable."
        for step in final[:2]
    )
    assert sink.events[-1].type == "done"


def test_internal_exception_is_logged_not_exposed(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(
        _env_file=None,
        providers=[
            ProviderConfig(
                name="test",
                base_url="https://example.com",
                api_key="private-test-key",
                model="test",
            )
        ],
    )

    def broken_factory(_: str) -> None:
        raise RuntimeError("private-test-key detail for log")

    sink = Sink()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        with caplog.at_level(logging.ERROR, logger="app.pipeline"):
            run_check(
                "internal",
                ("test.ipynb", notebook_cells(["x = 1"])),
                PipelineDeps(settings, broken_factory, Proposer(), http, Cache()),
                sink,
            )
    assert sink.events[-1].error == "The check stopped because of an internal error."
    assert "detail for log" in caplog.text
    assert "private-test-key" not in caplog.text
    assert "RuntimeError" in caplog.text

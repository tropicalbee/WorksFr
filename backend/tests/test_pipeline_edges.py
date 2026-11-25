import logging

import httpx
import nbformat
import pytest
from support.local_sandbox import LocalRunnerSandbox
from test_pipeline import ROOT, Cache, Proposer, Sink

from app.config import ProviderConfig, Settings
from app.extract import PastedCode
from app.labels import judge_patch, label_step
from app.llm import LLMUnavailableError
from app.models import Environment, ErrorInfo, ExecResult, Label, PatchVerdict, StepKind
from app.pipeline import PipelineDeps, run_check
from app.repair.loop import RepairContext, RepairOutcome
from app.session import CheckSession
from app.step import Step


def notebook_cells(codes: list[str]) -> bytes:
    return nbformat.writes(
        nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell(code) for code in codes])
    ).encode()


@pytest.mark.parametrize(
    "module,install,label",
    [
        ("sk", "", Label.CANT_VERIFY),
        ("sklearn", "", Label.BROKEN),
        ("sk", "!pip install SK==1\n", Label.BROKEN),
    ],
)
def test_guessed_import_origin_reaches_label_and_skips_repairs(
    module: str, install: str, label: Label
) -> None:
    from app.models import ProbeResult

    class ImportSandbox:
        def start(self, packages: list[str], npm_packages: list[str] | None = None) -> Environment:
            return Environment(versions={package: "1" for package in packages})

        def exec(self, step_id: str, code: str, *, lang: str = "python") -> ExecResult:
            return ExecResult(
                id=step_id,
                status="error",
                error=ErrorInfo(
                    type="ImportError", message=f"cannot import name 'my_sk' from '{module}'"
                ),
            )

        def probe(self, probe_id: str, target: str, name: str) -> ProbeResult:
            return ProbeResult(id=probe_id, status="ok", target_found=False)

        def close(self) -> None:
            pass

    class UnavailableProposer:
        calls = 0

        def propose(self, messages: list[dict[str, str]]) -> None:
            self.calls += 1
            raise LLMUnavailableError(["test provider unavailable"])

    settings = Settings(_env_file=None, providers=[])
    proposer, sink = UnavailableProposer(), Sink()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        run_check(
            "guessed",
            ("guessed.ipynb", notebook_cells([f"{install}from {module} import my_sk"])),
            PipelineDeps(settings, lambda _: ImportSandbox(), proposer, http, Cache()),
            sink,
        )
    final = next(event.step for event in sink.events if event.type == "step_final")
    assert final.label == label
    assert proposer.calls == (0 if label == Label.CANT_VERIFY else 1)
    assert sink.events[-1].type == "done"


def test_cant_verify_dependency_is_not_sent_to_repair() -> None:
    class NeverPropose:
        def propose(self, messages: list[dict[str, str]]) -> None:
            pytest.fail("Unverifiable source and its dependent must not go to repair")

    settings = Settings(_env_file=None, providers=[])
    sink = Sink()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        run_check(
            "unverifiable-cascade",
            (
                "dependency.ipynb",
                notebook_cells(["data = open('missing-fixture-data.bin').read()", "print(data)"]),
            ),
            PipelineDeps(
                settings,
                lambda _: LocalRunnerSandbox(settings, runner_path=ROOT / "sandbox/runner.py"),
                NeverPropose(),
                http,
                Cache(),
            ),
            sink,
        )
    finals = [event.step for event in sink.events if event.type == "step_final"]
    assert [step.label for step in finals] == [Label.CANT_VERIFY, Label.CANT_VERIFY]
    assert finals[1].reason == "depends on step 1, which couldn't be verified"
    assert sink.events[-1].type == "done"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("", "This page has no code blocks to check."),
        ("<article><p>Just prose.</p></article>", "This page has no code blocks to check."),
        (
            '<pre class="language-shell">pip install pandas</pre>'
            '<pre class="language-java">int x = 1;</pre>'
            '<pre>{"x": 1}</pre>',
            "This page has code, but none of it is code that WorksFr can run.",
        ),
    ],
)
def test_empty_and_non_runnable_pages(
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


@pytest.mark.parametrize(
    "code",
    [
        "SELECT * FROM users WHERE id = 1;",
        "This is just a sentence about cooking pasta.",
        'public class Hello { void say() { System.out.println("hello"); } }',
        'fn main() { println!("hello"); }',
        'echo "hello"',
    ],
)
def test_unsupported_paste_has_clear_error_before_sandbox(code: str) -> None:
    settings = Settings(_env_file=None, providers=[])
    sink = Sink()

    def never_start(_: str) -> None:
        pytest.fail("Unsupported pasted code must not start a sandbox")

    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        run_check(
            "unsupported",
            PastedCode(code),
            PipelineDeps(settings, never_start, Proposer(), http, Cache()),
            sink,
        )
    assert sink.events[-1].type == "failed"
    assert sink.events[-1].error == "No Python, JavaScript, TypeScript, C or C++ code found."


@pytest.mark.parametrize(
    "code",
    [
        "```jsx\nfunction App() { return <div>Hello</div>; }\n```",
        "```tsx\nconst App = () => <div />;\n```",
        "```jsx\nconst score = 1;\n```",
        "```tsx\nconst score: number = 1;\n```",
        "```js\nfunction App() { return <div>Hello</div>; }\n```",
        "```ts\nconst App = () => <div />;\n```",
        "function App() { return <div>Hello</div>; }",
        "const card = <div />;",
    ],
)
def test_jsx_paste_is_reported_as_unverifiable_without_execution(code: str) -> None:
    settings = Settings(_env_file=None, providers=[])
    sandbox, sink = FakeSandbox(), Sink()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        run_check(
            "jsx",
            PastedCode(code),
            PipelineDeps(settings, lambda _: sandbox, Proposer(), http, Cache()),
            sink,
        )
    assert sink.events[-1].type == "done"
    final = next(event.step for event in sink.events if event.type == "step_final")
    assert final.label == Label.CANT_VERIFY
    assert final.reason == "JSX needs a build step, so this step wasn't run."
    assert sandbox.exec_count == 0


@pytest.mark.parametrize(
    "code",
    [
        '```js\nconst html = "<div>Hello</div>";\nconsole.log(html);\n```',
        "```ts\nfunction identity<T>(x: T): T { return x; }\n```",
        "```ts\nconst score = <number>42;\n```",
        "```js\nconsole.log(0 < x && x > 2);\n```",
    ],
)
def test_node_paste_without_jsx_reaches_sandbox(code: str) -> None:
    settings = Settings(_env_file=None, providers=[])
    sandbox, sink = FakeSandbox(), Sink()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        run_check(
            "node",
            PastedCode(code),
            PipelineDeps(settings, lambda _: sandbox, Proposer(), http, Cache()),
            sink,
        )
    assert sink.events[-1].type == "done"
    final = next(event.step for event in sink.events if event.type == "step_final")
    assert final.label == Label.WORKS
    assert sandbox.exec_count == 1


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
        def start(self, packages: list[str], npm_packages: list[str] | None = None) -> Environment:
            return Environment(failed_packages=["pandas"])

        def exec(self, step_id: str, code: str, *, lang: str = "python") -> ExecResult:
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


class FakeSandbox:
    """Mocked sandbox: steps containing 'bad' fail; restart can be made to run out of time."""

    def __init__(self, clock: list[float] | None = None, *, restart_error: Exception | None = None):
        self.clock, self.restart_error = clock, restart_error
        self.exec_count = 0

    def start(self, packages: list[str], npm_packages: list[str] | None = None) -> Environment:
        return Environment(versions={})

    def exec(self, step_id: str, code: str, *, lang: str = "python") -> ExecResult:
        self.exec_count += 1
        if self.clock is not None and self.exec_count == 1:
            self.clock[0] += 850
        if "bad" in code:
            return ExecResult(
                id=step_id, status="error", error=ErrorInfo(type="ValueError", message="bad value")
            )
        return ExecResult(id=step_id, status="ok")

    def probe(self, probe_id: str, target: str, name: str):
        from app.models import ProbeResult

        return ProbeResult(id=probe_id, status="ok", target_found=False)

    def restart(self, replay: list[tuple[StepKind, str]]) -> None:
        if self.restart_error is not None:
            raise self.restart_error

    def close(self) -> None:
        pass


def test_pipeline_dispatches_languages_and_repairs_python_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MixedSandbox(FakeSandbox):
        languages: list[str] = []

        def start(self, packages: list[str], npm_packages: list[str] | None = None) -> Environment:
            assert packages == []
            assert npm_packages == ["lodash"]
            return Environment(npm_versions={"lodash": "4.17.21"})

        def exec(self, step_id: str, code: str, *, lang: str = "python") -> ExecResult:
            self.languages.append(lang)
            return super().exec(step_id, code, lang=lang)

    def repair(
        _session: CheckSession,
        step: Step,
        context: RepairContext,
        _proposer: object,
        **_kwargs: object,
    ) -> RepairOutcome:
        assert step.block.kind == StepKind.PYTHON
        if step.idx == 1:
            return RepairOutcome(None)
        assert step.idx == 2
        assert context.earlier_code == "value = 42"
        return RepairOutcome(None, llm_unavailable=True)

    body = """<article>
        <pre class="language-javascript">const _ = require('lodash');</pre>
        <pre class="language-python">value = 42</pre>
        <pre class="language-python">int('bad')</pre>
        <pre class="language-typescript">throw new Error('bad');</pre>
        <pre class="language-c">#include &lt;curl/curl.h&gt;</pre>
        <pre class="language-cpp">int main() { return 0; }</pre>
    </article>"""
    monkeypatch.setattr("app.extract.fetch.resolve_host", lambda _: ["93.184.216.34"])
    monkeypatch.setattr("app.pipeline.repair_step", repair)
    settings = Settings(_env_file=None, providers=[])
    sandbox, sink = MixedSandbox(), Sink()
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as http:
        deps = PipelineDeps(settings, lambda _: sandbox, Proposer(), http, Cache())
        run_check("mixed", "https://example.com", deps, sink)
    finals = [event.step for event in sink.events if event.type == "step_final"]
    assert sandbox.languages == ["javascript", "python", "python", "typescript", "cpp"]
    assert [step.label for step in finals] == [
        Label.WORKS,
        Label.WORKS,
        Label.BROKEN,
        Label.BROKEN,
        Label.CANT_VERIFY,
        Label.WORKS,
    ]
    assert finals[3].reason == "ValueError: bad value"
    assert finals[3].error is not None
    assert finals[3].repair_attempts == []
    assert finals[4].reason == "needs the system library curl/curl.h"
    assert finals[4].error is None
    assert sink.events[-1].type == "done"


def test_no_repair_when_little_time_is_left() -> None:
    clock = [0.0]

    class CountingProposer:
        calls = 0

        def propose(self, messages: list[dict[str, str]]) -> None:
            self.calls += 1
            raise AssertionError("no repair should be tried")

    settings = Settings(_env_file=None, providers=[])
    proposer, sink = CountingProposer(), Sink()
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        deps = PipelineDeps(
            settings, lambda _: FakeSandbox(clock), proposer, http, Cache(), clock=lambda: clock[0]
        )
        run_check(
            "late",
            ("late.ipynb", notebook_cells(["int('bad')", "x = 1", "int('bad2')"])),
            deps,
            sink,
        )
    finals = [event.step for event in sink.events if event.type == "step_final"]
    assert proposer.calls == 0
    assert [step.label for step in finals] == [Label.BROKEN, Label.WORKS, Label.BROKEN]
    note = "No repair was tried: the check was running out of time."
    assert note in finals[0].reason and note in finals[2].reason
    assert note not in finals[1].reason
    assert sink.events[-1].type == "done"


def test_time_limit_during_restart_keeps_finished_steps() -> None:
    from app.sandbox.interface import SandboxTimeLimit

    settings = Settings(_env_file=None, providers=[])
    sink = Sink()
    sandbox = FakeSandbox(restart_error=SandboxTimeLimit("Whole-check timeout exceeded"))
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        deps = PipelineDeps(settings, lambda _: sandbox, Proposer(), http, Cache())
        run_check(
            "limit",
            ("limit.ipynb", notebook_cells(["x = 1", "y = 2", "int('bad')"])),
            deps,
            sink,
        )
    types = [event.type for event in sink.events]
    assert types.count("step_final") == 2
    assert types[-1] == "failed"
    assert sink.events[-1].error == (
        "This check reached its 15-minute limit at step 3. The steps before it are shown."
    )

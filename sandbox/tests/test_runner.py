"""Runner protocol and source processing checks that need no toolchains."""

import io
import json
import os
import queue
import subprocess
import sys
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from sandbox.runner import (
    MAX_REPLY_BYTES,
    OUTPUT_LIMIT,
    Capture,
    ChildExecutor,
    compiler_error,
    compiler_redeclaration,
    compiler_warnings,
    encode_reply,
    node_error,
    node_extension,
    node_redeclaration,
    node_warnings,
    output_after_marker,
    wrap_c_main,
)

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "sandbox" / "runner.py"
NODE_REDECLARATION = (
    "/home/sandbox/work/example.cjs:4\nconst value = 42;\n      ^\n\n"
    "SyntaxError: Identifier 'value' has already been declared\n"
    "    at wrapSafe (node:internal/modules/cjs/loader:1700:18)\n"
)
NODE_TOP_LEVEL_AWAIT = (
    "SyntaxError: await is only valid in async functions and the top level bodies of modules\n"
)
TSX_TOP_LEVEL_AWAIT = (
    "Error: Transform failed with 1 error:\n"
    "/home/sandbox/work/example.cts:2:14: ERROR: Top-level await is currently not "
    'supported with the "cjs" output format\n'
)
C_REDECLARATION = (
    "current.c:2:7: error: conflicting types for ‘value’; have ‘float’\n"
    "history.c:1:5: note: previous definition of ‘value’ with type ‘int’\n"
)


class RunnerHelperTests(unittest.TestCase):
    def test_marker_removes_history_even_after_the_output_limit(self) -> None:
        marker = b"worksfr-unique-marker\n"
        history = b"x" * (OUTPUT_LIMIT * 2 - 3)
        stream = io.BytesIO(history + marker + b"new output\n")
        self.assertEqual(output_after_marker(stream, marker), (b"new output\n", True))
        stream = io.BytesIO(b"error before the marker\n")
        self.assertEqual(output_after_marker(stream, marker), (stream.getvalue(), False))

    def test_child_reply_fits_protocol_with_highly_escaped_output(self) -> None:
        hostile_text = "\x00" * OUTPUT_LIMIT
        reply = {
            "id": "s2",
            "status": "error",
            "stdout": hostile_text,
            "stderr": hostile_text,
            "error": {
                "type": "TypeError",
                "message": hostile_text,
                "traceback": hostile_text,
            },
            "warnings": [],
        }
        encoded = encode_reply(reply, child=True)
        self.assertLessEqual(len(encoded), MAX_REPLY_BYTES)
        self.assertTrue(encoded.endswith(b"\n"))
        result = json.loads(encoded)
        self.assertEqual(result["error"]["type"], "TypeError")
        self.assertTrue(result["stdout"].endswith("[truncated]"))

    def test_node_extension_distinguishes_static_modules(self) -> None:
        cases = (
            ("const value = require('lodash')", False, ".cjs"),
            ("const value: number = 1", True, ".cts"),
            ("import {\n isEqual,\n} from 'lodash';", False, ".mjs"),
            ("const n = 1; export { n };", True, ".mts"),
            ("import 'node:assert/strict';", False, ".mjs"),
            ("import('lodash').then(console.log);", False, ".cjs"),
            ("/*\nexport default {}\n*/\nconsole.log('import')", False, ".cjs"),
            ("const path = require('node:path');\nconst copy = { export: path };", True, ".cts"),
        )
        for source, typescript, extension in cases:
            with self.subTest(source=source):
                self.assertEqual(node_extension(source, typescript), extension)

    def test_node_error_uses_uncaught_error_and_keeps_traceback(self) -> None:
        cases = (
            (
                "/home/sandbox/work/example.cjs:2\nvalue()\n^\n\n"
                "TypeError: value is not a function\n"
                "    at Object.<anonymous> (/home/sandbox/work/example.cjs:2:1)\n",
                "TypeError",
                "value is not a function",
            ),
            (
                "/home/sandbox/work/example.cjs:2\nconsole.log(document)\n            ^\n\n"
                "ReferenceError: document is not defined\n"
                "    at Object.<anonymous> (/home/sandbox/work/example.cjs:2:13)\n",
                "ReferenceError",
                "document is not defined",
            ),
            (
                "/home/sandbox/work/example.cjs:2\nconst = 1\n      ^\n\n"
                "SyntaxError: Unexpected token '='\n"
                "    at wrapSafe (node:internal/modules/cjs/loader:1700:18)\n",
                "SyntaxError",
                "Unexpected token '='",
            ),
            (
                "node:internal/modules/package_json_reader:316\n"
                "  throw new ERR_MODULE_NOT_FOUND(packageName, fileURLToPath(base), null);\n"
                "        ^\n\n"
                "Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'missing' imported from "
                "/home/sandbox/work/example.mjs\n"
                "    at Object.getPackageJSONURL "
                "(node:internal/modules/package_json_reader:316:9)\n",
                "ERR_MODULE_NOT_FOUND",
                "Cannot find package 'missing' imported from /home/sandbox/work/example.mjs",
            ),
        )
        for stderr, category, message in cases:
            with self.subTest(category=category):
                self.assertEqual(
                    node_error(stderr, 1, "prefix\n" + stderr),
                    {"type": category, "message": message, "traceback": "prefix\n" + stderr},
                )
        self.assertEqual(node_error("other output", 3, "other output")["type"], "ExitStatus")
        stderr = "ReferenceError: document is not defined\nTypeError: invalid argument\n"
        self.assertEqual(node_error(stderr, 1, stderr)["type"], "TypeError")

    def test_node_redeclaration_diagnostics(self) -> None:
        positives = (
            NODE_REDECLARATION,
            (
                "file:///home/sandbox/work/example.mjs:4\nimport fs from 'node:fs';\n       ^\n\n"
                "SyntaxError: Identifier 'fs' has already been declared\n"
                "    at compileSourceTextModule (node:internal/modules/esm/utils:346:16)\n"
            ),
            (
                "Error: Transform failed with 1 error:\n"
                '/home/sandbox/work/example.cts:4:6: ERROR: The symbol "value" '
                "has already been declared\n"
                "    at failureErrorWithLog (/usr/local/lib/node_modules/esbuild/lib/main.js:1:1)\n"
            ),
        )
        negatives = (
            "SyntaxError: Unexpected token '='\n",
            "TypeError: Identifier 'value' has already been declared\n",
            "Error: Transform failed with 1 error:\nexample.cts:4:6: ERROR: Expected identifier\n",
        )
        for stderr in positives:
            with self.subTest(stderr=stderr):
                self.assertTrue(node_redeclaration(node_error(stderr, 1, stderr)))
        for stderr in negatives:
            with self.subTest(stderr=stderr):
                self.assertFalse(node_redeclaration(node_error(stderr, 1, stderr)))

    def test_compiler_redeclaration_diagnostics(self) -> None:
        positives = (
            C_REDECLARATION,
            "current.cpp:1:5: error: redefinition of ‘int add(int, int)’\n",
            "history.c:1:5: note: previous definition of ‘value’ with type ‘int’\n",
        )
        negatives = (
            "current.c:4:3: error: ‘missing’ undeclared (first use in this function)\n",
            "current.c:1:10: fatal error: missing.h: No such file or directory\n",
            '    4 |   printf("redefinition of ‘value’");\n',
        )
        for stderr in positives:
            with self.subTest(stderr=stderr):
                self.assertTrue(compiler_redeclaration(stderr))
        for stderr in negatives:
            with self.subTest(stderr=stderr):
                self.assertFalse(compiler_redeclaration(stderr))

    def test_delayed_node_warning_from_history_does_not_count(self) -> None:
        stderr = (
            "(node:42) [DEP0005] DeprecationWarning: Buffer() is deprecated\n"
            "    at showFlaggedDeprecation (node:buffer:198:11)\n"
            "    at Object.<anonymous> (/home/sandbox/work/example.cjs:1:1)\n"
            "(node:42) [DEP0123] DeprecationWarning: old API\n"
            "    at oldMethod (/home/sandbox/work/example.cjs:2:1)\n"
            "    at Object.<anonymous> (/home/sandbox/work/example.cjs:5:1)\n"
        )
        warnings = node_warnings(stderr, "example.cjs", 4, "<step-s2>")
        self.assertEqual(
            warnings,
            [
                {
                    "category": "DeprecationWarning",
                    "message": "old API",
                    "filename": "<step-s2>",
                    "lineno": 2,
                    "module": "__step__",
                }
            ],
        )

    def test_compiler_error_and_current_step_deprecation(self) -> None:
        stderr = (
            "history.c:3:3: warning: 'old' is deprecated [-Wdeprecated-declarations]\n"
            "current.c: In function 'main':\n"
            "current.c:6:3: warning: 'old' is deprecated [-Wdeprecated-declarations]\n"
            "current.c:2:6: note: declared here\n"
            "current.c:7:3: error: 'unknown' undeclared (first use in this function)\n"
            "current.c:8:3: error: expected ';' before '}' token\n"
        )
        self.assertEqual(
            compiler_error(stderr, 1),
            {
                "type": "CompileError",
                "message": (
                    "current.c:7:3: error: 'unknown' undeclared (first use in this function)"
                ),
                "traceback": stderr,
            },
        )
        self.assertEqual(
            compiler_warnings(stderr, "current.c", "<step-s2>"),
            [
                {
                    "category": "DeprecationWarning",
                    "message": "'old' is deprecated",
                    "filename": "<step-s2>",
                    "lineno": 6,
                    "module": "__step__",
                }
            ],
        )

    def test_c_main_moves_directives_and_keeps_body_line_numbers(self) -> None:
        source = "\n".join(
            (
                "#include <stdio.h>",
                'printf("%d\\n", DOUBLE(2));',
                "#define DOUBLE(x) \\",
                "  ((x) * 2)",
            )
        )
        preamble, body = wrap_c_main(source, "current.c").split("int main(void) {\n")
        self.assertEqual(
            preamble,
            '#line 1 "current.c"\n#include <stdio.h>\n\n#define DOUBLE(x) \\\n  ((x) * 2)\n',
        )
        self.assertTrue(body.startswith('#line 1 "current.c"\n\nprintf'))
        self.assertNotIn("#include", body)
        self.assertNotIn("#define", body)
        self.assertNotIn("((x) * 2)", body)


class ChildExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capture = Mock(spec=Capture)
        self.capture.read.side_effect = lambda: (
            self.capture.stdout.getvalue().decode("utf-8"),
            self.capture.stderr.getvalue().decode("utf-8"),
        )
        self.executor = ChildExecutor(ROOT / "sandbox", self.capture)
        self.sources: list[str] = []
        source_patch = patch.object(
            self.executor, "write_source", side_effect=lambda path, code: self.sources.append(code)
        )
        source_patch.start()
        self.addCleanup(source_patch.stop)

    def output(self, status: int, stdout: str = "", stderr: str = "") -> int:
        self.capture.stdout = io.BytesIO(stdout.encode("utf-8"))
        self.capture.stderr = io.BytesIO(stderr.encode("utf-8"))
        return status

    def node_marker(self) -> str:
        boundary = self.sources[-1].split("globalThis.process.stderr.write(", 1)[1]
        return json.JSONDecoder().raw_decode(boundary)[0]

    def test_node_top_level_await_retries_as_module(self) -> None:
        for lang, suffixes, diagnostic in (
            ("javascript", [".cjs", ".mjs"], NODE_TOP_LEVEL_AWAIT),
            ("typescript", [".cts", ".mts"], TSX_TOP_LEVEL_AWAIT),
        ):
            with self.subTest(lang=lang):
                old = "const previous = 7;"
                code = "const value = await Promise.resolve(previous); console.log(value);"
                self.executor.node_history = [(lang, old)]
                self.sources.clear()
                attempts: list[str] = []

                def run(
                    args: list[str],
                    workdir: Path,
                    attempts: list[str] = attempts,
                    diagnostic: str = diagnostic,
                ) -> int:
                    attempts.append(Path(args[-1]).suffix)
                    if len(attempts) == 1:
                        return self.output(1, stderr=diagnostic)
                    marker = self.node_marker()
                    return self.output(0, marker + "7\n", marker)

                self.capture.run_child.side_effect = run
                result = self.executor.execute({"id": "s2", "code": code}, lang)
                self.assertEqual(attempts, suffixes)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["stdout"], "7\n")
                self.assertIn(old, self.sources[-1])
                self.assertEqual(self.executor.node_history, [(lang, old), (lang, code)])

    def test_node_top_level_await_retries_only_once(self) -> None:
        self.capture.run_child.side_effect = lambda *_: self.output(1, stderr=NODE_TOP_LEVEL_AWAIT)
        result = self.executor.execute(
            {"id": "s1", "code": "await Promise.resolve(1);"}, "javascript"
        )
        self.assertEqual(self.capture.run_child.call_count, 2)
        self.assertEqual(result["error"]["type"], "SyntaxError")

    def test_node_history_exit_retries_current_step_in_isolation(self) -> None:
        for prior_exit in (0, 7):
            with self.subTest(prior_exit=prior_exit):
                old = "process.exit();"
                code = "console.log(42);"
                self.executor.node_history = [("javascript", old)]
                self.sources.clear()

                def run(args: list[str], workdir: Path, prior_exit: int = prior_exit) -> int:
                    if len(self.sources) == 1:
                        return self.output(prior_exit)
                    marker = self.node_marker()
                    return self.output(0, marker + "42\n", marker)

                self.capture.run_child.side_effect = run
                result = self.executor.execute({"id": "s2", "code": code}, "javascript")
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["stdout"], "42\n")
                self.assertNotIn(old, self.sources[-1])
                self.assertEqual(self.executor.node_history, [("javascript", code)])

    def test_node_current_exit_is_not_retried(self) -> None:
        old = "const value = 7;"
        self.executor.node_history = [("javascript", old)]

        def run(args: list[str], workdir: Path) -> int:
            marker = self.node_marker()
            return self.output(7, marker, marker)

        self.capture.run_child.side_effect = run
        result = self.executor.execute({"id": "s2", "code": "process.exit(7);"}, "javascript")
        self.assertEqual(self.capture.run_child.call_count, 1)
        self.assertEqual(result["error"]["type"], "ExitStatus")
        self.assertEqual(self.executor.node_history, [("javascript", old)])

    def test_node_successful_retry_replaces_history(self) -> None:
        old = "const value = 1;\nconst previousOnly = 4;"
        code = "const value = 42;\nconsole.log(value);"
        self.executor.node_history.append(("javascript", old))

        def run(args: list[str], workdir: Path) -> int:
            if len(self.sources) == 1:
                return self.output(1, stderr=NODE_REDECLARATION)
            marker = self.node_marker()
            return self.output(0, marker + "42\n", marker)

        self.capture.run_child.side_effect = run
        result = self.executor.execute({"id": "s2", "code": code}, "javascript")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["stdout"], "42\n")
        self.assertEqual(self.capture.run_child.call_count, 2)
        self.assertIn(old, self.sources[0])
        self.assertNotIn(old, self.sources[1])
        self.assertEqual(self.executor.node_history, [("javascript", code)])

    def test_node_failed_retry_preserves_history_and_reports_its_error(self) -> None:
        old = "const value = 1;\nconst previousOnly = 4;"
        code = "const value = 42;\nconsole.log(previousOnly);"
        self.executor.node_history.append(("javascript", old))

        def run(args: list[str], workdir: Path) -> int:
            if len(self.sources) == 1:
                return self.output(1, stderr=NODE_REDECLARATION)
            marker = self.node_marker()
            return self.output(1, marker, marker + "ReferenceError: previousOnly is not defined\n")

        self.capture.run_child.side_effect = run
        result = self.executor.execute({"id": "s2", "code": code}, "javascript")
        self.assertEqual(result["error"]["type"], "ReferenceError")
        self.assertEqual(self.capture.run_child.call_count, 2)
        self.assertEqual(self.executor.node_history, [("javascript", old)])

    def test_runtime_syntax_error_does_not_retry(self) -> None:
        old = "const value = 1;"
        self.executor.node_history.append(("javascript", old))

        def run(args: list[str], workdir: Path) -> int:
            marker = self.node_marker()
            return self.output(1, marker, marker + NODE_REDECLARATION)

        self.capture.run_child.side_effect = run
        code = "eval('const value = 1; const value = 2;');"
        result = self.executor.execute({"id": "s2", "code": code}, "javascript")
        self.assertEqual(result["error"]["type"], "SyntaxError")
        self.assertEqual(self.capture.run_child.call_count, 1)
        self.assertEqual(self.executor.node_history, [("javascript", old)])

    def test_native_compile_only_result_keeps_definitions_for_later_run(self) -> None:
        for lang in ("c", "cpp"):
            with self.subTest(lang=lang):
                code = "int value(void) { return 42; }"
                self.sources.clear()
                self.capture.reset_mock()
                self.capture.run_child.side_effect = lambda *_: self.output(0)
                compile_only = self.executor.execute({"id": "s1", "code": code}, lang)
                self.assertEqual(compile_only["status"], "ok")
                self.assertEqual(compile_only["result"], "compiled")
                self.assertEqual(self.capture.run_child.call_count, 1)
                self.assertIn("-c", self.capture.run_child.call_args.args[0])

                program = "int main(void) { return value() == 42 ? 0 : 1; }"
                executed = self.executor.execute({"id": "s2", "code": program}, lang)
                self.assertEqual(executed["status"], "ok")
                self.assertIsNone(executed["result"])
                self.assertEqual(self.capture.run_child.call_count, 3)
                self.assertIn(code, self.sources[-1])

    def test_native_retry_uses_isolated_wrapping_and_replaces_only_on_success(self) -> None:
        old = "int value = 1;"
        code = '#include <stdio.h>\nfloat value = 2;\nprintf("%.0f\\n", value);'
        for exit_status in (0, 7):
            with self.subTest(exit_status=exit_status):
                self.executor.native_history["c"] = [(old, False)]
                self.sources.clear()
                self.capture.reset_mock()

                def run(args: list[str], workdir: Path, exit_status: int = exit_status) -> int:
                    if len(args) == 1:
                        return self.output(exit_status, "2\n")
                    if len(self.sources) == 1:
                        return self.output(1, stderr=C_REDECLARATION)
                    if "-c" in args:
                        return self.output(1, stderr="current.c:3:8: error: expected declaration\n")
                    return self.output(0)

                self.capture.run_child.side_effect = run
                result = self.executor.execute({"id": "s2", "code": code}, "c")
                self.assertEqual(self.capture.run_child.call_count, 4)
                combined, isolated, wrapped = self.sources
                self.assertIn(old, combined)
                self.assertNotIn(old, isolated)
                self.assertNotIn(old, wrapped)
                self.assertIn("int main(void) {", wrapped)
                history = self.executor.native_history["c"]
                if exit_status == 0:
                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(result["stdout"], "2\n")
                    self.assertEqual(len(history), 1)
                    self.assertTrue(history[0][1])
                    self.assertIn('printf("%.0f\\n", value);', history[0][0])
                else:
                    self.assertEqual(result["error"]["type"], "ExitStatus")
                    self.assertEqual(history, [(old, False)])


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = ROOT / ".scratch" / "runner-tests" / uuid.uuid4().hex
        self.work.mkdir(parents=True)
        self.capture_dir = self.work.parent / "captures" / self.work.name
        self.capture_dir.mkdir(parents=True)
        self.log = (self.work / "runner.stderr").open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(RUNNER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            cwd=self.work,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(self.work),
                "WORKSFR_CAPTURE_DIR": str(self.capture_dir),
            },
            text=True,
            encoding="utf-8",
        )
        self.lines: queue.Queue[str] = queue.Queue()
        self.reader = threading.Thread(target=self.read_lines, daemon=True)
        self.reader.start()
        self.ready = self.reply()

    def read_lines(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put("")

    def tearDown(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        if self.process.stdout is not None:
            self.process.stdout.close()
        self.log.close()

    def reply(self) -> dict:
        try:
            line = self.lines.get(timeout=10)
        except queue.Empty:
            self.fail("Runner did not reply within 10 seconds")
        self.assertTrue(line, "Runner exited without a reply")
        return json.loads(line)

    def request(self, request: dict) -> dict:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        return self.reply()

    def execute(self, code: str, step_id: str = "s1") -> dict:
        return self.request({"op": "exec", "id": step_id, "code": code})

    def test_ready_and_persistent_namespace(self) -> None:
        self.assertEqual(self.ready["op"], "ready")
        self.assertTrue(self.ready["python"].startswith("3."))
        self.assertEqual(self.execute("value = 40")["status"], "ok")
        reply = self.execute("value + 2", "s2")
        self.assertEqual(reply["result"], "42")
        self.assertEqual(reply["id"], "s2")
        self.assertEqual(self.execute("__name__")["result"], "'__main__'")

    def test_python_native_and_child_output(self) -> None:
        reply = self.execute(
            "import os, subprocess, sys\n"
            "print('printed', flush=True)\n"
            "os.write(1, b'native-out\\n')\n"
            "os.write(2, b'native-err\\n')\n"
            "subprocess.run([sys.executable, '-c', "
            "\"import sys; print('child-out'); print('child-err', file=sys.stderr)\"], "
            "check=True)\n"
        )
        self.assertEqual(reply["status"], "ok")
        for output in ("printed", "native-out", "child-out"):
            self.assertIn(output, reply["stdout"])
        for output in ("native-err", "child-err"):
            self.assertIn(output, reply["stderr"])
        self.assertEqual(self.execute("6 * 7")["result"], "42")

    def test_input_and_direct_stdin_are_unavailable(self) -> None:
        reply = self.execute("input('prompt')")
        self.assertEqual(reply["error"]["type"], "StdinUnavailableError")
        reply = self.execute("import sys, os\n(sys.stdin.read(), os.read(0, 1))")
        self.assertEqual(reply["result"], "('', b'')")
        self.assertEqual(self.execute("42")["result"], "42")

    def test_exception_traceback_and_system_exit(self) -> None:
        reply = self.execute("def fail():\n    raise ValueError('broken')\nfail()")
        self.assertEqual(reply["status"], "error")
        self.assertEqual(reply["error"]["type"], "ValueError")
        self.assertEqual(reply["error"]["message"], "broken")
        self.assertIn("<step-s1>", reply["error"]["traceback"])
        self.assertNotIn(str(RUNNER), reply["error"]["traceback"])
        self.assertEqual(self.execute("raise SystemExit(7)")["error"]["type"], "SystemExit")
        self.assertEqual(self.execute("42")["result"], "42")

    def test_syntax_error_and_keyboard_interrupt(self) -> None:
        self.assertEqual(self.execute("if")["error"]["type"], "SyntaxError")
        self.assertEqual(
            self.execute("raise KeyboardInterrupt()")["error"]["type"], "KeyboardInterrupt"
        )
        self.assertEqual(self.execute("42")["result"], "42")

    def test_warnings_include_origin_and_survive_error(self) -> None:
        reply = self.execute(
            "import warnings\n"
            "warnings.warn('old', DeprecationWarning)\n"
            "warnings.warn('old', DeprecationWarning)\n"
            "raise RuntimeError('after warning')"
        )
        self.assertEqual(len(reply["warnings"]), 2)
        warning = reply["warnings"][0]
        self.assertEqual(warning["category"], "DeprecationWarning")
        self.assertEqual(warning["message"], "old")
        self.assertEqual(warning["module"], "__step__")
        self.assertEqual(warning["filename"], "<step-s1>")
        self.assertEqual(warning["lineno"], 2)
        reply = self.execute(
            "warnings.warn_explicit('library', FutureWarning, "
            "'/opt/venv/lib/python3.12/site-packages/pandas/core/frame.py', 9)"
        )
        self.assertEqual(reply["warnings"][0]["module"], "pandas")

    def test_result_none_and_limits(self) -> None:
        self.assertEqual(self.execute("x = 3\nx + 1")["result"], "4")
        self.assertIsNone(self.execute("print('hello')")["result"])
        self.assertIsNone(self.execute("x = 9")["result"])
        reply = self.execute(
            "import os\nos.write(1, b'x' * 100000)\nos.write(2, b'y' * 100000)\n'\\u20ac' * 10000"
        )
        for field, limit in (("stdout", 65536), ("stderr", 65536), ("result", 8192)):
            self.assertLessEqual(len(reply[field].encode("utf-8")), limit)
            self.assertIn("[truncated]", reply[field])

    def test_stream_replacement_and_closure(self) -> None:
        reply = self.execute(
            "import sys, builtins\nsys.stdout.close()\nsys.stderr.close()\n"
            "sys.stdin = None\nbuiltins.input = lambda: 'replaced'"
        )
        self.assertEqual(reply["status"], "ok")
        self.assertEqual(self.execute("print('restored')")["stdout"], "restored\n")
        self.assertEqual(self.execute("input()")["error"]["type"], "StdinUnavailableError")

    def test_background_output_cannot_corrupt_protocol(self) -> None:
        self.assertEqual(
            self.execute(
                "import threading, os\n"
                "def late():\n    print('late')\n    os.write(1, b'late-fd\\n')\n"
                "timer = threading.Timer(0.2, late)\n"
                "timer.start()"
            )["status"],
            "ok",
        )
        threading.Event().wait(0.5)
        self.assertEqual(self.execute("42")["result"], "42")

    def test_logging_stream_references_survive_steps(self) -> None:
        self.execute(
            "import logging, sys\nlogging.basicConfig(stream=sys.stdout, level=logging.INFO)"
        )
        reply = self.execute("logging.info('hello')")
        self.assertIn("hello", reply["stdout"])
        self.assertEqual(reply["stderr"], "")

    def test_capture_files_are_outside_working_directory(self) -> None:
        reply = self.execute("import pathlib\nlist(pathlib.Path('.').glob('worksfr-*'))")
        self.assertEqual(reply["result"], "[]")
        self.assertEqual(len(list(self.capture_dir.glob("worksfr-*"))), 2)

    def test_probe_module_namespace_and_missing_target(self) -> None:
        reply = self.request({"op": "probe", "id": "p1", "target": "json", "name": "dumpz"})
        self.assertTrue(reply["target_found"])
        self.assertFalse(reply["exists"])
        self.assertIn("dump", reply["close_matches"])
        self.assertEqual(reply["package"], "json")
        self.assertIsNone(reply["version"])
        self.assertIn("signature", reply["closest"])
        self.assertLessEqual(len(reply["closest"]["doc"]), 2000)
        self.execute("items = []")
        reply = self.request({"op": "probe", "id": "p2", "target": "items", "name": "append"})
        self.assertTrue(reply["exists"])
        self.assertEqual(reply["closest"]["name"], "append")
        reply = self.request(
            {"op": "probe", "id": "p3", "target": "worksfr_missing_xyz.nope", "name": "a"}
        )
        self.assertFalse(reply["target_found"])

    def test_probe_submodule_and_uninspectable_attribute(self) -> None:
        reply = self.request(
            {"op": "probe", "id": "p1", "target": "json.decoder.JSONDecoder", "name": "decode"}
        )
        self.assertTrue(reply["exists"])
        self.assertEqual(reply["closest"]["name"], "decode")
        reply = self.request({"op": "probe", "id": "p2", "target": "json", "name": "decoder"})
        self.assertTrue(reply["exists"])
        self.assertIsNone(reply["closest"]["signature"])

    def test_probe_package_discovery_and_distribution_mapping(self) -> None:
        package = self.work / "probe_fixture"
        package.mkdir()
        (package / "__init__.py").write_text("print('import output')\n", encoding="utf-8")
        (package / "helpers.py").write_text('"""Helper module."""\n', encoding="utf-8")
        metadata = self.work / "worksfr_probe_fixture-1.2.3.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: worksfr-probe-fixture\nVersion: 1.2.3\n",
            encoding="utf-8",
        )
        (metadata / "top_level.txt").write_text("probe_fixture\n", encoding="utf-8")
        reply = self.request(
            {"op": "probe", "id": "p1", "target": "probe_fixture", "name": "helperz"}
        )
        self.assertEqual(reply["status"], "ok")
        self.assertFalse(reply["exists"])
        self.assertIn("helpers", reply["close_matches"])
        self.assertEqual(reply["closest"]["name"], "helpers")
        self.assertEqual(reply["closest"]["doc"], "Helper module.")
        self.assertEqual(reply["package"], "probe_fixture")
        self.assertEqual(reply["version"], "1.2.3")
        self.assertEqual(self.execute("42")["result"], "42")

    def test_chained_exception_and_failed_repr_recover(self) -> None:
        reply = self.execute(
            "try:\n    raise ValueError('first')\n"
            "except ValueError as exc:\n    raise RuntimeError('second') from exc"
        )
        self.assertIn("ValueError: first", reply["error"]["traceback"])
        self.assertIn("RuntimeError: second", reply["error"]["traceback"])
        self.assertNotIn(str(RUNNER), reply["error"]["traceback"])
        reply = self.execute(
            "class BadRepr:\n    def __repr__(self):\n        raise ValueError('repr')\nBadRepr()"
        )
        self.assertEqual(reply["error"]["type"], "ValueError")
        self.assertEqual(self.execute("42")["result"], "42")

    def test_invalid_utf8_output_is_bounded_and_parseable(self) -> None:
        reply = self.execute("import os\nos.write(1, b'\\xff' * 65536)\n'\\u20ac'")
        self.assertEqual(reply["status"], "ok")
        self.assertLessEqual(len(reply["stdout"].encode("utf-8")), 65536)
        self.assertIn("[truncated]", reply["stdout"])
        self.assertEqual(reply["result"], "'\u20ac'")

    def test_bad_requests_do_not_end_session(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write("{bad json\n")
        self.process.stdin.flush()
        reply = self.reply()
        self.assertIsNone(reply["id"])
        self.assertEqual(reply["error"]["type"], "ProtocolError")
        for request in (
            [],
            {"op": "other", "id": "bad"},
            {"op": "exec", "code": 5},
            {"op": "exec", "code": "", "lang": "shell"},
            {"op": "exec", "code": "", "lang": None},
        ):
            self.assertEqual(self.request(request)["error"]["type"], "ProtocolError")
        self.assertEqual(self.execute("42")["result"], "42")
        self.assertEqual(self.execute("value = 41")["status"], "ok")
        reply = self.request({"op": "exec", "code": "value + 1", "lang": "python"})
        self.assertEqual(reply["result"], "42")

    def test_eof_exits_successfully(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()
        self.assertEqual(self.process.wait(timeout=5), 0)


if __name__ == "__main__":
    unittest.main()

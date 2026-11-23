"""Protocol checks using only small, locally authored Python snippets."""

import json
import os
import queue
import subprocess
import sys
import threading
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "sandbox" / "runner.py"


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
        for request in ([], {"op": "other", "id": "bad"}, {"op": "exec", "code": 5}):
            self.assertEqual(self.request(request)["error"]["type"], "ProtocolError")
        self.assertEqual(self.execute("42")["result"], "42")

    def test_eof_exits_successfully(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()
        self.assertEqual(self.process.wait(timeout=5), 0)


if __name__ == "__main__":
    unittest.main()

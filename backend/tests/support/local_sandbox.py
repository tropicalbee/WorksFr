"""For tests with trusted authored snippets only. Never run tutorial code on the host."""

import importlib.metadata
import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from app.config import Settings
from app.models import Environment, ExecResult, ProbeResult
from app.sandbox.interface import SandboxError, StepTimeoutError

Result = TypeVar("Result", ExecResult, ProbeResult)
ROOT = Path(__file__).resolve().parents[3]


class LocalRunnerSandbox:
    def __init__(self, settings: Settings, *, runner_path: Path) -> None:
        self.settings, self.runner_path = settings, runner_path
        self.process: subprocess.Popen[bytes] | None = None
        self.packages: list[str] = []
        self.requests: list[dict[str, str]] = []
        self._log = None

    def start(self, packages: list[str]) -> Environment:
        self.packages = list(packages)
        directory = ROOT / ".scratch" / "local-runner" / uuid.uuid4().hex
        captures, work = directory / "captures", directory / "work"
        captures.mkdir(parents=True)
        work.mkdir()
        self._log = (directory / "stderr.log").open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(self.runner_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,
            cwd=work,
            env={
                **os.environ,
                "WORKSFR_CAPTURE_DIR": str(captures),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        try:
            ready = json.loads(self._exchange(None))
            if ready.get("op") != "ready":
                raise SandboxError("Invalid ready message")
        except Exception:
            self.close()
            raise
        versions, failed = {}, []
        for package in packages:
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                failed.append(package)
        return Environment(versions=versions, failed_packages=failed)

    def _exchange(self, request: dict[str, str] | None) -> bytes:
        process = self.process
        if process is None or process.stdin is None or process.stdout is None:
            raise SandboxError("Local runner not started")
        replies: queue.Queue[bytes | Exception] = queue.Queue(maxsize=1)

        def communicate() -> None:
            try:
                if request is not None:
                    self.requests.append(request)
                    process.stdin.write((json.dumps(request) + "\n").encode())
                    process.stdin.flush()
                replies.put(process.stdout.readline(1024 * 1024 + 1))
            except Exception as exc:
                replies.put(exc)

        threading.Thread(target=communicate, daemon=True).start()
        try:
            line = replies.get(timeout=self.settings.step_timeout_seconds)
        except queue.Empty:
            self.close()
            raise StepTimeoutError("Local test step timed out") from None
        if isinstance(line, Exception) or not line or len(line) > 1024 * 1024:
            raise SandboxError("Invalid local runner reply")
        return line

    def _request(self, request: dict[str, str], schema: type[Result]) -> Result:
        try:
            result = schema.model_validate_json(self._exchange(request))
        except ValidationError as exc:
            raise SandboxError("Invalid local runner JSON") from exc
        if result.id != request["id"]:
            raise SandboxError("Local runner id mismatch")
        return result

    def exec(self, step_id: str, code: str) -> ExecResult:
        return self._request({"op": "exec", "id": step_id, "code": code}, ExecResult)

    def probe(self, probe_id: str, target: str, name: str) -> ProbeResult:
        return self._request(
            {"op": "probe", "id": probe_id, "target": target, "name": name}, ProbeResult
        )

    def restart(self, replay: list[str]) -> None:
        self.close()
        self.start(self.packages)
        for index, code in enumerate(replay):
            self.exec(f"replay-{index}", code)

    def close(self) -> None:
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
        if self._log is not None:
            self._log.close()
            self._log = None

"""Download a public YouTube video stream in an isolated Docker container."""

import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.config import Settings
from app.extract.video import redact_code

logger = logging.getLogger(__name__)
_WATCH_URL = re.compile(r"https://www\.youtube\.com/watch\?v=[A-Za-z0-9_-]{11}")
_VIDEO_FILE = re.compile(r"video\.[A-Za-z0-9]+")


class Runner(Protocol):
    def __call__(
        self,
        args: list[str],
        /,
        *,
        timeout: int,
        capture_output: bool,
        text: bool,
        encoding: str,
        errors: str,
        check: bool,
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True)
class DownloadResult:
    path: Path | None
    reason: str = ""


def _args(url: str, settings: Settings, out: Path, name: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "bridge",
        "--user",
        "10001:10001",
        "--read-only",
        "--tmpfs",
        f"/tmp:size={settings.video_tools_tmpfs_mb}m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        settings.video_tools_memory,
        "--memory-swap",
        settings.video_tools_memory,
        "--cpus",
        str(settings.video_tools_cpus).removesuffix(".0"),
        "--pids-limit",
        str(settings.video_tools_pids_limit),
        "--label",
        "worksfr=1",
        "-v",
        f"{out}:/out",
        "--",
        settings.video_tools_image,
        "yt-dlp",
        "--ignore-config",
        "--no-playlist",
        "--no-cache-dir",
        "--no-exec",
        "--restrict-filenames",
        "-f",
        "bv*[height<=720][ext=mp4]/bv*[height<=720]",
        "--max-filesize",
        f"{settings.video_download_max_mb}M",
        "--match-filter",
        f"!is_live & duration <= {settings.video_max_minutes * 60}",
        "-o",
        "/out/video.%(ext)s",
        "--",
        url,
    ]


def _download(url: str, settings: Settings, out: Path, run: Runner) -> DownloadResult:
    name = f"worksfr-video-{uuid4().hex}"
    args = _args(url, settings, out, name)
    try:
        completed = run(
            args,
            timeout=settings.video_download_timeout_seconds,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return DownloadResult(None, "Docker was not found.")
    except BaseException as exc:
        # The daemon can keep running after its attached CLI exits unexpectedly.
        try:
            run(
                ["docker", "kill", name],
                timeout=5,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except Exception:
            pass
        if isinstance(exc, subprocess.TimeoutExpired):
            return DownloadResult(None, "Video download timed out.")
        if not isinstance(exc, Exception):
            raise
        logger.info("Video download could not be completed (%s).", type(exc).__name__)
        return DownloadResult(None, "The video download failed.")
    if completed.returncode:
        logger.info("Video download failed: %s", redact_code(completed.stderr or "")[:2000])
        return DownloadResult(None, "The video download failed.")

    files = list(out.iterdir())
    if len(files) != 1 or not _VIDEO_FILE.fullmatch(files[0].name):
        return DownloadResult(None, "The download did not produce exactly one video file.")
    path = files[0]
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        return DownloadResult(None, "The download output is not a regular video file.")
    if info.st_size > settings.video_download_max_mb * 1024**2:
        return DownloadResult(None, "The video exceeds the download size limit.")
    return DownloadResult(path)


@contextmanager
def downloaded_video(
    url: str, settings: Settings, *, run: Runner = subprocess.run
) -> Iterator[DownloadResult]:
    if not settings.video_download:
        yield DownloadResult(None, "Video download is off.")
        return
    if not _WATCH_URL.fullmatch(url):
        yield DownloadResult(None, "Provide a canonical YouTube video URL.")
        return

    folder: Path | None = None
    try:
        try:
            folder = Path(tempfile.mkdtemp(prefix="worksfr-video-"))
            out = folder / "out"
            out.mkdir()
            # The container UID can write the bind mount. Its host parent stays private.
            if os.name == "posix":
                out.chmod(0o777)
            result = _download(url, settings, out, run)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            logger.info("Video download could not be completed (%s).", type(exc).__name__)
            result = DownloadResult(None, "Could not prepare or read the video download.")
        yield result
    finally:
        if folder is not None:
            try:
                shutil.rmtree(folder)
            except OSError as exc:
                logger.warning("Could not clean up the video download (%s).", type(exc).__name__)

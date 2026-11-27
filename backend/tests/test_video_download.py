import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.video_download import _args, downloaded_video

URL = "https://www.youtube.com/watch?v=_zN8QziXjd8"


@pytest.fixture(autouse=True)
def workspace_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, video_download=True, video_download_max_mb=1)


def unexpected_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    pytest.fail("Docker should not be called")


def test_off_by_default() -> None:
    with downloaded_video(URL, Settings(_env_file=None), run=unexpected_run) as result:
        assert result.path is None
        assert result.reason == "Video download is off."


@pytest.mark.parametrize(
    "url", ["https://www.youtube.com/playlist?list=test", "https://evil.test/", URL + "&--exec=x"]
)
def test_bad_url(url: str, settings: Settings) -> None:
    with downloaded_video(url, settings, run=unexpected_run) as result:
        assert result.path is None and result.reason


def mounted_dir(args: list[str]) -> Path:
    return Path(args[args.index("-v") + 1].removesuffix(":/out"))


def test_download_limits_use_settings(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        video_tools_memory="2g",
        video_tools_cpus=2.5,
        video_tools_pids_limit=192,
        video_tools_tmpfs_mb=96,
    )
    args = _args(URL, settings, tmp_path, "video-test")
    for flag, value in {
        "--memory": "2g",
        "--memory-swap": "2g",
        "--cpus": "2.5",
        "--pids-limit": "192",
        "--tmpfs": "/tmp:size=96m",
    }.items():
        assert args[args.index(flag) + 1] == value


def test_success_and_arguments(settings: Settings) -> None:
    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        directory = mounted_dir(args)
        name = args[args.index("--name") + 1]
        assert re.fullmatch(r"worksfr-video-[0-9a-f]{32}", name)
        assert args == [
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
            "/tmp:size=64m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "128",
            "--label",
            "worksfr=1",
            "-v",
            f"{directory}:/out",
            "--",
            "worksfr-video-tools",
            "yt-dlp",
            "--ignore-config",
            "--no-playlist",
            "--no-cache-dir",
            "--no-exec",
            "--restrict-filenames",
            "-f",
            "bv*[height<=720][ext=mp4]/bv*[height<=720]",
            "--max-filesize",
            "1M",
            "--match-filter",
            "!is_live & duration <= 3600",
            "-o",
            "/out/video.%(ext)s",
            "--",
            URL,
        ]
        assert kwargs["timeout"] == settings.video_download_timeout_seconds
        assert not kwargs.get("shell", False)
        (directory / "video.mp4").write_bytes(b"video")
        return subprocess.CompletedProcess(args, 0)

    with downloaded_video(URL, settings, run=run) as result:
        assert result.path is not None
        assert result.path.read_bytes() == b"video"
        directory = result.path.parent
    assert not directory.exists()


@pytest.mark.parametrize(
    "failure", ["exit", "timeout", "exception", "missing", "none", "two", "symlink", "large"]
)
def test_failed_download_cleanup(
    failure: str, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[1] == "kill":
            assert kwargs["timeout"] < settings.video_download_timeout_seconds
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        directory = mounted_dir(args)
        if failure == "exit":
            return subprocess.CompletedProcess(args, 1, stderr="Download error")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 1)
        if failure == "exception":
            raise RuntimeError("runner failed")
        if failure == "missing":
            raise FileNotFoundError
        if failure == "two":
            (directory / "video.mp4").touch()
            (directory / "video.webm").touch()
        if failure == "symlink":
            link = directory / "video.mp4"
            try:
                link.symlink_to(tmp_path / "outside.mp4")
            except OSError:
                # Windows sandboxes may lack symlink creation privileges.
                link.touch()
                lstat = Path.lstat

                def symlink_stat(path: Path) -> Any:
                    info = lstat(path)
                    return type(info)((stat.S_IFLNK, *info[1:])) if path == link else info

                monkeypatch.setattr(Path, "lstat", symlink_stat)
        if failure == "large":
            (directory / "video.mp4").write_bytes(b"x" * (1024**2 + 1))
        return subprocess.CompletedProcess(args, 0)

    with downloaded_video(URL, settings, run=run) as result:
        assert result.path is None and result.reason
    if failure in ("timeout", "exception"):
        name = calls[0][calls[0].index("--name") + 1]
        assert calls[1:] == [["docker", "kill", name]]
    else:
        assert len(calls) == 1
    assert not list(tmp_path.glob("worksfr-video-*"))


def test_caller_exception_still_cleans_up(settings: Settings, tmp_path: Path) -> None:
    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        (mounted_dir(args) / "video.mp4").touch()
        return subprocess.CompletedProcess(args, 0)

    with pytest.raises(RuntimeError, match="caller"), downloaded_video(URL, settings, run=run):
        raise RuntimeError("caller")
    assert not list(tmp_path.glob("worksfr-video-*"))


@pytest.mark.docker
def test_download_real_video(settings: Settings) -> None:
    settings.video_download_max_mb = 1024
    with downloaded_video(URL, settings) as result:
        assert result.path is not None, result.reason
        assert result.path.is_file()
        directory = result.path.parent
    assert not directory.exists()

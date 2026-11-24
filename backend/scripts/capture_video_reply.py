"""Capture one raw video-reader reply for later parser development."""

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from app.config import Settings

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "video"
PROMPT = (
    "You are reading a programming tutorial video. List every block of code visible "
    "on screen in this clip, copied exactly as shown, in the order it appears. "
    "Do not fix, complete or invent code. If a block is cut off, copy only what is "
    "visible and set partial to true. For each block give start_seconds (whole seconds "
    "from the start of this clip, not the whole video, when it first appears), "
    "kind (python, shell, output or file), code, and partial. "
    "file: the contents of a non-Python file such as requirements.txt or .env. "
    'Return only JSON: {"blocks": [...]}.'
)
MISSING_CONFIG = (
    "Reading videos needs VIDEO_READER_BASE_URL, VIDEO_READER_API_KEY and "
    "VIDEO_READER_MODEL in backend/.env."
)
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


def video_id(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
        ):
            return None
        host = parsed.hostname
        path = parsed.path.rstrip("/")
        if host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            if path == "/watch":
                values = parse_qs(parsed.query).get("v", [])
                candidate = values[0] if len(values) == 1 else ""
            elif path.startswith("/shorts/"):
                candidate = path.removeprefix("/shorts/")
            else:
                return None
        elif host in ("youtu.be", "www.youtu.be"):
            candidate = path.removeprefix("/")
        else:
            return None
        return candidate if VIDEO_ID.fullmatch(candidate) else None
    except ValueError:
        return None


def _redact(text: str, key: str) -> str:
    return text.replace(key, "[redacted]").replace(json.dumps(key)[1:-1], "[redacted]")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("youtube_url", help="Public YouTube watch, shorts or youtu.be URL")
    parser.add_argument("--start", type=int, default=0, help="First whole second (default: 0)")
    parser.add_argument("--end", type=int, default=300, help="End whole second (default: 300)")
    args = parser.parse_args(argv)
    identifier = video_id(args.youtube_url)
    if identifier is None:
        print("Provide a YouTube watch, shorts or youtu.be URL with an 11-character video ID.")
        return 2
    if args.start < 0 or args.end <= args.start:
        print("Offsets must be nonnegative whole seconds, with end greater than start.")
        return 2

    try:
        settings = Settings()
        key = settings.video_reader_api_key.get_secret_value()
        if not all(
            value.strip()
            for value in (settings.video_reader_base_url, key, settings.video_reader_model)
        ):
            print(MISSING_CONFIG)
            return 2
        prefix = f"{identifier}_{args.start}-{args.end}"
        reply_path = OUTPUT_DIR / f"{prefix}.json"
        request_path = OUTPUT_DIR / f"{prefix}.request.json"
        if reply_path.exists() or request_path.exists():
            print("Capture already exists; choose another clip window to preserve the saved reply.")
            return 1
        body = {
            "model": settings.video_reader_model,
            "input": [
                {"type": "text", "text": PROMPT},
                {
                    "type": "video",
                    "uri": f"https://www.youtube.com/watch?v={identifier}",
                    "processing": {
                        "type": "static",
                        "start_offset": f"{args.start}s",
                        "end_offset": f"{args.end}s",
                    },
                },
            ],
        }
        request_json = json.dumps(body, indent=2, ensure_ascii=False) + "\n"
        if key in request_json:
            print("Capture refused: the request body contains the configured API key.")
            return 1
        started = time.monotonic()
        with httpx.Client(timeout=300, follow_redirects=False) as client:
            response = client.post(
                settings.video_reader_base_url.rstrip("/") + "/interactions",
                headers={"x-goog-api-key": key},
                json=body,
            )
        elapsed = time.monotonic() - started
        print(f"HTTP {response.status_code}; elapsed {elapsed:.2f}s")
        if response.status_code != 200:
            print(_redact(response.text, key))
            return 1
        reply = response.json()
        if key in response.text or key in json.dumps(reply, ensure_ascii=False):
            print("Capture refused: the response contains the configured API key.")
            return 1
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with reply_path.open("xb") as output:
            output.write(response.content)
        with request_path.open("x", encoding="utf-8") as output:
            output.write(request_json)
        print(f"Reply: {reply_path}")
        print(f"Request: {request_path}")
        return 0
    except Exception as exc:
        # HTTP and validation errors may embed credentials in their messages.
        print(f"Video capture failed ({type(exc).__name__}).")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

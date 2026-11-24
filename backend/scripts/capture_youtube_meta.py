"""Capture raw YouTube Data API metadata replies for fixture development."""

import argparse
import json
from pathlib import Path
from urllib.parse import unquote, unquote_plus

import httpx

from app.config import Settings
from app.extract.routing import route_url
from app.extract.youtube_links import redact_api_key_logs

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "youtube"
_API_URL = "https://www.googleapis.com/youtube/v3"


def _contains_key(response: httpx.Response, key: str) -> bool:
    decoded = json.dumps(response.json(), ensure_ascii=False)
    return any(
        key in text
        for text in (
            response.text,
            decoded,
            unquote(response.text),
            unquote(decoded),
            unquote_plus(response.text),
            unquote_plus(decoded),
        )
    )


def _comments_disabled(response: httpx.Response) -> bool:
    if response.status_code != 403:
        return False
    error = response.json().get("error", {})
    return isinstance(error, dict) and any(
        isinstance(item, dict) and item.get("reason") == "commentsDisabled"
        for item in error.get("errors", [])
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("youtube_url", help="Public YouTube watch, shorts or youtu.be URL")
    args = parser.parse_args(argv)
    try:
        route = route_url(args.youtube_url)
    except ValueError:
        route = None
    if route is None or route.kind != "video":
        print("Provide a YouTube watch, shorts or youtu.be URL with an 11-character video ID.")
        return 2
    identifier = route.fetch_urls[0].split("v=", 1)[1]

    try:
        videos_path = OUTPUT_DIR / f"{identifier}.videos.json"
        comments_path = OUTPUT_DIR / f"{identifier}.comments.json"
        if videos_path.exists() or comments_path.exists():
            print("Capture already exists; preserve the saved replies.")
            return 1
        key = Settings().youtube_api_key.get_secret_value()
        if not key.strip():
            print("Capturing YouTube metadata needs YOUTUBE_API_KEY in backend/.env.")
            return 2
        with redact_api_key_logs(key), httpx.Client(timeout=30, follow_redirects=False) as client:
            videos = client.get(
                f"{_API_URL}/videos", params={"part": "snippet", "id": identifier, "key": key}
            )
            if videos.status_code != 200:
                print(f"YouTube metadata capture failed (videos HTTP {videos.status_code}).")
                return 1
            if _contains_key(videos, key):
                print("Capture refused: a reply contains the configured API key.")
                return 1
            comments = client.get(
                f"{_API_URL}/commentThreads",
                params={
                    "part": "snippet",
                    "videoId": identifier,
                    "maxResults": 50,
                    "order": "relevance",
                    "key": key,
                },
            )
            if comments.status_code != 200 and not _comments_disabled(comments):
                print(f"YouTube metadata capture failed (comments HTTP {comments.status_code}).")
                return 1
            if _contains_key(comments, key):
                print("Capture refused: a reply contains the configured API key.")
                return 1
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with videos_path.open("xb") as output:
            output.write(videos.content)
        with comments_path.open("xb") as output:
            output.write(comments.content)
        print(f"Videos reply: {videos_path}")
        print(f"Comments reply: {comments_path}")
        return 0
    except Exception as exc:
        print(f"YouTube metadata capture failed ({type(exc).__name__}).")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

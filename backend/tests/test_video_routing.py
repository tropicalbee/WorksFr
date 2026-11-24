"""Video routing is offline and only accepts a single public video identifier."""

import pytest

from app.extract.routing import route_url

WATCH = "https://www.youtube.com/watch?v=CbpsDMwFG2g"


@pytest.mark.parametrize(
    "url",
    [
        WATCH,
        "https://youtube.com/watch?v=CbpsDMwFG2g&t=5&list=PL123",
        "https://youtu.be/CbpsDMwFG2g?si=tracking",
        "https://www.youtube.com/shorts/CbpsDMwFG2g",
        "http://m.youtube.com/shorts/CbpsDMwFG2g/",
        "  https://www.youtube.com/watch?v=CbpsDMwFG2g  ",
    ],
)
def test_video_routes_to_normalized_watch_url(url: str) -> None:
    route = route_url(url)
    assert route.kind == "video"
    assert route.fetch_urls == (WATCH,)


@pytest.mark.parametrize(
    "url",
    [
        "https://youtube.com/watch?v=short",
        "https://youtube.com/watch?v=CbpsDMwFG2gX",
        "https://youtube.com/watch?v=CbpsDMwFG2!",
        "https://youtu.be/CbpsDMwFG2g/extra",
        "https://youtube.com/shorts/CbpsDMwFG2g/extra",
        "https://youtube.com/playlist?list=PL123",
        "https://youtube.com/@tutorials",
        "https://youtube.com/channel/CbpsDMwFG2g",
        "https://youtube.com/watch?v=CbpsDMwFG2g&v=other_id123",
        "https://youtube.com/watch?v=CbpsDMwFG2g&v=",
        "https://youtube.com.evil.test/watch?v=CbpsDMwFG2g",
        "https://evil.test/youtu.be/CbpsDMwFG2g",
        "ftp://youtube.com/watch?v=CbpsDMwFG2g",
        "https://user@youtube.com/watch?v=CbpsDMwFG2g",
        "https://youtube.com:123/watch?v=CbpsDMwFG2g",
        "https://youtube.com:bad/watch?v=CbpsDMwFG2g",
    ],
)
def test_other_urls_keep_existing_fallback(url: str) -> None:
    assert route_url(url).kind == "html"

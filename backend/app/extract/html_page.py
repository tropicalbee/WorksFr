"""Code blocks and metadata from an HTML page (also used for Stack Exchange answer bodies)."""

import re
from datetime import date

import trafilatura
from bs4 import BeautifulSoup, NavigableString, Tag

from app.extract.blocks import RawBlock

_PROSE_TAGS = ["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"]
_CHROME_TAGS = {"nav", "footer", "aside", "form", "noscript", "template", "script", "style"}
_CHROME_TOKENS = {
    "sidebar", "comments", "comment-list", "comment-body", "site-footer", "footer", "navbar",
    "related", "related-posts", "advertisement", "cookie-banner",
}  # fmt: skip
_HINT_CLASS = re.compile(
    r"(?:language|lang|highlight-source|highlight-text|highlight|brush:?|syntax)[-_:]?([\w+#-]+)",
    re.I,
)
_MAX_CONTEXT_CHARS = 600


def _tokens(tag: Tag) -> list[str]:
    classes = tag.get("class") or []
    if isinstance(classes, str):
        classes = classes.split()
    ident = tag.get("id")
    return [*classes, *([ident] if isinstance(ident, str) else [])]


def _in_chrome(tag: Tag) -> bool:
    for parent in tag.parents:
        if parent.name in _CHROME_TAGS:
            return True
        if any(token.lower() in _CHROME_TOKENS for token in _tokens(parent)):
            return True
    return False


def _hint(pre: Tag) -> str | None:
    """Language named by the classes on the block, its code child or its wrapper."""
    candidates = [pre, *pre.find_all("code", limit=1), *list(pre.parents)[:2]]
    for tag in candidates:
        for token in _tokens(tag):
            match = _HINT_CLASS.fullmatch(token)
            if match:
                return match.group(1).lower()
    return None


def _text_of(node: Tag, *, line_mode: bool) -> tuple[str, str]:
    """(full text, text without `<em>` output spans) of a code element."""
    full: list[str] = []
    bare: list[str] = []

    def walk(current: Tag, in_output: bool) -> None:
        for child in current.children:
            if isinstance(child, NavigableString):
                full.append(str(child))
                if not in_output:
                    bare.append(str(child))
            elif isinstance(child, Tag):
                if child.name == "br":
                    full.append("\n")
                    bare.append("\n")
                    continue
                walk(child, in_output or child.name == "em")
                if line_mode and "line" in _tokens(child):
                    full.append("\n")
                    bare.append("\n")

    walk(node, False)
    return "".join(full), "".join(bare)


def _pre_block(pre: Tag) -> RawBlock:
    lines = [t for t in pre.find_all(True) if "line" in _tokens(t)]
    line_mode = len(lines) >= 2 and "\n" not in pre.get_text()
    full, bare = _text_of(pre, line_mode=line_mode)
    has_output = bool(pre.find("em"))
    return RawBlock(text=full, hint=_hint(pre), without_output=bare if has_output else None)


def _blob_table_block(table: Tag) -> RawBlock:
    """GitHub's file view: one `td.blob-code-inner` per source line."""
    lines = [cell.get_text() for cell in table.select("td.blob-code-inner")]
    return RawBlock(text="\n".join(lines))


def _prose_text(tag: Tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()


def parse_html_blocks(content: bytes | str) -> list[RawBlock]:
    """Code blocks in page order, with the prose just before each as context.

    Handles `<pre>`, `<pre><code>`, highlighter wrappers, one-span-per-line layouts, and
    GitHub file tables. Navigation, footers, sidebars and comment sections are skipped.
    """
    soup = BeautifulSoup(content, "lxml")
    blocks: list[RawBlock] = []
    recent: list[str] = []
    wanted = [*_PROSE_TAGS, "pre", "table"]
    for tag in soup.find_all(wanted):
        if _in_chrome(tag):
            continue
        if tag.name == "pre":
            if tag.find_parent("pre") is not None:
                continue
            block = _pre_block(tag)
        elif tag.name == "table":
            if not tag.select_one("td.blob-code-inner") or tag.find_parent("table"):
                continue
            block = _blob_table_block(tag)
        else:
            if tag.find_parent("pre") is not None or (tag.name == "li" and tag.find("p")):
                continue
            text = _prose_text(tag)
            if text and (not recent or recent[-1] != text):
                recent.append(text)
            continue
        context = "\n\n".join(recent[-2:])
        block.context = context[-_MAX_CONTEXT_CHARS:]
        blocks.append(block)
        recent = []
    return blocks


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    return re.sub(r"\s+", " ", text).strip() or None


def page_metadata(content: bytes | str, url: str | None = None) -> tuple[str | None, date | None]:
    """Page title and the publish date the page states, if any.

    The title is the `<title>` text, as shown in a browser tab; trafilatura's cleaned title
    and `og:title` are fallbacks. The date comes from trafilatura (JSON-LD, meta tags).
    """
    soup = BeautifulSoup(content, "lxml")
    title = _clean(soup.title.get_text() if soup.title else None)
    published: date | None = None
    meta = None
    try:
        meta = trafilatura.extract_metadata(content, default_url=url)
    except Exception:  # noqa: BLE001 - trafilatura can fail on malformed markup; metadata is optional
        meta = None
    if meta is not None:
        title = title or _clean(meta.title)
        if meta.date:
            try:
                published = date.fromisoformat(meta.date[:10])
            except ValueError:
                published = None
    if title is None:
        og = soup.find("meta", attrs={"property": "og:title"})
        if isinstance(og, Tag):
            content_attr = og.get("content")
            title = _clean(content_attr if isinstance(content_attr, str) else None)
    return title, published

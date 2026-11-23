"""Fenced code blocks and title from a Markdown file (GitHub READMEs)."""

import re

from app.extract.blocks import RawBlock

_FENCE_OPEN = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*(?P<info>[^`]*?)[ \t]*$")
_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MAX_CONTEXT_CHARS = 600


def _clean_heading(text: str) -> str | None:
    text = _LINK.sub(r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    # Drop leading emoji and symbols; a README title starts at its first letter or digit.
    match = re.search(r"[^\W_]", text)
    return text[match.start() :].strip() or None if match else None


def parse_markdown(text: str) -> tuple[str | None, list[RawBlock]]:
    """(title, fenced blocks). The title is the first heading outside any fence."""
    title: str | None = None
    blocks: list[RawBlock] = []
    prose: list[str] = []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        opening = _FENCE_OPEN.match(line)
        if opening:
            fence = opening.group("fence")
            indent = len(opening.group("indent").expandtabs(4))
            info = opening.group("info").split()
            hint = info[0].lower().strip("{}.") if info else None
            body: list[str] = []
            i += 1
            while i < len(lines):
                closing = lines[i].strip()
                if closing.startswith(fence[0] * len(fence)) and set(closing) == {fence[0]}:
                    break
                body.append(lines[i][indent:] if lines[i][:indent].strip() == "" else lines[i])
                i += 1
            context = "\n".join(prose[-3:])
            blocks.append(
                RawBlock(text="\n".join(body), hint=hint, context=context[-_MAX_CONTEXT_CHARS:])
            )
            prose = []
            i += 1
            continue
        heading = _ATX_HEADING.match(line)
        if heading and title is None:
            title = _clean_heading(heading.group(1))
        stripped = line.strip()
        if stripped and not stripped.startswith(("![", "<")):
            prose.append(_LINK.sub(r"\1", stripped))
        i += 1
    return title, blocks

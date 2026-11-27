"""Extraction from a Jupyter notebook (.ipynb). The notebook is read as data, never run."""

import re

import nbformat

from app.extract.blocks import LANGUAGE_KINDS, classify_notebook_cell
from app.extract.errors import ExtractionError
from app.extract.libs import library_fields
from app.models import Block, Extraction, SourceFormat, SourceKind, StepKind

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.M)
_MAX_CONTEXT_CHARS = 1000


def _cell_source(cell: nbformat.NotebookNode) -> str:
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, list) else str(source)


def _shown_output(cell: nbformat.NotebookNode) -> str | None:
    pieces: list[str] = []
    outputs = cell.get("outputs")
    if not isinstance(outputs, list):
        return None
    for output in outputs:
        if not isinstance(output, dict):
            continue
        kind = output.get("output_type")
        if kind == "error":
            return None
        if kind == "stream" and output.get("name") == "stdout":
            text = output.get("text")
        elif kind == "execute_result" and isinstance(data := output.get("data"), dict):
            text = data.get("text/plain")
        else:
            continue
        if isinstance(text, list) and all(isinstance(line, str) for line in text):
            text = "".join(text)
        if isinstance(text, str):
            pieces.append(text)
    return "".join(pieces) or None


def _title(nb: nbformat.NotebookNode) -> str | None:
    meta_title = nb.get("metadata", {}).get("title")
    if isinstance(meta_title, str) and meta_title.strip():
        return meta_title.strip()
    for cell in nb.cells:
        if cell.get("cell_type") == "markdown":
            match = _HEADING.search(_cell_source(cell))
            if match:
                return re.sub(r"[*_`]", "", match.group(1)).strip() or None
    return None


def _kernel_kind(nb: nbformat.NotebookNode) -> StepKind:
    """Language of the code cells: the kernelspec, else language_info. Unknown means Python."""
    metadata = nb.get("metadata", {})
    candidates = (
        (metadata.get("kernelspec") or {}).get("language"),
        (metadata.get("language_info") or {}).get("name"),
    )
    for name in candidates:
        if isinstance(name, str) and name.strip().lower() in LANGUAGE_KINDS:
            return LANGUAGE_KINDS[name.strip().lower()]
    return StepKind.PYTHON


def parse_notebook(source: str, data: bytes, *, source_kind: SourceKind) -> Extraction:
    """Code cells in order, empty ones dropped, with the Markdown cells before each as context."""
    try:
        nb = nbformat.reads(data.decode("utf-8-sig"), as_version=4)
    except (UnicodeDecodeError, ValueError, nbformat.reader.NotJSONError, KeyError) as exc:
        raise ExtractionError(f"Could not read {source} as a Jupyter notebook: {exc}") from exc

    language = _kernel_kind(nb)
    blocks: list[Block] = []
    markdown: list[str] = []
    for cell in nb.cells:
        cell_type = cell.get("cell_type")
        if cell_type == "markdown":
            text = _cell_source(cell).strip()
            if text:
                markdown.append(text)
        elif cell_type == "code":
            block = classify_notebook_cell(_cell_source(cell), language)
            if block is None:
                continue
            block.idx = len(blocks)
            block.context = "\n\n".join(markdown)[-_MAX_CONTEXT_CHARS:]
            block.shown_output = _shown_output(cell)
            blocks.append(block)
            markdown = []

    return Extraction(
        source_kind=source_kind,
        source_format=SourceFormat.NOTEBOOK,
        source=source,
        title=_title(nb),
        blocks=blocks,
        **library_fields(blocks),
    )

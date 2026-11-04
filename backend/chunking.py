"""Parsing jerárquico y división parent/child sin dependencias de red."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from .config import Config
from .models import Child, Parent, Section


def count_tokens(text: str, ratio: float = 1.3) -> int:
    """Estima tokens con un ratio configurable calibrado contra el modelo."""
    return int(len(re.findall(r"\w+|[^\w\s]", text)) * ratio)


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(parts)))


MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
CHAPTER = re.compile(
    r"^(?:cap[ií]tulo|chapter|secci[oó]n|section)\s+[\dIVXLC]+\b[\s\.:\-–]*(.*)$",
    re.I,
)
NUMBERED = re.compile(r"^(\d+(?:\.\d+){0,3})[\.\)]?\s+(\S.{0,90})$")


def _plain_heading(lines: list[str], index: int) -> tuple[int, str] | None:
    line = lines[index].strip()
    if not line or len(line) > 100 or line.endswith((".", ",", ";", ":")):
        return None
    if index > 0 and lines[index - 1].strip():
        return None
    if CHAPTER.match(line):
        level = 1 if line.lower().startswith(("cap", "chapter")) else 2
        return level, line
    match = NUMBERED.match(line)
    return (match.group(1).count(".") + 1, line) if match else None


def parse_sections(text: str) -> list[Section]:
    """Parsea títulos Markdown o texto plano y conserva su jerarquía."""
    lines = text.replace("\r\n", "\n").splitlines()
    has_markdown_headings = any(MD_HEADING.match(line) for line in lines)
    stack: list[tuple[int, str]] = []
    sections: list[Section] = []
    buffer: list[str] = []
    current_path: list[str] = []
    in_fence = False

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append(Section(path=list(current_path), body=body))
        buffer.clear()

    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        heading = None
        if not in_fence:
            if has_markdown_headings:
                match = MD_HEADING.match(line)
                if match:
                    heading = (len(match.group(1)), match.group(2).strip())
            else:
                heading = _plain_heading(lines, index)
        if heading:
            flush()
            level, title = heading
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            current_path = [title for _, title in stack]
        else:
            buffer.append(line)
    flush()
    return sections


def _with_title(section: Section) -> str:
    return f"{section.path[-1]}\n{section.body}" if section.path else section.body


def merge_small(
    sections: list[Section], minimum_tokens: int, token_ratio: float = 1.3
) -> list[Section]:
    """Fusiona secciones pequeñas con una sección hermana compatible."""
    result: list[Section] = []
    pending: Section | None = None
    for section in sections:
        if pending is not None:
            if pending.path[:-1] == section.path[:-1]:
                section = Section(
                    section.path, f"{_with_title(pending)}\n\n{section.body}"
                )
            else:
                result.append(pending)
            pending = None
        if count_tokens(section.body, token_ratio) < minimum_tokens:
            pending = section
        else:
            result.append(section)
    if pending is not None:
        if result and result[-1].path[:-1] == pending.path[:-1]:
            previous = result[-1]
            result[-1] = Section(
                previous.path, f"{previous.body}\n\n{_with_title(pending)}"
            )
        else:
            result.append(pending)
    return result


def _paragraphs(text: str) -> list[str]:
    return [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    ]


def split_parent_text(
    text: str, max_tokens: int, token_ratio: float = 1.3
) -> list[str]:
    """Divide un parent grande por párrafos sin superar el límite configurado."""
    if count_tokens(text, token_ratio) <= max_tokens:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for paragraph in _paragraphs(text):
        paragraph_tokens = count_tokens(paragraph, token_ratio)
        if current and current_tokens + paragraph_tokens > max_tokens:
            parts.append("\n\n".join(current))
            current, current_tokens = [], 0
        current.append(paragraph)
        current_tokens += paragraph_tokens
    if current:
        parts.append("\n\n".join(current))
    return parts


def _split_words(
    sentence: str, target: int, overlap_ratio: float, token_ratio: float = 1.3
) -> list[str]:
    words = sentence.split()
    if not words:
        return []
    tokens_per_word = max(count_tokens(sentence, token_ratio) / len(words), 1.0)
    size = max(1, int(target / tokens_per_word))
    overlap = int(size * overlap_ratio)
    step = max(1, size - overlap)
    return [
        " ".join(words[start : start + size])
        for start in range(0, len(words), step)
        if start < len(words)
    ]


def _split_long_paragraph(
    paragraph: str,
    target: int,
    overlap_ratio: float,
    token_ratio: float = 1.3,
) -> list[str]:
    sentences = re.split(r"(?<=[.!?…])\s+", paragraph)
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for sentence in sentences:
        sentence_tokens = count_tokens(sentence, token_ratio)
        if sentence_tokens > target:
            if current:
                pieces.append(" ".join(current))
                current, current_tokens = [], 0
            pieces.extend(_split_words(sentence, target, overlap_ratio, token_ratio))
        elif current and current_tokens + sentence_tokens > target:
            pieces.append(" ".join(current))
            current, current_tokens = [], 0
            current.append(sentence)
            current_tokens = sentence_tokens
        else:
            current.append(sentence)
            current_tokens += sentence_tokens
    if current:
        pieces.append(" ".join(current))
    return pieces


def split_children(
    text: str,
    target: int,
    overlap_ratio: float,
    token_ratio: float = 1.3,
) -> list[str]:
    """Crea children por párrafos y frases, usando overlap solo al cortar palabras."""
    units: list[str] = []
    for paragraph in _paragraphs(text):
        if count_tokens(paragraph, token_ratio) <= target:
            units.append(paragraph)
        else:
            units.extend(
                _split_long_paragraph(paragraph, target, overlap_ratio, token_ratio)
            )
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for unit in units:
        unit_tokens = count_tokens(unit, token_ratio)
        if current and current_tokens + unit_tokens > target:
            chunks.append("\n\n".join(current))
            current, current_tokens = [], 0
        current.append(unit)
        current_tokens += unit_tokens
    if current:
        chunks.append("\n\n".join(current))
    if len(chunks) > 1 and count_tokens(chunks[-1], token_ratio) < target * 0.25:
        if (
            count_tokens(chunks[-2], token_ratio)
            + count_tokens(chunks[-1], token_ratio)
            <= target * 1.3
        ):
            chunks[-2] = f"{chunks[-2]}\n\n{chunks.pop()}"
    return chunks


def chunk_text(
    text: str, doc_id: str, config: Config
) -> tuple[list[Parent], list[Child]]:
    """Transforma un documento en parents recuperables y children indexables.

    Los children reciben la ruta del documento en `embed_text` para que el
    embedding incluya contexto semántico sin alterar el texto que verá el LLM.
    """
    title = Path(doc_id).stem
    sections = merge_small(
        parse_sections(text),
        config.min_section_tokens,
        config.token_estimate_ratio,
    )
    parents: list[Parent] = []
    children: list[Child] = []
    parent_index = 0
    for section in sections:
        for part in split_parent_text(
            section.body,
            config.parent_max_tokens,
            config.token_estimate_ratio,
        ):
            parent = Parent(
                id=_uid(doc_id, "parent", str(parent_index)),
                doc_id=doc_id,
                index=parent_index,
                path=section.path,
                text=part,
            )

            parents.append(parent)
            breadcrumb = " > ".join([title, *section.path])

            # Se indexan children pequeños, pero se recupera el parent completo
            # para evitar entregar al modelo fragmentos sin contexto suficiente.
            for child_index, child_text in enumerate(
                split_children(
                    part,
                    config.child_target_tokens,
                    config.overlap_ratio,
                    config.token_estimate_ratio,
                )
            ):
                children.append(
                    Child(
                        id=_uid(doc_id, "child", str(parent_index), str(child_index)),
                        parent_id=parent.id,
                        doc_id=doc_id,
                        index=child_index,
                        path=section.path,
                        text=child_text,
                        embed_text="\n".join(
                            part for part in (breadcrumb, child_text) if part
                        ),
                    )
                )
            parent_index += 1
    return parents, children

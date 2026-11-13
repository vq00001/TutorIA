"""
PDF (solo texto) -> Markdown limpio para RAG / LLM.

Uso:
    python pdf_to_md.py documento.pdf
    python pdf_to_md.py documento.pdf -o salida.md
"""
import re
from argparse import ArgumentParser
from pathlib import Path

import ftfy
import pymupdf
import pymupdf4llm

# Líneas que inician una estructura Markdown (no se deben unir con la anterior)
STRUCTURAL = re.compile(r"^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>|```|---|===)")

# Caracteres invisibles / de control / basura típica de PDFs
JUNK_CHARS = re.compile(
    "[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f"        # control (salvo \t \n \r)
    "\u00ad"                                               # guion blando
    "\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060\ufeff"   # ancho cero, bidi, BOM
    "\ufffd"                                               # carácter de reemplazo
    "\ue000-\uf8ff]"                                       # área de uso privado
)

class NotConvertible(Exception):
    """El archivo no se puede convertir; el mensaje es apto para mostrar al usuario."""


def open_pdf(data: bytes) -> pymupdf.Document:
    if b"%PDF-" not in data[:1024]:
        raise NotConvertible("El archivo no es un PDF válido.")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise NotConvertible(f"PDF corrupto o ilegible: {e}")
    if doc.needs_pass:
        doc.close()
        raise NotConvertible("El PDF está protegido con contraseña.")
    if doc.page_count == 0:
        doc.close()
        raise NotConvertible("El PDF no tiene páginas.")
    return doc


def check_text(doc: pymupdf.Document, min_chars: int = 30, min_ratio: float = 0.5) -> None:
    ok = sum(len(p.get_text().strip()) >= min_chars for p in doc)
    if ok / doc.page_count < min_ratio:
        raise NotConvertible("El PDF no tiene texto extraíble (posible escaneo).")


def pdf_bytes_to_markdown(data: bytes) -> str:
    with open_pdf(data) as doc:
        check_text(doc)
        md = pymupdf4llm.to_markdown(doc, header=False, footer=False)
        if not md.strip():
            md = "\n\n".join(page.get_text("text") for page in doc)
    return clean_text(md)


def merge_paragraph_lines(text: str) -> str:
    """Une líneas partidas dentro de un párrafo, respetando la estructura Markdown."""
    out, in_code = [], False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if in_code or not line.strip() or STRUCTURAL.match(line):
            out.append(line)
            continue
        prev = out[-1] if out else ""
        if prev.strip() and not STRUCTURAL.match(prev):
            out[-1] = prev.rstrip() + " " + line.lstrip()
        else:
            out.append(line)
    return "\n".join(out)


def clean_text(text: str) -> str:
    # 1. Codificación: mojibake, ligaduras (ﬁ -> fi), comillas raras, etc.
    text = ftfy.fix_text(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 2. Quitar caracteres invisibles / de control
    text = JUNK_CHARS.sub("", text)

    # 3. Palabras cortadas con guion al final de línea ("procesa-\nmiento")
    #    Solo si la continuación empieza en minúscula (evita romper "Norte-\nSur")
    text = re.sub(r"(\w)-[ \t]*\n[ \t]*([a-záéíóúüñ])", r"\1\2", text)

    # 4. Unir líneas partidas dentro de un mismo párrafo
    text = merge_paragraph_lines(text)

    # 5. Espacios: colapsar horizontales y quitar los del final de línea
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" +\n", "\n", text)

    # 6. Máximo una línea en blanco entre bloques
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"





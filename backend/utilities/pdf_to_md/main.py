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


def has_extractable_text(pdf_path: Path, min_chars: int = 50) -> bool:
    with pymupdf.open(pdf_path) as doc:
        sum_chars = 0
        for page in doc:
            sum_chars += len(page.get_text("text").strip())
            if sum_chars >= min_chars:
                return True
    return False


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


def pdf_to_markdown(pdf_path: Path) -> str:
    if not has_extractable_text(pdf_path):
        raise ValueError(
            f"'{pdf_path}' no contiene texto extraíble (posible PDF escaneado)."
        )

    # header/footer=False: la librería omite encabezados y pies de página
    markdown = pymupdf4llm.to_markdown(str(pdf_path), header=False, footer=False)
    return clean_text(markdown)


if __name__ == "__main__":
    parser = ArgumentParser(description="Convierte un PDF con texto a Markdown limpio.")
    parser.add_argument("pdf_path", type=Path, help="Archivo PDF de entrada")
    parser.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Archivo de salida (por defecto: <entrada>.md)"
    )
    args = parser.parse_args()

    output = args.output or args.pdf_path.with_name(args.pdf_path.stem + ".md")
    try:
        output.write_text(pdf_to_markdown(args.pdf_path), encoding="utf-8")
    except ValueError as e:
        print(f"Archivo rechazado: {e}")
        raise SystemExit(1)

    print(f"Listo: {output}")
import pymupdf
import pytest

from backend.utilities.pdf_to_md.pdf_to_md import NotConvertible, validate_pdf_bytes


def _pdf_bytes(*, with_table=False, with_image=False):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Este documento contiene suficiente texto extraible para validar el PDF.",
    )
    if with_table:
        for x in (72, 220, 360):
            page.draw_line((x, 200), (x, 280))
        for y in (200, 240, 280):
            page.draw_line((72, y), (360, y))
        page.insert_text((90, 225), "Columna A")
        page.insert_text((240, 225), "Columna B")
        page.insert_text((90, 265), "Uno")
        page.insert_text((240, 265), "Dos")
    if with_image:
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 5, 5), 0)
        pixmap.clear_with(255)
        page.insert_image(pymupdf.Rect(72, 300, 120, 348), pixmap=pixmap)
    data = doc.tobytes()
    doc.close()
    return data


def test_validate_pdf_bytes_accepts_text_only_pdf():
    validate_pdf_bytes(_pdf_bytes())


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"with_table": True}, "tablas"),
        ({"with_image": True}, "imágenes"),
    ],
)
def test_validate_pdf_bytes_rejects_tables_and_images(options, message):
    with pytest.raises(NotConvertible, match=message):
        validate_pdf_bytes(_pdf_bytes(**options))

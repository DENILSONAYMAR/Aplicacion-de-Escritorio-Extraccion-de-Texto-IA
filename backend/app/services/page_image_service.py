import os
import shutil
from typing import Optional

import fitz
from PIL import Image, ImageOps


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp")


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def render_pdf_pages_to_images(
    file_path: str,
    output_dir: str,
    max_pages: Optional[int] = None,
    zoom: float = 2.0,
) -> list[dict]:
    """
    Convierte cada página de un PDF en una imagen PNG.
    Devuelve una lista con page_number e image_path.
    """

    ensure_dir(output_dir)

    pages = []

    with fitz.open(file_path) as doc:
        total_pages = len(doc)

        if max_pages is not None:
            total_pages = min(total_pages, max_pages)

        for index in range(total_pages):
            page = doc[index]

            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)

            page_number = index + 1
            image_path = os.path.join(
                output_dir,
                f"page_{page_number:03d}.png"
            )

            pix.save(image_path)

            pages.append(
                {
                    "page_number": page_number,
                    "image_path": image_path,
                    "mime_type": "image/png",
                }
            )

    return pages


def normalize_image_to_png(
    file_path: str,
    output_dir: str,
) -> list[dict]:
    """
    Convierte una imagen subida a PNG normalizado.
    Para imágenes simples se trata como página 1.
    """

    ensure_dir(output_dir)

    image = Image.open(file_path)
    image = ImageOps.exif_transpose(image)

    output_path = os.path.join(output_dir, "page_001.png")
    image.save(output_path, format="PNG")

    return [
        {
            "page_number": 1,
            "image_path": output_path,
            "mime_type": "image/png",
        }
    ]


def prepare_document_images(
    file_path: str,
    document_id: str,
    base_output_dir: str | None = None,
    max_pages: Optional[int] = None,
) -> list[dict]:
    """
    Prepara imágenes por página para análisis multimodal.
    - PDF: renderiza cada página.
    - Imagen: la normaliza como página 1.
    """

    if base_output_dir is None:
        base_output_dir = os.path.join(os.getenv("STORAGE_ROOT", "/storage"), "page_images")

    document_output_dir = os.path.join(base_output_dir, document_id)
    ensure_dir(document_output_dir)

    lower_path = file_path.lower()

    if lower_path.endswith(".pdf"):
        return render_pdf_pages_to_images(
            file_path=file_path,
            output_dir=document_output_dir,
            max_pages=max_pages,
        )

    if lower_path.endswith(IMAGE_EXTENSIONS):
        return normalize_image_to_png(
            file_path=file_path,
            output_dir=document_output_dir,
        )

    raise ValueError("Tipo de archivo no compatible para visión multimodal")

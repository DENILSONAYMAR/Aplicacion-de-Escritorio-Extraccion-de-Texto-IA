import fitz
import pytesseract
from PIL import Image
import io


def split_text_into_chunks(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = end - overlap

    return chunks


def extract_pages_from_pdf(file_path: str) -> list[dict]:
    pages = []

    with fitz.open(file_path) as doc:
        for index, page in enumerate(doc, start=1):
            page_text = page.get_text().strip()

            if not page_text:
                pix = page.get_pixmap()
                img_bytes = pix.tobytes("png")
                image = Image.open(io.BytesIO(img_bytes))
                page_text = pytesseract.image_to_string(
                    image, lang="spa+eng").strip()

            pages.append({
                "page_number": index,
                "page_text": page_text
            })

    return pages


def extract_text_from_pdf(file_path: str) -> str:
    pages = extract_pages_from_pdf(file_path)
    return "\n\n".join([p["page_text"] for p in pages if p["page_text"]]).strip()

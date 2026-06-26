import os
import uuid
import shutil
import hashlib
import mimetypes
from pathlib import Path

import fitz
from PIL import Image
import pytesseract
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app import models

router = APIRouter(tags=["documentos"])

STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", "/storage"))
UPLOAD_DIR = STORAGE_ROOT / "uploads"
PAGE_IMAGE_DIR = STORAGE_ROOT / "page_images"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
PAGE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def _set_if_exists(instance, field_name: str, value):
    if hasattr(instance, field_name):
        setattr(instance, field_name, value)


def _get_if_exists(instance, field_name: str, default=None):
    if hasattr(instance, field_name):
        return getattr(instance, field_name)

    return default


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_filename(filename: str) -> str:
    filename = filename or "archivo"

    invalid = ['\\', '/', ':', '*', '?', '"', '<', '>', '|']

    for char in invalid:
        filename = filename.replace(char, "_")

    return filename.strip() or "archivo"


def _delete_file_if_exists(path: str | None):
    if not path:
        return

    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _delete_document_related_data(db: Session, document_id: str):
    """
    Borra todo lo relacionado a un documento.
    Conserva proyectos/plantillas permanentes.
    """

    if hasattr(models, "ExportFile"):
        db.query(models.ExportFile).filter(
            models.ExportFile.document_id == document_id
        ).delete(synchronize_session=False)

    if hasattr(models, "ExtractionEvidence") and hasattr(models, "ExtractionResult"):
        result_ids = [
            row.id
            for row in db.query(models.ExtractionResult.id)
            .filter(models.ExtractionResult.document_id == document_id)
            .all()
        ]

        if result_ids:
            db.query(models.ExtractionEvidence).filter(
                models.ExtractionEvidence.extraction_result_id.in_(result_ids)
            ).delete(synchronize_session=False)

            if hasattr(models, "HumanReview"):
                db.query(models.HumanReview).filter(
                    models.HumanReview.extraction_result_id.in_(result_ids)
                ).delete(synchronize_session=False)

    if hasattr(models, "ExtractionResult"):
        db.query(models.ExtractionResult).filter(
            models.ExtractionResult.document_id == document_id
        ).delete(synchronize_session=False)

    if hasattr(models, "DocumentChunk"):
        db.query(models.DocumentChunk).filter(
            models.DocumentChunk.document_id == document_id
        ).delete(synchronize_session=False)

    if hasattr(models, "DocumentPage"):
        db.query(models.DocumentPage).filter(
            models.DocumentPage.document_id == document_id
        ).delete(synchronize_session=False)

    if hasattr(models, "DocumentContent"):
        db.query(models.DocumentContent).filter(
            models.DocumentContent.document_id == document_id
        ).delete(synchronize_session=False)

    if hasattr(models, "AnalysisHistory"):
        db.query(models.AnalysisHistory).filter(
            models.AnalysisHistory.document_id == document_id
        ).delete(synchronize_session=False)


def _delete_existing_documents_with_same_hash(db: Session, file_hash: str):
    """
    Si el mismo archivo ya existe, no bloquea la subida.
    Elimina la versión anterior y permite crear una nueva.
    """

    existing_documents = (
        db.query(models.Document)
        .filter(models.Document.file_hash == file_hash)
        .all()
    )

    for document in existing_documents:
        document_id = str(document.id)
        original_path = _get_if_exists(document, "original_path")

        _delete_document_related_data(db, document_id)
        _delete_file_if_exists(original_path)

        db.delete(document)

    db.commit()


def _save_file_to_storage(filename: str, content: bytes) -> str:
    safe_name = _safe_filename(filename)
    unique_name = f"{uuid.uuid4()}_{safe_name}"
    file_path = UPLOAD_DIR / unique_name

    with open(file_path, "wb") as f:
        f.write(content)

    return str(file_path)


def _create_document_record(
    db: Session,
    document_id: str,
    filename: str,
    file_path: str,
    file_hash: str,
    mime_type: str | None,
):
    document = models.Document()

    _set_if_exists(document, "id", document_id)
    _set_if_exists(document, "file_name", filename)
    _set_if_exists(document, "original_path", file_path)
    _set_if_exists(document, "mime_type", mime_type)
    _set_if_exists(document, "file_hash", file_hash)
    _set_if_exists(document, "status", "processed")

    db.add(document)
    db.commit()
    db.refresh(document)

    return document


def _create_document_content(
    db: Session,
    document_id: str,
    text: str,
    extraction_method: str,
):
    if not hasattr(models, "DocumentContent"):
        return

    content = models.DocumentContent()

    _set_if_exists(content, "document_id", document_id)

    if hasattr(content, "content"):
        _set_if_exists(content, "content", text)
    elif hasattr(content, "text"):
        _set_if_exists(content, "text", text)
    elif hasattr(content, "raw_text"):
        _set_if_exists(content, "raw_text", text)

    _set_if_exists(content, "extraction_method", extraction_method)

    db.add(content)


def _create_document_page(
    db: Session,
    document_id: str,
    page_number: int,
    page_text: str,
):
    if not hasattr(models, "DocumentPage"):
        return

    page = models.DocumentPage()

    _set_if_exists(page, "document_id", document_id)
    _set_if_exists(page, "page_number", page_number)

    if hasattr(page, "page_text"):
        _set_if_exists(page, "page_text", page_text)
    elif hasattr(page, "text"):
        _set_if_exists(page, "text", page_text)
    elif hasattr(page, "content"):
        _set_if_exists(page, "content", page_text)

    db.add(page)


def _extract_pdf_text_by_pages(file_path: str) -> tuple[str, list[dict]]:
    full_text_parts = []
    pages = []

    pdf = fitz.open(file_path)

    for index, page in enumerate(pdf, start=1):
        text = page.get_text("text") or ""
        text = text.strip()

        if not text:
            try:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                image_path = PAGE_IMAGE_DIR / \
                    f"{uuid.uuid4()}_page_{index}.png"
                pix.save(str(image_path))

                image = Image.open(image_path)
                text = pytesseract.image_to_string(
                    image, lang="spa+eng").strip()
            except Exception:
                text = ""

        pages.append(
            {
                "page_number": index,
                "page_text": text,
            }
        )

        if text:
            full_text_parts.append(f"[Página {index}]\n{text}")

    pdf.close()

    return "\n\n".join(full_text_parts).strip(), pages


def _extract_image_text(file_path: str) -> tuple[str, list[dict]]:
    try:
        image = Image.open(file_path)
        text = pytesseract.image_to_string(image, lang="spa+eng").strip()
    except Exception:
        text = ""

    return text, [
        {
            "page_number": 1,
            "page_text": text,
        }
    ]


@router.get("/")
def list_documents(
    db: Session = Depends(get_db),
):
    query = db.query(models.Document)

    if hasattr(models.Document, "created_at"):
        query = query.order_by(models.Document.created_at.desc())
    else:
        query = query.order_by(models.Document.id.desc())

    return query.all()


@router.get("/{document_id}/preview")
def preview_document(
    document_id: str,
    db: Session = Depends(get_db),
):
    document = (
        db.query(models.Document)
        .filter(models.Document.id == document_id)
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    file_path = _get_if_exists(document, "original_path")

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Archivo fisico no encontrado")

    filename = _get_if_exists(document, "file_name", "documento")
    media_type = _get_if_exists(document, "mime_type") or mimetypes.guess_type(filename)[0]

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type=media_type or "application/octet-stream",
    )


def _preview_page_dir(document_id: str) -> Path:
    return PAGE_IMAGE_DIR / "preview" / document_id


def _render_preview_pages(file_path: str, document_id: str) -> list[Path]:
    output_dir = _preview_page_dir(document_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    existing_pages = sorted(output_dir.glob("page_*.png"))
    if existing_pages:
        return existing_pages

    pages: list[Path] = []

    with fitz.open(file_path) as pdf:
        for index, page in enumerate(pdf, start=1):
            image_path = output_dir / f"page_{index:03d}.png"
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pix.save(str(image_path))
            pages.append(image_path)

    return pages


@router.get("/{document_id}/preview-pages")
def preview_document_pages(
    document_id: str,
    db: Session = Depends(get_db),
):
    document = (
        db.query(models.Document)
        .filter(models.Document.id == document_id)
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    file_path = _get_if_exists(document, "original_path")

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Archivo fisico no encontrado")

    filename = _get_if_exists(document, "file_name", "documento")

    if not filename.lower().endswith(".pdf"):
        return {
            "pages": [
                {
                    "page_number": 1,
                    "url": f"/documentos/{document_id}/preview",
                }
            ]
        }

    pages = _render_preview_pages(file_path, document_id)

    return {
        "pages": [
            {
                "page_number": index + 1,
                "url": f"/documentos/{document_id}/preview-pages/{index + 1}",
            }
            for index, _ in enumerate(pages)
        ]
    }


@router.get("/{document_id}/preview-pages/{page_number}")
def preview_document_page_image(
    document_id: str,
    page_number: int,
    db: Session = Depends(get_db),
):
    document = (
        db.query(models.Document)
        .filter(models.Document.id == document_id)
        .first()
    )

    if not document:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    file_path = _get_if_exists(document, "original_path")

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Archivo físico no encontrado")

    filename = _get_if_exists(document, "file_name", "documento")

    if not filename.lower().endswith(".pdf"):
        return FileResponse(path=file_path)

    pages = _render_preview_pages(file_path, document_id)

    if page_number < 1 or page_number > len(pages):
        raise HTTPException(status_code=404, detail="Pagina no encontrada")

    return FileResponse(
        path=str(pages[page_number - 1]),
        media_type="image/png",
    )


@router.post("/subir")
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Subida temporal.

    Si el mismo archivo ya existe:
    - elimina la versión anterior;
    - crea una nueva entrada;
    - permite trabajar nuevamente con el archivo.
    """

    filename = file.filename or "archivo"
    filename = _safe_filename(filename)

    try:
        content = await file.read()

        if not content:
            raise HTTPException(
                status_code=400,
                detail="El archivo está vacío",
            )

        file_hash = _sha256_bytes(content)

        _delete_existing_documents_with_same_hash(db, file_hash)

        mime_type = file.content_type or mimetypes.guess_type(filename)[0]
        file_path = _save_file_to_storage(filename, content)

        document_id = str(uuid.uuid4())

        document = _create_document_record(
            db=db,
            document_id=document_id,
            filename=filename,
            file_path=file_path,
            file_hash=file_hash,
            mime_type=mime_type,
        )

        lower_name = filename.lower()

        if lower_name.endswith(".pdf"):
            full_text, pages = _extract_pdf_text_by_pages(file_path)
            extraction_method = "pdf_text_ocr"
        elif lower_name.endswith((".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp")):
            full_text, pages = _extract_image_text(file_path)
            extraction_method = "image_ocr"
        else:
            full_text = ""
            pages = []
            extraction_method = "unknown"

        _create_document_content(
            db=db,
            document_id=document_id,
            text=full_text,
            extraction_method=extraction_method,
        )

        for page in pages:
            _create_document_page(
                db=db,
                document_id=document_id,
                page_number=page["page_number"],
                page_text=page["page_text"],
            )

        db.commit()
        db.refresh(document)

        return document

    except HTTPException:
        raise

    except Exception as e:
        db.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Error subiendo documento: {str(e)}",
        )


@router.delete("/limpiar-documentos")
def clear_documents_and_results(
    db: Session = Depends(get_db),
):
    """
    Limpia documentos, resultados, jobs y archivos físicos temporales.
    """

    try:
        if hasattr(models, "ExportFile"):
            db.query(models.ExportFile).delete(synchronize_session=False)

        if hasattr(models, "HumanReview"):
            db.query(models.HumanReview).delete(synchronize_session=False)

        if hasattr(models, "ExtractionEvidence"):
            db.query(models.ExtractionEvidence).delete(
                synchronize_session=False)

        if hasattr(models, "ExtractionResult"):
            db.query(models.ExtractionResult).delete(synchronize_session=False)

        if hasattr(models, "ExtractionJob"):
            db.query(models.ExtractionJob).delete(synchronize_session=False)

        if hasattr(models, "AnalysisHistory"):
            db.query(models.AnalysisHistory).delete(synchronize_session=False)

        if hasattr(models, "DocumentChunk"):
            db.query(models.DocumentChunk).delete(synchronize_session=False)

        if hasattr(models, "DocumentPage"):
            db.query(models.DocumentPage).delete(synchronize_session=False)

        if hasattr(models, "DocumentContent"):
            db.query(models.DocumentContent).delete(synchronize_session=False)

        db.query(models.Document).delete(synchronize_session=False)

        if (
            hasattr(models, "ExtractionProject")
            and hasattr(models, "ExtractionTemplate")
            and hasattr(models, "ExtractionTemplateField")
        ):
            temporary_projects = (
                db.query(models.ExtractionProject)
                .filter(models.ExtractionProject.status == "temporary")
                .all()
            )

            temporary_project_ids = [
                project.id for project in temporary_projects]

            if temporary_project_ids:
                temporary_templates = (
                    db.query(models.ExtractionTemplate)
                    .filter(models.ExtractionTemplate.project_id.in_(temporary_project_ids))
                    .all()
                )

                temporary_template_ids = [
                    template.id for template in temporary_templates]

                if temporary_template_ids:
                    db.query(models.ExtractionTemplateField).filter(
                        models.ExtractionTemplateField.template_id.in_(
                            temporary_template_ids)
                    ).delete(synchronize_session=False)

                db.query(models.ExtractionTemplate).filter(
                    models.ExtractionTemplate.project_id.in_(
                        temporary_project_ids)
                ).delete(synchronize_session=False)

                db.query(models.ExtractionProject).filter(
                    models.ExtractionProject.id.in_(temporary_project_ids)
                ).delete(synchronize_session=False)

        db.commit()

        folders_to_clean = [
            str(STORAGE_ROOT / "uploads"),
            str(STORAGE_ROOT / "page_images"),
            str(STORAGE_ROOT / "exports"),
        ]

        for folder in folders_to_clean:
            if os.path.exists(folder):
                for name in os.listdir(folder):
                    path = os.path.join(folder, name)

                    if os.path.isfile(path) or os.path.islink(path):
                        os.remove(path)
                    elif os.path.isdir(path):
                        shutil.rmtree(path)

        return {
            "status": "ok",
            "message": "Documentos, resultados y archivos temporales eliminados correctamente.",
        }

    except Exception as e:
        db.rollback()

        raise HTTPException(
            status_code=500,
            detail=f"Error limpiando documentos: {str(e)}",
        )

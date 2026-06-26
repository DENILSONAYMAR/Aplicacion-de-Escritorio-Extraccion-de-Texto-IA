from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app import models
from app.services.page_image_service import prepare_document_images
from app.services.openai_service import (
    extract_fields_from_document_text,
    extract_fields_from_document_image,
)
from app.services.prediction_service import run_prediction_pass_for_job


def _template_fields_to_dict(template_fields) -> list[dict]:
    fields = []

    for field in template_fields:
        fields.append(
            {
                "field_name": field.field_name,
                "display_name": field.display_name,
                "target_location": field.target_location,
                "required": field.required,
                "description": field.description,
            }
        )

    return fields


def _create_missing_field_result(
    db: Session,
    job_id: int,
    document: models.Document,
    field_name: str,
    reason: str,
):
    result = models.ExtractionResult(
        job_id=job_id,
        document_id=document.id,
        page_id=None,
        file_name=document.file_name,
        page_number=None,
        field_name=field_name,
        raw_value=None,
        normalized_value=None,
        source_type="no_visible",
        confidence_level="ninguna",
        status="campo_no_encontrado",
        needs_review=True,
        evidence_text=reason,
    )

    db.add(result)
    return result


def run_text_extraction_job(
    db: Session,
    job_id: int,
    template_id: int,
    document_ids: list[str] | None = None,
) -> dict:
    job = (
        db.query(models.ExtractionJob)
        .filter(models.ExtractionJob.id == job_id)
        .first()
    )

    if not job:
        raise ValueError("Job de extracción no encontrado")

    template = (
        db.query(models.ExtractionTemplate)
        .filter(models.ExtractionTemplate.id == template_id)
        .first()
    )

    if not template:
        raise ValueError("Plantilla no encontrada")

    template_fields = (
        db.query(models.ExtractionTemplateField)
        .filter(models.ExtractionTemplateField.template_id == template_id)
        .order_by(models.ExtractionTemplateField.id.asc())
        .all()
    )

    if not template_fields:
        raise ValueError("La plantilla no tiene campos definidos")

    field_specs = _template_fields_to_dict(template_fields)
    expected_field_names = [field["field_name"] for field in field_specs]

    documents_query = db.query(models.Document)

    if document_ids:
        documents_query = documents_query.filter(
            models.Document.id.in_(document_ids))

    documents = documents_query.order_by(
        models.Document.created_at.asc()).all()

    if not documents:
        raise ValueError("No hay documentos para procesar")

    job.status = "running"
    job.started_at = func.now()
    job.total_files = len(documents)
    job.processed_files = 0
    job.failed_files = 0
    job.error_message = None

    db.add(job)
    db.commit()
    db.refresh(job)

    created_results = 0

    for document in documents:
        try:
            content = (
                db.query(models.DocumentContent)
                .filter(models.DocumentContent.document_id == document.id)
                .first()
            )

            document_text = (content.extracted_text if content else "") or ""

            if not document_text.strip():
                for field_name in expected_field_names:
                    _create_missing_field_result(
                        db=db,
                        job_id=job.id,
                        document=document,
                        field_name=field_name,
                        reason="El documento no tiene texto extraído disponible.",
                    )
                    created_results += 1

                job.failed_files += 1
                db.add(job)
                db.commit()
                continue

            ai_result = extract_fields_from_document_text(
                document_text=document_text,
                fields=field_specs,
                file_name=document.file_name,
            )

            extracted_fields = ai_result.get("fields", [])
            seen_fields = set()

            for item in extracted_fields:
                field_name = item.get("field_name")

                if field_name not in expected_field_names:
                    continue

                result = models.ExtractionResult(
                    job_id=job.id,
                    document_id=document.id,
                    page_id=None,
                    file_name=document.file_name,
                    page_number=None,
                    field_name=field_name,
                    raw_value=item.get("raw_value"),
                    normalized_value=item.get("normalized_value"),
                    source_type=item.get("source_type") or "desconocido",
                    confidence_level=item.get("confidence_level") or "baja",
                    status=item.get("status") or "pending_review",
                    needs_review=bool(item.get("needs_review", True)),
                    evidence_text=item.get("evidence_text"),
                )

                db.add(result)
                created_results += 1
                seen_fields.add(field_name)

            missing_fields = set(expected_field_names) - seen_fields

            for field_name in missing_fields:
                _create_missing_field_result(
                    db=db,
                    job_id=job.id,
                    document=document,
                    field_name=field_name,
                    reason="La IA no devolvió este campo en la extracción.",
                )
                created_results += 1

            job.processed_files += 1
            db.add(job)
            db.commit()

        except Exception as e:
            job.failed_files += 1
            job.error_message = str(e)
            db.add(job)
            db.commit()

    if job.failed_files == 0:
        job.status = "completed"
    elif job.processed_files > 0:
        job.status = "completed_with_warnings"
    else:
        job.status = "failed"

    job.finished_at = func.now()

    db.add(job)
    db.commit()
    db.refresh(job)

    return {
        "job_id": job.id,
        "status": job.status,
        "processed_files": job.processed_files,
        "failed_files": job.failed_files,
        "created_results": created_results,
        "message": "Extracción textual finalizada",
    }


def _get_or_create_document_page(
    db: Session,
    document_id: str,
    page_number: int,
    page_text: str | None = None,
) -> models.DocumentPage:
    page = (
        db.query(models.DocumentPage)
        .filter(
            models.DocumentPage.document_id == document_id,
            models.DocumentPage.page_number == page_number,
        )
        .first()
    )

    if page:
        return page

    page = models.DocumentPage(
        document_id=document_id,
        page_number=page_number,
        page_text=page_text,
    )

    db.add(page)
    db.commit()
    db.refresh(page)

    return page


def run_vision_extraction_job(
    db: Session,
    job_id: int,
    template_id: int,
    document_ids: list[str] | None = None,
    max_pages_per_document: int = 3,
) -> dict:
    """
    Ejecuta extracción multimodal por imagen de página.
    Este flujo es mejor para PDFs escaneados, imágenes y manuscritos.
    """

    job = (
        db.query(models.ExtractionJob)
        .filter(models.ExtractionJob.id == job_id)
        .first()
    )

    if not job:
        raise ValueError("Job de extracción no encontrado")

    template = (
        db.query(models.ExtractionTemplate)
        .filter(models.ExtractionTemplate.id == template_id)
        .first()
    )

    if not template:
        raise ValueError("Plantilla no encontrada")

    template_fields = (
        db.query(models.ExtractionTemplateField)
        .filter(models.ExtractionTemplateField.template_id == template_id)
        .order_by(models.ExtractionTemplateField.id.asc())
        .all()
    )

    if not template_fields:
        raise ValueError("La plantilla no tiene campos definidos")

    field_specs = _template_fields_to_dict(template_fields)
    expected_field_names = [field["field_name"] for field in field_specs]

    documents_query = db.query(models.Document)

    if document_ids:
        documents_query = documents_query.filter(
            models.Document.id.in_(document_ids))

    documents = documents_query.order_by(
        models.Document.created_at.asc()).all()

    if not documents:
        raise ValueError("No hay documentos para procesar")

    job.status = "running"
    job.started_at = func.now()
    job.total_files = len(documents)
    job.processed_files = 0
    job.failed_files = 0
    job.error_message = None

    db.add(job)
    db.commit()
    db.refresh(job)

    created_results = 0
    processed_pages = 0

    for document in documents:
        try:
            page_images = prepare_document_images(
                file_path=document.original_path,
                document_id=document.id,
                max_pages=max_pages_per_document,
            )

            if not page_images:
                for field_name in expected_field_names:
                    _create_missing_field_result(
                        db=db,
                        job_id=job.id,
                        document=document,
                        field_name=field_name,
                        reason="No se pudieron generar imágenes para el documento.",
                    )
                    created_results += 1

                job.failed_files += 1
                db.add(job)
                db.commit()
                continue

            for page_image in page_images:
                page_number = page_image["page_number"]
                image_path = page_image["image_path"]

                existing_page = (
                    db.query(models.DocumentPage)
                    .filter(
                        models.DocumentPage.document_id == document.id,
                        models.DocumentPage.page_number == page_number,
                    )
                    .first()
                )

                support_text = existing_page.page_text if existing_page else None

                page = _get_or_create_document_page(
                    db=db,
                    document_id=document.id,
                    page_number=page_number,
                    page_text=support_text,
                )

                ai_result = extract_fields_from_document_image(
                    image_path=image_path,
                    fields=field_specs,
                    file_name=document.file_name,
                    page_number=page_number,
                    support_text=support_text,
                )

                extracted_fields = ai_result.get("fields", [])
                seen_fields = set()

                for item in extracted_fields:
                    field_name = item.get("field_name")

                    if field_name not in expected_field_names:
                        continue

                    result = models.ExtractionResult(
                        job_id=job.id,
                        document_id=document.id,
                        page_id=page.id,
                        file_name=document.file_name,
                        page_number=page_number,
                        field_name=field_name,
                        raw_value=item.get("raw_value"),
                        normalized_value=item.get("normalized_value"),
                        source_type=item.get("source_type") or "desconocido",
                        confidence_level=item.get(
                            "confidence_level") or "baja",
                        status=item.get("status") or "pending_review",
                        needs_review=bool(item.get("needs_review", True)),
                        evidence_text=item.get("evidence_text"),
                    )

                    db.add(result)
                    db.flush()

                    evidence = models.ExtractionEvidence(
                        extraction_result_id=result.id,
                        image_path=image_path,
                        crop_path=None,
                        bbox_json=None,
                        notes=(
                            f"Evidencia visual de página {page_number}. "
                            "Aún no se genera recorte por campo."
                        ),
                    )

                    db.add(evidence)

                    created_results += 1
                    seen_fields.add(field_name)

                missing_fields = set(expected_field_names) - seen_fields

                for field_name in missing_fields:
                    result = models.ExtractionResult(
                        job_id=job.id,
                        document_id=document.id,
                        page_id=page.id,
                        file_name=document.file_name,
                        page_number=page_number,
                        field_name=field_name,
                        raw_value=None,
                        normalized_value=None,
                        source_type="no_visible",
                        confidence_level="ninguna",
                        status="campo_no_encontrado",
                        needs_review=True,
                        evidence_text="La IA no devolvió este campo en la página analizada.",
                    )

                    db.add(result)
                    db.flush()

                    evidence = models.ExtractionEvidence(
                        extraction_result_id=result.id,
                        image_path=image_path,
                        crop_path=None,
                        bbox_json=None,
                        notes=f"Campo no encontrado en página {page_number}.",
                    )

                    db.add(evidence)

                    created_results += 1

                processed_pages += 1
                db.commit()

            job.processed_files += 1
            db.add(job)
            db.commit()

        except Exception as e:
            job.failed_files += 1
            job.error_message = str(e)
            db.add(job)
            db.commit()

    if job.failed_files == 0:
        job.status = "completed"
    elif job.processed_files > 0:
        job.status = "completed_with_warnings"
    else:
        job.status = "failed"

    job.finished_at = func.now()

    db.add(job)
    db.commit()
    db.refresh(job)

    prediction_summary = run_prediction_pass_for_job(
        db=db,
        job_id=job.id,
        template_id=template_id,
    )

    return {
        "job_id": job.id,
        "status": job.status,
        "processed_files": job.processed_files,
        "failed_files": job.failed_files,
        "processed_pages": processed_pages,
        "created_results": created_results,
        "predicted_updates": prediction_summary["auto_corrected"],
        "prediction_review_pending": prediction_summary["needs_human_review"],
        "prediction_unresolved": prediction_summary["unresolved"],
        "message": "Extracción multimodal por imagen finalizada con predicción lógica",
    }

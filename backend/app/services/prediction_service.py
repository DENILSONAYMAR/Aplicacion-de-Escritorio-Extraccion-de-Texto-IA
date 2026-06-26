from sqlalchemy.orm import Session

from app import models
from app.services.openai_service import predict_failed_field_from_context


PROBLEMATIC_STATUSES = {
    "requiere_revision",
    "ilegible",
    "campo_no_encontrado",
    "pending_review",
    "error_formato",
    "inferido",
}

LOW_CONFIDENCE_LEVELS = {
    "baja",
    "ninguna",
    None,
}


def _is_problematic_result(result: models.ExtractionResult) -> bool:
    if result.needs_review:
        return True

    if result.status in PROBLEMATIC_STATUSES:
        return True

    if result.confidence_level in LOW_CONFIDENCE_LEVELS:
        return True

    value = result.normalized_value or result.raw_value

    if value is None or str(value).strip() == "":
        return True

    return False


def _get_field_description(
    db: Session,
    template_id: int | None,
    field_name: str,
) -> str | None:
    if template_id is None:
        return None

    field = (
        db.query(models.ExtractionTemplateField)
        .filter(
            models.ExtractionTemplateField.template_id == template_id,
            models.ExtractionTemplateField.field_name == field_name,
        )
        .first()
    )

    if not field:
        return None

    parts = []

    if field.display_name:
        parts.append(f"Nombre visible: {field.display_name}")

    if field.description:
        parts.append(f"Descripción: {field.description}")

    if field.required:
        parts.append("Campo requerido.")

    if field.target_location:
        parts.append(f"Destino en plantilla: {field.target_location}")

    return " ".join(parts).strip() or None


def _get_support_text(
    db: Session,
    result: models.ExtractionResult,
) -> str | None:
    if result.page_id:
        page = (
            db.query(models.DocumentPage)
            .filter(models.DocumentPage.id == result.page_id)
            .first()
        )

        if page and page.page_text:
            return page.page_text

    if result.document_id and result.page_number:
        page = (
            db.query(models.DocumentPage)
            .filter(
                models.DocumentPage.document_id == result.document_id,
                models.DocumentPage.page_number == result.page_number,
            )
            .first()
        )

        if page and page.page_text:
            return page.page_text

    return None


def _get_image_path(
    db: Session,
    result: models.ExtractionResult,
) -> str | None:
    evidence = (
        db.query(models.ExtractionEvidence)
        .filter(models.ExtractionEvidence.extraction_result_id == result.id)
        .first()
    )

    if evidence and evidence.image_path:
        return evidence.image_path

    return None


def _get_same_page_context(
    db: Session,
    result: models.ExtractionResult,
) -> list[dict]:
    query = db.query(models.ExtractionResult).filter(
        models.ExtractionResult.job_id == result.job_id,
    )

    if result.document_id:
        query = query.filter(
            models.ExtractionResult.document_id == result.document_id)

    if result.page_number is not None:
        query = query.filter(
            models.ExtractionResult.page_number == result.page_number)

    rows = query.order_by(models.ExtractionResult.id.asc()).all()

    context = []

    for row in rows:
        if row.id == result.id:
            continue

        context.append(
            {
                "field_name": row.field_name,
                "value": row.normalized_value or row.raw_value,
                "status": row.status,
                "confidence_level": row.confidence_level,
                "needs_review": row.needs_review,
                "evidence_text": row.evidence_text,
            }
        )

    return context


def _append_prediction_note(
    original_note: str | None,
    prediction: dict,
) -> str:
    note = original_note or ""

    prediction_note = (
        "\n\n[PREDICCIÓN LÓGICA]\n"
        f"Decisión: {prediction.get('decision')}\n"
        f"Valor predicho: {prediction.get('predicted_value')}\n"
        f"Confianza: {prediction.get('confidence_level')}\n"
        f"Resumen: {prediction.get('reasoning_summary')}"
    )

    return (note + prediction_note).strip()


def run_prediction_pass_for_job(
    db: Session,
    job_id: int,
    template_id: int | None = None,
    max_items: int = 50,
) -> dict:
    """
    Revisa resultados fallidos/dudosos después de run-vision.
    Si la IA puede corregir con evidencia fuerte, actualiza el resultado.
    Si no, deja el resultado para revisión humana.
    """

    job = (
        db.query(models.ExtractionJob)
        .filter(models.ExtractionJob.id == job_id)
        .first()
    )

    if not job:
        raise ValueError("Job de extracción no encontrado")

    all_results = (
        db.query(models.ExtractionResult)
        .filter(models.ExtractionResult.job_id == job_id)
        .order_by(models.ExtractionResult.id.asc())
        .all()
    )

    problematic_results = [
        result for result in all_results if _is_problematic_result(result)
    ][:max_items]

    checked = 0
    auto_corrected = 0
    needs_human_review = 0
    unresolved = 0

    for result in problematic_results:
        checked += 1

        field_description = _get_field_description(
            db=db,
            template_id=template_id,
            field_name=result.field_name,
        )

        support_text = _get_support_text(db=db, result=result)
        image_path = _get_image_path(db=db, result=result)
        same_page_context = _get_same_page_context(db=db, result=result)

        try:
            prediction = predict_failed_field_from_context(
                field_name=result.field_name,
                current_raw_value=result.raw_value,
                current_normalized_value=result.normalized_value,
                current_status=result.status,
                current_confidence=result.confidence_level,
                evidence_text=result.evidence_text,
                field_description=field_description,
                same_page_context=same_page_context,
                support_text=support_text,
                image_path=image_path,
            )
        except Exception as e:
            result.evidence_text = (
                (result.evidence_text or "")
                + f"\n\n[PREDICCIÓN LÓGICA ERROR]\n{str(e)}"
            ).strip()

            result.needs_review = True
            result.status = "requiere_revision"

            db.add(result)
            db.commit()

            needs_human_review += 1
            continue

        decision = prediction.get("decision")
        predicted_value = prediction.get(
            "normalized_value") or prediction.get("predicted_value")
        prediction_confidence = prediction.get("confidence_level")

        result.evidence_text = _append_prediction_note(
            original_note=result.evidence_text,
            prediction=prediction,
        )

        if (
            decision == "auto_corrected"
            and predicted_value
            and prediction_confidence == "alta"
        ):
            result.normalized_value = predicted_value
            result.source_type = "inferido"
            result.confidence_level = "alta"
            result.status = "ok"
            result.needs_review = False

            review = models.HumanReview(
                extraction_result_id=result.id,
                original_value=result.raw_value,
                corrected_value=predicted_value,
                review_status="auto_predicted",
                reviewer_notes=prediction.get("reasoning_summary"),
            )

            db.add(review)
            auto_corrected += 1

        elif decision == "unresolved":
            result.status = "ilegible"
            result.confidence_level = "ninguna"
            result.needs_review = True

            review = models.HumanReview(
                extraction_result_id=result.id,
                original_value=result.raw_value,
                corrected_value=None,
                review_status="prediction_unresolved",
                reviewer_notes=prediction.get("reasoning_summary"),
            )

            db.add(review)
            unresolved += 1

        else:
            result.status = "requiere_revision"
            result.needs_review = True

            review = models.HumanReview(
                extraction_result_id=result.id,
                original_value=result.raw_value,
                corrected_value=predicted_value,
                review_status="prediction_needs_human_review",
                reviewer_notes=prediction.get("reasoning_summary"),
            )

            db.add(review)
            needs_human_review += 1

        db.add(result)
        db.commit()

    return {
        "checked": checked,
        "auto_corrected": auto_corrected,
        "needs_human_review": needs_human_review,
        "unresolved": unresolved,
    }

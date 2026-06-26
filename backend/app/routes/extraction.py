from datetime import datetime
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app import models
from app.services.vision_extraction_service import run_vision_extraction_for_job
from app.services.export_excel_service import export_job_results_to_excel


router = APIRouter(prefix="/extraction", tags=["extraction"])
STORAGE_ROOT = os.getenv("STORAGE_ROOT", "/storage")


def _set_if_exists(instance, field_name: str, value):
    if hasattr(instance, field_name):
        setattr(instance, field_name, value)


def _get_or_404(db: Session, model, item_id: int, label: str):
    item = db.query(model).filter(model.id == item_id).first()

    if not item:
        raise HTTPException(status_code=404, detail=f"{label} no encontrado")

    return item


def _serialize(item):
    return jsonable_encoder(item)


@router.get("/")
def extraction_status():
    return {
        "message": "Módulo de extracción activo",
        "status": "ok",
    }


@router.post("/projects")
def create_extraction_project(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
):
    name = payload.get(
        "name") or f"Proyecto temporal {datetime.utcnow().isoformat()}"

    project = models.ExtractionProject()

    _set_if_exists(project, "name", name)
    _set_if_exists(project, "input_folder", payload.get("input_folder"))
    _set_if_exists(project, "output_folder", payload.get(
        "output_folder") or f"{STORAGE_ROOT}/exports")
    _set_if_exists(project, "status", payload.get("status") or "active")

    db.add(project)
    db.commit()
    db.refresh(project)

    return _serialize(project)


@router.get("/projects")
def list_extraction_projects(
    db: Session = Depends(get_db),
):
    projects = (
        db.query(models.ExtractionProject)
        .filter(models.ExtractionProject.status != "temporary")
        .order_by(models.ExtractionProject.id.desc())
        .all()
    )

    return _serialize(projects)


@router.get("/projects/{project_id}")
def get_extraction_project(
    project_id: int,
    db: Session = Depends(get_db),
):
    project = _get_or_404(db, models.ExtractionProject, project_id, "Proyecto")

    return _serialize(project)


@router.patch("/projects/{project_id}")
def update_extraction_project(
    project_id: int,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
):
    project = _get_or_404(db, models.ExtractionProject, project_id, "Proyecto")

    for field_name in ["name", "input_folder", "output_folder", "status"]:
        if field_name in payload:
            _set_if_exists(project, field_name, payload[field_name])

    db.commit()
    db.refresh(project)

    return _serialize(project)


@router.post("/projects/{project_id}/jobs")
def create_extraction_job(
    project_id: int,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
):
    _get_or_404(db, models.ExtractionProject, project_id, "Proyecto")

    job = models.ExtractionJob()

    _set_if_exists(job, "project_id", project_id)
    _set_if_exists(job, "status", "created")
    _set_if_exists(job, "total_files", int(payload.get("total_files") or 0))
    _set_if_exists(job, "processed_files", 0)
    _set_if_exists(job, "failed_files", 0)
    _set_if_exists(job, "error_message", None)

    db.add(job)
    db.commit()
    db.refresh(job)

    return _serialize(job)


@router.get("/jobs/{job_id}")
def get_extraction_job(
    job_id: int,
    db: Session = Depends(get_db),
):
    job = _get_or_404(db, models.ExtractionJob, job_id, "Job")

    return _serialize(job)


@router.post("/jobs/{job_id}/cancel")
def cancel_extraction_job(
    job_id: int,
    db: Session = Depends(get_db),
):
    job = _get_or_404(db, models.ExtractionJob, job_id, "Job")

    _set_if_exists(job, "status", "cancelled")
    _set_if_exists(job, "error_message", "Cancelado por el usuario.")

    db.commit()
    db.refresh(job)

    return _serialize(job)


@router.post("/jobs/{job_id}/run-vision")
def run_extraction_job_vision(
    job_id: int,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
):
    template_id = payload.get("template_id")
    document_ids = payload.get("document_ids") or []
    max_pages_per_document = int(payload.get("max_pages_per_document") or 10)

    if not template_id:
        raise HTTPException(
            status_code=400,
            detail="template_id es obligatorio",
        )

    if not document_ids:
        raise HTTPException(
            status_code=400,
            detail="document_ids es obligatorio",
        )

    max_pages_per_document = max(1, min(max_pages_per_document, 10))

    try:
        result = run_vision_extraction_for_job(
            db=db,
            job_id=job_id,
            template_id=int(template_id),
            document_ids=[str(item) for item in document_ids],
            max_pages_per_document=max_pages_per_document,
        )

        return result

    except Exception as e:
        job = db.query(models.ExtractionJob).filter(
            models.ExtractionJob.id == job_id).first()

        if job and getattr(job, "status", None) != "cancelled":
            _set_if_exists(job, "status", "failed")
            _set_if_exists(job, "error_message", str(e))
            db.commit()

        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@router.get("/jobs/{job_id}/results")
def list_job_results(
    job_id: int,
    db: Session = Depends(get_db),
):
    _get_or_404(db, models.ExtractionJob, job_id, "Job")

    results = (
        db.query(models.ExtractionResult)
        .filter(models.ExtractionResult.job_id == job_id)
        .order_by(models.ExtractionResult.id.asc())
        .all()
    )

    return _serialize(results)


@router.get("/review-items")
def list_review_items(
    db: Session = Depends(get_db),
):
    results = (
        db.query(models.ExtractionResult)
        .filter(models.ExtractionResult.needs_review == True)
        .order_by(models.ExtractionResult.id.asc())
        .all()
    )

    return _serialize(results)


@router.post("/results/{result_id}/review")
def review_result(
    result_id: int,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
):
    result = _get_or_404(db, models.ExtractionResult, result_id, "Resultado")

    review_status = payload.get("review_status")
    corrected_value = payload.get("corrected_value")
    reviewer_notes = payload.get("reviewer_notes")
    corrected_text = (
        str(corrected_value).strip()
        if corrected_value is not None and str(corrected_value).strip() != ""
        else None
    )

    if review_status == "corrected":
        if corrected_text is None:
            raise HTTPException(
                status_code=400,
                detail="corrected_value es obligatorio cuando review_status es corrected",
            )

        _set_if_exists(result, "normalized_value", corrected_text)
        _set_if_exists(result, "status", "ok")
        _set_if_exists(result, "confidence_level", "alta")
        _set_if_exists(result, "needs_review", False)

    elif review_status == "accepted":
        _set_if_exists(result, "status", "ok")
        _set_if_exists(result, "confidence_level", "alta")
        _set_if_exists(result, "needs_review", False)

    elif review_status == "marked_illegible":
        _set_if_exists(result, "normalized_value", corrected_text or "ilegible")
        _set_if_exists(result, "status", "ilegible")
        _set_if_exists(result, "confidence_level", "ninguna")
        _set_if_exists(result, "needs_review", False)

    elif review_status == "marked_no_visible":
        _set_if_exists(result, "normalized_value",
                       corrected_text or "no visible")
        _set_if_exists(result, "source_type", "no_visible")
        _set_if_exists(result, "status", "campo_no_encontrado")
        _set_if_exists(result, "confidence_level", "ninguna")
        _set_if_exists(result, "needs_review", False)

    elif review_status == "rejected":
        _set_if_exists(result, "normalized_value", "no aplica")
        _set_if_exists(result, "status", "rechazado")
        _set_if_exists(result, "confidence_level", "ninguna")
        _set_if_exists(result, "needs_review", False)

    else:
        raise HTTPException(
            status_code=400,
            detail="review_status inválido",
        )

    if hasattr(models, "HumanReview"):
        review = models.HumanReview()

        _set_if_exists(review, "extraction_result_id", result_id)
        _set_if_exists(review, "corrected_value", corrected_text)
        _set_if_exists(review, "review_status", review_status)
        _set_if_exists(review, "reviewer_notes", reviewer_notes)

        db.add(review)

    db.commit()
    db.refresh(result)

    return _serialize(result)


@router.post("/jobs/{job_id}/export-excel")
def export_job_excel(
    job_id: int,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
):
    template_id = payload.get("template_id")

    try:
        export_file = export_job_results_to_excel(
            db=db,
            job_id=job_id,
            template_id=int(template_id) if template_id else None,
        )

        return _serialize(export_file)

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )


@router.get("/exports/{export_id}/download")
def download_export(
    export_id: int,
    db: Session = Depends(get_db),
):
    export_file = _get_or_404(db, models.ExportFile, export_id, "Exportación")

    file_path = export_file.file_path

    if not file_path:
        raise HTTPException(
            status_code=404,
            detail="La exportación no tiene ruta de archivo",
        )

    return FileResponse(
        path=file_path,
        filename=file_path.split("/")[-1],
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

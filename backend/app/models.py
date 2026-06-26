from sqlalchemy import (
    Column,
    String,
    DateTime,
    Text,
    Integer,
    Boolean,
    ForeignKey,
    JSON,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db import Base


# ============================================================
# MODELOS EXISTENTES DEL PROYECTO ANTERIOR
# ============================================================

class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, index=True)
    file_name = Column(String, nullable=False)
    original_path = Column(Text, nullable=False)
    mime_type = Column(String, nullable=True)
    file_hash = Column(String, unique=True, nullable=True)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DocumentContent(Base):
    __tablename__ = "document_contents"

    document_id = Column(String, primary_key=True, index=True)
    extracted_text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AnalysisHistory(Base):
    __tablename__ = "analysis_history"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Text, nullable=True)
    prompt = Column(Text, nullable=False)
    response = Column(Text, nullable=False)
    analysis_type = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DocumentPage(Base):
    __tablename__ = "document_pages"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(String, nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    page_text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(String, nullable=False, index=True)
    page_number = Column(Integer, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# NUEVOS MODELOS PARA EXTRACCIÓN MASIVA
# ============================================================

class ExtractionProject(Base):
    __tablename__ = "extraction_projects"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(255), nullable=False)

    input_folder = Column(Text, nullable=True)
    output_folder = Column(Text, nullable=True)

    status = Column(String(50), nullable=False, default="created")
    # created, configured, running, completed, failed, cancelled

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True)

    templates = relationship(
        "ExtractionTemplate",
        back_populates="project",
        cascade="all, delete-orphan",
    )

    jobs = relationship(
        "ExtractionJob",
        back_populates="project",
        cascade="all, delete-orphan",
    )


class ExtractionTemplate(Base):
    __tablename__ = "extraction_templates"

    id = Column(Integer, primary_key=True, index=True)

    project_id = Column(
        Integer,
        ForeignKey("extraction_projects.id"),
        nullable=True,
        index=True,
    )

    name = Column(String(255), nullable=False)

    file_path = Column(Text, nullable=True)

    template_type = Column(String(50), nullable=False)
    # excel, word, generated_excel

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    project = relationship(
        "ExtractionProject",
        back_populates="templates",
    )

    fields = relationship(
        "ExtractionTemplateField",
        back_populates="template",
        cascade="all, delete-orphan",
    )


class ExtractionTemplateField(Base):
    __tablename__ = "extraction_template_fields"

    id = Column(Integer, primary_key=True, index=True)

    template_id = Column(
        Integer,
        ForeignKey("extraction_templates.id"),
        nullable=False,
        index=True,
    )

    field_name = Column(String(255), nullable=False)
    # Ejemplo: nombre_completo, dni, fecha, direccion

    display_name = Column(String(255), nullable=True)
    # Ejemplo: Nombre completo, DNI, Fecha

    target_location = Column(String(255), nullable=True)
    # Excel: B4, C8, Hoja1!B4
    # Word: {{nombre_completo}}, {{dni}}

    required = Column(Boolean, nullable=False, default=False)

    description = Column(Text, nullable=True)
    # Descripción del campo para ayudar a la IA a extraer mejor.

    template = relationship(
        "ExtractionTemplate",
        back_populates="fields",
    )


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"

    id = Column(Integer, primary_key=True, index=True)

    project_id = Column(
        Integer,
        ForeignKey("extraction_projects.id"),
        nullable=False,
        index=True,
    )

    status = Column(String(50), nullable=False, default="pending")
    # pending, running, completed, failed, cancelled

    total_files = Column(Integer, nullable=False, default=0)
    processed_files = Column(Integer, nullable=False, default=0)
    failed_files = Column(Integer, nullable=False, default=0)

    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship(
        "ExtractionProject",
        back_populates="jobs",
    )

    results = relationship(
        "ExtractionResult",
        back_populates="job",
        cascade="all, delete-orphan",
    )

    exports = relationship(
        "ExportFile",
        back_populates="job",
        cascade="all, delete-orphan",
    )


class ExtractionResult(Base):
    __tablename__ = "extraction_results"

    id = Column(Integer, primary_key=True, index=True)

    job_id = Column(
        Integer,
        ForeignKey("extraction_jobs.id"),
        nullable=False,
        index=True,
    )

    # IMPORTANTE:
    # En tu proyecto actual Document.id es String, por eso document_id aquí también es String.
    document_id = Column(
        String,
        ForeignKey("documents.id"),
        nullable=True,
        index=True,
    )

    page_id = Column(
        Integer,
        ForeignKey("document_pages.id"),
        nullable=True,
        index=True,
    )

    file_name = Column(String(255), nullable=True)
    page_number = Column(Integer, nullable=True)

    field_name = Column(String(255), nullable=False)
    # Ejemplo: nombre_completo, dni, fecha

    raw_value = Column(Text, nullable=True)
    # Valor bruto leído por IA/OCR.

    normalized_value = Column(Text, nullable=True)
    # Valor limpio/final normalizado.

    source_type = Column(String(50), nullable=True)
    # impreso, manuscrito, mixto, inferido, no_visible

    confidence_level = Column(String(50), nullable=True)
    # alta, media, baja

    status = Column(String(50), nullable=False, default="pending_review")
    # ok, requiere_revision, ilegible, inferido,
    # error_formato, campo_no_encontrado, pending_review, rechazado

    needs_review = Column(Boolean, nullable=False, default=True)

    evidence_text = Column(Text, nullable=True)
    # Explicación breve de dónde salió el dato.

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job = relationship(
        "ExtractionJob",
        back_populates="results",
    )

    evidences = relationship(
        "ExtractionEvidence",
        back_populates="result",
        cascade="all, delete-orphan",
    )

    reviews = relationship(
        "HumanReview",
        back_populates="result",
        cascade="all, delete-orphan",
    )


class ExtractionEvidence(Base):
    __tablename__ = "extraction_evidence"

    id = Column(Integer, primary_key=True, index=True)

    extraction_result_id = Column(
        Integer,
        ForeignKey("extraction_results.id"),
        nullable=False,
        index=True,
    )

    image_path = Column(Text, nullable=True)
    # Imagen completa de la página procesada.

    crop_path = Column(Text, nullable=True)
    # Recorte visual del campo, si luego agregamos detección por zona.

    bbox_json = Column(JSON, nullable=True)
    # Ejemplo:
    # {
    #   "x": 10,
    #   "y": 20,
    #   "width": 100,
    #   "height": 40
    # }

    notes = Column(Text, nullable=True)

    result = relationship(
        "ExtractionResult",
        back_populates="evidences",
    )


class HumanReview(Base):
    __tablename__ = "human_reviews"

    id = Column(Integer, primary_key=True, index=True)

    extraction_result_id = Column(
        Integer,
        ForeignKey("extraction_results.id"),
        nullable=False,
        index=True,
    )

    original_value = Column(Text, nullable=True)
    corrected_value = Column(Text, nullable=True)

    review_status = Column(String(50), nullable=False)
    # accepted, corrected, marked_illegible, rejected

    reviewer_notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    result = relationship(
        "ExtractionResult",
        back_populates="reviews",
    )


class ExportFile(Base):
    __tablename__ = "export_files"

    id = Column(Integer, primary_key=True, index=True)

    job_id = Column(
        Integer,
        ForeignKey("extraction_jobs.id"),
        nullable=False,
        index=True,
    )

    document_id = Column(
        String,
        ForeignKey("documents.id"),
        nullable=True,
        index=True,
    )

    export_type = Column(String(50), nullable=False)
    # excel, word

    file_path = Column(Text, nullable=False)

    has_warnings = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    job = relationship(
        "ExtractionJob",
        back_populates="exports",
    )

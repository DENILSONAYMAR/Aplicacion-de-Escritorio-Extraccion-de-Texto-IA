from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ChatAnalyzeRequest(BaseModel):
    document_id: str
    prompt: str


class ChatAnalyzeResponse(BaseModel):
    document_id: str
    prompt: str
    answer: str


class MultiDocumentAnalyzeRequest(BaseModel):
    document_ids: list[str]
    prompt: str


class MultiDocumentAnalyzeResponse(BaseModel):
    document_ids: list[str]
    prompt: str
    answer: str


class ExtractionTemplateFieldCreate(BaseModel):
    field_name: str
    display_name: Optional[str] = None
    target_location: Optional[str] = None
    required: bool = False
    description: Optional[str] = None


class ExtractionTemplateFieldResponse(BaseModel):
    id: int
    template_id: int
    field_name: str
    display_name: Optional[str] = None
    target_location: Optional[str] = None
    required: bool
    description: Optional[str] = None

    class Config:
        from_attributes = True


class ExtractionTemplateCreate(BaseModel):
    project_id: Optional[int] = None
    name: str
    file_path: Optional[str] = None
    template_type: str = "generated_excel"
    fields: list[ExtractionTemplateFieldCreate] = Field(default_factory=list)


class ExtractionTemplateResponse(BaseModel):
    id: int
    project_id: Optional[int] = None
    name: str
    file_path: Optional[str] = None
    template_type: str
    created_at: Optional[datetime] = None
    fields: list[ExtractionTemplateFieldResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True

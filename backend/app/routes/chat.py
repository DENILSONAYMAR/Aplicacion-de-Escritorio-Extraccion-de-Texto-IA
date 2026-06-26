from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import re
import unicodedata

from app.db import get_db
from app.models import Document, DocumentContent, DocumentPage, AnalysisHistory
from app.schemas import (
    ChatAnalyzeRequest,
    ChatAnalyzeResponse,
    MultiDocumentAnalyzeRequest,
    MultiDocumentAnalyzeResponse,
)
from app.services.openai_service import (
    analyze_document_text,
    analyze_multiple_documents,
)

router = APIRouter()


def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_query_terms(prompt: str) -> list[str]:
    normalized = normalize_text(prompt)
    terms = re.split(r"[^a-z0-9]+", normalized)
    return [t for t in terms if len(t) >= 3]


def extract_page_number(prompt: str):
    normalized = normalize_text(prompt)
    match = re.search(r"pagina\s+(\d+)", normalized)
    if match:
        return int(match.group(1))
    return None


def build_relevant_context_from_pages(document_id: str, prompt: str, db: Session) -> str:
    pages = (
        db.query(DocumentPage)
        .filter(DocumentPage.document_id == document_id)
        .order_by(DocumentPage.page_number.asc())
        .all()
    )

    if not pages:
        return ""

    # 1. si el usuario pide una página exacta, devolver esa página
    requested_page = extract_page_number(prompt)
    if requested_page is not None:
        for page in pages:
            if page.page_number == requested_page:
                return f"[PÁGINA {page.page_number}]\n{(page.page_text or '')[:5000]}"

        return ""

    # 2. búsqueda normal por términos
    normalized_prompt = normalize_text(prompt)
    query_terms = extract_query_terms(prompt)
    scored_pages = []

    for page in pages:
        text = (page.page_text or "").strip()
        if not text:
            continue

        normalized_page = normalize_text(text)
        score = 0

        if normalized_prompt and normalized_prompt in normalized_page:
            score += 20

        for term in query_terms:
            if term in normalized_page:
                score += 1

        article_match = re.search(r"articulo\s+(\d+)", normalized_prompt)
        if article_match:
            article_num = article_match.group(1)
            if f"articulo {article_num}" in normalized_page:
                score += 10
            if article_num in normalized_page:
                score += 2

        if score > 0:
            scored_pages.append((score, page.page_number, text))

    if not scored_pages:
        fallback_pages = pages[:5]
        return "\n\n".join(
            [
                f"[PÁGINA {p.page_number}]\n{(p.page_text or '')[:2500]}"
                for p in fallback_pages
                if p.page_text
            ]
        ).strip()

    scored_pages.sort(key=lambda x: (-x[0], x[1]))
    top_pages = scored_pages[:5]

    context_parts = []
    for _, page_number, text in top_pages:
        context_parts.append(f"[PÁGINA {page_number}]\n{text[:2500]}")

    return "\n\n".join(context_parts).strip()


@router.get("/")
def chat_status():
    return {"message": "ruta chat activa"}


@router.get("/historial")
def get_history(db: Session = Depends(get_db)):
    history = db.query(AnalysisHistory).order_by(
        AnalysisHistory.created_at.desc()).all()

    return [
        {
            "id": item.id,
            "document_id": item.document_id,
            "prompt": item.prompt,
            "response": item.response,
            "analysis_type": item.analysis_type,
            "created_at": item.created_at,
        }
        for item in history
    ]


@router.post("/buscar-paginas")
def search_pages(data: ChatAnalyzeRequest, db: Session = Depends(get_db)):
    document = db.query(Document).filter(
        Document.id == data.document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    pages = (
        db.query(DocumentPage)
        .filter(DocumentPage.document_id == data.document_id)
        .order_by(DocumentPage.page_number.asc())
        .all()
    )

    requested_page = extract_page_number(data.prompt)
    if requested_page is not None:
        for page in pages:
            if page.page_number == requested_page:
                return [{
                    "page_number": page.page_number,
                    "score": 999,
                    "page_text": (page.page_text or "")[:3000]
                }]
        return []

    normalized_prompt = normalize_text(data.prompt)
    query_terms = extract_query_terms(data.prompt)
    results = []

    for page in pages:
        text = (page.page_text or "").strip()
        if not text:
            continue

        normalized_page = normalize_text(text)
        score = 0

        if normalized_prompt and normalized_prompt in normalized_page:
            score += 20

        for term in query_terms:
            if term in normalized_page:
                score += 1

        article_match = re.search(r"articulo\s+(\d+)", normalized_prompt)
        if article_match:
            article_num = article_match.group(1)
            if f"articulo {article_num}" in normalized_page:
                score += 10
            if article_num in normalized_page:
                score += 2

        if score > 0:
            results.append({
                "page_number": page.page_number,
                "score": score,
                "page_text": text[:2000]
            })

    results.sort(key=lambda x: (-x["score"], x["page_number"]))
    return results[:5]


@router.post("/analizar", response_model=ChatAnalyzeResponse)
def analyze_document(data: ChatAnalyzeRequest, db: Session = Depends(get_db)):
    document = db.query(Document).filter(
        Document.id == data.document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    content = db.query(DocumentContent).filter(
        DocumentContent.document_id == data.document_id
    ).first()
    if not content or not content.extracted_text:
        raise HTTPException(
            status_code=400, detail="El documento no tiene texto extraído")

    relevant_context = build_relevant_context_from_pages(
        document_id=data.document_id,
        prompt=data.prompt,
        db=db
    )

    if not relevant_context:
        relevant_context = content.extracted_text[:10000]

    try:
        answer = analyze_document_text(
            prompt=data.prompt,
            document_text=relevant_context
        )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error en análisis individual: {str(e)}")

    history = AnalysisHistory(
        document_id=data.document_id,
        prompt=data.prompt,
        response=answer,
        analysis_type="single"
    )
    db.add(history)
    db.commit()

    return ChatAnalyzeResponse(
        document_id=data.document_id,
        prompt=data.prompt,
        answer=answer
    )


@router.post("/analizar-multiples", response_model=MultiDocumentAnalyzeResponse)
def analyze_multiple(data: MultiDocumentAnalyzeRequest, db: Session = Depends(get_db)):
    try:
        if not data.document_ids:
            raise HTTPException(
                status_code=400, detail="Debes enviar al menos un document_id")

        if len(data.document_ids) > 10:
            raise HTTPException(
                status_code=400, detail="Máximo 10 documentos por análisis múltiple")

        documents_for_analysis = []

        for document_id in data.document_ids:
            document = db.query(Document).filter(
                Document.id == document_id).first()
            if not document:
                raise HTTPException(
                    status_code=404, detail=f"Documento no encontrado: {document_id}")

            content = db.query(DocumentContent).filter(
                DocumentContent.document_id == document_id
            ).first()
            if not content or not content.extracted_text:
                raise HTTPException(
                    status_code=400,
                    detail=f"El documento no tiene texto extraído: {document_id}"
                )

            relevant_context = build_relevant_context_from_pages(
                document_id=document_id,
                prompt=data.prompt,
                db=db
            )

            if not relevant_context:
                relevant_context = content.extracted_text[:2500]

            documents_for_analysis.append({
                "id": document.id,
                "file_name": document.file_name,
                "text": relevant_context,
            })

        answer = analyze_multiple_documents(
            prompt=data.prompt,
            documents=documents_for_analysis
        )

        history = AnalysisHistory(
            document_id=",".join(data.document_ids),
            prompt=data.prompt,
            response=answer,
            analysis_type="multiple"
        )
        db.add(history)
        db.commit()

        return MultiDocumentAnalyzeResponse(
            document_ids=data.document_ids,
            prompt=data.prompt,
            answer=answer
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error real en análisis múltiple: {str(e)}")

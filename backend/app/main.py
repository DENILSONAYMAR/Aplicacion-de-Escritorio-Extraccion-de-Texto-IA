from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import documents, chat, extraction, templates
from app.db import Base, engine
import app.models

app = FastAPI(title="Documento AI Escritorio Backend")

Base.metadata.create_all(bind=engine)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "https://tauri.localhost",
        "http://tauri.localhost",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(documents.router, prefix="/documentos", tags=["documentos"])
app.include_router(chat.router, prefix="/charlar", tags=["charlar"])

app.include_router(extraction.router)
app.include_router(templates.router)


@app.get("/salud")
def health():
    return {"status": "ok"}

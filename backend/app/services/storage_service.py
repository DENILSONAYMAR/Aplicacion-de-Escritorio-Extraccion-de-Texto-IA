import os
import shutil
import hashlib
from uuid import uuid4


def ensure_storage_path(path: str):
    os.makedirs(path, exist_ok=True)


def calculate_file_hash(file_path: str) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def save_upload_file(upload_file, storage_path: str):
    ensure_storage_path(storage_path)

    document_id = str(uuid4())
    original_filename = upload_file.filename or "archivo_sin_nombre"
    extension = os.path.splitext(original_filename)[1]
    saved_filename = f"{document_id}{extension}"
    saved_path = os.path.join(storage_path, saved_filename)

    with open(saved_path, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)

    file_hash = calculate_file_hash(saved_path)

    return {
        "document_id": document_id,
        "original_filename": original_filename,
        "saved_path": saved_path,
        "file_hash": file_hash,
        "extension": extension,
        "mime_type": upload_file.content_type
    }

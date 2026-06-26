import mimetypes
import base64
import os
import json
import re
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

MAX_RELEVANT_CONTEXT_CHARS = 12000
MAX_MULTI_DOCS = 10
MAX_MULTI_DOC_CONTEXT = 2500
MAX_EXTRACTION_CONTEXT_CHARS = 18000


def analyze_document_text(prompt: str, document_text: str) -> str:
    safe_text = (document_text or "")[:MAX_RELEVANT_CONTEXT_CHARS]

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Eres un analista documental. "
                            "Responde únicamente con base en el contenido proporcionado. "
                            "Si falta información, dilo claramente. "
                            "Si el contenido incluye referencias de página, úsalas."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"PREGUNTA DEL USUARIO:\n{prompt}\n\n"
                            f"CONTENIDO RELEVANTE DEL DOCUMENTO:\n{safe_text}"
                        ),
                    }
                ],
            },
        ],
    )

    return response.output_text


def analyze_multiple_documents(prompt: str, documents: list[dict]) -> str:
    safe_docs = documents[:MAX_MULTI_DOCS]
    parts = []

    for doc in safe_docs:
        text = (doc.get("text") or "").strip()[:MAX_MULTI_DOC_CONTEXT]
        parts.append(
            f"DOCUMENTO: {doc.get('file_name')}\n"
            f"CONTENIDO RELEVANTE:\n{text}\n"
        )

    combined_text = "\n\n---\n\n".join(parts)

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Eres un analista documental. "
                            "Debes comparar documentos sobre una misma persona, caso o tema. "
                            "No asumas contradicción solo porque un documento tenga menos detalle que otro. "
                            "Debes distinguir entre: "
                            "1) información consistente, "
                            "2) información complementaria, "
                            "3) contradicción real, "
                            "4) evidencia insuficiente. "
                            "Responde de forma clara y útil."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"PREGUNTA DEL USUARIO:\n{prompt}\n\n"
                            f"DOCUMENTOS RELEVANTES:\n{combined_text}\n\n"
                            "Responde con esta estructura:\n"
                            "1. Resumen de cada documento\n"
                            "2. Coincidencias\n"
                            "3. Diferencias\n"
                            "4. Conclusión final"
                        ),
                    }
                ],
            },
        ],
    )

    return response.output_text


def _clean_json_text(text: str) -> str:
    text = (text or "").strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()

    return text


def _build_extraction_schema(field_names: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "field_name": {
                            "type": "string",
                            "enum": field_names,
                        },
                        "raw_value": {
                            "type": ["string", "null"],
                        },
                        "normalized_value": {
                            "type": ["string", "null"],
                        },
                        "source_type": {
                            "type": "string",
                            "enum": [
                                "impreso",
                                "manuscrito",
                                "mixto",
                                "inferido",
                                "no_visible",
                                "desconocido",
                            ],
                        },
                        "confidence_level": {
                            "type": "string",
                            "enum": [
                                "alta",
                                "media",
                                "baja",
                                "ninguna",
                            ],
                        },
                        "status": {
                            "type": "string",
                            "enum": [
                                "ok",
                                "requiere_revision",
                                "ilegible",
                                "inferido",
                                "error_formato",
                                "campo_no_encontrado",
                                "pending_review",
                            ],
                        },
                        "needs_review": {
                            "type": "boolean",
                        },
                        "evidence_text": {
                            "type": ["string", "null"],
                        },
                    },
                    "required": [
                        "field_name",
                        "raw_value",
                        "normalized_value",
                        "source_type",
                        "confidence_level",
                        "status",
                        "needs_review",
                        "evidence_text",
                    ],
                },
            }
        },
        "required": ["fields"],
    }


def extract_fields_from_document_text(
    document_text: str,
    fields: list[dict],
    file_name: str | None = None,
) -> dict:
    """
    Extrae campos estructurados desde texto documental.
    Esta función es conservadora: si el dato no es claro, debe marcar revisión.
    """

    safe_text = (document_text or "").strip()[:MAX_EXTRACTION_CONTEXT_CHARS]

    if not safe_text:
        return {"fields": []}

    field_names = [field["field_name"] for field in fields]

    field_descriptions = []
    for field in fields:
        field_descriptions.append(
            {
                "field_name": field.get("field_name"),
                "display_name": field.get("display_name"),
                "required": field.get("required", False),
                "description": field.get("description"),
                "target_location": field.get("target_location"),
            }
        )

    schema = _build_extraction_schema(field_names)

    system_text = (
        "Eres un sistema conservador de extracción documental. "
        "Tu tarea es extraer campos desde texto obtenido de PDFs, OCR o documentos escaneados. "
        "No inventes datos. "
        "Si un dato no aparece, usa null. "
        "Si un dato parece dudoso, marca needs_review=true. "
        "Si infieres por contexto, usa source_type='inferido' y status='inferido'. "
        "Si el campo no existe en el documento, usa status='campo_no_encontrado'. "
        "Si el dato es ilegible o incompleto, usa status='ilegible' o 'requiere_revision'."
    )

    user_text = (
        f"ARCHIVO:\n{file_name or 'sin_nombre'}\n\n"
        f"CAMPOS A EXTRAER:\n{json.dumps(field_descriptions, ensure_ascii=False, indent=2)}\n\n"
        f"TEXTO DEL DOCUMENTO:\n{safe_text}\n\n"
        "Devuelve todos los campos solicitados. "
        "Cada campo debe tener field_name, raw_value, normalized_value, source_type, "
        "confidence_level, status, needs_review y evidence_text."
    )

    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": system_text,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_text,
                        }
                    ],
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "document_extraction_result",
                    "schema": schema,
                    "strict": True,
                }
            },
        )

        return json.loads(response.output_text)

    except TypeError:
        # Fallback si tu versión del SDK no acepta todavía text.format.
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": system_text,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                user_text
                                + "\n\nIMPORTANTE: Devuelve SOLO JSON válido con esta forma:\n"
                                + json.dumps(
                                    {
                                        "fields": [
                                            {
                                                "field_name": "nombre_del_campo",
                                                "raw_value": "valor bruto o null",
                                                "normalized_value": "valor limpio o null",
                                                "source_type": "impreso/manuscrito/mixto/inferido/no_visible/desconocido",
                                                "confidence_level": "alta/media/baja/ninguna",
                                                "status": "ok/requiere_revision/ilegible/inferido/error_formato/campo_no_encontrado/pending_review",
                                                "needs_review": True,
                                                "evidence_text": "explicación breve o null",
                                            }
                                        ]
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                )
                            ),
                        }
                    ],
                },
            ],
        )

        cleaned = _clean_json_text(response.output_text)
        return json.loads(cleaned)


def _image_file_to_data_url(image_path: str) -> str:
    mime_type, _ = mimetypes.guess_type(image_path)

    if not mime_type:
        mime_type = "image/png"

    with open(image_path, "rb") as image_file:
        b64_image = base64.b64encode(image_file.read()).decode("utf-8")

    return f"data:{mime_type};base64,{b64_image}"


def extract_fields_from_document_image(
    image_path: str,
    fields: list[dict],
    file_name: str | None = None,
    page_number: int | None = None,
    support_text: str | None = None,
) -> dict:
    """
    Extrae campos estructurados desde una imagen de página.
    Este flujo usa visión multimodal.
    """

    field_names = [field["field_name"] for field in fields]

    field_descriptions = []

    for field in fields:
        field_descriptions.append(
            {
                "field_name": field.get("field_name"),
                "display_name": field.get("display_name"),
                "required": field.get("required", False),
                "description": field.get("description"),
                "target_location": field.get("target_location"),
            }
        )

    schema = _build_extraction_schema(field_names)

    image_data_url = _image_file_to_data_url(image_path)

    system_text = (
        "Eres un sistema conservador de extracción documental con visión. "
        "Tu tarea es leer una imagen de documento y extraer campos definidos. "
        "Debes diferenciar texto impreso, texto manuscrito, sellos, firmas y campos vacíos. "
        "No inventes datos. "
        "Si un dato no es claro, devuelve null o el valor parcial y marca needs_review=true. "
        "Si infieres un valor por contexto, usa source_type='inferido' y status='inferido'. "
        "Si el campo no aparece, usa status='campo_no_encontrado'. "
        "Si el dato está borroso, incompleto o ambiguo, usa status='requiere_revision' o 'ilegible'."
    )

    user_text = (
        f"ARCHIVO:\n{file_name or 'sin_nombre'}\n\n"
        f"PÁGINA:\n{page_number or 1}\n\n"
        f"CAMPOS A EXTRAER:\n{json.dumps(field_descriptions, ensure_ascii=False, indent=2)}\n\n"
    )

    if support_text:
        user_text += (
            "TEXTO OCR DE APOYO, PUEDE CONTENER ERRORES:\n"
            f"{support_text[:4000]}\n\n"
        )

    user_text += (
        "Extrae todos los campos solicitados desde la imagen. "
        "Devuelve field_name, raw_value, normalized_value, source_type, "
        "confidence_level, status, needs_review y evidence_text. "
        "Sé más conservador con texto manuscrito o borroso."
    )

    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": system_text,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": user_text,
                        },
                        {
                            "type": "input_image",
                            "image_url": image_data_url,
                        },
                    ],
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "document_image_extraction_result",
                    "schema": schema,
                    "strict": True,
                }
            },
        )

        return json.loads(response.output_text)

    except TypeError:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": system_text,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                user_text
                                + "\n\nIMPORTANTE: Devuelve SOLO JSON válido con esta forma:\n"
                                + json.dumps(
                                    {
                                        "fields": [
                                            {
                                                "field_name": "nombre_del_campo",
                                                "raw_value": "valor bruto o null",
                                                "normalized_value": "valor limpio o null",
                                                "source_type": "impreso/manuscrito/mixto/inferido/no_visible/desconocido",
                                                "confidence_level": "alta/media/baja/ninguna",
                                                "status": "ok/requiere_revision/ilegible/inferido/error_formato/campo_no_encontrado/pending_review",
                                                "needs_review": True,
                                                "evidence_text": "explicación breve o null",
                                            }
                                        ]
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                )
                            ),
                        },
                        {
                            "type": "input_image",
                            "image_url": image_data_url,
                        },
                    ],
                },
            ],
        )

        cleaned = _clean_json_text(response.output_text)
        return json.loads(cleaned)


def predict_failed_field_from_context(
    field_name: str,
    current_raw_value: str | None,
    current_normalized_value: str | None,
    current_status: str | None,
    current_confidence: str | None,
    evidence_text: str | None,
    field_description: str | None,
    same_page_context: list[dict],
    support_text: str | None = None,
    image_path: str | None = None,
) -> dict:
    """
    Segunda capa de predicción lógica.
    Solo debe corregir automáticamente si hay evidencia fuerte.
    Si no hay suficiente evidencia, debe dejar el campo para revisión humana.
    """

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decision": {
                "type": "string",
                "enum": [
                    "auto_corrected",
                    "needs_human_review",
                    "unresolved",
                ],
            },
            "predicted_value": {
                "type": ["string", "null"],
            },
            "normalized_value": {
                "type": ["string", "null"],
            },
            "confidence_level": {
                "type": "string",
                "enum": [
                    "alta",
                    "media",
                    "baja",
                    "ninguna",
                ],
            },
            "status": {
                "type": "string",
                "enum": [
                    "ok",
                    "requiere_revision",
                    "ilegible",
                    "inferido",
                ],
            },
            "needs_review": {
                "type": "boolean",
            },
            "reasoning_summary": {
                "type": ["string", "null"],
            },
        },
        "required": [
            "decision",
            "predicted_value",
            "normalized_value",
            "confidence_level",
            "status",
            "needs_review",
            "reasoning_summary",
        ],
    }

    system_text = (
        "Eres una segunda capa de validación documental. "
        "Tu tarea es revisar campos fallidos o dudosos después de una extracción multimodal. "
        "Debes actuar de forma conservadora. "
        "No inventes datos. "
        "No uses conocimiento externo para completar nombres, números, fechas o documentos. "
        "Solo puedes corregir automáticamente si el valor está claramente respaldado por la imagen, "
        "el OCR de apoyo o el contexto interno del mismo documento. "
        "Si el dato sigue ambiguo, debes marcarlo para revisión humana. "
        "Si no se puede recuperar, márcalo como unresolved o ilegible."
    )

    user_text = (
        f"CAMPO A REVISAR:\n{field_name}\n\n"
        f"DESCRIPCIÓN DEL CAMPO:\n{field_description or 'Sin descripción'}\n\n"
        f"VALOR BRUTO ACTUAL:\n{current_raw_value}\n\n"
        f"VALOR NORMALIZADO ACTUAL:\n{current_normalized_value}\n\n"
        f"ESTADO ACTUAL:\n{current_status}\n\n"
        f"CONFIANZA ACTUAL:\n{current_confidence}\n\n"
        f"EVIDENCIA ACTUAL:\n{evidence_text or 'Sin evidencia'}\n\n"
        f"OTROS CAMPOS DE LA MISMA PÁGINA:\n"
        f"{json.dumps(same_page_context, ensure_ascii=False, indent=2)}\n\n"
    )

    if support_text:
        user_text += (
            "TEXTO OCR DE APOYO, PUEDE CONTENER ERRORES:\n"
            f"{support_text[:5000]}\n\n"
        )

    user_text += (
        "Devuelve una decisión con estas reglas:\n"
        "1. Usa decision='auto_corrected' solo si el valor corregido es claro y está respaldado.\n"
        "2. Usa decision='needs_human_review' si tienes una posible sugerencia, pero no es totalmente segura.\n"
        "3. Usa decision='unresolved' si el dato sigue ilegible o no se puede recuperar.\n"
        "4. Si decision='auto_corrected', confidence_level debe ser 'alta', status debe ser 'ok' y needs_review debe ser false.\n"
        "5. Si hay duda, needs_review debe ser true.\n"
    )

    content = [
        {
            "type": "input_text",
            "text": user_text,
        }
    ]

    if image_path:
        try:
            content.append(
                {
                    "type": "input_image",
                    "image_url": _image_file_to_data_url(image_path),
                }
            )
        except Exception:
            pass

    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": system_text,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": content,
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "failed_field_prediction",
                    "schema": schema,
                    "strict": True,
                }
            },
        )

        return json.loads(response.output_text)

    except TypeError:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": system_text,
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": content
                    + [
                        {
                            "type": "input_text",
                            "text": (
                                "\n\nDevuelve SOLO JSON válido con esta estructura:\n"
                                + json.dumps(
                                    {
                                        "decision": "auto_corrected | needs_human_review | unresolved",
                                        "predicted_value": "valor predicho o null",
                                        "normalized_value": "valor final o null",
                                        "confidence_level": "alta | media | baja | ninguna",
                                        "status": "ok | requiere_revision | ilegible | inferido",
                                        "needs_review": True,
                                        "reasoning_summary": "explicación breve",
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                )
                            ),
                        }
                    ],
                },
            ],
        )

        cleaned = _clean_json_text(response.output_text)
        return json.loads(cleaned)

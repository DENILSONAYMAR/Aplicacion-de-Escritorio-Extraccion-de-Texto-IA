import base64
import io
import json
import os
import re
from dataclasses import dataclass
from typing import Any

import fitz
from PIL import Image
from openai import OpenAI
from sqlalchemy.orm import Session

from app import models


MAX_IMAGE_LONG_EDGE = int(os.getenv("VISION_MAX_IMAGE_LONG_EDGE", "1600"))
JPEG_QUALITY = int(os.getenv("VISION_JPEG_QUALITY", "68"))
VISION_DETAIL = os.getenv("OPENAI_VISION_DETAIL", "low")
VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini")
MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "3000"))
MAX_OCR_CONTEXT_CHARS = int(os.getenv("OPENAI_MAX_OCR_CONTEXT_CHARS", "7000"))
MAX_USER_CONTEXT_CHARS = int(os.getenv("OPENAI_MAX_USER_CONTEXT_CHARS", "3500"))
MAX_FIELD_INSTRUCTION_CHARS = int(os.getenv("OPENAI_MAX_FIELD_INSTRUCTION_CHARS", "700"))

INTERNAL_CONTEXT_MARKER = "--- CONTEXTO INTERNO DEL SISTEMA ---"

GENERAL_DOCUMENT_CRITERIA = """
CRITERIOS GENERALES DE ANÁLISIS DOCUMENTAL

Actúa como un extractor documental inteligente. Tu tarea no es copiar el primer texto parecido, sino entender el rol de cada dato dentro del documento.

1. Identifica primero el tipo de documento:
- factura, boleta, ticket, recibo, guía de remisión, contrato, formulario, estado de cuenta, bono, pasaje, constancia, reporte, solicitud u otro.
- Usa el diseño visual, títulos, cabeceras, secciones, logos, RUC, fechas, montos y etiquetas para entender el documento.

2. Distingue roles documentales:
- emisor, proveedor o empresa que brinda el servicio;
- cliente, comprador, receptor o consumidor;
- beneficiario, trabajador, pasajero o usuario;
- remitente, destinatario, transportista o punto de entrega;
- entidad financiera, titular, cuenta, movimiento, cargo, abono o saldo;
- producto, servicio, concepto, categoría, monto, impuesto, fecha, código o identificador.

3. No confundas proveedor con cliente:
- En comprobantes, tickets, pasajes o recibos, el proveedor suele aparecer arriba, cerca del logo, nombre comercial, RUC y dirección principal.
- El cliente o receptor suele aparecer debajo del número de comprobante o en campos como cliente, razón social, RUC cliente, señor(es), comprador, receptor o datos del usuario.
- Si el campo solicitado pide "empresa que brinda el servicio", "proveedor", "emisor" o "empresa del servicio", usa el proveedor/emisor, no el cliente.

4. Documentos de transporte:
- La empresa de transporte normalmente es el proveedor del servicio.
- El pasajero, trabajador o empresa compradora no es el proveedor.
- Ruta, asiento, embarque, fecha de viaje, hora de viaje, DNI y servicio son datos operativos del pasaje.

5. Guías de remisión:
- Diferencia remitente, destinatario, transportista, punto de partida, punto de llegada, motivo de traslado, bienes transportados y fecha de traslado.
- No mezcles transportista con destinatario.

6. Estados de cuenta:
- Diferencia entidad financiera, titular, número de cuenta, periodo, saldo inicial, movimientos, cargos, abonos y saldo final.
- No confundas un comercio dentro de un movimiento con el banco emisor.

7. Comprobantes laborales, bonos o formularios:
- Diferencia entidad emisora, trabajador o beneficiario, periodo, concepto, monto, cargo, área y observaciones.
- Si el dato pertenece al trabajador, no lo confundas con la empresa emisora.

8. Reglas numéricas:
- DNI peruano normalmente tiene 8 dígitos.
- RUC peruano normalmente tiene 11 dígitos.
- Si un DNI aparece con 9 dígitos y el primer dígito parece ruido visual, evalúa si los últimos 8 dígitos forman el DNI más probable.
- No agregues dígitos que no existan visualmente.
- Si el campo es RUC, no lo confundas con DNI.
- Si el campo es total, prioriza etiquetas cercanas a TOTAL, TOTAL S/, IMPORTE TOTAL o equivalentes.

9. Reglas de encabezados:
- Lo escrito entre paréntesis () representa validaciones estrictas, opciones permitidas o reglas cerradas.
- Lo escrito entre corchetes [] representa contexto libre, explicación o criterio de interpretación.
- Si existen opciones estrictas, responde usando una opción válida cuando corresponda.
- Si ninguna opción aplica razonablemente, responde "no aplica".
- Si el usuario pide una categoría, clasifica según el contexto documental y no solo por coincidencia literal.

10. Base de contexto:
- Si existe una base Excel de referencia, úsala como apoyo contextual.
- La base puede contener empresas, categorías, direcciones, conceptos, consumos, centros de costo, proveedores, clientes u otros datos frecuentes.
- No inventes valores usando la base. Úsala solo cuando haya relación razonable con el documento.

11. Control de incertidumbre:
- Si el dato no existe en el documento, responde exactamente: no aplica.
- Si el dato es parcialmente ilegible pero puede inferirse con alta seguridad por contexto, puedes proponerlo.
- Si hay duda importante, usa confianza "baja" o estado "requiere_revision".
- No confundas inferencia razonable con invención.
"""

GENERAL_DOCUMENT_CRITERIA = (
    "Eres un extractor documental conservador. Identifica el tipo de documento y "
    "distingue roles: emisor/proveedor, cliente/receptor, beneficiario, remitente, "
    "destinatario, transportista, banco, titular, producto, concepto, fecha, monto "
    "e identificadores. No confundas proveedor con cliente; en comprobantes el proveedor "
    "suele estar en cabecera/logo/RUC y el cliente debajo del numero o etiqueta. En "
    "transporte, la empresa es proveedor y el pasajero no. En guias, diferencia remitente, "
    "destinatario y transportista. En estados de cuenta, diferencia banco, titular, cargos, "
    "abonos y comercios. DNI peruano: 8 digitos; RUC: 11 digitos. Para totales, prioriza "
    "TOTAL/IMPORTE TOTAL. Usa validaciones del usuario solo con evidencia razonable. "
    "No inventes; si falta evidencia responde no visible. Si hay duda importante, confianza "
    "baja y requiere revision."
)


@dataclass
class FieldSpec:
    field_name: str
    visible_instruction: str
    internal_instruction: str


@dataclass
class PreparedDocument:
    document_id: str
    file_name: str
    file_path: str
    extracted_text: str
    page_count: int
    images: list[dict[str, Any]]
    text_native: bool = False


def _get_attr(obj, names: list[str], default=None):
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value

    return default


def _set_attr_if_exists(obj, name: str, value):
    if hasattr(obj, name):
        setattr(obj, name, value)


def _clean_text(text: Any) -> str:
    if text is None:
        return ""

    return str(text).strip()


def _truncate_text(text: str, max_chars: int) -> str:
    text = _clean_text(text)

    if len(text) <= max_chars:
        return text

    return text[:max_chars].rsplit(" ", 1)[0].strip()


def _split_description(description: str | None, fallback_name: str) -> tuple[str, str]:
    text = _clean_text(description)

    if not text:
        return fallback_name, ""

    if INTERNAL_CONTEXT_MARKER in text:
        visible, internal = text.split(INTERNAL_CONTEXT_MARKER, 1)
        return visible.strip() or fallback_name, internal.strip()

    return text, ""


def _extract_user_context_from_internal(internal: str) -> str:
    if not internal:
        return ""

    marker = "BASE DE CONTEXTO GENERAL CARGADA POR EL USUARIO:"

    if marker not in internal:
        return ""

    return internal.split(marker, 1)[1].strip()


def _extract_field_specific_instruction(internal: str) -> str:
    """
    Si el frontend antiguo guardó el prompt general dentro de cada campo,
    aquí lo removemos para no gastar tokens repetidos.
    """

    if not internal:
        return ""

    base_context_marker = "BASE DE CONTEXTO GENERAL CARGADA POR EL USUARIO:"

    if base_context_marker in internal:
        internal = internal.split(base_context_marker, 1)[0].strip()

    field_marker = "Campo solicitado:"

    if field_marker in internal:
        return field_marker + internal.split(field_marker, 1)[1].strip()

    return internal.strip()


def _build_field_specs(template_fields: list[models.ExtractionTemplateField]) -> tuple[list[FieldSpec], str]:
    fields: list[FieldSpec] = []
    context_blocks: list[str] = []
    seen_contexts = set()

    for field in template_fields:
        field_name = _clean_text(field.field_name)

        if not field_name:
            continue

        visible, internal = _split_description(field.description, field_name)

        context_block = _extract_user_context_from_internal(internal)

        if context_block and context_block not in seen_contexts:
            seen_contexts.add(context_block)
            context_blocks.append(context_block)

        field_instruction = _extract_field_specific_instruction(internal)

        if not field_instruction:
            field_instruction = visible

        fields.append(
            FieldSpec(
                field_name=field_name,
                visible_instruction=visible,
                internal_instruction=field_instruction,
            )
        )

    return fields, "\n\n".join(context_blocks).strip()


def _resize_image(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    width, height = image.size
    long_edge = max(width, height)

    if long_edge <= MAX_IMAGE_LONG_EDGE:
        return image

    scale = MAX_IMAGE_LONG_EDGE / long_edge
    new_size = (int(width * scale), int(height * scale))

    return image.resize(new_size)


def _image_to_data_url(image: Image.Image) -> str:
    image = _resize_image(image)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)

    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return f"data:image/jpeg;base64,{encoded}"


def _is_text_native_pdf(text_parts: list[str], page_count: int) -> bool:
    text = "\n".join(text_parts)
    visible_chars = len(re.sub(r"\s+", "", text))

    return visible_chars >= max(350, page_count * 180)


def _prepare_pdf_document(document, max_pages: int) -> PreparedDocument:
    file_path = _get_attr(document, ["original_path", "file_path", "path"])
    file_name = _get_attr(
        document, ["file_name", "filename", "name"], "documento.pdf")
    document_id = str(_get_attr(document, ["id"]))

    if not file_path or not os.path.exists(file_path):
        raise ValueError(
            f"No existe el archivo físico del documento: {file_name}")

    pdf = fitz.open(file_path)
    page_count = pdf.page_count

    if page_count > max_pages:
        pdf.close()
        raise ValueError(
            f"El archivo '{file_name}' tiene {page_count} paginas. "
            f"Este flujo admite maximo {max_pages} paginas por archivo."
        )

    text_parts = []

    for index in range(page_count):
        page = pdf[index]

        page_text = page.get_text("text") or ""

        if page_text.strip():
            text_parts.append(f"[Página {index + 1}]\n{page_text.strip()}")

    text_native = _is_text_native_pdf(text_parts, page_count)
    images = []

    if not text_native:
        for image_index in range(page_count):
            page = pdf[image_index]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png")))

            images.append(
                {
                    "page_number": image_index + 1,
                    "image_url": _image_to_data_url(image),
                }
            )

    pdf.close()

    return PreparedDocument(
        document_id=document_id,
        file_name=file_name,
        file_path=file_path,
        extracted_text="\n\n".join(text_parts).strip(),
        page_count=page_count,
        images=images,
        text_native=text_native,
    )


def _prepare_image_document(document) -> PreparedDocument:
    file_path = _get_attr(document, ["original_path", "file_path", "path"])
    file_name = _get_attr(
        document, ["file_name", "filename", "name"], "imagen")
    document_id = str(_get_attr(document, ["id"]))

    if not file_path or not os.path.exists(file_path):
        raise ValueError(
            f"No existe el archivo físico del documento: {file_name}")

    image = Image.open(file_path)

    return PreparedDocument(
        document_id=document_id,
        file_name=file_name,
        file_path=file_path,
        extracted_text="",
        page_count=1,
        images=[
            {
                "page_number": 1,
                "image_url": _image_to_data_url(image),
            }
        ],
        text_native=False,
    )


def _prepare_document(document, max_pages: int) -> PreparedDocument:
    file_name = _get_attr(document, ["file_name", "filename", "name"], "")
    lower_name = file_name.lower()

    if lower_name.endswith(".pdf"):
        prepared = _prepare_pdf_document(document, max_pages=max_pages)
    else:
        prepared = _prepare_image_document(document)

    if prepared.page_count > max_pages:
        raise ValueError(
            f"El archivo '{prepared.file_name}' tiene {prepared.page_count} páginas. "
            f"Este flujo admite máximo {max_pages} páginas por archivo."
        )

    prepared.images = prepared.images[:max_pages]

    return prepared


def _build_user_prompt(
    prepared: PreparedDocument,
    fields: list[FieldSpec],
    user_context: str,
) -> str:
    field_lines = []

    for index, field in enumerate(fields, start=1):
        field_lines.append(
            json.dumps(
                {
                    "index": index,
                    "field_name": field.field_name,
                    "visible_instruction": field.visible_instruction,
                    "analysis_instruction": field.internal_instruction,
                },
                ensure_ascii=False,
            )
        )

    context_block = user_context.strip(
    ) if user_context else "Sin base de contexto adicional."

    extracted_text = prepared.extracted_text.strip()

    if not extracted_text:
        extracted_text = "No hay texto OCR confiable disponible. Usa principalmente la imagen."

    return f"""
Analiza el documento completo y extrae todos los campos solicitados en una sola respuesta.

Archivo: {prepared.file_name}
Páginas enviadas: {len(prepared.images)}

BASE DE CONTEXTO DEL USUARIO:
{context_block}

TEXTO OCR DISPONIBLE:
{extracted_text[:12000]}

CAMPOS SOLICITADOS:
{chr(10).join(field_lines)}

FORMATO DE RESPUESTA OBLIGATORIO:
Responde solo JSON válido, sin markdown, sin explicación externa.

Estructura exacta:
{{
  "document_type": "tipo de documento detectado",
  "document_summary": "resumen breve del documento",
  "fields": [
    {{
      "field_name": "nombre exacto del campo solicitado",
      "raw_value": "valor leído o no aplica",
      "normalized_value": "valor final limpio o no aplica",
      "source_type": "visual|ocr|inferido|no_visible",
      "confidence_level": "alta|media|baja|ninguna",
      "status": "ok|requiere_revision|campo_no_encontrado",
      "needs_review": true,
      "evidence_text": "evidencia breve, máximo 160 caracteres",
      "page_number": 1
    }}
  ]
}}

Reglas finales:
- Devuelve exactamente un objeto por cada campo solicitado.
- No inventes datos.
- Si no encuentras un dato, usa "no aplica".
- Si el dato fue inferido con duda, usa confidence_level "baja" y needs_review true.
- Si hay validaciones estrictas entre paréntesis, respétalas.
"""

def _build_user_prompt(
    prepared: PreparedDocument,
    fields: list[FieldSpec],
    user_context: str,
) -> str:
    field_lines = []

    for index, field in enumerate(fields, start=1):
        field_lines.append(
            json.dumps(
                {
                    "i": index,
                    "name": field.field_name,
                    "ask": _truncate_text(
                        field.internal_instruction or field.visible_instruction,
                        MAX_FIELD_INSTRUCTION_CHARS,
                    ),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    context_block = (
        _truncate_text(user_context, MAX_USER_CONTEXT_CHARS)
        if user_context
        else "Sin base de contexto adicional."
    )

    extracted_text = prepared.extracted_text.strip()

    if not extracted_text:
        extracted_text = "No hay texto OCR confiable disponible. Usa principalmente la imagen."
    else:
        extracted_text = _truncate_text(extracted_text, MAX_OCR_CONTEXT_CHARS)

    analysis_mode = (
        "PDF digital con texto embebido; analiza el texto y su contexto sin imagen para ahorrar costo."
        if prepared.text_native
        else "Documento visual o escaneado; usa imagen y OCR disponible."
    )

    return f"""
Extrae todos los campos del documento en una sola respuesta JSON.

Archivo: {prepared.file_name}
Paginas: {prepared.page_count}
Modo: {analysis_mode}

Contexto usuario:
{context_block}

OCR disponible:
{extracted_text}

Campos solicitados, usa name como field_name exacto:
{chr(10).join(field_lines)}

Reglas:
- Devuelve exactamente un objeto por campo.
- Proceso interno obligatorio por campo: primero lee el texto bruto visible/OCR, luego mira contexto cercano (izquierda, derecha, arriba, abajo, cabecera, tabla o bloque), y recien despues normaliza contra el ITEM del usuario.
- No inventes ni completes por deseo del usuario: usa evidencia documental visible/OCR. La validacion Excel o el prompt del ITEM solo ayudan a elegir entre opciones cuando existe evidencia en el documento.
- Si el campo no aparece, raw_value y normalized_value deben ser "no visible".
- Si aparece pero es borroso, incompleto o tiene probabilidad muy baja, usa "ilegible", confidence_level="baja" o "ninguna", status="ilegible", needs_review=true.
- Usa "no aplica" solo cuando el dato realmente no corresponde al tipo de documento o a una validacion cerrada.
- Si hay duda recuperable por contexto cercano, propone el valor mas probable, source_type="inferido", confidence_level="baja", status="requiere_revision", needs_review=true.
- Si no aparece, source_type="no_visible", confidence_level="ninguna", status="campo_no_encontrado", needs_review=true.
- evidence_text debe incluir evidencia y ubicacion/contexto breve, maximo 120 caracteres. Ejemplo: "Etiqueta RUC en cabecera derecha; valor debajo".
"""


def _build_response_schema(field_names: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document_type": {"type": ["string", "null"]},
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "field_name": {"type": "string", "enum": field_names},
                        "raw_value": {"type": "string"},
                        "normalized_value": {"type": "string"},
                        "source_type": {
                            "type": "string",
                            "enum": ["visual", "ocr", "inferido", "no_visible"],
                        },
                        "confidence_level": {
                            "type": "string",
                            "enum": ["alta", "media", "baja", "ninguna"],
                        },
                        "status": {
                            "type": "string",
                            "enum": ["ok", "requiere_revision", "campo_no_encontrado", "ilegible"],
                        },
                        "needs_review": {"type": "boolean"},
                        "evidence_text": {"type": "string"},
                        "page_number": {"type": ["integer", "null"]},
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
                        "page_number",
                    ],
                },
            },
        },
        "required": ["document_type", "fields"],
    }


def _max_output_tokens_for_fields(field_count: int) -> int:
    dynamic_limit = 500 + (max(field_count, 1) * 170)
    return min(MAX_OUTPUT_TOKENS, max(1000, dynamic_limit))


def _extract_json_from_text(text: str) -> dict[str, Any]:
    text = (text or "").strip()

    if not text:
        raise ValueError("La IA devolvió una respuesta vacía")

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{[\s\S]*\}", text)

    if not match:
        raise ValueError(
            f"No se pudo extraer JSON de la respuesta: {text[:500]}")

    return json.loads(match.group(0))


def _normalize_value_by_field(field_name: str, value: Any) -> str:
    if value is None:
        return "no visible"

    field = (field_name or "").lower()
    text = str(value).strip()

    if not text:
        return "no visible"

    lower_text = text.lower()

    if lower_text in ["null", "none", "nan", "no encontrado", "[campo no encontrado]"]:
        return "no visible"

    if lower_text in ["ilegible", "[ilegible]", "no visible"]:
        return lower_text

    digits = re.sub(r"\D", "", text)

    if "dni" in field or "documento de identidad" in field:
        if len(digits) == 8:
            return digits

        if len(digits) > 8:
            return digits[-8:]

        return text

    if "ruc" in field:
        if len(digits) == 11:
            return digits

        if len(digits) > 11:
            return digits[:11]

        return text

    return text


def _safe_confidence(value: Any) -> str:
    value = str(value or "").lower().strip()

    if value in ["alta", "media", "baja", "ninguna"]:
        return value

    return "baja"


def _safe_status(value: Any, normalized_value: str) -> str:
    value = str(value or "").lower().strip()

    if normalized_value == "ilegible":
        return "ilegible"

    if normalized_value == "no visible":
        return "campo_no_encontrado"

    if value in ["ok", "requiere_revision", "campo_no_encontrado", "ilegible"]:
        return value

    return "requiere_revision"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.lower().strip() in ["true", "1", "yes", "si", "sí"]

    return bool(value)


def _ensure_all_fields_present(
    ai_fields: list[dict[str, Any]],
    requested_fields: list[FieldSpec],
) -> list[dict[str, Any]]:
    by_name = {
        str(item.get("field_name", "")).strip().lower(): item
        for item in ai_fields
        if item.get("field_name")
    }

    completed = []

    for field in requested_fields:
        key = field.field_name.lower()

        if key in by_name:
            completed.append(by_name[key])
        else:
            completed.append(
                {
                    "field_name": field.field_name,
                    "raw_value": "no visible",
                    "normalized_value": "no visible",
                    "source_type": "no_visible",
                    "confidence_level": "ninguna",
                    "status": "campo_no_encontrado",
                    "needs_review": True,
                    "evidence_text": "Campo no encontrado en el documento.",
                    "page_number": None,
                }
            )

    return completed


def analyze_document_one_request(
    document,
    template_fields: list[models.ExtractionTemplateField],
    max_pages: int,
) -> dict[str, Any]:
    client = OpenAI()

    fields, user_context = _build_field_specs(template_fields)

    if not fields:
        raise ValueError("No hay encabezados para extraer")

    prepared = _prepare_document(document, max_pages=max_pages)

    content = [
        {
            "type": "input_text",
            "text": _build_user_prompt(
                prepared=prepared,
                fields=fields,
                user_context=user_context,
            ),
        }
    ]

    for image in prepared.images:
        content.append(
            {
                "type": "input_image",
                "image_url": image["image_url"],
                "detail": VISION_DETAIL,
            }
        )

    request_payload = {
        "model": VISION_MODEL,
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": GENERAL_DOCUMENT_CRITERIA,
                    }
                ],
            },
            {
                "role": "user",
                "content": content,
            },
        ],
        "temperature": 0.1,
        "max_output_tokens": _max_output_tokens_for_fields(len(fields)),
    }

    try:
        response = client.responses.create(
            **request_payload,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "document_vision_extraction",
                    "schema": _build_response_schema(
                        [field.field_name for field in fields]
                    ),
                    "strict": True,
                }
            },
        )
    except TypeError:
        response = client.responses.create(**request_payload)

    output_text = getattr(response, "output_text", "")
    data = _extract_json_from_text(output_text)

    ai_fields = data.get("fields", [])

    if not isinstance(ai_fields, list):
        ai_fields = []

    completed_fields = _ensure_all_fields_present(
        ai_fields=ai_fields,
        requested_fields=fields,
    )

    return {
        "document_id": prepared.document_id,
        "file_name": prepared.file_name,
        "page_count": prepared.page_count,
        "document_type": data.get("document_type"),
        "fields": completed_fields,
    }


def save_analysis_results(
    db: Session,
    job_id: int,
    analysis: dict[str, Any],
) -> int:
    created = 0

    for item in analysis["fields"]:
        field_name = _clean_text(item.get("field_name"))
        raw_value = item.get("raw_value")
        normalized_value = _normalize_value_by_field(
            field_name=field_name,
            value=item.get("normalized_value") or raw_value,
        )

        source_type = str(item.get("source_type") or "visual").lower().strip()
        if source_type not in ["visual", "ocr", "inferido", "no_visible"]:
            source_type = "visual"

        confidence_level = _safe_confidence(item.get("confidence_level"))
        status = _safe_status(item.get("status"), normalized_value)
        evidence_text = _clean_text(item.get("evidence_text"))[:300]

        needs_review = _as_bool(item.get("needs_review"))

        if source_type in ["inferido", "no_visible"]:
            needs_review = True

            if source_type == "inferido" and status == "ok":
                status = "requiere_revision"

            if source_type == "no_visible":
                confidence_level = "ninguna"
                status = "campo_no_encontrado"

        if status == "ok" and not evidence_text:
            status = "requiere_revision"
            needs_review = True

        if confidence_level in ["media", "baja", "ninguna"]:
            needs_review = True

        if status != "ok" and status != "campo_no_encontrado":
            needs_review = True

        if status == "campo_no_encontrado" and normalized_value not in ["no visible", "ilegible"]:
            status = "requiere_revision"
            needs_review = True

        result = models.ExtractionResult()

        _set_attr_if_exists(result, "job_id", job_id)
        _set_attr_if_exists(result, "document_id", analysis.get("document_id"))
        _set_attr_if_exists(result, "page_id", None)
        _set_attr_if_exists(result, "file_name", analysis.get("file_name"))
        _set_attr_if_exists(result, "page_number", item.get("page_number"))
        _set_attr_if_exists(result, "field_name", field_name)
        _set_attr_if_exists(result, "raw_value", raw_value)
        _set_attr_if_exists(result, "normalized_value", normalized_value)
        _set_attr_if_exists(result, "source_type", source_type)
        _set_attr_if_exists(result, "confidence_level", confidence_level)
        _set_attr_if_exists(result, "status", status)
        _set_attr_if_exists(result, "needs_review", needs_review)
        _set_attr_if_exists(result, "evidence_text", evidence_text)

        db.add(result)
        created += 1

    db.commit()

    return created


def run_vision_extraction_for_job(
    db: Session,
    job_id: int,
    template_id: int,
    document_ids: list[str],
    max_pages_per_document: int = 10,
) -> dict[str, Any]:
    job = (
        db.query(models.ExtractionJob)
        .filter(models.ExtractionJob.id == job_id)
        .first()
    )

    if not job:
        raise ValueError("Job de extracción no encontrado")

    template_fields = (
        db.query(models.ExtractionTemplateField)
        .filter(models.ExtractionTemplateField.template_id == template_id)
        .order_by(models.ExtractionTemplateField.id.asc())
        .all()
    )

    if not template_fields:
        raise ValueError("La plantilla no tiene encabezados")

    documents = (
        db.query(models.Document)
        .filter(models.Document.id.in_(document_ids))
        .all()
    )

    if not documents:
        raise ValueError("No hay documentos para procesar")

    processed_files = 0
    failed_files = 0
    processed_pages = 0
    created_results = 0
    errors = []

    _set_attr_if_exists(job, "status", "running")
    db.commit()

    for document in documents:
        try:
            analysis = analyze_document_one_request(
                document=document,
                template_fields=template_fields,
                max_pages=max_pages_per_document,
            )

            created_results += save_analysis_results(
                db=db,
                job_id=job_id,
                analysis=analysis,
            )

            processed_files += 1
            processed_pages += int(analysis.get("page_count") or 1)

        except Exception as e:
            failed_files += 1
            file_name = _get_attr(
                document, ["file_name", "filename", "name"], "documento")
            errors.append(f"{file_name}: {str(e)}")

    status = "completed" if failed_files == 0 else "completed_with_errors"

    _set_attr_if_exists(job, "status", status)
    _set_attr_if_exists(job, "processed_files", processed_files)
    _set_attr_if_exists(job, "failed_files", failed_files)

    if errors:
        _set_attr_if_exists(job, "error_message", "\n".join(errors[:10]))

    db.commit()
    db.refresh(job)

    pending_review = (
        db.query(models.ExtractionResult)
        .filter(
            models.ExtractionResult.job_id == job_id,
            models.ExtractionResult.needs_review == True,
        )
        .count()
    )

    unresolved = (
        db.query(models.ExtractionResult)
        .filter(
            models.ExtractionResult.job_id == job_id,
            models.ExtractionResult.status == "campo_no_encontrado",
        )
        .count()
    )

    return {
        "job_id": job_id,
        "status": status,
        "processed_files": processed_files,
        "failed_files": failed_files,
        "processed_pages": processed_pages,
        "created_results": created_results,
        "predicted_updates": 0,
        "prediction_review_pending": pending_review,
        "prediction_unresolved": unresolved,
        "errors": errors,
        "message": "Extracción optimizada ejecutada: una solicitud multimodal por documento.",
    }

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import "./App.css";

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";
const MAX_PAGES_PER_DOCUMENT = 10;
const INTERNAL_CONTEXT_MARKER = "\n\n--- CONTEXTO INTERNO DEL SISTEMA ---\n";

type ModuleName = "data_extraction" | "long_text";
type ResultViewMode = "all" | "doubtful";
type ResultType = "" | "date" | "time" | "text" | "decimal" | "integer" | "calculation";
type ReviewDecision = "validated" | "illegible" | "no_visible";

type DocumentItem = {
  id: string;
  file_name: string;
  original_path: string;
  mime_type?: string | null;
  file_hash?: string | null;
  status: string;
  created_at?: string | null;
};

type ExtractionProject = {
  id: number;
  name: string;
  input_folder?: string | null;
  output_folder?: string | null;
  status: string;
  created_at?: string | null;
};

type ExtractionTemplateField = {
  id?: number;
  template_id?: number;
  field_name: string;
  display_name?: string | null;
  target_location?: string | null;
  required?: boolean;
  description?: string | null;
};

type ExtractionTemplate = {
  id: number;
  project_id?: number | null;
  name: string;
  file_path?: string | null;
  template_type: string;
  created_at?: string | null;
  fields: ExtractionTemplateField[];
};

type ExtractionJob = {
  id: number;
  project_id: number;
  status: string;
  total_files: number;
  processed_files: number;
  failed_files: number;
  error_message?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

type ExtractionResult = {
  id: number;
  job_id: number;
  document_id?: string | null;
  page_id?: number | null;
  file_name?: string | null;
  page_number?: number | null;
  field_name: string;
  raw_value?: string | null;
  normalized_value?: string | null;
  source_type?: string | null;
  confidence_level?: string | null;
  status: string;
  needs_review: boolean;
  evidence_text?: string | null;
  created_at?: string | null;
};

type ExportFile = {
  id: number;
  job_id: number;
  document_id?: string | null;
  export_type: string;
  file_path: string;
  has_warnings: boolean;
  created_at?: string | null;
};

type HeaderRow = {
  id: string;
  name: string;
  context: string;
  resultType: ResultType;
};

type ValidationOption = {
  raw: string;
  value: string;
  context?: string | null;
};

type ValidationItem = {
  item_index: number;
  item_name: string;
  options: ValidationOption[];
};

type ReviewModalState = {
  result: ExtractionResult;
} | null;

type PreviewModalState = {
  documentKey: string;
  items: ExtractionResult[];
} | null;

type PreviewPage = {
  page_number: number;
  url: string;
};

function createId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }

  return `${Date.now()}-${Math.random()}`;
}

function createEmptyHeader(): HeaderRow {
  return {
    id: createId(),
    name: "",
    context: "",
    resultType: "",
  };
}

function resultTypeLabel(type: ResultType) {
  const labels: Record<ResultType, string> = {
    "": "Sin tipo seleccionado",
    date: "Fecha",
    time: "Hora",
    text: "Texto",
    decimal: "Decimal",
    integer: "Número",
    calculation: "Cálculo",
  };

  return labels[type];
}

function resultTypeInstruction(type: ResultType) {
  const instructions: Record<ResultType, string> = {
    "": "El usuario no seleccionó tipo de resultado. Marca revisión.",
    date: "El resultado final debe ser una fecha limpia. Usa formato DD/MM/AAAA cuando sea posible.",
    time: "El resultado final debe ser una hora limpia. Usa formato HH:MM cuando sea posible.",
    text: "El resultado final debe ser texto limpio. Conserva nombres, direcciones, códigos e IDs sin explicación adicional.",
    decimal: "El resultado final debe ser un número decimal limpio. Úsalo para dinero o precios con centavos; conserva el símbolo de moneda solo si está visible.",
    integer: "El resultado final debe ser un número entero limpio. No agregues símbolos, unidades ni explicación adicional.",
    calculation: "El resultado final debe indicar si el cálculo o suma solicitada es correcto. Devuelve un valor corto y verificable.",
  };

  return instructions[type];
}

function normalizeValue(value?: string | null) {
  const clean = String(value ?? "").trim();

  if (!clean) return "no visible";

  return clean;
}

function isPendingResult(result: ExtractionResult) {
  const status = String(result.status || "").toLowerCase();
  const confidence = String(result.confidence_level || "").toLowerCase();

  return (
    result.needs_review ||
    status !== "ok" ||
    ["media", "baja", "ninguna"].includes(confidence)
  );
}

function classifyResult(result: ExtractionResult) {
  const finalValue = normalizeValue(result.normalized_value || result.raw_value).toLowerCase();
  const status = String(result.status || "").toLowerCase();
  const confidence = String(result.confidence_level || "").toLowerCase();
  const sourceType = String(result.source_type || "").toLowerCase();

  if (status === "ok" && !result.needs_review && confidence === "alta") {
    return "valid";
  }

  if (
    sourceType === "no_visible" ||
    status === "campo_no_encontrado" ||
    confidence === "ninguna"
  ) {
    return "no_visible";
  }

  if (finalValue === "no aplica") {
    return "doubtful";
  }

  if (
    result.needs_review ||
    status !== "ok" ||
    ["media", "baja"].includes(confidence)
  ) {
    return "doubtful";
  }

  return "valid";
}

function validationToText(options: ValidationOption[]) {
  if (!options.length) return "";

  return options
    .map((item) => {
      if (item.context) {
        return `${item.value} (${item.context})`;
      }

      return item.value;
    })
    .join("; ");
}

function isHeaderComplete(header: HeaderRow) {
  return header.name.trim().length > 0 && header.resultType !== "";
}

function App() {
  const [activeModule, setActiveModule] = useState<ModuleName>("data_extraction");
  const [backendStatus, setBackendStatus] = useState("verificando...");

  const [sessionDocuments, setSessionDocuments] = useState<DocumentItem[]>([]);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);

  const [headerRows, setHeaderRows] = useState<HeaderRow[]>([createEmptyHeader()]);
  const [validationItems, setValidationItems] = useState<ValidationItem[]>([]);
  const [validationFileName, setValidationFileName] = useState("");
  const [incompleteHeaderIds, setIncompleteHeaderIds] = useState<string[]>([]);

  const [currentJob, setCurrentJob] = useState<ExtractionJob | null>(null);
  const [temporaryTemplateId, setTemporaryTemplateId] = useState<number | null>(null);

  const [results, setResults] = useState<ExtractionResult[]>([]);
  const [resultViewMode, setResultViewMode] = useState<ResultViewMode>("all");
  const [expandedDocuments, setExpandedDocuments] = useState<Record<string, boolean>>({});
  const [reviewModal, setReviewModal] = useState<ReviewModalState>(null);
  const [previewModal, setPreviewModal] = useState<PreviewModalState>(null);
  const [showValidationTutorial, setShowValidationTutorial] = useState(false);
  const [showResultTypeHelp, setShowResultTypeHelp] = useState(false);

  const [reviewValue, setReviewValue] = useState("");
  const [reviewNotes, setReviewNotes] = useState("");
  const [shouldSaveReviewNotes, setShouldSaveReviewNotes] = useState(false);
  const [reviewDecision, setReviewDecision] = useState<ReviewDecision>("validated");
  const [previewZoom, setPreviewZoom] = useState(1);
  const [previewPages, setPreviewPages] = useState<PreviewPage[]>([]);
  const [previewPagesLoading, setPreviewPagesLoading] = useState(false);

  const [exportFile, setExportFile] = useState<ExportFile | null>(null);

  const [isUploading, setIsUploading] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [message, setMessage] = useState("");

  const [draggedHeaderId, setDraggedHeaderId] = useState<string | null>(null);
  const [expandedHeaderId, setExpandedHeaderId] = useState<string | null>(null);

  const [progress, setProgress] = useState(0);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  const extractionAbortControllerRef = useRef<AbortController | null>(null);

  const selectedDocuments = useMemo(() => {
    const selected = new Set(selectedDocumentIds);
    return sessionDocuments.filter((doc) => selected.has(doc.id));
  }, [sessionDocuments, selectedDocumentIds]);

  const validHeaders = useMemo(() => {
    return headerRows.filter((item) => item.name.trim().length > 0);
  }, [headerRows]);

  useEffect(() => {
    if (!previewModal) {
      setPreviewPages([]);
      setPreviewPagesLoading(false);
      return;
    }

    void loadPreviewPages(previewModal.items);
  }, [previewModal?.documentKey]);

  const resultsByDocument = useMemo(() => {
    const grouped: Record<string, ExtractionResult[]> = {};

    for (const result of results) {
      const key = result.document_id || result.file_name || `resultado-${result.id}`;

      if (!grouped[key]) {
        grouped[key] = [];
      }

      grouped[key].push(result);
    }

    return grouped;
  }, [results]);

  const resultStats = useMemo(() => {
    const stats = {
      valid: 0,
      doubtful: 0,
      no_visible: 0,
      total: results.length,
    };

    for (const result of results) {
      const category = classifyResult(result);
      stats[category] += 1;
    }

    return stats;
  }, [results]);

  useEffect(() => {
    checkBackend();
  }, []);

  useEffect(() => {
    let timer: number | undefined;

    if (isProcessing) {
      const startedAt = Date.now();
      setProgress(8);
      setElapsedSeconds(0);

      timer = window.setInterval(() => {
        const seconds = Math.floor((Date.now() - startedAt) / 1000);
        setElapsedSeconds(seconds);

        setProgress((previous) => {
          if (previous >= 92) return previous;

          if (seconds < 10) return Math.min(previous + 4, 45);
          if (seconds < 30) return Math.min(previous + 2, 70);
          return Math.min(previous + 1, 92);
        });
      }, 1000);
    }

    return () => {
      if (timer) window.clearInterval(timer);
    };
  }, [isProcessing]);

  useEffect(() => {
    function handlePointerMove(event: PointerEvent) {
      if (!draggedHeaderId) return;

      const element = document.elementFromPoint(event.clientX, event.clientY);
      const target = element?.closest("[data-header-id]") as HTMLElement | null;

      if (!target) return;

      const targetId = target.dataset.headerId;

      if (!targetId || targetId === draggedHeaderId) return;

      setHeaderRows((previous) => {
        const fromIndex = previous.findIndex((item) => item.id === draggedHeaderId);
        const toIndex = previous.findIndex((item) => item.id === targetId);

        if (fromIndex < 0 || toIndex < 0 || fromIndex === toIndex) return previous;

        const next = [...previous];
        const temp = next[fromIndex];
        next[fromIndex] = next[toIndex];
        next[toIndex] = temp;

        return next;
      });
    }

    function handlePointerUp() {
      setDraggedHeaderId(null);
      document.body.classList.remove("dragging-header");
    }

    if (draggedHeaderId) {
      document.body.classList.add("dragging-header");
      window.addEventListener("pointermove", handlePointerMove);
      window.addEventListener("pointerup", handlePointerUp);
    }

    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      document.body.classList.remove("dragging-header");
    };
  }, [draggedHeaderId]);

  async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(`${API_URL}${path}`, {
      ...options,
      headers: {
        ...(options?.headers || {}),
      },
    });

    if (!response.ok) {
      let errorText = "";

      try {
        const errorJson = await response.json();
        errorText = JSON.stringify(errorJson, null, 2);
      } catch {
        errorText = await response.text();
      }

      throw new Error(errorText || `Error HTTP ${response.status}`);
    }

    return response.json();
  }

  async function checkBackend() {
    try {
      const data = await apiFetch<{ status: string }>("/salud");
      setBackendStatus(data.status);
    } catch {
      setBackendStatus("no disponible");
    }
  }

  function resetWorkspace() {
    const confirmed = window.confirm(
      "Se limpiarán documentos, encabezados, validaciones, resultados y exportación.\n\n¿Deseas continuar?"
    );

    if (!confirmed) return;

    extractionAbortControllerRef.current?.abort();
    extractionAbortControllerRef.current = null;

    setSessionDocuments([]);
    setSelectedDocumentIds([]);
    setHeaderRows([createEmptyHeader()]);
    setValidationItems([]);
    setValidationFileName("");
    setIncompleteHeaderIds([]);
    setCurrentJob(null);
    setTemporaryTemplateId(null);
    setResults([]);
    setResultViewMode("all");
    setExpandedDocuments({});
    setReviewModal(null);
    setPreviewModal(null);
    setShowValidationTutorial(false);
    setShowResultTypeHelp(false);
    setReviewValue("");
    setReviewNotes("");
    setShouldSaveReviewNotes(false);
    setReviewDecision("validated");
    setPreviewZoom(1);
    setPreviewPages([]);
    setPreviewPagesLoading(false);
    setExportFile(null);
    setProgress(0);
    setElapsedSeconds(0);
    setMessage("Sesión limpiada.");
  }

  function toggleDocument(documentId: string) {
    setSelectedDocumentIds((previous) => {
      if (previous.includes(documentId)) {
        return previous.filter((id) => id !== documentId);
      }

      return [...previous, documentId];
    });
  }

  function selectAllSessionDocuments() {
    setSelectedDocumentIds(sessionDocuments.map((doc) => doc.id));
  }

  function clearSelectedDocuments() {
    setSelectedDocumentIds([]);
  }

  function addDocumentsToSession(docs: DocumentItem[]) {
    if (!docs.length) return;

    setSessionDocuments((previous) => {
      const map = new Map<string, DocumentItem>();

      for (const doc of previous) map.set(doc.id, doc);
      for (const doc of docs) map.set(doc.id, doc);

      return Array.from(map.values());
    });

    setSelectedDocumentIds(docs.map((doc) => doc.id));
  }

  async function uploadFiles(fileList: FileList | null) {
    const files = Array.from(fileList || []);

    if (!files.length) {
      setMessage("No seleccionaste archivos.");
      return;
    }

    const validFiles = files.filter((file) => {
      const lowerName = file.name.toLowerCase();

      return (
        lowerName.endsWith(".pdf") ||
        lowerName.endsWith(".png") ||
        lowerName.endsWith(".jpg") ||
        lowerName.endsWith(".jpeg") ||
        lowerName.endsWith(".bmp") ||
        lowerName.endsWith(".tiff") ||
        lowerName.endsWith(".webp")
      );
    });

    if (!validFiles.length) {
      setMessage("No se encontraron PDFs o imágenes compatibles.");
      return;
    }

    try {
      setIsUploading(true);
      setMessage(`Subiendo ${validFiles.length} archivo(s)...`);

      const uploadedDocs: DocumentItem[] = [];
      const errors: string[] = [];

      for (const file of validFiles) {
        const formData = new FormData();
        formData.append("file", file);

        try {
          const response = await fetch(`${API_URL}/documentos/subir`, {
            method: "POST",
            body: formData,
          });

          if (!response.ok) {
            const errorText = await response.text();
            errors.push(`${file.name}: ${errorText}`);
            continue;
          }

          const uploaded = (await response.json()) as DocumentItem;
          uploadedDocs.push(uploaded);
        } catch (error) {
          errors.push(`${file.name}: ${String(error)}`);
        }
      }

      addDocumentsToSession(uploadedDocs);

      setMessage(
        `Carga terminada.\n` +
          `Archivos listos: ${uploadedDocs.length}\n` +
          `Errores: ${errors.length}` +
          (errors.length ? `\n\n${errors.slice(0, 5).join("\n")}` : "")
      );
    } finally {
      setIsUploading(false);
    }
  }

  async function parseValidationExcel(fileList: FileList | null) {
    const file = fileList?.[0];

    if (!file) {
      setMessage("Selecciona un Excel de validación.");
      return;
    }

    try {
      setMessage("Leyendo base de validación...");

      const formData = new FormData();
      formData.append("file", file);

      const response = await fetch(`${API_URL}/templates/parse-validation-excel`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText);
      }

      const data = await response.json();

      setValidationItems(data.items || []);
      setValidationFileName(data.file_name || file.name);

      setMessage(
        `Base de validación cargada.\nArchivo: ${data.file_name}\nItems detectados: ${
          data.items?.length || 0
        }`
      );
    } catch (error) {
      setMessage(`Error leyendo base de validación: ${String(error)}`);
    }
  }

  function clearValidationExcel() {
    setValidationItems([]);
    setValidationFileName("");
    setMessage("Base de validación eliminada. Puedes subir otro Excel cuando quieras.");
  }

  function openDocumentPreview(documentKey: string, items: ExtractionResult[]) {
    setPreviewModal({ documentKey, items });
    setPreviewZoom(1);
    setPreviewPages([]);
  }

  function getPreviewDocument(items: ExtractionResult[]) {
    const documentId = items[0]?.document_id;

    if (documentId) {
      const found = sessionDocuments.find((doc) => doc.id === documentId);
      if (found) return found;
    }

    const fileName = items[0]?.file_name;

    if (fileName) {
      return sessionDocuments.find((doc) => doc.file_name === fileName) || null;
    }

    return null;
  }

  function getPreviewUrl(items: ExtractionResult[]) {
    const documentId = items[0]?.document_id;

    if (!documentId) return "";

    return `${API_URL}/documentos/${encodeURIComponent(documentId)}/preview`;
  }

  async function loadPreviewPages(items: ExtractionResult[]) {
    const documentId = items[0]?.document_id;

    if (!documentId) {
      setPreviewPages([]);
      return;
    }

    setPreviewPagesLoading(true);

    try {
      const data = await apiFetch<{ pages: PreviewPage[] }>(
        `/documentos/${encodeURIComponent(documentId)}/preview-pages`
      );

      setPreviewPages(data.pages || []);
    } catch {
      setPreviewPages([]);
    } finally {
      setPreviewPagesLoading(false);
    }
  }

  function isImageDocument(document: DocumentItem | null) {
    const mimeType = String(document?.mime_type || "").toLowerCase();
    const fileName = String(document?.file_name || "").toLowerCase();

    return (
      mimeType.startsWith("image/") ||
      [".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp"].some((extension) =>
        fileName.endsWith(extension)
      )
    );
  }

  function updateHeader(id: string, patch: Partial<HeaderRow>) {
    setHeaderRows((previous) =>
      previous.map((item) => {
        if (item.id === id) {
          const next = {
            ...item,
            ...patch,
          };

          if (isHeaderComplete(next)) {
            setIncompleteHeaderIds((ids) => ids.filter((itemId) => itemId !== id));
          }

          return next;
        }

        return item;
      })
    );
  }

  function addHeaderRow() {
    const incomplete = headerRows
      .filter((item) => !isHeaderComplete(item))
      .map((item) => item.id);

    if (incomplete.length > 0) {
      setIncompleteHeaderIds(incomplete);
      setMessage("Completa los encabezados y el tipo de resultado antes de agregar otro.");
      return;
    }

    setHeaderRows((previous) => [...previous, createEmptyHeader()]);
  }

  function removeHeaderRow(id: string) {
    setHeaderRows((previous) => {
      const next = previous.filter((item) => item.id !== id);

      if (!next.length) {
        return [createEmptyHeader()];
      }

      return next;
    });

    setIncompleteHeaderIds((previous) => previous.filter((item) => item !== id));
  }

  function startHeaderDrag(event: React.PointerEvent, id: string) {
    event.preventDefault();
    setDraggedHeaderId(id);
  }

  function getValidationForIndex(index: number) {
    return validationItems.find((item) => item.item_index === index + 1)?.options || [];
  }

  function buildVisiblePayload(header: HeaderRow, index: number) {
    const validationOptions = getValidationForIndex(index);

    return {
      encabezado: header.name.trim(),
      contexto: header.context.trim(),
      tipo_resultado: resultTypeLabel(header.resultType),
      validacion: validationToText(validationOptions),
    };
  }

  function buildInternalInstruction(header: HeaderRow, index: number) {
    const validationOptions = getValidationForIndex(index);

    const lines = [
      `Campo solicitado: ${header.name.trim()}.`,
      `Tipo de resultado obligatorio: ${resultTypeInstruction(header.resultType)}`,
    ];

    if (header.context.trim()) {
      lines.push(
        `Contexto del encabezado: ${header.context.trim()}. Usa este contexto para interpretar el dato solicitado.`
      );
    }

    if (validationOptions.length) {
      lines.push(
        "Base de validación estricta para este campo: " +
          validationOptions
            .map((item) => {
              if (item.context) {
                return `${item.value} (${item.context})`;
              }

              return item.value;
            })
            .join(" | ") +
          ". Si la respuesta debe clasificarse, normalized_value debe ser una de estas opciones exactas. Si ninguna aplica razonablemente, usa no aplica y marca revisión."
      );
    }

    lines.push(
      "Si el campo no aparece en el documento, responde no visible. Si aparece pero es borroso, incompleto o tiene probabilidad muy baja, responde ilegible y marca revisión. Usa no aplica solo cuando el dato realmente no corresponde al documento."
    );

    return lines.join("\n");
  }

  function validateHeadersBeforeRun() {
    const incomplete = headerRows
      .filter((item) => !isHeaderComplete(item))
      .map((item) => item.id);

    setIncompleteHeaderIds(incomplete);

    if (incomplete.length > 0) {
      setMessage("Hay encabezados incompletos. Completa los campos marcados en rojo.");
      return false;
    }

    return true;
  }

  function buildFieldsFromHeaders(): ExtractionTemplateField[] {
    const names = new Set<string>();

    return headerRows.map((header, index) => {
      const fieldName = header.name.trim();

      if (!fieldName) {
        throw new Error("Hay un encabezado vacío.");
      }

      if (names.has(fieldName.toLowerCase())) {
        throw new Error(`Encabezado duplicado: ${fieldName}`);
      }

      names.add(fieldName.toLowerCase());

      const visiblePayload = buildVisiblePayload(header, index);
      const internalInstruction = buildInternalInstruction(header, index);

      return {
        field_name: fieldName,
        display_name: fieldName,
        target_location: `B${index + 4}`,
        required: false,
        description: `${JSON.stringify(visiblePayload)}${INTERNAL_CONTEXT_MARKER}${internalInstruction}`,
      };
    });
  }

  async function createTemporaryConfiguration(signal?: AbortSignal): Promise<{
    projectId: number;
    templateId: number;
  } | null> {
    let fields: ExtractionTemplateField[];

    try {
      fields = buildFieldsFromHeaders();
    } catch (error) {
      setMessage(String(error));
      return null;
    }

    if (!fields.length) {
      setMessage("Agrega al menos un encabezado.");
      return null;
    }

    try {
      setMessage("Preparando encabezados...");

      const project = await apiFetch<ExtractionProject>("/extraction/projects", {
        method: "POST",
        signal,
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          name: `Temporal ${new Date().toISOString()}`,
          input_folder: null,
          output_folder: "/storage/exports",
        }),
      });

      await apiFetch<ExtractionProject>(`/extraction/projects/${project.id}`, {
        method: "PATCH",
        signal,
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          status: "temporary",
        }),
      });

      const template = await apiFetch<ExtractionTemplate>("/templates", {
        method: "POST",
        signal,
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          project_id: project.id,
          name: `Temporal ${new Date().toISOString()}`,
          file_path: null,
          template_type: "generated_excel",
          fields,
        }),
      });

      setTemporaryTemplateId(template.id);

      return {
        projectId: project.id,
        templateId: template.id,
      };
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        setMessage("Extracción cancelada.");
        return null;
      }

      setMessage(`Error preparando encabezados: ${String(error)}`);
      return null;
    }
  }

  async function startSmartExtraction() {
    if (isProcessing) {
      setMessage("Ya hay una extracción en curso.");
      return;
    }

    if (!selectedDocuments.length) {
      setMessage("Selecciona al menos un documento.");
      return;
    }

    if (!validateHeadersBeforeRun()) return;

    const confirmed = window.confirm(
      `Vas a analizar ${selectedDocuments.length} documento(s).\n\n` +
        `Máximo automático: ${MAX_PAGES_PER_DOCUMENT} páginas por archivo.\n\n` +
        `¿Deseas continuar?`
    );

    if (!confirmed) return;

    const controller = new AbortController();
    extractionAbortControllerRef.current = controller;

    const config = await createTemporaryConfiguration(controller.signal);

    if (!config) return;

    try {
      setIsProcessing(true);
      setProgress(10);
      setResults([]);
      setExportFile(null);

      const documentIds = selectedDocuments.map((doc) => doc.id);

      const job = await apiFetch<ExtractionJob>(
        `/extraction/projects/${config.projectId}/jobs`,
        {
          method: "POST",
          signal: controller.signal,
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            total_files: documentIds.length,
          }),
        }
      );

      setCurrentJob(job);
      setMessage("Analizando documentos...");

      const runResult = await apiFetch<{
        job_id: number;
        status: string;
        processed_files: number;
        failed_files: number;
        processed_pages: number;
        created_results: number;
        prediction_review_pending?: number;
        prediction_unresolved?: number;
        message: string;
      }>(`/extraction/jobs/${job.id}/run-vision`, {
        method: "POST",
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          template_id: config.templateId,
          document_ids: documentIds,
          max_pages_per_document: MAX_PAGES_PER_DOCUMENT,
        }),
      });

      const loadedResults = await apiFetch<ExtractionResult[]>(
        `/extraction/jobs/${job.id}/results`,
        {
          signal: controller.signal,
        }
      );

      setResults(loadedResults);
      setResultViewMode("all");
      setProgress(100);

      const grouped: Record<string, boolean> = {};
      for (const result of loadedResults) {
        const key = result.document_id || result.file_name || `resultado-${result.id}`;
        grouped[key] = false;
      }
      setExpandedDocuments(grouped);

      setMessage(
        `Extracción terminada.\n` +
          `Documentos procesados: ${runResult.processed_files}\n` +
          `Campos extraídos: ${runResult.created_results}\n` +
          `Tiempo: ${formatElapsed(elapsedSeconds)}`
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        setMessage("Extracción cancelada.");
      } else {
        setMessage(`Error en extracción: ${String(error)}`);
      }
    } finally {
      extractionAbortControllerRef.current = null;
      setIsProcessing(false);
    }
  }

  async function cancelExtraction() {
    extractionAbortControllerRef.current?.abort();

    if (currentJob?.id) {
      try {
        await apiFetch(`/extraction/jobs/${currentJob.id}/cancel`, {
          method: "POST",
        });
      } catch {
        // cancelación local aplicada
      }
    }

    setIsProcessing(false);
    setMessage("Cancelación solicitada. Si una llamada ya estaba en curso, puede finalizar internamente.");
  }

  async function loadCurrentResults() {
    if (!currentJob) return;

    try {
      const loadedResults = await apiFetch<ExtractionResult[]>(
        `/extraction/jobs/${currentJob.id}/results`
      );

      setResults(loadedResults);
    } catch (error) {
      setMessage(`Error cargando resultados: ${String(error)}`);
    }
  }

  function openReview(result: ExtractionResult) {
    setReviewModal({ result });
    setReviewValue(result.normalized_value || result.raw_value || "");
    setReviewNotes("");
    setShouldSaveReviewNotes(false);

    const category = classifyResult(result);
    if (category === "no_visible") {
      setReviewDecision("no_visible");
    } else if (category === "doubtful") {
      setReviewDecision("illegible");
    } else {
      setReviewDecision("validated");
    }
  }

  async function saveReview() {
    if (!reviewModal?.result) {
      setMessage("Selecciona un campo para revisar.");
      return;
    }

    if (!reviewValue.trim()) {
      setMessage("Escribe el valor corregido.");
      return;
    }

    const reviewStatus =
      reviewDecision === "illegible"
        ? "marked_illegible"
        : reviewDecision === "no_visible"
          ? "marked_no_visible"
          : "corrected";
    const notesToSave = shouldSaveReviewNotes ? reviewNotes.trim() : "";

    try {
      const savedResult = await apiFetch<ExtractionResult>(
        `/extraction/results/${reviewModal.result.id}/review`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            corrected_value: reviewValue,
            review_status: reviewStatus,
            reviewer_notes: notesToSave || null,
          }),
        }
      );

      setResults((previous) =>
        previous.map((item) => (item.id === savedResult.id ? savedResult : item))
      );

      setPreviewModal((current) => {
        if (!current) return current;

        return {
          ...current,
          items: current.items.map((item) =>
            item.id === savedResult.id ? savedResult : item
          ),
        };
      });

      setReviewModal(null);
      setReviewValue("");
      setReviewNotes("");
      setShouldSaveReviewNotes(false);
      setReviewDecision("validated");

      await loadCurrentResults();

      setMessage("Revisión guardada.");
    } catch (error) {
      setMessage(`Error guardando revisión: ${String(error)}`);
    }
  }

  async function exportExcel() {
    if (!currentJob || !temporaryTemplateId) {
      setMessage("Primero ejecuta una extracción.");
      return;
    }

    try {
      setMessage("Generando Excel...");

      const exportResult = await apiFetch<ExportFile>(
        `/extraction/jobs/${currentJob.id}/export-excel`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            template_id: temporaryTemplateId,
          }),
        }
      );

      setExportFile(exportResult);
      setMessage("Excel generado. Ahora puedes guardarlo.");
    } catch (error) {
      setMessage(`Error exportando Excel: ${String(error)}`);
    }
  }

  async function saveExcelAs() {
    if (!exportFile) {
      setMessage("Primero genera el Excel.");
      return;
    }

    try {
      const response = await fetch(
        `${API_URL}/extraction/exports/${exportFile.id}/download`
      );

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(errorText || `Error HTTP ${response.status}`);
      }

      const blob = await response.blob();
      const suggestedName = "extraccion.xlsx";

      if ((window as any).showSaveFilePicker) {
        const fileHandle = await (window as any).showSaveFilePicker({
          suggestedName,
          types: [
            {
              description: "Excel",
              accept: {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [
                  ".xlsx",
                ],
              },
            },
          ],
        });

        const writable = await fileHandle.createWritable();
        await writable.write(blob);
        await writable.close();

        setMessage("Excel guardado.");
        return;
      }

      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");

      link.href = url;
      link.download = suggestedName;

      document.body.appendChild(link);
      link.click();
      link.remove();

      window.URL.revokeObjectURL(url);

      setMessage("Excel descargado.");
    } catch (error) {
      setMessage(`Error guardando Excel: ${String(error)}`);
    }
  }

  function formatElapsed(seconds: number) {
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;

    return `${minutes}m ${String(rest).padStart(2, "0")}s`;
  }

  function resultLabel(category: string) {
    const labels: Record<string, string> = {
      valid: "Validados",
      doubtful: "Ilegibles",
      no_visible: "No visibles",
    };

    return labels[category] || category;
  }

  function resultColorClass(category: string) {
    const map: Record<string, string> = {
      valid: "stat-valid",
      doubtful: "stat-doubtful",
      no_visible: "stat-hidden",
    };

    return map[category] || "";
  }

  function toggleExpandedDocument(key: string) {
    setExpandedDocuments((previous) => ({
      ...previous,
      [key]: !previous[key],
    }));
  }

  function visibleResultsForDocument(items: ExtractionResult[]) {
    if (resultViewMode === "doubtful") {
      return items.filter((item) => isPendingResult(item));
    }

    return items;
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <h1>Extractor Inteligente de Documentos</h1>
          <p>Extracción de datos con encabezados, validaciones y revisión humana.</p>
        </div>

        <div className="status-card">
          <span>Backend</span>
          <strong>{backendStatus}</strong>
        </div>
      </header>

      <section className="module-selector">
        <button
          className={activeModule === "data_extraction" ? "module-active" : ""}
          onClick={() => setActiveModule("data_extraction")}
        >
          Extracción inteligente a Excel
        </button>

        <button
          className={activeModule === "long_text" ? "module-active" : ""}
          onClick={() => setActiveModule("long_text")}
        >
          Textos largos
        </button>

        <button className="danger" onClick={resetWorkspace}>
          Limpiar todo
        </button>
      </section>

      {message && <div className="message-box">{message}</div>}

      {activeModule === "long_text" && (
        <main className="placeholder-panel">
          <h2>Extracción total de textos largos</h2>
          <p>Este módulo se desarrollará después.</p>
        </main>
      )}

      {activeModule === "data_extraction" && (
        <main className="workflow">
          <section className="card">
            <h2>1. Documentos</h2>

            <div className="upload-row">
              <label className="upload-button">
                Subir archivos
                <input
                  type="file"
                  multiple
                  hidden
                  accept=".pdf,.png,.jpg,.jpeg,.bmp,.tiff,.webp"
                  onChange={(event) => uploadFiles(event.target.files)}
                  disabled={isUploading || isProcessing}
                />
              </label>

              <label className="upload-button secondary-upload">
                Subir carpeta
                <input
                  type="file"
                  multiple
                  hidden
                  {...({ webkitdirectory: "", directory: "" } as any)}
                  onChange={(event) => uploadFiles(event.target.files)}
                  disabled={isUploading || isProcessing}
                />
              </label>
            </div>

            <div className="document-actions compact-actions">
              <button className="secondary" onClick={selectAllSessionDocuments} disabled={isProcessing}>
                Seleccionar todo
              </button>

              <button className="secondary" onClick={clearSelectedDocuments} disabled={isProcessing}>
                Limpiar selección
              </button>
            </div>

            <div className="document-list">
              {sessionDocuments.length === 0 && (
                <p className="muted">No hay documentos cargados.</p>
              )}

              {sessionDocuments.map((doc) => {
                const checked = selectedDocumentIds.includes(doc.id);

                return (
                  <label key={doc.id} className="document-item">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleDocument(doc.id)}
                      disabled={isProcessing}
                    />
                    <div>
                      <strong>{doc.file_name}</strong>
                      <span>{doc.status}</span>
                    </div>
                  </label>
                );
              })}
            </div>
          </section>

          <section className="card">
            <div className="card-title-row">
              <h2>2. Crear encabezados</h2>
            </div>

            <div className="headers-editor">
              {headerRows.map((row, index) => {
                const validations = getValidationForIndex(index);
                const incomplete = incompleteHeaderIds.includes(row.id);

                return (
                  <div
                    key={row.id}
                    data-header-id={row.id}
                    className={`header-row ${draggedHeaderId === row.id ? "dragging" : ""} ${
                      incomplete ? "incomplete" : ""
                    }`}
                  >
                    <div
                      className="drag-border"
                      onPointerDown={(event) => startHeaderDrag(event, row.id)}
                      title="Arrastrar para cambiar posición"
                    />

                    <div className="header-index">{index + 1}</div>

                    <div className="header-main">
                      <div className="field-with-expand">
                        <input
                          value={row.name}
                          onChange={(event) => updateHeader(row.id, { name: event.target.value })}
                          placeholder="Encabezado"
                          disabled={isProcessing}
                        />

                        <button
                          className="small-expand"
                          onClick={() => setExpandedHeaderId(row.id)}
                          title="Expandir"
                          type="button"
                        >
                          ⛶
                        </button>
                      </div>

                      <textarea
                        className="compact-textarea"
                        value={row.context}
                        onChange={(event) => updateHeader(row.id, { context: event.target.value })}
                        placeholder=""
                        rows={2}
                        disabled={isProcessing}
                      />

                      <div className="header-meta-row">
                        <select
                          value={row.resultType}
                          onChange={(event) =>
                            updateHeader(row.id, {
                              resultType: event.target.value as ResultType,
                            })
                          }
                          disabled={isProcessing}
                        >
                          <option value="" disabled>Selecciona</option>
                          <option value="date">Fecha</option>
                          <option value="time">Hora</option>
                          <option value="text">Texto</option>
                          <option value="decimal">Decimal</option>
                          <option value="integer">Número</option>
                          <option value="calculation">Cálculo</option>
                        </select>

                        <button
                          className="help-dot"
                          type="button"
                          onClick={() => setShowResultTypeHelp(true)}
                          title="Ver explicación de tipos"
                        >
                          ?
                        </button>

                        <span className="mini-chip">
                          Validaciones: {validations.length}
                        </span>
                      </div>
                    </div>

                    <button
                      className="trash-button"
                      onClick={() => removeHeaderRow(row.id)}
                      disabled={isProcessing}
                      title="Eliminar"
                    >
                      🗑
                    </button>
                  </div>
                );
              })}

              <button
                className="add-header-button"
                onClick={addHeaderRow}
                disabled={isProcessing}
                title="Agregar encabezado"
                type="button"
              >
                + Agregar encabezado
              </button>
            </div>
          </section>

          <section className="card">
            <h2>3. Base de validación</h2>

            <p className="muted">
              Opcional. El Excel puede contener ITEM 1, ITEM 2, ITEM 7, etc. en cualquier celda.
            </p>

            <div className="validation-actions">
              <label className="upload-button secondary-upload">
                Subir Excel
                <input
                  type="file"
                  hidden
                  accept=".xlsx,.xlsm"
                  onChange={(event) => parseValidationExcel(event.target.files)}
                  disabled={isProcessing}
                />
              </label>

              {validationFileName && (
                <button
                  className="danger"
                  type="button"
                  onClick={clearValidationExcel}
                  disabled={isProcessing}
                >
                  Borrar Excel
                </button>
              )}
            </div>

            {validationFileName && (
              <div className="info-box">
                <strong>Base cargada</strong>
                <span>{validationFileName}</span>
                <span>Items: {validationItems.length}</span>
              </div>
            )}

            <button
              className="text-link"
              type="button"
              onClick={() => setShowValidationTutorial(true)}
            >
              Ver tutorial
            </button>
          </section>

          <section className="card">
            <h2>4. Ejecutar</h2>

            <div className="summary-box visual-summary">
              <span>Documentos: {selectedDocuments.length}</span>
              <span>Encabezados: {validHeaders.length}</span>
              <span>Máximo: {MAX_PAGES_PER_DOCUMENT} páginas</span>
            </div>

            {(isProcessing || progress > 0) && (
              <div className="progress-panel">
                <div className="progress-head">
                  <span>{isProcessing ? "Procesando" : "Completado"}</span>
                  <strong>{progress}%</strong>
                </div>

                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${progress}%` }} />
                </div>

                <small>Tiempo: {formatElapsed(elapsedSeconds)}</small>
              </div>
            )}

            <button
              className="primary-large"
              onClick={startSmartExtraction}
              disabled={isProcessing}
            >
              Iniciar extracción
            </button>

            {isProcessing && (
              <button
                className="danger primary-large"
                onClick={cancelExtraction}
              >
                Cancelar extracción
              </button>
            )}
          </section>

          <section className="card wide">
            <h2>5. Resultados</h2>

            {results.length > 0 && (
              <AnalyticsPanel
                stats={resultStats}
                labelFor={resultLabel}
                colorClassFor={resultColorClass}
              />
            )}

            <div className="result-toolbar">
              <button
                className={resultViewMode === "doubtful" ? "module-active" : "secondary"}
                onClick={() => setResultViewMode("doubtful")}
              >
                Revisar pendientes
              </button>

              <button
                className={resultViewMode === "all" ? "module-active" : "secondary"}
                onClick={() => setResultViewMode("all")}
              >
                Ver todos
              </button>
            </div>

            {results.length === 0 ? (
              <p className="muted">No hay resultados.</p>
            ) : (
              <div className="document-result-list">
                {Object.entries(resultsByDocument).map(([key, items]) => {
                  const visibleItems = visibleResultsForDocument(items);
                  const pendingCount = items.filter((item) => isPendingResult(item)).length;
                  const isOpen = expandedDocuments[key];

                  if (resultViewMode === "doubtful" && visibleItems.length === 0) {
                    return null;
                  }

                  return (
                    <div key={key} className="document-result-card">
                      {pendingCount > 0 && <span className="pending-dot" />}

                      <div className="document-result-header">
                        <div>
                          <strong>{items[0]?.file_name || "Documento"}</strong>
                          <span>
                            Campos: {items.length} · Pendientes: {pendingCount}
                          </span>
                        </div>

                        <div className="document-header-actions">
                          <button
                            className="secondary"
                            onClick={() => openDocumentPreview(key, items)}
                          >
                            Visualizar
                          </button>

                          <button
                            className="secondary"
                            onClick={() => toggleExpandedDocument(key)}
                          >
                            {isOpen ? "Ocultar" : "Mostrar"}
                          </button>
                        </div>
                      </div>

                      {isOpen && (
                        <div className="fields-grid">
                          {visibleItems.map((result) => {
                            const category = classifyResult(result);
                            const pending = isPendingResult(result);

                            return (
                              <div key={result.id} className={`field-card ${resultColorClass(category)}`}>
                                {pending && <span className="field-pending-dot" />}

                                <div className="field-card-head">
                                  <strong>{result.field_name}</strong>
                                  <span>{result.confidence_level || "-"}</span>
                                </div>

                                <div className="field-value">
                                  {normalizeValue(result.normalized_value || result.raw_value)}
                                </div>

                                {result.evidence_text && (
                                  <p>{result.evidence_text}</p>
                                )}

                                <button
                                  className="tiny"
                                  onClick={() => openReview(result)}
                                >
                                  Revisar
                                </button>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          <section className="card">
            <h2>6. Excel</h2>

            <button onClick={exportExcel} disabled={isProcessing || !currentJob}>
              Generar Excel
            </button>

            {exportFile && (
              <div className="info-box">
                <strong>Excel listo</strong>

                <button onClick={saveExcelAs} disabled={isProcessing}>
                  Guardar como
                </button>
              </div>
            )}
          </section>
        </main>
      )}

      {expandedHeaderId && (
        <HeaderEditorModal
          header={headerRows.find((item) => item.id === expandedHeaderId) || null}
          onClose={() => setExpandedHeaderId(null)}
          onChange={(patch) => updateHeader(expandedHeaderId, patch)}
        />
      )}

      {showResultTypeHelp && (
        <InfoModal title="Tipos de dato" onClose={() => setShowResultTypeHelp(false)}>
          <div className="help-list">
            <p><strong>Fecha</strong> ➜ Para días, meses y años.</p>
            <p><strong>Hora</strong> ➜ Para tiempo y horarios.</p>
            <p><strong>Texto</strong> ➜ Para nombres, direcciones y códigos/IDs.</p>
            <p><strong>Decimal</strong> ➜ Para dinero y precios con centavos.</p>
            <p><strong>Número</strong> ➜ Para cantidades enteras.</p>
            <p><strong>Cálculo</strong> ➜ Para verificar automáticamente si las sumas son correctas.</p>
          </div>
        </InfoModal>
      )}

      {showValidationTutorial && (
        <InfoModal title="Base de validación" onClose={() => setShowValidationTutorial(false)}>
          <div className="tutorial-sheet">
            <table>
              <thead>
                <tr>
                  <th>ITEM 1</th>
                  <th>ITEM 2</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Factura</td>
                  <td>Soles</td>
                </tr>
                <tr>
                  <td>Boleta</td>
                  <td>Dólares</td>
                </tr>
                <tr>
                  <td>Nota de crédito</td>
                  <td>Euros</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="tutorial-copy">
            <p>Esta sección permite subir un archivo Excel para agregar validaciones personalizadas.</p>
            <p>En el ejemplo, ITEM 1 puede representar “Tipo de documento” y ITEM 2 “Tipo de moneda”. Cada columna toma su contexto desde el orden de los encabezados configurados.</p>
            <p>Durante la extracción, el resultado se ajusta estrictamente a los valores disponibles en las celdas de cada columna.</p>
          </div>
        </InfoModal>
      )}

      {previewModal && (
        <div className="modal-backdrop">
          <div className="preview-modal">
            <div className="modal-head">
              <div>
                <h3>{getPreviewDocument(previewModal.items)?.file_name || previewModal.items[0]?.file_name || "Documento"}</h3>
                <span>Campos: {previewModal.items.length}</span>
              </div>

              <button className="icon-button" onClick={() => setPreviewModal(null)}>
                ×
              </button>
            </div>

            <div className="preview-tools">
              <button className="secondary" onClick={() => setPreviewZoom((value) => Math.max(0.5, value - 0.1))}>
                -
              </button>
              <span>{Math.round(previewZoom * 100)}%</span>
              <button className="secondary" onClick={() => setPreviewZoom((value) => Math.min(2.5, value + 0.1))}>
                +
              </button>
            </div>

            <div className="preview-layout">
              <div className="preview-document-pane">
                {(() => {
                  const previewDocument = getPreviewDocument(previewModal.items);
                  const previewUrl = getPreviewUrl(previewModal.items);

                  if (!previewUrl) {
                    return <p className="muted">No se encontró el archivo para previsualizar.</p>;
                  }

                  if (isImageDocument(previewDocument)) {
                    return (
                      <img
                        src={previewUrl}
                        alt="Vista previa del documento"
                        style={{ width: `${100 * previewZoom}%` }}
                      />
                    );
                  }

                  if (previewPagesLoading) {
                    return <p className="muted">Cargando vista previa...</p>;
                  }

                  if (previewPages.length > 0) {
                    return (
                      <div className="preview-page-stack">
                        {previewPages.map((page) => (
                          <img
                            key={page.page_number}
                            src={`${API_URL}${page.url}`}
                            alt={`Pagina ${page.page_number}`}
                            style={{ width: `${100 * previewZoom}%` }}
                          />
                        ))}
                      </div>
                    );
                  }

                  return (
                    <iframe
                      title="Vista previa del documento"
                      src={previewUrl}
                      style={{
                        width: `${100 * previewZoom}%`,
                        height: `${100 * previewZoom}%`,
                      }}
                    />
                  );
                })()}
              </div>

              <div className="preview-fields-pane">
                {previewModal.items.map((result) => {
                  const category = classifyResult(result);

                  return (
                    <div key={result.id} className={`preview-field ${resultColorClass(category)}`}>
                      <div>
                        <strong>{result.field_name}</strong>
                        <span>{resultLabel(category)}</span>
                      </div>
                      <p>{normalizeValue(result.normalized_value || result.raw_value)}</p>
                      {result.evidence_text && <small>{result.evidence_text}</small>}
                      <button className="tiny" onClick={() => openReview(result)}>
                        Revisar
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {reviewModal && (
        <div className="modal-backdrop">
          <div className="review-modal">
            <div className="modal-head">
              <div>
                <h3>{reviewModal.result.field_name}</h3>
                <span>{reviewModal.result.file_name}</span>
              </div>

              <button className="icon-button" onClick={() => setReviewModal(null)}>
                ×
              </button>
            </div>

            <label>Valor corregido</label>
            <input
              value={reviewValue}
              onChange={(event) => setReviewValue(event.target.value)}
            />

            <label>Estado de validación</label>
            <select
              value={reviewDecision}
              onChange={(event) => setReviewDecision(event.target.value as ReviewDecision)}
            >
              <option value="validated">Validado</option>
              <option value="illegible">Ilegible</option>
              <option value="no_visible">No visible</option>
            </select>

            <label>Notas</label>
            <textarea
              value={reviewNotes}
              onChange={(event) => setReviewNotes(event.target.value)}
              rows={5}
            />

            <label className="note-check">
              <input
                type="checkbox"
                checked={shouldSaveReviewNotes}
                onChange={(event) => setShouldSaveReviewNotes(event.target.checked)}
              />
              Guardar esta nota
            </label>

            <div className="modal-actions">
              <button className="primary-large" onClick={saveReview}>
                Actualizar datos
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function HeaderEditorModal({
  header,
  onChange,
  onClose,
}: {
  header: HeaderRow | null;
  onChange: (patch: Partial<HeaderRow>) => void;
  onClose: () => void;
}) {
  if (!header) return null;

  return (
    <div className="modal-backdrop">
      <div className="review-modal">
        <div className="modal-head">
          <div>
            <h3>Editar encabezado</h3>
            <span>Campo ampliado</span>
          </div>

          <button className="icon-button" onClick={onClose}>
            ×
          </button>
        </div>

        <label>Encabezado</label>
        <input
          value={header.name}
          onChange={(event) => onChange({ name: event.target.value })}
        />

        <label>Contexto</label>
        <textarea
          value={header.context}
          onChange={(event) => onChange({ context: event.target.value })}
          rows={6}
        />

        <label>Tipo de resultado</label>
        <select
          value={header.resultType}
          onChange={(event) => onChange({ resultType: event.target.value as ResultType })}
        >
          <option value="" disabled>Selecciona</option>
          <option value="date">Fecha</option>
          <option value="time">Hora</option>
          <option value="text">Texto</option>
          <option value="decimal">Decimal</option>
          <option value="integer">Número</option>
          <option value="calculation">Cálculo</option>
        </select>

        <div className="modal-actions">
          <button onClick={onClose}>Cerrar</button>
        </div>
      </div>
    </div>
  );
}

function InfoModal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="info-modal">
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon-button" onClick={onClose}>
            ×
          </button>
        </div>

        {children}
      </div>
    </div>
  );
}

function AnalyticsPanel({
  stats,
  labelFor,
  colorClassFor,
}: {
  stats: {
    valid: number;
    doubtful: number;
    no_visible: number;
    total: number;
  };
  labelFor: (key: string) => string;
  colorClassFor: (key: string) => string;
}) {
  const items = [
    { key: "valid", value: stats.valid },
    { key: "doubtful", value: stats.doubtful },
    { key: "no_visible", value: stats.no_visible },
  ];

  return (
    <div className="analytics-card">
      <div className="analytics-list">
        {items.map((item) => {
          const percent = stats.total ? Math.round((item.value / stats.total) * 100) : 0;

          return (
            <div key={item.key} className="analytics-row">
              <div>
                <strong>{labelFor(item.key)}</strong>
                <span>{item.value}</span>
              </div>

              <div className="analytics-bar">
                <span
                  className={colorClassFor(item.key)}
                  style={{ width: `${percent}%` }}
                />
              </div>

              <div className="analytics-percent">
                {percent}%
              </div>
            </div>
          );
        })}

      </div>

      <PieStats stats={stats} />
    </div>
  );
}

function PieStats({
  stats,
}: {
  stats: {
    valid: number;
    doubtful: number;
    no_visible: number;
    total: number;
  };
}) {
  const total = stats.total || 1;
  const radius = 58;
  const circumference = 2 * Math.PI * radius;

  const segments = [
    { key: "valid", value: stats.valid, className: "pie-valid" },
    { key: "doubtful", value: stats.doubtful, className: "pie-doubtful" },
    { key: "no_visible", value: stats.no_visible, className: "pie-hidden" },
  ];

  let offset = 0;

  return (
    <div className="pie-card">
      <svg width="190" height="190" viewBox="0 0 190 190">
        <circle
          cx="95"
          cy="95"
          r={radius}
          fill="transparent"
          stroke="#e5e7eb"
          strokeWidth="32"
        />

        {segments.map((segment) => {
          const length = (segment.value / total) * circumference;
          const dashArray = `${length} ${circumference - length}`;
          const dashOffset = -offset;

          offset += length;

          return (
            <circle
              key={segment.key}
              cx="95"
              cy="95"
              r={radius}
              fill="transparent"
              strokeWidth="32"
              className={segment.className}
              strokeDasharray={dashArray}
              strokeDashoffset={dashOffset}
              transform="rotate(-90 95 95)"
            />
          );
        })}

        <text x="95" y="90" textAnchor="middle" className="pie-total">
          {stats.total}
        </text>
        <text x="95" y="112" textAnchor="middle" className="pie-label">
          campos
        </text>
      </svg>
    </div>
  );
}

export default App;

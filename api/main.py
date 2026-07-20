"""
API HTTP d'EduTutor — façade du cœur multi-agents pour le front web (React).

Ne contient AUCUNE logique métier : elle appelle la même couche que Streamlit
(`agents.orchestrator`, `agents.titler`, `interface.exporters`, `rag.indexer`).
Les deux UIs coexistent pendant la validation du nouveau front.

Point clé : POST /api/conversations/{id}/messages répond en **SSE**
(Server-Sent Events) — le front affiche la progression réelle des agents
(planification → étapes → résultat) au lieu d'un spinner muet de plusieurs
minutes.

Lancement (dev) :
    .venv/bin/uvicorn api.main:app --port 8000 --reload
"""

import json
import queue
import sys
import threading
from pathlib import Path

# Racine du projet importable quel que soit le cwd d'uvicorn.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from agents import orchestrator
from agents.titler import generate_title
from config import settings
from services.exporters import (
    PDF_AVAILABLE,
    safe_filename,
    to_docx_bytes,
    to_markdown_bytes,
    to_pdf_bytes,
)
from rag.indexer import sync_courses_index
from api import store

app = FastAPI(title="EduTutor API", version="0.1.0")

# Front de dev (Vite). Application locale mono-utilisateur : pas d'auth.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_EXPORTERS = {
    "docx": (to_docx_bytes, _DOCX_MIME),
    "md": (lambda md, _title: to_markdown_bytes(md), "text/markdown"),
    "pdf": (to_pdf_bytes, "application/pdf"),
}


# --- Santé -------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "pdf_export": PDF_AVAILABLE}


# --- Documents (cours) ---------------------------------------------------------

@app.get("/api/documents")
def list_documents() -> list[dict]:
    """Cours disponibles + statut d'analyse + taille/date (pour la Bibliothèque)."""
    documents = []
    for d in orchestrator.list_documents():
        stat = Path(d["path"]).stat()
        documents.append({
            "filename": d["filename"],
            "analyzed": d["analyzed"],
            "size": stat.st_size,
            "modified": stat.st_mtime,
        })
    return documents


@app.post("/api/documents")
def upload_document(file: UploadFile) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in settings.SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Format non supporté : {suffix or '(aucun)'}")
    settings.COURSES_DIR.mkdir(parents=True, exist_ok=True)
    target = settings.COURSES_DIR / Path(file.filename).name
    target.write_bytes(file.file.read())
    result = sync_courses_index()  # vectorisation du nouveau cours (peut être long)
    return {"saved": target.name, "index": result}


@app.get("/api/documents/{filename}/file")
def get_document_file(filename: str) -> FileResponse:
    """Sert le fichier du cours pour ouverture dans le navigateur (PDF inline)."""
    target = settings.COURSES_DIR / Path(filename).name  # .name : pas de traversée
    if not target.exists():
        raise HTTPException(404, "Cours introuvable.")
    return FileResponse(
        target, filename=target.name, content_disposition_type="inline"
    )


@app.get("/api/deliverables")
def list_deliverables() -> list[dict]:
    """Bibliothèque : tous les livrables générés (fiches, résumés, documents)."""
    return store.list_all_deliverables()


# --- Pièces jointes du chat -------------------------------------------------------

def _unique_attachment_path(name: str) -> Path:
    """Chemin de sauvegarde sans collision (suffixe _2, _3… si le nom existe)."""
    base = settings.ATTACHMENTS_DIR / Path(name).name
    if not base.exists():
        return base
    stem, suffix = base.stem, base.suffix
    for index in range(2, 1000):
        candidate = settings.ATTACHMENTS_DIR / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise HTTPException(500, "Trop de fichiers homonymes.")


@app.post("/api/attachments")
def upload_attachment(file: UploadFile) -> dict:
    """Joint un fichier au chat : sauvegardé hors des cours (pas indexé au RAG)."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in settings.SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Format non supporté : {suffix or '(aucun)'}")
    settings.ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    target = _unique_attachment_path(file.filename)
    target.write_bytes(file.file.read())
    return {"filename": target.name, "size": target.stat().st_size}


@app.get("/api/attachments/{filename}/file")
def get_attachment_file(filename: str) -> FileResponse:
    target = settings.ATTACHMENTS_DIR / Path(filename).name
    if not target.exists():
        raise HTTPException(404, "Pièce jointe introuvable.")
    return FileResponse(
        target, filename=target.name, content_disposition_type="inline"
    )


def _attachment_texts(filenames: list[str]) -> list[dict]:
    """Extrait (au mieux) le texte de chaque pièce jointe pour ancrer le tuteur.
    Réutilise le pipeline documentaire (PDF/docx/pptx/images OCR)."""
    from rag.document_loader import load_document_segments

    attachments = []
    for name in filenames:
        target = settings.ATTACHMENTS_DIR / Path(name).name
        if not target.exists():
            continue
        try:
            segments = load_document_segments(str(target))
            text = "\n\n".join(s["text"] for s in segments)
        except Exception:
            text = ""  # image sans texte / format illisible : le tuteur le dira
        attachments.append({"filename": target.name, "text": text})
    return attachments


@app.delete("/api/documents/{filename}")
def delete_document(filename: str) -> dict:
    target = settings.COURSES_DIR / Path(filename).name  # .name : pas de traversée
    if not target.exists():
        raise HTTPException(404, "Cours introuvable.")
    target.unlink()
    result = sync_courses_index()
    return {"deleted": target.name, "index": result}


# --- Conversations --------------------------------------------------------------

@app.get("/api/conversations")
def list_conversations() -> list[dict]:
    return store.list_conversations()


@app.post("/api/conversations", status_code=201)
def create_conversation() -> dict:
    return store.create_conversation()


@app.get("/api/conversations/{conv_id}")
def get_conversation(conv_id: int) -> dict:
    conv = store.get_conversation(conv_id)
    if conv is None:
        raise HTTPException(404, "Conversation introuvable.")
    return conv


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: int) -> dict:
    if not store.delete_conversation(conv_id):
        raise HTTPException(404, "Conversation introuvable.")
    return {"deleted": conv_id}


# --- Chat (SSE : progression temps réel) -----------------------------------------

class MessageIn(BaseModel):
    content: str
    attachments: list[str] = []  # noms de fichiers déjà uploadés via /api/attachments


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _assistant_message(result: dict | None) -> dict:
    """Construit le message assistant persisté — MÊME schéma que Streamlit."""
    if not result or result.get("status") == "error":
        return {"role": "assistant", "status": "error",
                "content": (result or {}).get("answer")
                or "Le tuteur n'a pas pu répondre. Réessaie."}
    kind = result.get("kind", "tutor")
    mode = (result.get("payload") or {}).get("mode") if kind == "tutor" else kind
    message = {"role": "assistant", "status": "success",
               "content": result.get("answer", ""),
               "mode": mode or "general_tutor",
               "agents": result.get("steps_run", [])}
    if result.get("deliverable"):
        message["deliverable"] = result["deliverable"]
    return message


@app.post("/api/conversations/{conv_id}/messages")
def post_message(conv_id: int, message: MessageIn) -> StreamingResponse:
    """
    Envoie un message étudiant et STREAME la progression en SSE :
      {"type": "planning"} · {"type": "plan", intent, steps}
      {"type": "step_start"/"step_done", agent, …}
      {"type": "message", message}   <- réponse finale persistée
      {"type": "title", title}       <- 1er échange uniquement
      {"type": "done"}
    """
    conv = store.get_conversation(conv_id)
    if conv is None:
        raise HTTPException(404, "Conversation introuvable.")
    content = (message.content or "").strip()
    if not content:
        raise HTTPException(400, "Message vide.")

    history = [{"role": m["role"], "content": m["content"]}
               for m in conv.get("messages", [])]
    is_first = not history
    deliverable = store.last_deliverable(conv)

    # Pièces jointes : texte extrait pour le tuteur + trace dans le message user.
    attachments = _attachment_texts(message.attachments or [])
    user_message: dict = {"role": "user", "content": content}
    if attachments:
        att_dir = settings.ATTACHMENTS_DIR
        user_message["attachments"] = [
            {"filename": a["filename"],
             "size": (att_dir / a["filename"]).stat().st_size}
            for a in attachments
        ]
    store.append_messages(conv_id, [user_message])

    events: queue.Queue = queue.Queue()

    # Titre de la discussion : en PARALLÈLE de la réponse (latence masquée).
    title_box: dict = {}
    title_thread = None
    if is_first:
        title_thread = threading.Thread(
            target=lambda: title_box.__setitem__("title", generate_title(content)),
            daemon=True,
        )
        title_thread.start()

    def worker() -> None:
        try:
            result = orchestrator.handle(
                content, history=history, use_planner=True,
                last_deliverable=deliverable, on_event=events.put,
                attachments=attachments or None,
            )
        except Exception:
            result = None
        events.put({"type": "_result", "result": result})

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while True:
            event = events.get()
            if event.get("type") != "_result":
                yield _sse(event)
                continue
            assistant = _assistant_message(event["result"])
            store.append_messages(conv_id, [assistant])
            yield _sse({"type": "message", "message": assistant})
            if title_thread is not None and assistant["status"] != "error":
                title_thread.join(timeout=90)
                title = title_box.get("title")
                if title:
                    store.set_title(conv_id, title)
                    yield _sse({"type": "title", "title": title})
            yield _sse({"type": "done"})
            return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Export des livrables ---------------------------------------------------------

class ExportIn(BaseModel):
    markdown: str
    title: str = "livrable"
    format: str = "docx"  # docx | md | pdf


@app.post("/api/export")
def export_deliverable(payload: ExportIn) -> Response:
    exporter = _EXPORTERS.get(payload.format)
    if exporter is None:
        raise HTTPException(400, f"Format inconnu : {payload.format}")
    if payload.format == "pdf" and not PDF_AVAILABLE:
        raise HTTPException(501, "Export PDF indisponible sur ce serveur.")
    build, mime = exporter
    data = build(payload.markdown, payload.title)
    filename = safe_filename(payload.title, payload.format)
    return Response(
        content=data, media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

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

import contextvars
import json
import queue
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

# Racine du projet importable quel que soit le cwd d'uvicorn.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from agents import orchestrator
from agents.titler import generate_title
from config import settings, workspace
from services.exporters import (
    PDF_AVAILABLE,
    safe_filename,
    to_docx_bytes,
    to_markdown_bytes,
    to_pdf_bytes,
)
from rag.indexer import sync_courses_index
from api import auth, store

def _warm_rag_models() -> None:
    """Précharge embedding + reranker EN RAM (au démarrage, pas au 1er message).

    Le chargement de ces modèles (~0,5 Go) se faisait paresseusement à la
    première recherche RAG, pénalisant le tout premier message. On le déplace au
    boot pour que le chat soit réactif dès la première question. Best-effort :
    un échec de préchauffage ne doit pas empêcher le serveur de démarrer.
    """
    try:
        from rag.embeddings import get_embedding_function
        get_embedding_function()(["préchauffage"])  # force le chargement réel
    except Exception:
        pass
    try:
        from rag.reranker import get_cross_encoder
        get_cross_encoder()  # charge le cross-encoder (si activé)
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: "FastAPI"):
    # Préchauffage en TÂCHE DE FOND (non bloquant) : le serveur est prêt
    # immédiatement, les modèles chauffent pendant que l'utilisateur se connecte
    # et saisit sa 1re question. Un préchargement bloquant retarderait (voire
    # gèlerait, sur vérif réseau HF Hub) le démarrage — à proscrire.
    threading.Thread(target=_warm_rag_models, daemon=True).start()
    # Workers Hermes chauds : lancés en fond, supervisés, coupés à l'arrêt.
    # Le spawn (Popen) ne bloque pas ; le préchauffage (~6 s) se fait dans le
    # worker, l'adaptateur retombe sur la CLI tant qu'un socket n'est pas prêt.
    from services import hermes_worker_manager
    hermes_worker_manager.start()
    try:
        yield
    finally:
        hermes_worker_manager.stop()


app = FastAPI(title="EduTutor API", version="0.1.0", lifespan=lifespan)

# Compte propriétaire des données pré-existantes (créé au démarrage).
auth.ensure_default_user()

# Front de dev (Vite). Application locale : auth par token (identifiant + mdp).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Authentification & contexte utilisateur ----------------------------------

@app.middleware("http")
async def _user_context(request, call_next):
    """Pose l'utilisateur courant (ContextVar) pour toute la requête, à partir du
    token. Les couches profondes (RAG, caches, store) résolvent alors le bon
    workspace SANS recevoir user_id en paramètre. L'AUTORISATION (401) reste
    faite par la dépendance `current_user` sur chaque endpoint protégé."""
    authz = request.headers.get("authorization", "")
    token = authz[7:] if authz.lower().startswith("bearer ") else ""
    user_id = auth.read_token(token) if token else None
    if user_id:
        workspace.set_current_user(user_id)
    return await call_next(request)


def current_user(authorization: str = Header(default="")) -> str:
    """Dépendance d'AUTORISATION : 401 si pas de token valide."""
    token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    user_id = auth.read_token(token) if token else None
    if user_id is None:
        raise HTTPException(401, "Authentification requise.")
    workspace.set_current_user(user_id)  # ceinture+bretelles (endpoints sync)
    return user_id


class CredentialsIn(BaseModel):
    email: str
    password: str


@app.post("/api/auth/register", status_code=201)
def register(body: CredentialsIn) -> dict:
    try:
        user = auth.create_user(body.email, body.password)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc))
    return {"token": auth.make_token(user["id"]), "user": user}


@app.post("/api/auth/login")
def login(body: CredentialsIn) -> dict:
    try:
        user = auth.authenticate(body.email, body.password)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc))
    return {"token": auth.make_token(user["id"]), "user": user}


@app.get("/api/auth/me")
def me(user_id: str = Depends(current_user)) -> dict:
    user = auth.get_user(user_id)
    if user is None:
        raise HTTPException(401, "Compte introuvable.")
    return {"id": user["id"], "email": user["email"], "created_at": user.get("created_at")}

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
def list_documents(user_id: str = Depends(current_user)) -> list[dict]:
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


def _enrich_vision_background(course_path: str) -> None:
    """Décrit les figures/schémas du cours et les indexe, en tâche de fond
    (un appel LLM par image = lent) : le cours texte est déjà disponible."""
    try:
        from rag.indexer import enrich_course_vision
        enrich_course_vision(course_path)
    except Exception:
        pass  # best-effort : le texte du cours reste indexé quoi qu'il arrive


@app.post("/api/documents")
def upload_document(file: UploadFile, user_id: str = Depends(current_user)) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in settings.SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Format non supporté : {suffix or '(aucun)'}")
    workspace.courses_dir().mkdir(parents=True, exist_ok=True)
    target = workspace.courses_dir() / Path(file.filename).name
    target.write_bytes(file.file.read())
    result = sync_courses_index()  # texte : rapide, disponible immédiatement
    # Figures/schémas : enrichis en arrière-plan (le texte n'attend pas). Le
    # thread hérite du contexte utilisateur (workspace du bon compte).
    if settings.RAG_VISION_ENABLED:
        ctx = contextvars.copy_context()
        threading.Thread(
            target=lambda: ctx.run(_enrich_vision_background, str(target)),
            daemon=True,
        ).start()
    return {"saved": target.name, "index": result}


@app.get("/api/documents/{filename}/file")
def get_document_file(filename: str, user_id: str = Depends(current_user)) -> FileResponse:
    """Sert le fichier du cours pour ouverture dans le navigateur (PDF inline)."""
    target = workspace.courses_dir() / Path(filename).name  # .name : pas de traversée
    if not target.exists():
        raise HTTPException(404, "Cours introuvable.")
    return FileResponse(
        target, filename=target.name, content_disposition_type="inline"
    )


@app.get("/api/deliverables")
def list_deliverables(user_id: str = Depends(current_user)) -> list[dict]:
    """Bibliothèque : tous les livrables générés (fiches, résumés, documents)."""
    return store.list_all_deliverables()


# --- Pièces jointes du chat -------------------------------------------------------

def _unique_attachment_path(name: str) -> Path:
    """Chemin de sauvegarde sans collision (suffixe _2, _3… si le nom existe)."""
    base = workspace.attachments_dir() / Path(name).name
    if not base.exists():
        return base
    stem, suffix = base.stem, base.suffix
    for index in range(2, 1000):
        candidate = workspace.attachments_dir() / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise HTTPException(500, "Trop de fichiers homonymes.")


@app.post("/api/attachments")
def upload_attachment(file: UploadFile, user_id: str = Depends(current_user)) -> dict:
    """Joint un fichier au chat : sauvegardé hors des cours (pas indexé au RAG)."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in settings.SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Format non supporté : {suffix or '(aucun)'}")
    workspace.attachments_dir().mkdir(parents=True, exist_ok=True)
    target = _unique_attachment_path(file.filename)
    target.write_bytes(file.file.read())
    return {"filename": target.name, "size": target.stat().st_size}


@app.get("/api/attachments/{filename}/file")
def get_attachment_file(filename: str, user_id: str = Depends(current_user)) -> FileResponse:
    target = workspace.attachments_dir() / Path(filename).name
    if not target.exists():
        raise HTTPException(404, "Pièce jointe introuvable.")
    return FileResponse(
        target, filename=target.name, content_disposition_type="inline"
    )


def _attachment_texts(filenames: list[str]) -> list[dict]:
    """Prépare chaque pièce jointe pour ancrer le tuteur :
    - IMAGE (schéma, diagramme) -> `image_path` : le modèle la VOIT (vision native
      Hermes), pas d'OCR (la vision lit aussi le texte de l'image) ;
    - autres formats -> `text` extrait via le pipeline documentaire (PDF/docx/pptx).
    """
    from rag.document_loader import load_document_segments

    attachments = []
    for name in filenames:
        target = workspace.attachments_dir() / Path(name).name
        if not target.exists():
            continue
        if target.suffix.lower() in settings.IMAGE_EXTENSIONS:
            attachments.append({"filename": target.name, "text": "",
                                "image_path": str(target.resolve())})
            continue
        try:
            segments = load_document_segments(str(target))
            text = "\n\n".join(s["text"] for s in segments)
        except Exception:
            text = ""  # format illisible : le tuteur le dira
        attachments.append({"filename": target.name, "text": text})
    return attachments


@app.delete("/api/documents/{filename}")
def delete_document(filename: str, user_id: str = Depends(current_user)) -> dict:
    target = workspace.courses_dir() / Path(filename).name  # .name : pas de traversée
    if not target.exists():
        raise HTTPException(404, "Cours introuvable.")
    target.unlink()
    result = sync_courses_index()
    return {"deleted": target.name, "index": result}


# --- Conversations --------------------------------------------------------------

@app.get("/api/conversations")
def list_conversations(user_id: str = Depends(current_user)) -> list[dict]:
    return store.list_conversations()


@app.post("/api/conversations", status_code=201)
def create_conversation(user_id: str = Depends(current_user)) -> dict:
    return store.create_conversation()


@app.get("/api/conversations/{conv_id}")
def get_conversation(conv_id: int, user_id: str = Depends(current_user)) -> dict:
    conv = store.get_conversation(conv_id)
    if conv is None:
        raise HTTPException(404, "Conversation introuvable.")
    return conv


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: int, user_id: str = Depends(current_user)) -> dict:
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
def post_message(conv_id: int, message: MessageIn, user_id: str = Depends(current_user)) -> StreamingResponse:
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
        att_dir = workspace.attachments_dir()
        user_message["attachments"] = [
            {"filename": a["filename"],
             "size": (att_dir / a["filename"]).stat().st_size}
            for a in attachments
        ]
    store.append_messages(conv_id, [user_message])

    events: queue.Queue = queue.Queue()

    # Chaque thread reçoit SA PROPRE copie du contexte (qui capture l'utilisateur
    # courant) : un même objet Context ne peut pas être « entré » (ctx.run) par
    # deux threads à la fois — sinon RuntimeError « cannot enter context: is
    # already entered », le worker meurt et le flux SSE tourne sans jamais répondre.

    # Titre de la discussion : en PARALLÈLE de la réponse (latence masquée).
    title_box: dict = {}
    title_thread = None
    if is_first:
        title_ctx = contextvars.copy_context()
        title_thread = threading.Thread(
            target=lambda: title_ctx.run(
                lambda: title_box.__setitem__("title", generate_title(content))),
            daemon=True,
        )
        title_thread.start()

    def worker() -> None:
        try:
            result = orchestrator.handle(
                content, history=history, use_planner=settings.HERMES_USE_PLANNER,
                last_deliverable=deliverable, on_event=events.put,
                attachments=attachments or None,
            )
        except Exception:
            result = None
        events.put({"type": "_result", "result": result})

    worker_ctx = contextvars.copy_context()
    threading.Thread(target=lambda: worker_ctx.run(worker), daemon=True).start()

    def stream():
        workspace.set_current_user(user_id)  # le générateur peut tourner hors requête
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
def export_deliverable(payload: ExportIn, user_id: str = Depends(current_user)) -> Response:
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

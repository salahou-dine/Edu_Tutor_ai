"""
EduTutor — interface étudiant du tuteur académique multi-agents.

L'interface n'appelle QUE la couche orchestration propre :
    from agents import orchestrator          # chat -> orchestrator.handle(...)
    from agents.titler import generate_title # titre de discussion (1er échange)
    from interface.exporters import ...      # exports des livrables (docx/md/pdf)

Elle n'affiche jamais d'éléments développeur (chunks, distances, top_k, prompt
Hermes, section_id, vectorstore, logs). L'étudiant voit : sa question, la
réponse (badge de mode + agents intervenus), les cartes livrables
téléchargeables, et l'historique des discussions.

PÉRIMÈTRE (hypothèse assumée) : application MONO-UTILISATEUR. L'état vit en
session Streamlit et les données (cours, vectorstore, conversations.json) sont
partagées et non concurrentes. Le passage multi-utilisateurs (authentification,
stockage par étudiant, gestion de la concurrence) est une phase ultérieure du
projet, pas un correctif ponctuel.
"""

import html
import json
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import streamlit as st


# --- Accès à la couche agent (sans chemin absolu) ---------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

# Les chemins (vectorstore, cours, conversations…) sont tous dérivés de la racine
# du projet dans config.settings : pas besoin de changer le répertoire courant.

from agents import orchestrator
from agents.titler import generate_title
from interface.exporters import (
    PDF_AVAILABLE,
    safe_filename,
    to_docx_bytes,
    to_markdown_bytes,
    to_pdf_bytes,
)
from rag.indexer import sync_courses_index

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


COURSES_DIR = PROJECT_ROOT / "data" / "courses"
UPLOAD_TYPES = ["md", "txt", "pdf", "docx", "pptx", "png", "jpg", "jpeg", "tiff", "bmp", "webp"]

# Les cours sont recopiés ici pour être servis par Streamlit (fichiers statiques)
# et ainsi ouvrables dans un nouvel onglet du navigateur.
STATIC_COURSES_DIR = Path(__file__).resolve().parent / "static" / "courses"
STATIC_COURSES_URL = "app/static/courses"

# Discussions persistées entre les sessions.
CONVERSATIONS_PATH = PROJECT_ROOT / "data" / "conversations.json"

# Plafond du nombre de discussions conservées : borne la taille du fichier
# (réécrit en entier à chaque message). Volontairement élevé — on ne purge que
# les fils les plus anciens (par dernière activité), jamais le fil ouvert.
MAX_CONVERSATIONS = 200

# Amorces pédagogiques GÉNÉRIQUES (indépendantes du corpus) : l'étudiant uploade
# ses propres cours, en nombre variable — on ne sait pas lesquels ni sur quoi il
# va interroger. Ces suggestions marchent quel que soit le cours et montrent ce
# que le tuteur sait faire (cf. persona EduTutor).
EXAMPLE_QUESTIONS = [
    "Résume-moi un de mes cours",
    "Explique-moi une notion que je n'ai pas comprise",
    "Interroge-moi pour réviser",
    "Aide-moi à résoudre un exercice",
]

# Libellé pédagogique (jamais le nom technique du mode) + classe CSS du badge.
MODE_INFO = {
    "course_grounded": ("Réponse basée sur ton cours", "course"),
    "mixed": ("Cours + complément pédagogique", "mixed"),
    "general_tutor": ("Réponse générale", "general"),
    "clarify": ("Quel cours ?", "mixed"),
    "course_summary": ("Résumé du cours", "course"),
    "idp": ("Analyse du document", "mixed"),
    "content": ("Ressource d'étude", "course"),
    "analyze": ("Analyse + résumé", "course"),
}

# Agents du système multi-agents : libellé affiché dans le chat pour montrer
# QUEL agent a répondu (le planner délègue ; on rend la délégation visible).
AGENT_INFO = {
    "tutor":       ("🎓", "Tuteur"),
    "idp":         ("🔎", "Analyse (IDP)"),
    "content":     ("📝", "Contenu"),
    "compose":     ("✍️", "Rédaction"),
    "clarify":     ("❓", "Clarification"),
    "no_document": ("📂", "Aucun document"),
}

# Icône par type de livrable (résumé / fiche de révision / document rédigé).
DELIVERABLE_ICON = {"summary": "📄", "revision": "🎴", "document": "📝"}

ERROR_MESSAGE = (
    "Le tuteur n'a pas pu générer une réponse pour le moment. "
    "Réessaie dans quelques instants."
)


st.set_page_config(
    page_title="EduTutor — Tuteur académique",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown(
    """
    <style>
    :root {
        --edu-border: rgba(255, 255, 255, 0.12);
        --edu-muted: #9aa3b2;
        --edu-panel: #1f242c;
        --edu-ink: #ececf0;
        --edu-accent: #5eb1c2;
    }

    .main .block-container {
        padding-top: 1rem;
        padding-bottom: 1.25rem;
        max-width: 1080px;
    }

    [data-testid="stSidebar"] {
        border-right: 1px solid var(--edu-border);
    }

    .edu-title {
        color: var(--edu-ink);
        font-size: 1.6rem;
        font-weight: 760;
        margin-bottom: 0.1rem;
    }

    .edu-subtitle {
        color: var(--edu-muted);
        font-size: 0.95rem;
        margin-bottom: 0.9rem;
    }

    /* Badge de mode pédagogique (discret) */
    .edu-badge {
        display: inline-block;
        padding: 0.16rem 0.65rem;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 650;
        margin: 0.1rem 0 0.6rem;
    }
    .edu-badge.course  { background: #e6f4ea; color: #1e7d4f; }
    .edu-badge.mixed   { background: #eaf0fb; color: #2f5fc0; }
    .edu-badge.general { background: #f1eefa; color: #6b4bb0; }

    /* Agents ayant répondu : rangée de chips (montre la délégation du planner) */
    .edu-agents {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 0.3rem;
        margin: 0.1rem 0 0.6rem;
    }
    .edu-agents .edu-agents-label {
        font-size: 0.72rem;
        color: #8a8f98;
        margin-right: 0.15rem;
    }
    .edu-agentchip {
        display: inline-flex;
        align-items: center;
        gap: 0.25rem;
        padding: 0.12rem 0.55rem;
        border-radius: 999px;
        font-size: 0.74rem;
        font-weight: 600;
        background: #eef1f5;
        color: #3a3f47;
        border: 1px solid #e0e4ea;
    }
    .edu-agents .edu-agent-arrow { color: #b3b9c2; font-size: 0.8rem; }

    /* En-tête de la carte livrable (façon Artifact) */
    .edu-deliverable-head {
        font-size: 1.02rem;
        margin: 0.1rem 0 0.5rem;
        color: var(--edu-ink);
    }

    /* Lien d'ouverture d'un cours (nouvel onglet) dans la liste */
    .edu-course-link {
        display: block;
        padding: 0.45rem 0.65rem;
        margin: 0.3rem 0;
        border: 1px solid var(--edu-border);
        border-radius: 8px;
        background: rgba(255, 255, 255, 0.05);
        color: var(--edu-ink) !important;
        text-decoration: none !important;
        font-size: 0.88rem;
        font-weight: 500;
        overflow-wrap: anywhere;
    }
    .edu-course-link:hover {
        background: rgba(255, 255, 255, 0.12);
    }

    /*  FRAGILE : les sélecteurs `data-testid="st…"` ci-dessous dépendent des
       INTERNES de Streamlit (non garantis stables entre versions). C'est le seul
       moyen d'obtenir ce rendu épuré (uploader renommé, chat sans avatar, bulles
       à droite). À REVALIDER à chaque montée de version de Streamlit ; la borne
       `streamlit<2.0` de requirements.txt protège des cassures majeures. */

    /* Renomme le bouton par défaut de l'uploader en "Ajouter mes cours" */
    [data-testid="stFileUploaderDropzone"] button p { font-size: 0 !important; }
    [data-testid="stFileUploaderDropzone"] button p::after {
        content: "Ajouter mes cours";
        font-size: 0.9rem !important;
    }

    /* Historique : hauteur selon le contenu, plafonnée à la zone visible, puis
       défilement interne (pas de grand vide quand il y a peu de discussions). */
    .st-key-conv_history {
        max-height: calc(100vh - 360px);
        overflow-y: auto;
        overflow-x: hidden;
    }
    .st-key-course_list button {
        justify-content: flex-start !important;
        text-align: left !important;
        font-weight: 500 !important;
    }

    /* Historique façon Claude : lignes compactes, police réduite, une seule ligne
       tronquée, discussion active en gris subtil (PAS de bande orange). */
    .st-key-conv_history [data-testid="stVerticalBlock"] { gap: 0.05rem !important; }
    .st-key-conv_history button {
        justify-content: flex-start !important;
        text-align: left !important;
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        border-radius: 8px !important;
        padding: 0.3rem 0.5rem !important;
        min-height: 0 !important;
        color: #c7c9d1 !important;
        overflow: hidden !important;
    }
    /* Les conteneurs internes du bouton grandissent à 100% et recentrent le texte :
       on les force à gauche pour un alignement façon Claude. */
    .st-key-conv_history button > div,
    .st-key-conv_history button [data-testid="stMarkdownContainer"] {
        justify-content: flex-start !important;
        text-align: left !important;
        width: 100% !important;
    }
    .st-key-conv_history button p {
        display: block !important;
        width: 100% !important;
        text-align: left !important;
        font-size: 0.875rem !important;
        font-weight: 400 !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
    }
    /* Dièse « # » devant chaque titre (remplace l'ancienne bulle 💬). */
    .st-key-conv_history button p::before {
        content: "#";
        color: #7b7f8a;
        font-weight: 500;
        margin-right: 0.45rem;
    }
    .st-key-conv_history button:hover {
        background: rgba(255, 255, 255, 0.06) !important;
        color: #ffffff !important;
    }
    /* Discussion active : gris subtil (le ciblage précis de l'id est injecté
       dynamiquement dans render_history — voir _highlight_active_conversation).
       On n'utilise PLUS type="primary" -> plus jamais l'orange du thème. */

    /* Chat épuré (façon Claude/ChatGPT) : pas d'avatar, pas de bulle colorée */
    [data-testid^="stChatMessageAvatar"] { display: none !important; }
    [data-testid="stChatMessage"] {
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        padding: 0.15rem 0 !important;
        gap: 0 !important;
    }

    /* Questions de l'étudiant alignées à DROITE (façon ChatGPT) */
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        flex-direction: row-reverse;
        text-align: right;
    }
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
        [data-testid="stChatMessageContent"] {
        background: #3b4252;
        color: #f0f2f6;
        border-radius: 14px;
        padding: 0.5rem 0.9rem;
        width: fit-content;
        max-width: 80%;
        margin-left: auto;
        text-align: left;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# --- Helpers ----------------------------------------------------------------

def list_course_titles() -> list[str]:
    """Noms des cours présents dans data/courses/ (sans chemin technique)."""
    if not COURSES_DIR.exists():
        return []
    return sorted(p.name for p in COURSES_DIR.iterdir() if p.is_file())


def sync_static_courses() -> None:
    """
    Recopie les cours dans interface/static/courses/ pour qu'ils soient servis
    par Streamlit (et ouvrables dans un nouvel onglet). Synchronise les ajouts
    et les suppressions, sans recopier inutilement.
    """
    STATIC_COURSES_DIR.mkdir(parents=True, exist_ok=True)
    sources = (
        {p.name: p for p in COURSES_DIR.iterdir() if p.is_file()}
        if COURSES_DIR.exists()
        else {}
    )
    for name, src in sources.items():
        dst = STATIC_COURSES_DIR / name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass
    # Retire les fichiers statiques dont le cours n'existe plus.
    for p in STATIC_COURSES_DIR.iterdir():
        if p.is_file() and p.name not in sources:
            try:
                p.unlink()
            except OSError:
                pass


def course_url(name: str) -> str:
    """URL servie par Streamlit pour ouvrir un cours dans un nouvel onglet."""
    return f"{STATIC_COURSES_URL}/{quote(name)}"


def save_uploaded_courses(uploaded_files) -> int:
    """Sauvegarde (ou écrase) les cours uploadés dans data/courses/. Retourne le nb."""
    COURSES_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for uploaded in uploaded_files:
        safe_name = Path(uploaded.name).name
        (COURSES_DIR / safe_name).write_bytes(uploaded.getbuffer())
        saved += 1
    return saved


def delete_course(name: str) -> None:
    """
    Supprime le fichier d'un cours de data/courses/. La désindexation (retrait de
    ses chunks du vectorstore) et le nettoyage de la copie statique se font au
    rerun suivant (sync incrémental via ensure_index_up_to_date + sync_static_courses).
    """
    path = COURSES_DIR / Path(name).name
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def courses_signature() -> frozenset:
    """Empreinte du dossier des cours (nom + date de modif) pour détecter un changement."""
    if not COURSES_DIR.exists():
        return frozenset()
    return frozenset(
        (p.name, p.stat().st_mtime_ns)
        for p in COURSES_DIR.iterdir()
        if p.is_file()
    )


def ensure_index_up_to_date() -> None:
    """
    Auto-indexation INCRÉMENTALE : on synchronise le vectorstore avec le dossier
    des cours. Chaque cours n'est vectorisé qu'une fois (à l'ajout) ; un cours
    inchangé n'est jamais réindexé. L'empreinte de session évite même de lancer
    le sync à chaque rerun ; quand il tourne, il ne fait que les différences.
    """
    signature = courses_signature()
    if st.session_state.get("indexed_signature") == signature:
        return

    with st.spinner("Indexation des cours…"):
        result = sync_courses_index()
    st.session_state.indexed_signature = signature

    touched = len(result.get("added", [])) + len(result.get("updated", []))
    if touched:
        st.toast(f"{touched} cours indexé(s).", icon="✅")
    for err in result.get("errors", []):
        st.toast(err, icon="⚠️")


def render_mode_badge(mode: str) -> None:
    label, css_class = MODE_INFO.get(mode, ("Réponse", "general"))
    st.markdown(
        f"<span class='edu-badge {css_class}'>{html.escape(label)}</span>",
        unsafe_allow_html=True,
    )


def render_agents(agents: list[str]) -> None:
    """Montre, sur une réponse, quel(s) agent(s) y ont répondu (dans l'ordre d'exécution).

    Rend visible la délégation du planner : « 🔎 Analyse (IDP) → 📝 Contenu »,
    ou « 🎓 Tuteur » pour une réponse directe.
    """
    known = [a for a in (agents or []) if a in AGENT_INFO]
    if not known:
        return
    chips = []
    for agent in known:
        emoji, label = AGENT_INFO[agent]
        chips.append(
            f"<span class='edu-agentchip'>{emoji} {html.escape(label)}</span>"
        )
    row = "<span class='edu-agent-arrow'>→</span>".join(chips)
    st.markdown(
        f"<div class='edu-agents'><span class='edu-agents-label'>Répondu par</span>{row}</div>",
        unsafe_allow_html=True,
    )


def _deliverable_downloads(deliverable: dict, seed: str, short: bool = False) -> None:
    """Boutons de téléchargement d'un livrable (.docx, .md, et .pdf si disponible).

    `short` = libellés courts (pour la carte, plus étroite) ; le PDF n'apparaît
    que si les dépendances sont installées (PDF_AVAILABLE).
    """
    md = deliverable.get("markdown", "")
    title = deliverable.get("title", "livrable")
    labels = (("⬇️ .docx", "⬇️ .md", "⬇️ .pdf") if short
              else ("⬇️ Word (.docx)", "⬇️ Markdown (.md)", "⬇️ PDF (.pdf)"))
    cols = st.columns(3 if PDF_AVAILABLE else 2)
    with cols[0]:
        st.download_button(
            labels[0], data=to_docx_bytes(md, title),
            file_name=safe_filename(title, "docx"), mime=_DOCX_MIME,
            key=f"docx_{seed}", use_container_width=True,
        )
    with cols[1]:
        st.download_button(
            labels[1], data=to_markdown_bytes(md),
            file_name=safe_filename(title, "md"), mime="text/markdown",
            key=f"md_{seed}", use_container_width=True,
        )
    if PDF_AVAILABLE:
        with cols[2]:
            st.download_button(
                labels[2], data=to_pdf_bytes(md, title),
                file_name=safe_filename(title, "pdf"), mime="application/pdf",
                key=f"pdf_{seed}", use_container_width=True,
            )


@st.dialog("Livrable", width="large")
def _deliverable_dialog(deliverable: dict, seed: str) -> None:
    """Vue plein format (modal) d'un livrable : titre + contenu + téléchargements."""
    icon = DELIVERABLE_ICON.get(deliverable.get("type"), "📄")
    st.markdown(f"### {icon} {deliverable.get('title', 'Livrable')}")
    st.markdown(deliverable.get("markdown", ""))
    st.divider()
    _deliverable_downloads(deliverable, f"dlg_{seed}")


def render_deliverable(deliverable: dict, seed: str) -> None:
    """Carte livrable dans le chat (façon Artifact) : en-tête + aperçu défilant + actions."""
    icon = DELIVERABLE_ICON.get(deliverable.get("type"), "📄")
    title = html.escape(deliverable.get("title", "Livrable"))
    with st.container(border=True):
        st.markdown(
            f"<div class='edu-deliverable-head'>{icon} <b>{title}</b></div>",
            unsafe_allow_html=True,
        )
        # Conteneur à hauteur fixe : le contenu défile dans la carte (façon Artifact).
        with st.container(height=340):
            st.markdown(deliverable.get("markdown", ""))
        if st.button("⤢ Agrandir", key=f"exp_{seed}", use_container_width=True):
            _deliverable_dialog(deliverable, seed)
        _deliverable_downloads(deliverable, seed, short=True)


def render_assistant_message(message: dict, seed: str = "0") -> None:
    """
    Affiche une réponse du tuteur : badge + texte de la réponse.

    Source d'affichage UNIQUE = le texte de la réponse (qui contient déjà, en
    mode cours, le point 4 « Indication de la partie du cours » et le point 5
    « Question de vérification »). On n'ajoute donc plus de carte d'indication
    ni de bloc de vérification séparés (évite le doublon).
    """
    if message.get("status") == "success" and message.get("mode"):
        render_mode_badge(message["mode"])
    if message.get("agents"):
        render_agents(message["agents"])
    st.markdown(message["content"])
    if message.get("deliverable"):
        render_deliverable(message["deliverable"], seed)


# --- Discussions (multi-conversations façon Claude) -------------------------

def load_conversations() -> dict | None:
    """Charge les discussions persistées (ou None si absent/illisible)."""
    if not CONVERSATIONS_PATH.exists():
        return None
    try:
        data = json.loads(CONVERSATIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("conversations"), list):
        return data
    return None


def prune_conversations() -> None:
    """
    Borne le nombre de discussions à MAX_CONVERSATIONS en supprimant les plus
    anciennes (par dernière activité). Ne supprime jamais la discussion ouverte.
    """
    convs = st.session_state.conversations
    if len(convs) <= MAX_CONVERSATIONS:
        return

    def activity(conv: dict) -> float:
        return conv.get("updated_at") or conv.get("created_at") or 0.0

    # Les plus récentes d'abord ; on garde le haut de la liste.
    kept = sorted(convs, key=activity, reverse=True)[:MAX_CONVERSATIONS]

    # Garantir la présence de la discussion ouverte même si elle tombe hors du top.
    current_id = st.session_state.current_id
    if current_id is not None and all(c["id"] != current_id for c in kept):
        current = next((c for c in convs if c["id"] == current_id), None)
        if current is not None:
            kept = kept[: MAX_CONVERSATIONS - 1] + [current]

    kept_ids = {c["id"] for c in kept}
    # Filtrer la liste d'origine pour préserver l'ordre de création.
    st.session_state.conversations = [c for c in convs if c["id"] in kept_ids]


def save_conversations() -> None:
    """Sauvegarde les discussions sur disque (JSON), pour les retrouver plus tard."""
    prune_conversations()  # borne la taille du fichier avant écriture
    data = {
        "conversations": st.session_state.conversations,
        "next_conv_id": st.session_state.next_conv_id,
        "current_id": st.session_state.current_id,
    }
    try:
        CONVERSATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONVERSATIONS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def init_conversation_state() -> None:
    """
    Initialise l'état : restaure les discussions persistées si elles existent,
    sinon démarre une discussion vierge.
    """
    if "conversations" not in st.session_state:
        saved = load_conversations()
        if saved and saved.get("conversations"):
            st.session_state.conversations = saved["conversations"]
            ids = [c["id"] for c in st.session_state.conversations]
            st.session_state.next_conv_id = max(
                int(saved.get("next_conv_id", 0)), max(ids, default=-1) + 1
            )
            current = saved.get("current_id")
            st.session_state.current_id = current if current in ids else (
                ids[-1] if ids else None
            )
            if st.session_state.current_id is None:
                create_conversation()
        else:
            st.session_state.conversations = []
            st.session_state.next_conv_id = 0
            st.session_state.current_id = None
            create_conversation()


def create_conversation() -> int:
    """Crée une discussion vierge et la rend active."""
    cid = st.session_state.next_conv_id
    st.session_state.next_conv_id += 1
    now = time.time()
    st.session_state.conversations.append(
        {
            "id": cid,
            "title": "Nouvelle discussion",
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
    )
    st.session_state.current_id = cid
    return cid


def format_relative_time(ts: float | None) -> str:
    """
    Date lisible (relative) pour la liste des discussions. Permet de différencier
    deux discussions qui portent le même titre (ex. « Bonjour ») par leur date,
    sans avoir à dédoublonner les titres.
    """
    if not ts:
        return ""
    now = datetime.now()
    moment = datetime.fromtimestamp(ts)
    seconds = (now - moment).total_seconds()
    if seconds < 60:
        return "à l'instant"
    if seconds < 3600:
        return f"il y a {int(seconds // 60)} min"
    if moment.date() == now.date():
        return f"aujourd'hui {moment.strftime('%H:%M')}"
    if moment.date() == (now - timedelta(days=1)).date():
        return f"hier {moment.strftime('%H:%M')}"
    return moment.strftime("%d/%m/%Y")


def current_conversation() -> dict:
    """Retourne la discussion active (la dernière en secours)."""
    for conv in st.session_state.conversations:
        if conv["id"] == st.session_state.current_id:
            return conv
    return st.session_state.conversations[-1]


def make_title(text: str, limit: int = 40) -> str:
    """Titre court dérivé de la première question (comme Claude/ChatGPT)."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def start_new_discussion() -> None:
    """
    Ouvre une discussion vierge. L'ancienne n'est pas supprimée : elle reste
    dans l'historique. Si la discussion courante est déjà vide, on ne crée rien.
    """
    if current_conversation()["messages"]:
        create_conversation()


def _add_user_turn(conv: dict, label: str) -> list[dict]:
    """Ajoute le tour étudiant au fil et l'affiche ; retourne l'historique AVANT lui."""
    history = [{"role": m["role"], "content": m["content"]} for m in conv["messages"]]
    if not conv["messages"]:
        conv["title"] = make_title(label)
    conv["messages"].append({"role": "user", "content": label})
    conv["updated_at"] = time.time()
    with st.chat_message("user"):
        st.markdown(label)
    return history


def _store_assistant(conv: dict, result: dict | None) -> None:
    """Construit, rend et persiste la réponse du tuteur depuis un résultat orchestrateur."""
    if not result or result.get("status") == "error":
        message = {
            "role": "assistant",
            "status": "error",
            "content": (result or {}).get("answer") or ERROR_MESSAGE,
        }
    else:
        # Badge : pour le tuteur, on garde le mode pédagogique fin ; sinon le kind.
        kind = result.get("kind", "tutor")
        mode = (result.get("payload") or {}).get("mode") if kind == "tutor" else kind
        message = {
            "role": "assistant",
            "status": "success",
            "content": result.get("answer", ""),
            "mode": mode or "general_tutor",
            # Agents ayant réellement répondu (ordre d'exécution) → affichés dans le chat.
            "agents": result.get("steps_run", []),
        }
        # Livrable structuré (résumé / fiche) → rendu en carte téléchargeable.
        if result.get("deliverable"):
            message["deliverable"] = result["deliverable"]
    # seed = index que ce message occupera (cohérent avec le rejeu enumerate).
    render_assistant_message(message, seed=str(len(conv["messages"])))
    conv["messages"].append(message)


def _last_deliverable(conv: dict) -> dict | None:
    """Dernier livrable produit dans la discussion (cible d'une itération)."""
    for message in reversed(conv["messages"]):
        if message.get("deliverable"):
            return message["deliverable"]
    return None


def submit_question(question: str) -> None:
    """Traite une demande en langage naturel via l'orchestrateur multi-agents.

    Au 1er échange, le titre de la discussion est généré (Sonnet) EN PARALLÈLE de
    la réponse : comme la réponse est plus lente, le titre est prêt sans surcoût
    de latence perceptible (façon Claude)."""
    cleaned = (question or "").strip()
    if not cleaned:
        return
    conv = current_conversation()
    is_first = not conv["messages"]
    # Livrable courant pour une éventuelle itération en langage naturel
    # (« raccourcis-le », « ajoute une section »…) -> réutilise le contenu.
    last_deliverable = _last_deliverable(conv)
    history = _add_user_turn(conv, cleaned)

    # Titre en tâche de fond (seulement au 1er message), calculé pendant la réponse.
    title_box: dict = {}
    title_thread = None
    if is_first and not conv.get("title_generated"):
        title_thread = threading.Thread(
            target=lambda: title_box.__setitem__("title", generate_title(cleaned)),
            daemon=True,
        )
        title_thread.start()

    with st.chat_message("assistant"):
        with st.spinner("Le tuteur réfléchit…"):
            try:
                result = orchestrator.handle(
                    cleaned,
                    history=history,
                    selected_doc=st.session_state.get("selected_doc"),
                    use_planner=True,  # orchestrateur intelligent (Sonnet planifie ; filet déterministe en secours)
                    last_deliverable=last_deliverable,  # itération = réutilise le livrable courant
                )
            except Exception:
                result = None
        _store_assistant(conv, result)

    # Récupère le titre (déjà prêt le plus souvent) et rafraîchit la sidebar.
    if title_thread is not None and result and result.get("status") != "error":
        with st.spinner("…"):
            title_thread.join(timeout=90)
        conv["title_generated"] = True
        if title_box.get("title"):
            conv["title"] = title_box["title"]
            st.rerun()


# --- Sidebar ----------------------------------------------------------------

def render_history() -> None:
    """Liste des discussions (avec contenu), façon « Récentes » de Claude.

    Le conteneur occupe l'espace restant de la colonne (hauteur gérée en CSS via
    .st-key-conv_history) et défile quand l'historique devient long.
    """
    st.markdown("### Discussions")
    listed = [c for c in st.session_state.conversations if c["messages"]]
    # Tri par dernière activité décroissante : une discussion où l'on vient
    # d'écrire (updated_at récent) remonte en haut, même si elle est ancienne.
    listed.sort(
        key=lambda c: c.get("updated_at") or c.get("created_at") or 0, reverse=True
    )

    _highlight_active_conversation(st.session_state.current_id)
    with st.container(key="conv_history"):
        if not listed:
            st.caption("Aucune discussion enregistrée pour le moment.")
            return
        for conv in listed:  # plus récente (dernière activité) en haut
            # Date en INFOBULLE (au survol) plutôt qu'en ligne : garde l'espacement
            # compact façon Claude tout en distinguant deux titres identiques.
            stamp = format_relative_time(conv.get("updated_at") or conv.get("created_at"))
            if st.button(
                conv["title"],  # le « # » est ajouté en CSS (::before), pas dans le label
                key=f"conv_{conv['id']}",
                use_container_width=True,
                type="secondary",  # jamais "primary" -> pas d'orange ; actif surligné en CSS
                help=stamp or None,
            ):
                st.session_state.current_id = conv["id"]  # rouvrir + continuer
                st.rerun()


def _highlight_active_conversation(active_id) -> None:
    """Surligne la discussion ouverte (gris subtil) en ciblant sa classe de clé
    Streamlit (.st-key-conv_<id>) — robuste, sans dépendre du type "primary"."""
    if not active_id:
        return
    st.markdown(
        f"""
        <style>
        .st-key-conv_{active_id} button {{
            background: rgba(255, 255, 255, 0.09) !important;
            color: #ffffff !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("## 🎓 EduTutor")

        # --- Nouvelle discussion (l'ancienne reste dans l'historique) ---
        if st.button("➕ Nouvelle discussion", use_container_width=True, type="primary"):
            start_new_discussion()
            st.rerun()

        # --- Mes cours (auto-indexés à l'ajout) ---
        courses = list_course_titles()
        st.markdown(f"### Mes cours ({len(courses)})")
        # Clé dynamique : on la change après chaque upload pour VIDER le widget
        # (sinon les fichiers restent affichés avec leur croix).
        st.session_state.setdefault("uploader_round", 0)
        uploaded_files = st.file_uploader(
            "Formats acceptés : .md, .txt, .pdf",
            type=UPLOAD_TYPES,
            accept_multiple_files=True,
            key=f"course_uploader_{st.session_state.uploader_round}",
        )
        if uploaded_files:
            save_uploaded_courses(uploaded_files)
            st.session_state.uploader_round += 1  # réinitialise l'uploader
            # L'indexation se lance au rerun (ensure_index_up_to_date).
            st.rerun()

        if courses:
            # Recherche + liste scrollable cliquable (clic = ouvrir le cours).
            query = st.text_input(
                "Rechercher un cours",
                key="course_search",
                placeholder="Rechercher un cours…",
                label_visibility="collapsed",
            )
            needle = (query or "").strip().lower()
            filtered = [c for c in courses if needle in c.lower()] if needle else courses

            with st.container(height=220, key="course_list"):
                if not filtered:
                    st.caption("Aucun cours ne correspond.")
                for name in filtered:
                    if st.session_state.get("confirm_delete") == name:
                        # Confirmation inline (clé par nom -> pas d'état résiduel).
                        st.caption(f"Supprimer « {name} » ?")
                        c_yes, c_no = st.columns(2)
                        with c_yes:
                            if st.button(
                                "Oui, supprimer",
                                key=f"confyes_{name}",
                                type="primary",
                                use_container_width=True,
                            ):
                                delete_course(name)
                                st.session_state.confirm_delete = None
                                st.toast(f"Cours supprimé : {name}", icon="🗑️")
                                st.rerun()
                        with c_no:
                            if st.button(
                                "Annuler", key=f"confno_{name}", use_container_width=True
                            ):
                                st.session_state.confirm_delete = None
                                st.rerun()
                        continue

                    # Lien d'ouverture (clic) + bouton de suppression.
                    col_link, col_del = st.columns(
                        [0.82, 0.18], vertical_alignment="center"
                    )
                    with col_link:
                        st.markdown(
                            f'<a class="edu-course-link" href="{course_url(name)}" '
                            f'target="_blank" rel="noopener">📄 {html.escape(name)}</a>',
                            unsafe_allow_html=True,
                        )
                    with col_del:
                        if st.button(
                            "🗑", key=f"ask_del_{name}", use_container_width=True
                        ):
                            st.session_state.confirm_delete = name
                            st.rerun()
        else:
            st.caption("Aucun cours pour le moment.")

        # --- Historique des discussions (en bas) ---
        st.divider()
        render_history()


# --- Application ------------------------------------------------------------

def main() -> None:
    init_conversation_state()
    ensure_index_up_to_date()  # auto-indexation si les cours ont changé
    sync_static_courses()      # rend les cours ouvrables dans un nouvel onglet

    render_sidebar()

    st.markdown(
        "<div class='edu-title'>Pose ta question au tuteur</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div class='edu-subtitle'>EduTutor t'explique le cours, t'indique la "
        "partie à revoir et vérifie ta compréhension.</div>",
        unsafe_allow_html=True,
    )


    # Suggestions de questions (cliquables).
    st.markdown("**Exemples de questions :**")
    pending_question = None
    example_cols = st.columns(2)
    for index, example in enumerate(EXAMPLE_QUESTIONS):
        with example_cols[index % 2]:
            if st.button(example, key=f"example_{index}", use_container_width=True):
                pending_question = example

    # Échanges de la discussion active.
    for index, message in enumerate(current_conversation()["messages"]):
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                render_assistant_message(message, seed=str(index))
            else:
                st.markdown(message["content"])

    # Saisie libre (épinglée en bas par Streamlit).
    typed_question = st.chat_input("Pose une question sur le cours…")

    question = typed_question or pending_question
    if question and question.strip():
        submit_question(question)

    # Persistance : on sauvegarde l'état des discussions à la fin de chaque run.
    save_conversations()


if __name__ == "__main__":
    main()

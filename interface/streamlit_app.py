"""
EduTutor — interface étudiant du tuteur académique.

L'interface n'appelle QUE la couche agent propre :
    from agents.tutor_agent import answer_student_question_for_ui

Elle n'affiche jamais d'éléments développeur (chunks, distances, top_k, prompt
Hermes, vectorstore, logs). L'étudiant voit : sa question, la réponse du tuteur,
l'indication de la partie du cours, la question de vérification, et l'historique.

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

from agents.tutor_agent import answer_student_question_for_ui
from rag.indexer import sync_courses_index


COURSES_DIR = PROJECT_ROOT / "data" / "courses"
UPLOAD_TYPES = ["md", "txt", "pdf"]

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
}

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
    .st-key-conv_history button,
    .st-key-course_list button {
        justify-content: flex-start !important;
        text-align: left !important;
        font-weight: 500 !important;
    }

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


def render_assistant_message(message: dict) -> None:
    """
    Affiche une réponse du tuteur : badge + texte de la réponse.

    Source d'affichage UNIQUE = le texte de la réponse (qui contient déjà, en
    mode cours, le point 4 « Indication de la partie du cours » et le point 5
    « Question de vérification »). On n'ajoute donc plus de carte d'indication
    ni de bloc de vérification séparés (évite le doublon).
    """
    if message.get("status") == "success" and message.get("mode"):
        render_mode_badge(message["mode"])
    st.markdown(message["content"])


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


def submit_question(question: str) -> None:
    """
    Traite une question dans la discussion ACTIVE.

    Affiche la question IMMÉDIATEMENT dans le fil, puis construit la réponse
    sous l'indicateur d'attente (façon Claude/ChatGPT). Pas de rerun : les deux
    messages sont rendus en ligne et persistés dans l'état.
    """
    cleaned = (question or "").strip()
    if not cleaned:
        return

    conv = current_conversation()
    # Historique AVANT d'ajouter la nouvelle question (contexte des échanges).
    history = [
        {"role": m["role"], "content": m["content"]} for m in conv["messages"]
    ]
    if not conv["messages"]:
        conv["title"] = make_title(cleaned)  # titre tiré de la 1re question
    conv["messages"].append({"role": "user", "content": cleaned})
    conv["updated_at"] = time.time()  # dernière activité (affichée dans l'historique)

    # La question apparaît tout de suite dans le fil.
    with st.chat_message("user"):
        st.markdown(cleaned)

    # La réponse se construit sous la question, avec l'indicateur d'attente.
    with st.chat_message("assistant"):
        with st.spinner("Le tuteur réfléchit…"):
            try:
                result = answer_student_question_for_ui(
                    cleaned, history=history
                )
            except Exception:
                result = None

        if not result or result.get("status") != "success":
            message = {
                "role": "assistant",
                "status": (result or {}).get("status", "error"),
                "content": ERROR_MESSAGE,
            }
        else:
            message = {
                "role": "assistant",
                "status": "success",
                "content": result["student_answer"],
                "mode": result["mode"],
                "course_indications": result["course_indications"],
                "verification_question": result["verification_question"],
            }
        render_assistant_message(message)

    conv["messages"].append(message)


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

    with st.container(key="conv_history"):
        if not listed:
            st.caption("Aucune discussion enregistrée pour le moment.")
            return
        for conv in listed:  # plus récente (dernière activité) en haut
            is_active = conv["id"] == st.session_state.current_id
            if st.button(
                f"💬 {conv['title']}",
                key=f"conv_{conv['id']}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.session_state.current_id = conv["id"]  # rouvrir + continuer
                st.rerun()
            # Date de dernière activité : distingue deux discussions de même titre.
            stamp = format_relative_time(conv.get("updated_at") or conv.get("created_at"))
            if stamp:
                st.caption(stamp)


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
    for message in current_conversation()["messages"]:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                render_assistant_message(message)
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

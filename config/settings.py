"""
Configuration centrale du tuteur pédagogique Hermes Education.

Aucune clé API ici. Tous les chemins sont dérivés de la racine du projet
(calculée relativement à ce fichier) pour éviter les chemins absolus en dur.
"""

from pathlib import Path

# --- Chemins principaux -----------------------------------------------------

# Racine du projet : hermes-education/ (ce fichier est dans config/).
PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_ROOT / "data"
COURSES_DIR = DATA_DIR / "courses"        # cours actifs (indexés)
SAMPLES_DIR = DATA_DIR / "samples"        # exemples développeur (non indexés)
VECTORSTORE_PATH = DATA_DIR / "vectorstore"

# Formats de cours acceptés pour le MVP (.pdf si PyMuPDF est installé).
SUPPORTED_EXTENSIONS = (".md", ".txt", ".pdf")

# Prompt sauvegardé pour le fallback manuel (quand Hermes n'est pas appelable).
LAST_PROMPT_PATH = DATA_DIR / "last_tutor_prompt.md"

COLLECTION_NAME = "course_chunks"

# --- Embedding --------------------------------------------------------------

# Modèle d'embedding MULTILINGUE : permet de retrouver un cours en anglais à
# partir d'une question en français (et inversement). Téléchargé au 1er usage.
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
# Espace de distance ChromaDB : cosinus -> distances stables dans [0, 2].
EMBEDDING_SPACE = "cosine"

# --- Récupération RAG -------------------------------------------------------

DEFAULT_TOP_K = 3

# Longueur d'un extrait lisible affiché à l'étudiant (en caractères).
# Court et lisible pour une interface étudiant (~250-350 caractères).
EXCERPT_MIN_CHARS = 60
EXCERPT_MAX_CHARS = 90

# --- Seuils de mode pédagogique --------------------------------------------
#
# Basés sur la distance (L2) du MEILLEUR chunk retrouvé : plus la distance est
# faible, plus le passage est sémantiquement proche de la question.
#
# Calibrés pour l'embedding MULTILINGUE + distance COSINUS (échelle [0, 2]) :
# question FR précise sur un cours -> ~0.25-0.40 ; question vague ou hors-sujet
# -> ~0.70+. Valeurs dans la config car dépendantes du modèle et du corpus.
COURSE_GROUNDED_MAX_DISTANCE = 0.45  # < seuil  -> course_grounded
MIXED_MAX_DISTANCE = 0.65            # < seuil  -> mixed, sinon general_tutor

# --- Hermes -----------------------------------------------------------------

DEFAULT_SKILL_NAME = "education-tutor"
HERMES_TIMEOUT_SECONDS = 120

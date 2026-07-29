"""
Configuration centrale du tuteur pédagogique Hermes Education.

Aucune clé API ici. Tous les chemins sont dérivés de la racine du projet
(calculée relativement à ce fichier) pour éviter les chemins absolus en dur.
"""

import os
import tempfile
from pathlib import Path

# --- Chemins principaux -----------------------------------------------------

# Racine du projet : hermes-education/ (ce fichier est dans config/).
PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_ROOT / "data"
COURSES_DIR = DATA_DIR / "courses"        # cours actifs (indexés)
SAMPLES_DIR = DATA_DIR / "samples"        # exemples développeur (non indexés)
VECTORSTORE_PATH = DATA_DIR / "vectorstore"

# Multi-agents : artefacts documentaires (IDP) et contenus générés (agent contenu).
# Cachés sur disque, indexés par doc_id (hash de contenu). Voir agents/document_store.py.
ARTIFACTS_DIR = DATA_DIR / "artifacts"
GENERATED_DIR = DATA_DIR / "generated"

# Pièces jointes du chat (front web) : fichiers ponctuels joints à un message,
# distincts des cours (pas indexés dans le RAG). Ils alimentent la Bibliothèque.
ATTACHMENTS_DIR = DATA_DIR / "attachments"
# Budget de texte extrait d'une pièce jointe injecté au tuteur (caractères).
ATTACHMENT_CONTEXT_MAX_CHARS = int(os.getenv("ATTACHMENT_CONTEXT_MAX_CHARS", "15000"))

# Extensions image : ces pièces jointes sont analysées par VISION (le modèle
# multimodal voit les pixels — schémas, diagrammes) plutôt que par OCR seul.
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp", ".gif")

# --- RAG vision : indexation des schémas/figures des cours ------------------
#
# Quand un cours contient des images significatives (schémas, diagrammes,
# captures), on en génère une DESCRIPTION par vision, indexée comme du texte
# (donc recherchable). Coûteux (un appel LLM par image) -> filtre de taille,
# plafond par cours, cache par contenu d'image, et exécution en tâche de fond
# à l'upload (le texte du cours reste disponible immédiatement).
RAG_VISION_ENABLED = os.getenv("RAG_VISION_ENABLED", "1") not in ("0", "false", "False")
# Modèle vision d'indexation : rapide/économique et MULTIMODAL par défaut (une
# description destinée à la recherche n'exige pas le grand modèle). Doit voir les
# pixels -> un modèle Gemini Flash convient. Vide -> modèle par défaut Hermes.
RAG_VISION_MODEL = os.getenv("RAG_VISION_MODEL", "gemini-flash-latest")
# Filtre : on ignore les petites images (logos, puces, icônes).
RAG_VISION_MIN_WIDTH = int(os.getenv("RAG_VISION_MIN_WIDTH", "300"))
RAG_VISION_MIN_HEIGHT = int(os.getenv("RAG_VISION_MIN_HEIGHT", "200"))
# Plafond d'images décrites par cours (borne le coût/temps d'indexation).
RAG_VISION_MAX_IMAGES = int(os.getenv("RAG_VISION_MAX_IMAGES", "25"))
# Cache des descriptions (clé = hash du contenu de l'image) -> re-index gratuit.
VISION_CACHE_DIR = DATA_DIR / "vision_cache"
# Version du schéma DocumentArtifact : un bump invalide les artefacts en cache.
# v2 : le texte des sections est désormais stocké dans l'artefact (contrat complet).
ARTIFACT_SCHEMA_VERSION = 2

# Formats de cours acceptés : texte, PDF, Word, PowerPoint, et images (OCR).
SUPPORTED_EXTENSIONS = (
    ".md", ".txt", ".pdf",
    ".docx", ".pptx",
    ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp",
)

# OCR (Tesseract) : langues et résolution de rendu des pages PDF scannées.
OCR_LANG = os.getenv("OCR_LANG", "fra+eng")
OCR_DPI = int(os.getenv("OCR_DPI", "200"))

# Prompt sauvegardé pour le fallback manuel (quand Hermes n'est pas appelable).
LAST_PROMPT_PATH = DATA_DIR / "last_tutor_prompt.md"

COLLECTION_NAME = "course_chunks"

# --- Embedding --------------------------------------------------------------

# Modèle d'embedding MULTILINGUE : permet de retrouver un cours en anglais à
# partir d'une question en français (et inversement). Téléchargé au 1er usage.
EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
# Espace de distance ChromaDB : cosinus -> distances stables dans [0, 2].
EMBEDDING_SPACE = "cosine"

# --- Découpage (chunking) ---------------------------------------------------
#
# Découpage STRUCTURE-AWARE générique (cf. rag/document_loader.py) : on coupe sur
# les titres/sections détectés (numérotation, police, typographie, markdown) puis
# on empaquette à une taille cible. Surchargeable par variable d'environnement.

# Taille cible d'un chunk (caractères) et recouvrement entre chunks voisins.
CHUNK_TARGET_CHARS = int(os.getenv("CHUNK_TARGET_CHARS", "900"))
CHUNK_OVERLAP_CHARS = int(os.getenv("CHUNK_OVERLAP_CHARS", "120"))

# PDF : une ligne dont la police dépasse ce facteur × la taille « corps »
# (taille la plus fréquente du document) est considérée comme un titre probable.
HEADING_FONT_RATIO = float(os.getenv("HEADING_FONT_RATIO", "1.15"))

# --- Récupération RAG -------------------------------------------------------
#
# Découplage récupération / contexte (prépare le reranker) : on récupère un large
# vivier de candidats (RETRIEVAL_TOP_K) dans ChromaDB, puis on ne garde que les
# meilleurs (CONTEXT_TOP_K) pour le contexte envoyé à Hermes et les indications.
# Tant que le reranker n'est pas là, « garder les meilleurs » = prendre les
# premiers (déjà triés par distance). Surchargeable par variable d'environnement.

RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "20"))
CONTEXT_TOP_K = int(os.getenv("CONTEXT_TOP_K", "5"))

# --- Reranker (re-classement des candidats) ---------------------------------
#
# Cross-encoder MULTILINGUE : reclasse le vivier RETRIEVAL_TOP_K par pertinence
# question × chunk (bien plus fin que la similarité d'embedding), on garde
# ensuite CONTEXT_TOP_K. Modèle chargé/mis en cache au 1er usage (~0,5 Go).
# RERANKER_ENABLED=0 -> désactive (repli sur le tri par distance, sans modèle).
RERANKER_ENABLED = os.getenv("RERANKER_ENABLED", "1") not in ("0", "false", "False")
RERANKER_MODEL_NAME = os.getenv(
    "RERANKER_MODEL_NAME", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
)

# Longueur d'un extrait lisible affiché à l'étudiant (en caractères).
# Volontairement court pour une interface étudiant (~60-90 caractères).
EXCERPT_MIN_CHARS = 60
EXCERPT_MAX_CHARS = 90

# --- Seuils de mode pédagogique --------------------------------------------
#
# Basés sur la distance COSINUS du MEILLEUR chunk retrouvé : plus la distance est
# faible, plus le passage est sémantiquement proche de la question.
#
# Calibrés pour l'embedding MULTILINGUE + distance COSINUS (échelle [0, 2]) :
# question FR précise sur un cours -> ~0.25-0.40 ; question vague ou hors-sujet
# -> ~0.70+. Valeurs dans la config car dépendantes du modèle et du corpus.
COURSE_GROUNDED_MAX_DISTANCE = 0.45  # < seuil  -> course_grounded
MIXED_MAX_DISTANCE = 0.65            # < seuil  -> mixed, sinon general_tutor

# --- Hermes -----------------------------------------------------------------
#
# Tous ces réglages sont surchargeables par variable d'environnement, pour
# pouvoir les ajuster en production SANS toucher au code (pas de nombre magique
# figé). Les défauts sont calibrés sur la latence observée : un appel Hermes
# normal coûte ~70 s, donc 180 s laisse de la marge pour les pics (appels
# d'outils, lenteur ponctuelle du provider).

DEFAULT_SKILL_NAME = os.getenv("HERMES_SKILL_NAME", "education-tutor")

# Workers Hermes PERSISTANTS (« Hermes chaud ») : liste de chemins de sockets
# Unix séparés par des virgules. Si RENSEIGNÉ, l'adaptateur envoie les prompts à
# ces workers (agent chaud -> pas de cold-start ~10 s par message) et retombe sur
# la CLI `hermes -z` si aucun n'est joignable. VIDE (défaut) -> 100 % CLI, comme
# avant. Renseigné automatiquement par l'API quand elle lance ses workers.
HERMES_WORKER_SOCKETS = os.getenv("HERMES_WORKER_SOCKETS", "")

# --- Démarrage automatique des workers par l'API (cycle de vie, 2d) ----------
# Chemins vers le runtime Hermes — À SURCHARGER en conteneur.
HERMES_AGENT_ROOT = os.getenv("HERMES_AGENT_ROOT", str(PROJECT_ROOT.parent / "hermes-agent"))
HERMES_VENV_PYTHON = os.getenv(
    "HERMES_VENV_PYTHON", str(PROJECT_ROOT.parent / "hermes-agent" / "venv" / "bin" / "python")
)
# Auto-spawn des workers chauds au boot de l'API. Si le runtime Hermes est
# introuvable, on n'échoue pas : on retombe sur la CLI `hermes -z`.
HERMES_WORKERS_ENABLED = os.getenv("HERMES_WORKERS_ENABLED", "1") not in ("0", "false", "False")
HERMES_WORKER_COUNT = max(1, int(os.getenv("HERMES_WORKER_COUNT", "1")))
# Dossier des sockets Unix (chemins COURTS : limite système ~108 caractères).
HERMES_WORKER_SOCKET_DIR = os.getenv("HERMES_WORKER_SOCKET_DIR", tempfile.gettempdir())

# Modèle RAPIDE pour les appels utilitaires courts (planner de l'orchestrateur,
# titrage des discussions) : ces appels ne produisent qu'un petit JSON ou
# quelques mots — un grand modèle y est surdimensionné (latence ×3-4 pour rien).
# Les agents qui RÉDIGENT (tuteur, IDP, contenu, compose) restent sur le modèle
# par défaut configuré côté Hermes. Vide ("") -> désactive l'override.
# NB : l'ID doit correspondre au provider configuré côté Hermes (ici : gemini).
HERMES_FAST_MODEL = os.getenv("HERMES_FAST_MODEL", "gemini-flash-lite-latest")

# Planner LLM de l'orchestrateur : DÉSACTIVÉ par défaut (0) -> routage
# DÉTERMINISTE (table d'intention regex), sans appel LLM supplémentaire. Le
# planner ajoutait ~1 appel Hermes par message (~10 s de démarrage CLI) pour un
# gain marginal sur les formulations libres. HERMES_USE_PLANNER=1 le réactive.
# Le routage déterministe est de toute façon le chemin « primaire » de conception.
HERMES_USE_PLANNER = os.getenv("HERMES_USE_PLANNER", "0") in ("1", "true", "True")

# Résumé global d'un cours : on envoie le TEXTE INTÉGRAL du cours en UN seul
# appel (comme ChatGPT : tout le document tient dans le contexte). Garde-fou :
# au-delà de ce budget (~500 pages × ~3000 car.), on bascule sur un condensé
# (titres de section + amorces) pour ne pas dépasser la fenêtre de contexte.
MAX_SUMMARY_INPUT_CHARS = int(os.getenv("MAX_SUMMARY_INPUT_CHARS", "1500000"))

# Budget de temps d'UN appel à la boucle agent Hermes (en secondes).
HERMES_TIMEOUT_SECONDS = int(os.getenv("HERMES_TIMEOUT_SECONDS", "180"))

# Nombre TOTAL de tentatives (>= 1). 2 = un nouvel essai après un échec
# transitoire (timeout, code retour non nul, sortie vide).
HERMES_MAX_ATTEMPTS = max(1, int(os.getenv("HERMES_MAX_ATTEMPTS", "2")))

# Pause entre deux tentatives, en secondes (0 = pas de pause).
HERMES_RETRY_BACKOFF_SECONDS = float(os.getenv("HERMES_RETRY_BACKOFF_SECONDS", "2"))

# Journal des échecs Hermes : réservé au développeur, jamais montré à l'étudiant.
HERMES_ERROR_LOG = DATA_DIR / "hermes_errors.log"

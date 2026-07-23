"""
Identité documentaire (hash de CONTENU) + cache JSON des artefacts et des
contenus générés.

- `doc_id` = SHA-256 du contenu du fichier (pas le nom) → détecte modification et
  doublon, identité stable partagée par le RAG, l'IDP et l'agent de contenu.
- Artefacts IDP   : data/artifacts/<doc_id>.json (invalidé si schema_version change).
- Contenus générés: data/generated/<doc_id>__<type>__<opts>.json (clé = doc + type
  + options) → on ne rappelle pas Hermes pour une génération identique.
"""

import hashlib
import json

from config import settings, workspace
from agents.artifact import DocumentArtifact


def compute_doc_id(file_path: str) -> str:
    """SHA-256 (tronqué) du contenu du fichier. Lecture par blocs (gros PDF)."""
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


# --- Artefacts documentaires (IDP) ------------------------------------------

def _artifact_path(doc_id: str):
    return workspace.artifacts_dir() / f"{doc_id}.json"


def load_artifact(doc_id: str) -> DocumentArtifact | None:
    """Artefact en cache, ou None si absent / illisible / schéma obsolète."""
    path = _artifact_path(doc_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("schema_version") != settings.ARTIFACT_SCHEMA_VERSION:
        return None  # schéma changé -> on réanalysera
    try:
        return DocumentArtifact.from_dict(data)
    except (KeyError, TypeError):
        return None


def save_artifact(artifact: DocumentArtifact) -> None:
    """Persiste un artefact (jamais bloquant)."""
    try:
        workspace.artifacts_dir().mkdir(parents=True, exist_ok=True)
        _artifact_path(artifact.doc_id).write_text(
            json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


# --- Contenus générés (agent de contenu) ------------------------------------

def _options_key(options: dict | None) -> str:
    raw = json.dumps(options or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _generated_path(doc_id: str, content_type: str, options: dict | None):
    return workspace.generated_dir() / f"{doc_id}__{content_type}__{_options_key(options)}.json"


def load_generated(doc_id: str, content_type: str, options: dict | None = None) -> dict | None:
    """Contenu généré en cache pour (document, type, options), ou None."""
    path = _generated_path(doc_id, content_type, options)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_generated(
    doc_id: str, content_type: str, payload: dict, options: dict | None = None
) -> None:
    """Persiste un contenu généré (jamais bloquant)."""
    try:
        workspace.generated_dir().mkdir(parents=True, exist_ok=True)
        _generated_path(doc_id, content_type, options).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass

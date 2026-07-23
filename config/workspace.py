"""
Espace de travail par UTILISATEUR (isolation multi-utilisateur).

Chaque utilisateur a son propre espace, résolu à partir de son `user_id` :
- ses cours          data/users/<uid>/courses/
- ses artefacts IDP  data/users/<uid>/artifacts/
- ses contenus       data/users/<uid>/generated/
- ses pièces jointes data/users/<uid>/attachments/
- ses conversations  data/users/<uid>/conversations.json
- sa COLLECTION ChromaDB : course_chunks__<uid>  (isolation par construction —
  impossible de fuiter les cours d'un autre par un filtre oublié).

Le `user_id` courant vit dans une ContextVar posée par l'API au début de chaque
requête (`set_current_user`). Les couches profondes (RAG, caches) le lisent via
les helpers ci-dessous, SANS avoir à le recevoir en paramètre.

⚠️ Threads : une ContextVar ne se propage pas aux threads créés manuellement.
Pour le worker SSE et le titrage, l'API capture le contexte
(`contextvars.copy_context()`) et exécute le thread dedans.

Le compte historique `u_salah` est le DÉFAUT : le CLI, les usages hors requête et
la migration des données pré-multi-utilisateur ciblent cet espace.
"""

import contextvars

from config import settings

DEFAULT_USER_ID = "u_salah"

_current_user: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_user", default=DEFAULT_USER_ID
)


def set_current_user(user_id: str) -> None:
    _current_user.set(user_id)


def current_user() -> str:
    return _current_user.get()


def user_root(user_id: str | None = None):
    return settings.DATA_DIR / "users" / (user_id or current_user())


def courses_dir(user_id: str | None = None):
    return user_root(user_id) / "courses"


def artifacts_dir(user_id: str | None = None):
    return user_root(user_id) / "artifacts"


def generated_dir(user_id: str | None = None):
    return user_root(user_id) / "generated"


def attachments_dir(user_id: str | None = None):
    return user_root(user_id) / "attachments"


def conversations_path(user_id: str | None = None):
    return user_root(user_id) / "conversations.json"


def collection_name(user_id: str | None = None) -> str:
    return f"{settings.COLLECTION_NAME}__{user_id or current_user()}"

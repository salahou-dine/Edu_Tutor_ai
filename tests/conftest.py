"""
Fixtures partagées de la suite de tests.

Principe : ces tests couvrent la logique DÉTERMINISTE du projet (routage,
validateurs, découpage, exports…) — aucun appel Hermes/LLM, aucun accès au
vectorstore. Les entrées sont synthétiques ; les caches disque sont redirigés
vers des répertoires temporaires.
"""

import sys
from pathlib import Path

import pytest

# Racine du projet importable quel que soit le cwd de pytest.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _reset_current_user():
    """Isole l'utilisateur courant (ContextVar) entre tests : chaque test démarre
    sur le compte par défaut, et l'état est restauré après (pas de fuite d'un
    test API qui aurait basculé l'utilisateur)."""
    from config import workspace
    token = workspace._current_user.set(workspace.DEFAULT_USER_ID)
    yield
    workspace._current_user.reset(token)


@pytest.fixture
def corpus():
    """Corpus réaliste : 3 cours au nom très proche (tokens communs nombreux),
    comme le corpus réel — c'est ce qui a révélé le bug de résolution."""
    return [
        {
            "filename": "cybersecurity_OT_40_CM1_Spring_2026 (1).pdf",
            "path": "/fake/cybersecurity_OT_40_CM1_Spring_2026 (1).pdf",
            "doc_id": "aaaa000000000001",
            "analyzed": False,
        },
        {
            "filename": "cybersecurity_OT_40_CM3_Spring_2026.pdf",
            "path": "/fake/cybersecurity_OT_40_CM3_Spring_2026.pdf",
            "doc_id": "aaaa000000000003",
            "analyzed": False,
        },
        {
            "filename": "cybersecurity_OT_40_CM4_Spring_2026.pdf",
            "path": "/fake/cybersecurity_OT_40_CM4_Spring_2026.pdf",
            "doc_id": "aaaa000000000004",
            "analyzed": True,
        },
    ]


@pytest.fixture
def single_doc_corpus():
    return [
        {
            "filename": "intro_reseaux.pdf",
            "path": "/fake/intro_reseaux.pdf",
            "doc_id": "bbbb000000000001",
            "analyzed": False,
        },
    ]

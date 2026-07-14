"""
Adaptateur Hermes : construction de la commande (skill optionnel), retry sur
échec transitoire, statut not_available sans binaire. `subprocess.run` et
`shutil.which` sont MOCKÉS — aucun vrai appel Hermes.
"""

import subprocess

import pytest

from services import hermes_adapter


class FakeCompleted:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


@pytest.fixture
def hermes_present(monkeypatch, tmp_path):
    """Binaire « présent » + chemins de debug redirigés vers tmp."""
    from config import settings
    monkeypatch.setattr(hermes_adapter.shutil, "which", lambda _: "/usr/bin/hermes")
    monkeypatch.setattr(settings, "LAST_PROMPT_PATH", tmp_path / "last_prompt.md")
    monkeypatch.setattr(settings, "HERMES_ERROR_LOG", tmp_path / "errors.log")


def test_binaire_absent(monkeypatch, tmp_path):
    from config import settings
    monkeypatch.setattr(hermes_adapter.shutil, "which", lambda _: None)
    monkeypatch.setattr(settings, "LAST_PROMPT_PATH", tmp_path / "last_prompt.md")
    result = hermes_adapter.ask_hermes_with_skill("question")
    assert result["status"] == "not_available"
    # le prompt est sauvegardé pour test manuel
    assert (tmp_path / "last_prompt.md").read_text(encoding="utf-8") == "question"


def test_succes_avec_skill(hermes_present, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return FakeCompleted(stdout="réponse du tuteur")

    monkeypatch.setattr(hermes_adapter.subprocess, "run", fake_run)
    result = hermes_adapter.ask_hermes_with_skill("question", skill_name="education-tutor")
    assert result["status"] == "success"
    assert result["content"] == "réponse du tuteur"
    assert calls[0][1:3] == ["-z", "question"]
    assert calls[0][-2:] == ["--skills", "education-tutor"]


def test_sans_skill_pas_de_flag(hermes_present, monkeypatch):
    # skill_name=None (ex. titrage) -> la commande NE contient PAS --skills.
    calls = []
    monkeypatch.setattr(
        hermes_adapter.subprocess, "run",
        lambda cmd, **kw: (calls.append(cmd), FakeCompleted(stdout="OK"))[1],
    )
    result = hermes_adapter.ask_hermes_with_skill("titre ?", skill_name=None)
    assert result["status"] == "success"
    assert "--skills" not in calls[0]


def test_retry_sur_sortie_vide_puis_succes(hermes_present, monkeypatch):
    outputs = iter([FakeCompleted(stdout=""), FakeCompleted(stdout="réponse")])
    monkeypatch.setattr(hermes_adapter.subprocess, "run", lambda *a, **k: next(outputs))
    result = hermes_adapter.ask_hermes_with_skill("q", max_attempts=2, backoff=0)
    assert result["status"] == "success"
    assert result["content"] == "réponse"


def test_echec_apres_toutes_les_tentatives(hermes_present, monkeypatch, tmp_path):
    monkeypatch.setattr(
        hermes_adapter.subprocess, "run",
        lambda *a, **k: FakeCompleted(stdout="", returncode=1, stderr="boom"),
    )
    result = hermes_adapter.ask_hermes_with_skill("q", max_attempts=2, backoff=0)
    assert result["status"] == "error"
    assert "boom" in (result["error"] or "")
    # échec journalisé + prompt sauvegardé
    assert (tmp_path / "errors.log").exists()
    assert (tmp_path / "last_prompt.md").exists()


def test_timeout_traite_comme_echec(hermes_present, monkeypatch):
    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="hermes", timeout=1)

    monkeypatch.setattr(hermes_adapter.subprocess, "run", raise_timeout)
    result = hermes_adapter.ask_hermes_with_skill("q", max_attempts=1, backoff=0)
    assert result["status"] == "error"
    assert "Délai" in result["error"]

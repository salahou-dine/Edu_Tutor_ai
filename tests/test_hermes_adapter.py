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


def test_modele_rapide_ajoute_le_flag_m(hermes_present, monkeypatch):
    # model=<id> (planner/titrage) -> `-m <id>` AVANT --skills.
    calls = []
    monkeypatch.setattr(
        hermes_adapter.subprocess, "run",
        lambda cmd, **kw: (calls.append(cmd), FakeCompleted(stdout="OK"))[1],
    )
    hermes_adapter.ask_hermes_with_skill(
        "plan ?", skill_name="education-orchestrator",
        model="anthropic/claude-haiku-4-5",
    )
    cmd = calls[0]
    index = cmd.index("-m")
    assert cmd[index + 1] == "anthropic/claude-haiku-4-5"
    assert "--skills" in cmd


def test_sans_modele_pas_de_flag_m(hermes_present, monkeypatch):
    calls = []
    monkeypatch.setattr(
        hermes_adapter.subprocess, "run",
        lambda cmd, **kw: (calls.append(cmd), FakeCompleted(stdout="OK"))[1],
    )
    hermes_adapter.ask_hermes_with_skill("q")
    assert "-m" not in calls[0]


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


# --- Streaming (protocole NUL du patch oneshot) --------------------------------

def _fake_hermes(tmp_path, body: str):
    """Crée un faux binaire `hermes` (script python) qui joue un scénario stdout."""
    script = tmp_path / "fake_hermes"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        + body,
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script)


@pytest.fixture
def fake_streaming_hermes(monkeypatch, tmp_path):
    """Binaire simulé : 2 tokens, une frontière de tour (NUL), puis le canonique."""
    from config import settings
    monkeypatch.setattr(settings, "LAST_PROMPT_PATH", tmp_path / "last.md")
    monkeypatch.setattr(settings, "HERMES_ERROR_LOG", tmp_path / "err.log")
    path = _fake_hermes(tmp_path, (
        "sys.stdout.write('Bonjour, je réflé'); sys.stdout.flush()\n"
        "time.sleep(0.05)\n"
        "sys.stdout.write('chis…'); sys.stdout.flush()\n"
        "sys.stdout.write('\\x00'); sys.stdout.flush()\n"          # tour intermédiaire jeté
        "sys.stdout.write('Réponse finale.'); sys.stdout.flush()\n"
    ))
    monkeypatch.setattr(hermes_adapter.shutil, "which", lambda _: path)


def test_streaming_tokens_et_canonique(fake_streaming_hermes):
    deltas = []
    result = hermes_adapter.ask_hermes_with_skill(
        "q", skill_name=None, on_delta=deltas.append, max_attempts=1, backoff=0
    )
    assert result["status"] == "success"
    # La réponse canonique = DERNIER segment NUL (le tour intermédiaire est jeté).
    assert result["content"] == "Réponse finale."
    # Les tokens sont bien arrivés au fil de l'eau, avec les remises à zéro :
    # reset initial, tokens, reset (frontière NUL), canonique.
    assert deltas[0] is None
    text_deltas = [d for d in deltas if d]
    assert "".join(text_deltas).startswith("Bonjour, je réflé")
    assert deltas.count(None) == 2
    assert text_deltas[-1] == "Réponse finale."


def test_streaming_utf8_coupe_en_plein_multioctet(monkeypatch, tmp_path):
    """Un caractère accentué coupé entre deux lectures ne doit pas être corrompu."""
    from config import settings
    monkeypatch.setattr(settings, "LAST_PROMPT_PATH", tmp_path / "last.md")
    monkeypatch.setattr(settings, "HERMES_ERROR_LOG", tmp_path / "err.log")
    path = _fake_hermes(tmp_path, (
        "data = 'préparation sécurité'.encode('utf-8')\n"
        "sys.stdout.buffer.write(data[:3]); sys.stdout.buffer.flush()\n"  # coupe dans 'é'
        "time.sleep(0.05)\n"
        "sys.stdout.buffer.write(data[3:]); sys.stdout.buffer.flush()\n"
    ))
    monkeypatch.setattr(hermes_adapter.shutil, "which", lambda _: path)
    result = hermes_adapter.ask_hermes_with_skill(
        "q", skill_name=None, on_delta=lambda _t: None, max_attempts=1, backoff=0
    )
    assert result["content"] == "préparation sécurité"


def test_streaming_sortie_vide_declenche_retry(monkeypatch, tmp_path):
    """Sortie vide en streaming -> retry (même sémantique que le mode bloc)."""
    from config import settings
    monkeypatch.setattr(settings, "LAST_PROMPT_PATH", tmp_path / "last.md")
    monkeypatch.setattr(settings, "HERMES_ERROR_LOG", tmp_path / "err.log")
    marker = tmp_path / "second_try"
    path = _fake_hermes(tmp_path, (
        f"import os\n"
        f"if os.path.exists({str(marker)!r}):\n"
        f"    sys.stdout.write('OK au 2e essai')\n"
        f"else:\n"
        f"    open({str(marker)!r}, 'w').close()\n"  # 1er essai : sortie vide
    ))
    monkeypatch.setattr(hermes_adapter.shutil, "which", lambda _: path)
    deltas = []
    result = hermes_adapter.ask_hermes_with_skill(
        "q", skill_name=None, on_delta=deltas.append, max_attempts=2, backoff=0
    )
    assert result["status"] == "success"
    assert result["content"] == "OK au 2e essai"
    # le retry a bien remis le texte partiel à zéro avant de rejouer
    assert deltas.count(None) >= 2

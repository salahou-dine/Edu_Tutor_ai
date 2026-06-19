"""
Adaptateur d'appel à Hermes Agent.

Méthode retenue (cf. docs/agent_tutor_architecture.md) : la CLI Hermes en mode
one-shot non-interactif :

    hermes -z "<prompt>" --skills <skill>

Le mode `-z/--oneshot` imprime UNIQUEMENT la réponse finale sur stdout
(pas de bannière, pas de spinner, pas de session_id), charge le skill demandé
et auto-approuve les outils. C'est le point d'entrée officiel pour les scripts.

Ce module N'IMPORTE PAS le runtime Hermes (pour ne pas se coupler au code du
framework) et NE FAIT JAMAIS d'appel LLM direct (OpenRouter/DeepSeek) en
remplacement silencieux de Hermes. Si Hermes n'est pas appelable, on sauvegarde
le prompt pour un test manuel et on retourne `not_available`.

Robustesse : les échecs de Hermes sont INTERMITTENTS (timeout ponctuel, erreur
transitoire du provider, sortie vide). L'appel est donc réessayé
(`HERMES_MAX_ATTEMPTS`) et chaque échec est journalisé dans un fichier réservé
au développeur (`HERMES_ERROR_LOG`) — jamais montré à l'étudiant.
"""

import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from config import settings


def _save_prompt_for_manual(prompt: str) -> None:
    """Sauvegarde le prompt afin de pouvoir le tester à la main dans Hermes."""
    try:
        path = Path(settings.LAST_PROMPT_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prompt, encoding="utf-8")
    except OSError:
        # La sauvegarde est un confort de debug, jamais bloquante.
        pass


def _log_failure(
    attempt: int,
    attempts: int,
    skill_name: str,
    error: str,
    elapsed: float,
    stderr: str = "",
) -> None:
    """
    Journalise un échec d'appel Hermes (réservé développeur). Un échec n'est donc
    plus « silencieux » : on garde une trace horodatée pour diagnostiquer.
    L'écriture du journal n'est jamais bloquante.
    """
    try:
        path = Path(settings.HERMES_ERROR_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"[{datetime.now().isoformat(timespec='seconds')}] "
                f"tentative {attempt}/{attempts} skill={skill_name} "
                f"durée={elapsed:.1f}s : {error}\n"
            )
            if stderr:
                handle.write(f"    stderr: {stderr[-500:]}\n")
    except OSError:
        pass


def ask_hermes_with_skill(
    prompt: str,
    skill_name: str = settings.DEFAULT_SKILL_NAME,
    timeout: int = settings.HERMES_TIMEOUT_SECONDS,
    max_attempts: int | None = None,
    backoff: float | None = None,
) -> dict:
    """
    Envoie un prompt à Hermes via la CLI one-shot avec le skill préchargé.

    Réessaie automatiquement en cas d'échec transitoire (timeout, code retour
    non nul, sortie vide) jusqu'à `max_attempts` tentatives, avec une pause
    `backoff` entre chacune. Ne réessaie PAS si le binaire `hermes` est
    introuvable (un retry n'y changerait rien).

    Retour :
        {
            "status": "success" | "error" | "not_available",
            "content": str,            # réponse finale de Hermes (ou "")
            "method": "cli" | "not_available",
            "error": None | str,
        }
    """
    if max_attempts is None:
        max_attempts = settings.HERMES_MAX_ATTEMPTS
    if backoff is None:
        backoff = settings.HERMES_RETRY_BACKOFF_SECONDS

    hermes_bin = shutil.which("hermes")
    if not hermes_bin:
        _save_prompt_for_manual(prompt)
        return {
            "status": "not_available",
            "content": "",
            "method": "not_available",
            "error": (
                "Binaire 'hermes' introuvable dans le PATH. "
                f"Prompt sauvegardé pour test manuel : {settings.LAST_PROMPT_PATH}"
            ),
        }

    # Liste d'arguments (pas de shell) -> pas d'injection, prompt brut sûr.
    cmd = [hermes_bin, "-z", prompt, "--skills", skill_name]

    last_failure: dict | None = None

    for attempt in range(1, max_attempts + 1):
        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            error = f"Délai dépassé ({timeout}s) lors de l'appel à Hermes."
            _log_failure(attempt, max_attempts, skill_name, error, elapsed)
            last_failure = {
                "status": "error",
                "content": "",
                "method": "cli",
                "error": error,
            }
        except OSError as exc:
            elapsed = time.monotonic() - start
            error = f"Échec d'exécution de la CLI Hermes : {exc}"
            _log_failure(attempt, max_attempts, skill_name, error, elapsed)
            last_failure = {
                "status": "error",
                "content": "",
                "method": "cli",
                "error": error,
            }
        else:
            elapsed = time.monotonic() - start
            content = (result.stdout or "").strip()

            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                error = (
                    f"Hermes a renvoyé le code {result.returncode}. {stderr}".strip()
                )
                _log_failure(attempt, max_attempts, skill_name, error, elapsed, stderr)
                last_failure = {
                    "status": "error",
                    "content": content,
                    "method": "cli",
                    "error": error,
                }
            elif not content:
                error = "Hermes n'a renvoyé aucune réponse sur stdout."
                _log_failure(attempt, max_attempts, skill_name, error, elapsed)
                last_failure = {
                    "status": "error",
                    "content": "",
                    "method": "cli",
                    "error": error,
                }
            else:
                # Succès : on renvoie immédiatement (pas de retry inutile).
                return {
                    "status": "success",
                    "content": content,
                    "method": "cli",
                    "error": None,
                }

        # Échec de cette tentative : pause avant de réessayer s'il en reste.
        if attempt < max_attempts and backoff > 0:
            time.sleep(backoff)

    # Toutes les tentatives ont échoué : on sauvegarde le prompt pour test manuel
    # et on retourne le dernier échec observé.
    _save_prompt_for_manual(prompt)
    return last_failure

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
"""

import shutil
import subprocess
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


def ask_hermes_with_skill(
    prompt: str,
    skill_name: str = settings.DEFAULT_SKILL_NAME,
    timeout: int = settings.HERMES_TIMEOUT_SECONDS,
) -> dict:
    """
    Envoie un prompt à Hermes via la CLI one-shot avec le skill préchargé.

    Retour :
        {
            "status": "success" | "error" | "not_available",
            "content": str,            # réponse finale de Hermes (ou "")
            "method": "cli" | "not_available",
            "error": None | str,
        }
    """
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

    cmd = [hermes_bin, "-z", prompt, "--skills", skill_name]

    try:
        # Liste d'arguments (pas de shell) -> pas d'injection, prompt brut sûr.
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _save_prompt_for_manual(prompt)
        return {
            "status": "error",
            "content": "",
            "method": "cli",
            "error": f"Délai dépassé ({timeout}s) lors de l'appel à Hermes.",
        }
    except OSError as exc:
        _save_prompt_for_manual(prompt)
        return {
            "status": "error",
            "content": "",
            "method": "cli",
            "error": f"Échec d'exécution de la CLI Hermes : {exc}",
        }

    content = (result.stdout or "").strip()

    if result.returncode != 0:
        _save_prompt_for_manual(prompt)
        stderr = (result.stderr or "").strip()
        return {
            "status": "error",
            "content": content,
            "method": "cli",
            "error": f"Hermes a renvoyé le code {result.returncode}. {stderr}".strip(),
        }

    if not content:
        _save_prompt_for_manual(prompt)
        return {
            "status": "error",
            "content": "",
            "method": "cli",
            "error": "Hermes n'a renvoyé aucune réponse sur stdout.",
        }

    return {
        "status": "success",
        "content": content,
        "method": "cli",
        "error": None,
    }

"""
Génération d'un titre de discussion (façon Claude/ChatGPT).

À partir du premier message de l'étudiant, produit un titre court et descriptif
du SUJET (3–6 mots), via un appel Hermes NEUTRE (sans skill : pas de persona
tuteur qui répondrait au lieu de titrer). Best-effort : en cas d'échec, l'appelant
garde son titre de repli (premier message tronqué).
"""

import re

from services.hermes_adapter import ask_hermes_with_skill

_TITLE_TIMEOUT = 60
_MAX_TITLE_CHARS = 48


def _clean_title(raw: str) -> str | None:
    if not raw:
        return None
    # 1re ligne non vide, sans guillemets, sans puce/markdown ni ponctuation finale.
    line = next((l.strip() for l in raw.splitlines() if l.strip()), "")
    line = line.strip(" \t\"'«»`*#-–—").strip()
    line = re.sub(r"\s+", " ", line)
    line = line.rstrip(" .;:!,")
    if not line:
        return None
    return line if len(line) <= _MAX_TITLE_CHARS else line[:_MAX_TITLE_CHARS].rstrip() + "…"


def generate_title(first_message: str) -> str | None:
    """Titre court du sujet, ou None si Hermes échoue (l'appelant gère le repli)."""
    text = (first_message or "").strip()
    if not text:
        return None
    prompt = (
        "Donne un titre TRÈS court (3 à 6 mots) résumant le SUJET de cette demande "
        "d'un étudiant, pour l'afficher dans une liste de conversations. Pas de "
        "guillemets, pas de ponctuation finale, pas de phrase — juste le titre.\n\n"
        f"Demande :\n{text[:1000]}\n\n"
        "Réponds UNIQUEMENT par le titre."
    )
    result = ask_hermes_with_skill(prompt, skill_name=None, timeout=_TITLE_TIMEOUT)
    if result["status"] != "success":
        return None
    return _clean_title(result["content"])


if __name__ == "__main__":
    import sys

    msg = " ".join(sys.argv[1:]) or "Explique-moi la différence entre IT et OT en cybersécurité"
    print(generate_title(msg))

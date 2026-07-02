"""
Helpers partagés des agents (multi-agents).

Pour l'instant : parsing TOLÉRANT des sorties JSON de Hermes. Les agents IDP /
contenu / orchestrateur demandent à Hermes une sortie structurée (JSON) ; comme
un LLM peut l'entourer de texte ou de barrières ```json, on extrait l'objet de
façon robuste plutôt que de faire confiance à `json.loads` sur la réponse brute.
"""

import json
import re

# Bloc ```json ... ``` éventuel autour de la réponse.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> dict | list | None:
    """
    Extrait le premier objet/tableau JSON valide d'une réponse Hermes.

    Tolérant : gère les barrières ```json```, le texte avant/après, et se rabat
    sur l'objet `{...}` (ou `[...]`) le plus englobant. Retourne None si rien
    d'exploitable (l'appelant décide alors du repli / retry).
    """
    if not text:
        return None

    candidates: list[str] = []
    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text)

    for candidate in candidates:
        # Essai direct.
        stripped = candidate.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
        # Repli : bornes englobantes { } ou [ ].
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            start = candidate.find(open_ch)
            end = candidate.rfind(close_ch)
            if start != -1 and end > start:
                try:
                    return json.loads(candidate[start : end + 1])
                except json.JSONDecodeError:
                    continue
    return None

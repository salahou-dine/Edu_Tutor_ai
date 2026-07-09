"""
Registre de capacités — le « menu » déclaratif des agents.

Sert deux usages (Phase 3, orchestrateur intelligent) :
1. il est donné au skill *planner* Hermes pour qu'il sache quels agents existent
   et ce qu'ils attendent/produisent ;
2. il est la base de la VALIDATION/RÉPARATION côté Python : on vérifie qu'un plan
   ne référence que des agents connus et que leurs préconditions sont satisfaites
   (ex. `content` exige un artefact -> sinon on insère une étape `idp`).

Ajouter un agent = ajouter une entrée ici (et son module + skill).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    preconditions: tuple[str, ...] = ()


CAPABILITIES: dict[str, Capability] = {
    "tutor": Capability(
        name="tutor",
        description=(
            "Répond pédagogiquement à une question de l'étudiant (explication, "
            "reformulation, exemple, vérification), ancrée sur les cours (RAG) et "
            "l'historique de conversation."
        ),
        inputs=("question", "history"),
        outputs=("answer",),
    ),
    "idp": Capability(
        name="idp",
        description=(
            "Analyse un document : type, structure (sections/pages), thèmes, "
            "objectifs, définitions, consignes, dates — avec provenance. Produit "
            "un artefact documentaire structuré réutilisable."
        ),
        inputs=("doc_id",),
        outputs=("artifact",),
    ),
    "content": Capability(
        name="content",
        description=(
            "Génère une ressource d'étude (résumé structuré ou fiche de révision) "
            "à partir de l'analyse documentaire produite par l'IDP, éventuellement "
            "ciblée sur certaines sections."
        ),
        inputs=("doc_id", "content_type"),
        outputs=("generated",),
        preconditions=("artifact",),  # nécessite un artefact IDP existant
    ),
    "compose": Capability(
        name="compose",
        description=(
            "Rédige un document ORIGINAL et structuré (rapport, exposé, dissertation, "
            "note, synthèse, lettre…) à partir d'une consigne en langage naturel. "
            "N'exige aucun document source ; peut s'appuyer sur un cours si l'étudiant "
            "en nomme un, sinon mobilise des connaissances générales. Sortie Markdown."
        ),
        inputs=("instructions",),
        outputs=("generated",),
        # PAS de précondition : c'est un générateur, pas un transformateur de source.
    ),
}


def capability(name: str) -> Capability | None:
    return CAPABILITIES.get(name)


def list_capabilities() -> list[Capability]:
    return list(CAPABILITIES.values())

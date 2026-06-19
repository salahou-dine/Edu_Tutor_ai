"""
Reranker (cross-encoder) — 2ᵉ étage de la récupération RAG.

Le bi-encoder (embedding) récupère un large vivier de candidats rapidement mais
de façon approximative (il encode la question et le chunk SÉPARÉMENT). Le
cross-encoder lit la paire « question × chunk » ENSEMBLE et produit un score de
pertinence bien plus fin. On l'applique donc seulement au petit vivier
(RETRIEVAL_TOP_K), puis on garde les CONTEXT_TOP_K meilleurs.

Robustesse : le modèle est chargé et mis en cache au 1er usage. Si le chargement
échoue (hors-ligne, modèle absent, désactivé) ou si le scoring lève une erreur,
on retombe PROPREMENT sur le tri par distance (`chunks[:top_k]`) sans planter.
"""

import sys

from config import settings


# Sentinelles de cache : on ne tente le chargement (lent) qu'une seule fois.
_CROSS_ENCODER = None
_LOAD_ATTEMPTED = False


def get_cross_encoder():
    """Charge (en cache) le cross-encoder, ou None si indisponible/désactivé."""
    global _CROSS_ENCODER, _LOAD_ATTEMPTED
    if _LOAD_ATTEMPTED:
        return _CROSS_ENCODER
    _LOAD_ATTEMPTED = True

    if not settings.RERANKER_ENABLED:
        return None
    try:
        from sentence_transformers import CrossEncoder

        _CROSS_ENCODER = CrossEncoder(settings.RERANKER_MODEL_NAME)
    except Exception as exc:  # téléchargement KO, modèle introuvable, etc.
        print(
            f"[reranker] Indisponible ({exc}). Repli sur le tri par distance.",
            file=sys.stderr,
        )
        _CROSS_ENCODER = None
    return _CROSS_ENCODER


def rerank(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    """
    Reclasse `chunks` par pertinence question × chunk et renvoie les `top_k`
    meilleurs (chacun enrichi de `rerank_score`).

    Repli : si le reranker est indisponible, renvoie `chunks[:top_k]` (ordre par
    distance d'embedding, déjà trié par ChromaDB) — comportement identique à
    l'ancien placeholder.
    """
    if not chunks:
        return []

    encoder = get_cross_encoder()
    if encoder is None:
        return chunks[:top_k]

    try:
        pairs = [(query, chunk.get("text", "")) for chunk in chunks]
        scores = encoder.predict(pairs)
    except Exception as exc:
        print(
            f"[reranker] Échec du scoring ({exc}). Repli sur le tri par distance.",
            file=sys.stderr,
        )
        return chunks[:top_k]

    ranked = sorted(zip(chunks, scores), key=lambda cs: float(cs[1]), reverse=True)
    result: list[dict] = []
    for chunk, score in ranked[:top_k]:
        enriched = dict(chunk)
        enriched["rerank_score"] = float(score)
        result.append(enriched)
    return result

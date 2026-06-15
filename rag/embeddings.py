"""
Fonction d'embedding partagée par l'indexeur ET le retriever.

Il est CRITIQUE que l'indexation et la recherche utilisent exactement le même
modèle d'embedding, sinon les vecteurs stockés et les vecteurs de requête ne
sont pas comparables. On centralise donc ici, en un seul endroit.

Modèle multilingue (cf. config.settings.EMBEDDING_MODEL_NAME) : il rapproche
une question en français d'un passage de cours en anglais (et inversement).
Le modèle est mis en cache après le premier chargement.
"""

from chromadb.utils import embedding_functions

from config import settings


_EMBEDDING_FN = None


def get_embedding_function():
    """Retourne (en cache) la fonction d'embedding multilingue de ChromaDB."""
    global _EMBEDDING_FN
    if _EMBEDDING_FN is None:
        _EMBEDDING_FN = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=settings.EMBEDDING_MODEL_NAME
        )
    return _EMBEDDING_FN


def collection_metadata() -> dict:
    """Métadonnées de collection (espace de distance) cohérentes partout."""
    return {"hnsw:space": settings.EMBEDDING_SPACE}

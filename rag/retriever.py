from pathlib import Path

import chromadb

from rag.embeddings import get_embedding_function, collection_metadata


VECTORSTORE_PATH = "data/vectorstore"
COLLECTION_NAME = "course_chunks"


def list_indexed_courses() -> list[dict]:
    """
    Liste les cours réellement indexés (distincts), indépendamment de toute
    recherche. Permet à l'agent de "connaître" ses cours.

    Retourne une liste de {"title": ..., "source": ...}.
    """
    client = chromadb.PersistentClient(path=VECTORSTORE_PATH)
    try:
        collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=get_embedding_function(),
            metadata=collection_metadata(),
        )
        data = collection.get(include=["metadatas"])
    except Exception:
        return []

    courses: dict[str, dict] = {}
    for metadata in data.get("metadatas") or []:
        source = metadata.get("source")
        title = metadata.get("title")
        if not title and source:
            title = Path(source).stem.replace("_", " ").replace("-", " ").strip()
        if title and title not in courses:
            courses[title] = {"title": title, "source": source}
    return list(courses.values())


def search_course(query: str, n_results: int = 3):
    """
    Recherche les chunks les plus pertinents dans ChromaDB.
    """
    client = chromadb.PersistentClient(path=VECTORSTORE_PATH)
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=get_embedding_function(),
        metadata=collection_metadata(),
    )

    results = collection.query(
        query_texts=[query],
        n_results=n_results,
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    retrieved_chunks = []

    for i, document in enumerate(documents):
        metadata = metadatas[i] if i < len(metadatas) else {}
        distance = distances[i] if i < len(distances) else None

        retrieved_chunks.append(
            {
                "rank": i + 1,
                "text": document,
                "source": metadata.get("source"),
                "chunk_index": metadata.get("chunk_index"),
                "title": metadata.get("title"),
                "distance": distance,
            }
        )

    return retrieved_chunks


if __name__ == "__main__":
    question = "C'est quoi le RAG et pourquoi est-ce utile pour un tuteur pédagogique ?"

    chunks = search_course(question, n_results=3)

    print(f"Question : {question}\n")

    for chunk in chunks:
        print("=" * 80)
        print(f"Rang : {chunk['rank']}")
        print(f"Source : {chunk['source']}")
        print(f"Chunk : {chunk['chunk_index']}")
        print(f"Distance : {chunk['distance']}")
        print("-" * 80)
        print(chunk["text"][:800])
        print()

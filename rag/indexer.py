import re
from pathlib import Path

import chromadb

from rag.chunker import chunk_markdown_file, chunk_document_text
from rag.document_loader import load_document_text
from rag.embeddings import get_embedding_function, collection_metadata
from config import settings


VECTORSTORE_PATH = "data/vectorstore"
COLLECTION_NAME = "course_chunks"


def _is_indexable(path: Path) -> bool:
    """Vrai si le fichier est un cours indexable (format supporté, non temporaire)."""
    if not path.is_file():
        return False
    if path.name.startswith(".") or path.name.endswith("~"):
        return False
    return path.suffix.lower() in settings.SUPPORTED_EXTENSIONS


def _relative_source(path: Path) -> str:
    """Chemin du cours relatif à la racine du projet (pour un affichage propre)."""
    try:
        return path.relative_to(settings.PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _derive_title(text: str, path: Path) -> str:
    """Titre du cours : premier titre Markdown H1, sinon nom de fichier nettoyé."""
    match = re.search(r"^\s{0,3}#\s+(.+?)\s*$", text, flags=re.MULTILINE)
    if match:
        title = re.sub(r"^cours\s*:\s*", "", match.group(1), flags=re.IGNORECASE)
        return title.strip()
    return path.stem.replace("_", " ").replace("-", " ").strip()


def reset_collection(client, collection_name: str):
    """
    Supprime puis recrée la collection.
    Pour le POC, c'est plus simple : à chaque indexation, on repart proprement.
    """
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass

    return client.get_or_create_collection(
        name=collection_name,
        embedding_function=get_embedding_function(),
        metadata=collection_metadata(),
    )


def index_course(file_path: str):
    """
    Découpe un cours Markdown en chunks et les indexe dans ChromaDB.
    """
    chunks = chunk_markdown_file(file_path)

    if not chunks:
        raise ValueError("Aucun chunk généré. Vérifie le fichier de cours.")

    client = chromadb.PersistentClient(path=VECTORSTORE_PATH)
    collection = reset_collection(client, COLLECTION_NAME)

    ids = [chunk["id"] for chunk in chunks]
    documents = [chunk["text"] for chunk in chunks]
    metadatas = [
        {
            "source": chunk["source"],
            "chunk_index": chunk["chunk_index"],
        }
        for chunk in chunks
    ]

    collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
    )

    print(f"Indexation terminée.")
    print(f"Fichier indexé : {file_path}")
    print(f"Nombre de chunks indexés : {len(chunks)}")
    print(f"Collection : {COLLECTION_NAME}")
    print(f"Vectorstore : {VECTORSTORE_PATH}")


def index_all_courses(courses_dir: str | None = None, reset: bool = True) -> dict:
    """
    Indexe TOUS les cours supportés d'un dossier (logique principale du MVP).

    - lit les fichiers .md/.txt/.pdf de `courses_dir` (data/courses par défaut) ;
    - ignore dossiers, fichiers cachés/temporaires et formats non supportés ;
    - ne touche jamais à data/samples/ (dossier distinct) ;
    - réinitialise la collection si `reset=True` (base active = cours présents) ;
    - n'échoue pas s'il n'y a aucun cours.

    Retour :
        {"status": "success"|"empty", "documents_indexed": int,
         "chunks_indexed": int, "errors": [..], "message"?: str}
    """
    base = Path(courses_dir) if courses_dir else settings.COURSES_DIR
    if not base.is_absolute():
        base = settings.PROJECT_ROOT / base

    files = sorted(p for p in base.iterdir() if _is_indexable(p)) if base.exists() else []

    client = chromadb.PersistentClient(path=str(settings.VECTORSTORE_PATH))

    errors: list[str] = []
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    documents_indexed = 0

    for path in files:
        try:
            text = load_document_text(str(path))
            title = _derive_title(text, path)
            chunks = chunk_document_text(
                text=text, source=_relative_source(path), title=title
            )
            if not chunks:
                errors.append(f"{path.name} : aucun contenu exploitable.")
                continue

            for chunk in chunks:
                ids.append(chunk["id"])
                documents.append(chunk["text"])
                meta = {
                    "source": chunk["source"],
                    "chunk_index": chunk["chunk_index"],
                }
                if chunk.get("title"):
                    meta["title"] = chunk["title"]
                metadatas.append(meta)

            documents_indexed += 1
        except Exception as exc:
            errors.append(f"{path.name} : {exc}")

    if reset:
        # Construire dans une collection temporaire, puis basculer d'un coup :
        # la base active n'est jamais vide pendant la (lente) vectorisation.
        building_name = settings.COLLECTION_NAME + "_building"
        try:
            client.delete_collection(building_name)
        except Exception:
            pass
        building = client.get_or_create_collection(
            name=building_name,
            embedding_function=get_embedding_function(),
            metadata=collection_metadata(),
        )
        if ids:
            building.add(ids=ids, documents=documents, metadatas=metadatas)
        # Bascule quasi instantanée (pas de vectorisation ici).
        try:
            client.delete_collection(settings.COLLECTION_NAME)
        except Exception:
            pass
        building.modify(name=settings.COLLECTION_NAME)
    else:
        collection = client.get_or_create_collection(
            name=settings.COLLECTION_NAME,
            embedding_function=get_embedding_function(),
            metadata=collection_metadata(),
        )
        if ids:
            collection.add(ids=ids, documents=documents, metadatas=metadatas)

    if documents_indexed == 0:
        return {
            "status": "empty",
            "documents_indexed": 0,
            "chunks_indexed": 0,
            "errors": errors,
            "message": (
                f"Aucun cours indexable dans {_relative_source(base)}/. "
                "Ajoute des fichiers .md, .txt ou .pdf."
            ),
        }

    return {
        "status": "success",
        "documents_indexed": documents_indexed,
        "chunks_indexed": len(ids),
        "errors": errors,
    }


if __name__ == "__main__":
    result = index_all_courses()
    print(result)

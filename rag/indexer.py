import re
import threading
from pathlib import Path

import chromadb

from rag.chunker import chunk_segments
from rag.document_loader import load_document_segments
from rag.embeddings import get_embedding_function, collection_metadata
from config import settings, workspace

# Sérialise les écritures ChromaDB : l'enrichissement vision tourne en tâche de
# fond (après un upload) et ne doit pas écrire en même temps qu'un autre sync.
_WRITE_LOCK = threading.Lock()


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


def _derive_title(path: Path) -> str:
    """Titre du cours : premier titre Markdown H1 (.md), sinon nom de fichier nettoyé."""
    if path.suffix.lower() == ".md":
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                match = re.match(r"^\s{0,3}#\s+(.+?)\s*$", line)
                if match:
                    title = re.sub(
                        r"^cours\s*:\s*", "", match.group(1), flags=re.IGNORECASE
                    )
                    return title.strip()
        except OSError:
            pass
    return path.stem.replace("_", " ").replace("-", " ").strip()


def _course_records(path: Path) -> tuple[list[str], list[str], list[dict]] | None:
    """
    Construit (ids, documents, metadatas) pour UN cours. La métadonnée `mtime`
    (date de modif du fichier) permet au sync incrémental de détecter un cours
    modifié. Retourne None si le cours ne produit aucun chunk exploitable.
    """
    segments = load_document_segments(str(path))
    title = _derive_title(path)
    source = _relative_source(path)
    chunks = chunk_segments(segments, source=source, title=title)
    if not chunks:
        return None

    mtime = path.stat().st_mtime
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    for chunk in chunks:
        ids.append(chunk["id"])
        documents.append(chunk["text"])
        meta = {
            "source": chunk["source"],
            "chunk_index": chunk["chunk_index"],
            "mtime": mtime,
        }
        if chunk.get("title"):
            meta["title"] = chunk["title"]
        if chunk.get("section"):
            meta["section"] = chunk["section"]
        if chunk.get("page") is not None:
            meta["page"] = chunk["page"]
        metadatas.append(meta)
    return ids, documents, metadatas


# --- Vision : indexation des figures/schémas d'un cours ---------------------

def _course_vision_records(path: Path) -> tuple[list[str], list[str], list[dict]] | None:
    """(ids, documents, metadatas) des DESCRIPTIONS d'images d'un cours, ou None.
    ids distincts du texte (`<stem>_img_<n>`), métadonnée `kind="image"`."""
    from rag.vision_describe import course_vision_segments

    segments = course_vision_segments(str(path))
    if not segments:
        return None
    source = _relative_source(path)
    title = _derive_title(path)
    chunks = chunk_segments(segments, source=source, title=title)
    if not chunks:
        return None

    mtime = path.stat().st_mtime
    stem = Path(source).stem
    ids, documents, metadatas = [], [], []
    for index, chunk in enumerate(chunks, start=1):
        ids.append(f"{stem}_img_{index}")
        documents.append(chunk["text"])
        meta = {"source": source, "chunk_index": chunk["chunk_index"],
                "mtime": mtime, "kind": "image"}
        if chunk.get("title"):
            meta["title"] = chunk["title"]
        if chunk.get("section"):
            meta["section"] = chunk["section"]
        if chunk.get("page") is not None:
            meta["page"] = chunk["page"]
        metadatas.append(meta)
    return ids, documents, metadatas


def _collection(client=None):
    client = client or chromadb.PersistentClient(path=str(settings.VECTORSTORE_PATH))
    return client.get_or_create_collection(
        name=workspace.collection_name(),
        embedding_function=get_embedding_function(),
        metadata=collection_metadata(),
    )


def enrich_course_vision(path: str | Path) -> int:
    """
    Décrit les images d'un cours (schémas, figures) et les (ré)indexe comme du
    texte recherchable. Idempotent (retire d'abord les anciennes figures du
    cours) et sérialisé (verrou). Coûteux -> à lancer en tâche de fond.
    Retourne le nombre de figures indexées.
    """
    path = Path(path)
    if not settings.RAG_VISION_ENABLED:
        return 0
    records = _course_vision_records(path)  # appels vision HORS lock (lents)
    source = _relative_source(path)
    with _WRITE_LOCK:
        collection = _collection()
        try:
            collection.delete(where={"$and": [{"source": source}, {"kind": "image"}]})
        except Exception:
            pass
        if records:
            ids, documents, metadatas = records
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
            return len(ids)
    return 0


def _indexable_files(courses_dir: str | None) -> tuple[Path, dict[str, Path]]:
    """Dossier de base + mapping {source relatif: chemin} des cours indexables."""
    base = Path(courses_dir) if courses_dir else workspace.courses_dir()
    if not base.is_absolute():
        base = settings.PROJECT_ROOT / base
    files = (
        {_relative_source(p): p for p in base.iterdir() if _is_indexable(p)}
        if base.exists()
        else {}
    )
    return base, files


def sync_courses_index(courses_dir: str | None = None) -> dict:
    """
    Synchronisation INCRÉMENTALE du vectorstore avec le dossier des cours.

    Le vectorstore est la source persistante : chaque cours n'est vectorisé
    qu'UNE fois. On compare les cours présents (et leur date de modif) à ce qui
    est déjà indexé, puis on n'agit que sur les différences :
      - cours ajouté   -> on indexe ses chunks ;
      - cours supprimé -> on retire ses chunks ;
      - cours modifié  -> on remplace ses chunks ;
      - inchangé       -> on n'y touche pas (rechargement de page = no-op).

    Retour : {"status", "added", "updated", "removed", "chunks_added", "errors"}.
    """
    _, files = _indexable_files(courses_dir)
    client = chromadb.PersistentClient(path=str(settings.VECTORSTORE_PATH))
    collection = client.get_or_create_collection(
        name=workspace.collection_name(),
        embedding_function=get_embedding_function(),
        metadata=collection_metadata(),
    )

    # Cours déjà indexés (source -> mtime stockée).
    existing: dict[str, float | None] = {}
    data = collection.get(include=["metadatas"])
    for meta in data.get("metadatas") or []:
        source = meta.get("source")
        if source is not None and source not in existing:
            existing[source] = meta.get("mtime")

    current_mtimes = {source: path.stat().st_mtime for source, path in files.items()}
    removed = [s for s in existing if s not in files]
    changed = [
        s for s in files if s not in existing or existing.get(s) != current_mtimes[s]
    ]

    # On retire les chunks des cours supprimés ET des cours modifiés (avant ré-ajout).
    for source in removed + [s for s in changed if s in existing]:
        try:
            collection.delete(where={"source": source})
        except Exception:
            pass

    added: list[str] = []
    updated: list[str] = []
    errors: list[str] = []
    chunks_added = 0
    for source in changed:
        try:
            records = _course_records(files[source])
            if records is None:
                errors.append(f"{source} : aucun contenu exploitable.")
                continue
            ids, documents, metadatas = records
            collection.add(ids=ids, documents=documents, metadatas=metadatas)
            chunks_added += len(ids)
            (updated if source in existing else added).append(source)
        except Exception as exc:
            errors.append(f"{source} : {exc}")

    return {
        "status": "success",
        "added": added,
        "updated": updated,
        "removed": removed,
        "chunks_added": chunks_added,
        "errors": errors,
    }


def index_all_courses(courses_dir: str | None = None, reset: bool = True,
                      with_vision: bool | None = None) -> dict:
    """
    Indexe TOUS les cours d'un dossier en RECONSTRUISANT la base (rebuild complet).

    À utiliser quand la LOGIQUE d'indexation change (découpage, embedding) : le
    sync incrémental ne réindexe que les fichiers modifiés, donc un changement de
    logique nécessite ce rebuild. Pour l'usage normal (ajout/suppression de
    cours), préférer `sync_courses_index`.

    `with_vision` (défaut = `settings.RAG_VISION_ENABLED`) : décrit aussi les
    figures/schémas des cours (lent : un appel LLM par image). Le texte est
    indexé d'abord, puis les figures cours par cours.

    Retour :
        {"status": "success"|"empty", "documents_indexed": int,
         "chunks_indexed": int, "figures_indexed": int, "errors": [..], "message"?: str}
    """
    if with_vision is None:
        with_vision = settings.RAG_VISION_ENABLED
    base, files_map = _indexable_files(courses_dir)
    files = sorted(files_map.values())

    client = chromadb.PersistentClient(path=str(settings.VECTORSTORE_PATH))

    errors: list[str] = []
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    documents_indexed = 0

    for path in files:
        try:
            records = _course_records(path)
            if records is None:
                errors.append(f"{path.name} : aucun contenu exploitable.")
                continue
            file_ids, file_docs, file_metas = records
            ids.extend(file_ids)
            documents.extend(file_docs)
            metadatas.extend(file_metas)
            documents_indexed += 1
        except Exception as exc:
            errors.append(f"{path.name} : {exc}")

    if reset:
        # Construire dans une collection temporaire, puis basculer d'un coup :
        # la base active n'est jamais vide pendant la (lente) vectorisation.
        building_name = workspace.collection_name() + "_building"
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
            client.delete_collection(workspace.collection_name())
        except Exception:
            pass
        building.modify(name=workspace.collection_name())
    else:
        collection = client.get_or_create_collection(
            name=workspace.collection_name(),
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

    # Figures/schémas : décrits par vision et ajoutés à la collection finale.
    figures_indexed = 0
    if with_vision:
        for path in files:
            try:
                figures_indexed += enrich_course_vision(path)
            except Exception as exc:
                errors.append(f"{path.name} (vision) : {exc}")

    return {
        "status": "success",
        "documents_indexed": documents_indexed,
        "chunks_indexed": len(ids),
        "figures_indexed": figures_indexed,
        "errors": errors,
    }


if __name__ == "__main__":
    result = index_all_courses()
    print(result)

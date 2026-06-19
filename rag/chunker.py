import re
from pathlib import Path
from typing import List, Dict

from config import settings


def read_markdown_file(file_path: str) -> str:
    """
    Lit un fichier Markdown et retourne son contenu texte.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {file_path}")

    return path.read_text(encoding="utf-8")


def split_long_text(text: str, max_chars: int) -> List[str]:
    """
    Découpe un long texte en morceaux de taille raisonnable.
    Cette fonction évite de créer des chunks vides si un paragraphe est trop long.
    """
    words = text.split()
    chunks = []
    current = ""

    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current = f"{current} {word}".strip()
        else:
            if current.strip():
                chunks.append(current.strip())
            current = word

    if current.strip():
        chunks.append(current.strip())

    return chunks


def chunk_text_by_paragraph(
    text: str,
    source: str,
    max_chars: int = 800,
    overlap_chars: int = 100
) -> List[Dict]:
    """
    Découpe un texte en chunks basés sur les paragraphes.

    Objectifs :
    - éviter les chunks vides ;
    - respecter les paragraphes quand c'est possible ;
    - découper proprement les paragraphes trop longs ;
    - garder un petit overlap entre les chunks.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    raw_chunks = []
    current_chunk = ""

    for paragraph in paragraphs:
        # Si le paragraphe est lui-même trop long, on le découpe d'abord.
        paragraph_parts = (
            split_long_text(paragraph, max_chars)
            if len(paragraph) > max_chars
            else [paragraph]
        )

        for part in paragraph_parts:
            if len(current_chunk) + len(part) + 2 <= max_chars:
                current_chunk = (
                    f"{current_chunk}\n\n{part}".strip()
                    if current_chunk
                    else part
                )
            else:
                if current_chunk.strip():
                    raw_chunks.append(current_chunk.strip())

                overlap = (
                    current_chunk[-overlap_chars:].strip()
                    if current_chunk and overlap_chars > 0
                    else ""
                )

                current_chunk = (
                    f"{overlap}\n\n{part}".strip()
                    if overlap
                    else part
                )

    if current_chunk.strip():
        raw_chunks.append(current_chunk.strip())

    # Sécurité : suppression de tout chunk vide résiduel.
    raw_chunks = [chunk for chunk in raw_chunks if chunk.strip()]

    return [
        {
            "id": f"{Path(source).stem}_chunk_{i + 1}",
            "source": source,
            "chunk_index": i + 1,
            "text": chunk
        }
        for i, chunk in enumerate(raw_chunks)
    ]


def _split_text(text: str, target: int, overlap: int) -> List[str]:
    """
    Découpe un texte en morceaux de ~`target` caractères, en respectant d'abord
    les paragraphes, puis les phrases, avec un recouvrement `overlap` entre
    morceaux. Sert à découper un segment (section) trop long.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= target:
        return [text]

    # Unités élémentaires : paragraphes, eux-mêmes redécoupés par phrases si longs.
    units: List[str] = []
    for para in (p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()):
        if len(para) <= target:
            units.append(para)
            continue
        buffer = ""
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            if len(sentence) > target:  # phrase démesurée -> découpe dure
                if buffer:
                    units.append(buffer)
                    buffer = ""
                for i in range(0, len(sentence), target):
                    units.append(sentence[i : i + target])
            elif len(buffer) + len(sentence) + 1 <= target:
                buffer = f"{buffer} {sentence}".strip()
            else:
                if buffer:
                    units.append(buffer)
                buffer = sentence
        if buffer:
            units.append(buffer)

    # Empaquetage des unités vers la taille cible, avec recouvrement.
    pieces: List[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) + 2 > target:
            pieces.append(current.strip())
            tail = current[-overlap:].strip() if overlap > 0 else ""
            current = f"{tail}\n\n{unit}".strip() if tail else unit
        else:
            current = f"{current}\n\n{unit}".strip() if current else unit
    if current.strip():
        pieces.append(current.strip())
    return pieces


def chunk_segments(
    segments: List[Dict],
    source: str,
    title: str | None = None,
    target_chars: int | None = None,
    overlap_chars: int | None = None,
) -> List[Dict]:
    """
    Transforme des segments structurés (`{"text", "heading", "page"}`, produits
    par document_loader) en chunks prêts à indexer.

    Chaque chunk : id, source, chunk_index, text, et si disponibles title (cours),
    section (titre de section/diapo) et page. Un segment trop long est redécoupé à
    la taille cible ; on ne fusionne jamais deux sections différentes.
    """
    target = target_chars if target_chars is not None else settings.CHUNK_TARGET_CHARS
    overlap = overlap_chars if overlap_chars is not None else settings.CHUNK_OVERLAP_CHARS

    stem = Path(source).stem
    chunks: List[Dict] = []
    index = 0
    for segment in segments:
        for piece in _split_text(segment["text"], target, overlap):
            index += 1
            chunk: Dict = {
                "id": f"{stem}_chunk_{index}",
                "source": source,
                "chunk_index": index,
                "text": piece,
            }
            if title:
                chunk["title"] = title
            if segment.get("heading"):
                chunk["section"] = segment["heading"]
            if segment.get("page") is not None:
                chunk["page"] = segment["page"]
            chunks.append(chunk)
    return chunks


def chunk_markdown_file(file_path: str) -> List[Dict]:
    """
    Lit un fichier Markdown et retourne une liste de chunks.
    """
    text = read_markdown_file(file_path)
    return chunk_text_by_paragraph(text=text, source=file_path)


def chunk_document_text(
    text: str,
    source: str,
    title: str | None = None,
) -> List[Dict]:
    """
    Découpe le texte d'un document quelconque (Markdown, txt, PDF déjà extrait)
    en chunks. Générique : ne dépend pas du format d'origine.

    Chaque chunk contient : id, source, chunk_index, text, et title si fourni.
    """
    chunks = chunk_text_by_paragraph(text=text, source=source)
    if title:
        for chunk in chunks:
            chunk["title"] = title
    return chunks


if __name__ == "__main__":
    file_path = "data/courses/rag_intro.md"
    chunks = chunk_markdown_file(file_path)

    print(f"Nombre de chunks générés : {len(chunks)}\n")

    for chunk in chunks:
        print("=" * 80)
        print(f"ID : {chunk['id']}")
        print(f"Source : {chunk['source']}")
        print(f"Index : {chunk['chunk_index']}")
        print("-" * 80)
        print(chunk["text"][:700])
        print()

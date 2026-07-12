"""
Découpage des documents en chunks pour la base vectorielle.

Chemin actuel : `chunk_segments(segments, …)` — consomme les segments
STRUCTURÉS produits par `rag.document_loader.load_document_segments`
(`{"text", "heading", "page"}`) et les empaquette à une taille cible sans
jamais fusionner deux sections. Les anciennes fonctions de découpage « par
paragraphes » (V1, pré-structure-aware) ont été retirées : plus aucun code ne
les utilisait.
"""

import re
from pathlib import Path
from typing import Dict, List

from config import settings


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


if __name__ == "__main__":
    # Démo : découpe un document réel et montre les chunks produits.
    #   python -m rag.chunker "data/courses/<fichier>"
    import sys

    from rag.document_loader import load_document_segments

    if len(sys.argv) < 2:
        sys.exit("Usage : python -m rag.chunker <chemin du document>")

    file_path = sys.argv[1]
    segments = load_document_segments(file_path)
    chunks = chunk_segments(segments, source=file_path)

    print(f"Segments : {len(segments)} | Chunks générés : {len(chunks)}\n")
    for chunk in chunks[:10]:
        print("=" * 80)
        print(f"ID : {chunk['id']} | Section : {chunk.get('section')} | Page : {chunk.get('page')}")
        print("-" * 80)
        print(chunk["text"][:300])
        print()
    if len(chunks) > 10:
        print(f"… (+{len(chunks) - 10} autres chunks)")

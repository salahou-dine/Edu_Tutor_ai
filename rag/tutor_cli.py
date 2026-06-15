import argparse
from pathlib import Path

from rag.retriever import search_course
from rag.tutor_prompt import build_tutor_prompt, save_prompt


DEFAULT_OUTPUT_PATH = "data/last_tutor_prompt.md"


def display_retrieved_chunks(chunks):
    """
    Affiche les chunks retrouvés de manière lisible dans le terminal.
    """
    if not chunks:
        print("Aucun chunk retrouvé.")
        return

    print("\nChunks récupérés :\n")

    for chunk in chunks:
        print("=" * 80)
        print(f"Rang      : {chunk['rank']}")
        print(f"Source    : {chunk['source']}")
        print(f"Chunk     : {chunk['chunk_index']}")
        print(f"Distance  : {chunk['distance']}")
        print("-" * 80)

        preview = chunk["text"][:500].replace("\n", " ")
        print(preview)

        if len(chunk["text"]) > 500:
            print("...")

        print()


def run_tutor_cli(question: str, n_results: int, output_path: str):
    """
    Pipeline CLI complet :
    - recherche des chunks ;
    - génération du prompt ;
    - sauvegarde du prompt ;
    - affichage des instructions pour Hermes.
    
    """
    print("\nAgent Tuteur pédagogique — Préparation RAG")
    print("=" * 80)
    print(f"Question étudiant : {question}")
    print(f"Nombre de chunks demandés : {n_results}")

    chunks = search_course(question, n_results=n_results)
    display_retrieved_chunks(chunks)

    prompt = build_tutor_prompt(question=question, n_results=n_results)
    save_prompt(prompt, output_path=output_path)

    print("=" * 80)
    print("Prompt RAG généré avec succès.")
    print(f"Fichier : {output_path}")
    print("=" * 80)

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="CLI pour préparer une question RAG destinée au skill Hermes education-tutor."
    )

    parser.add_argument(
        "question",
        type=str,
        help="Question posée par l'étudiant."
    )

    parser.add_argument(
        "--n-results",
        type=int,
        default=3,
        help="Nombre de chunks à récupérer depuis ChromaDB."
    )

    parser.add_argument(
        "--output",
        type=str,
        default=DEFAULT_OUTPUT_PATH,
        help="Chemin du fichier prompt généré."
    )

    args = parser.parse_args()

    run_tutor_cli(
        question=args.question,
        n_results=args.n_results,
        output_path=args.output
    )

import argparse
from pathlib import Path

from rag.retriever import search_course


OUTPUT_PATH = "data/last_tutor_prompt.md"


def build_tutor_prompt(question: str, n_results: int = 3) -> str:
    """
    Construit un prompt RAG à donner à Hermes avec le skill education-tutor.
    """
    chunks = search_course(question, n_results=n_results)

    if not chunks:
        context_block = "Aucun contexte de cours n'a été retrouvé."
    else:
        context_parts = []

        for chunk in chunks:
            context_parts.append(
                f"""
[Source {chunk['rank']}]
Document: {chunk['source']}
Chunk: {chunk['chunk_index']}
Distance: {chunk['distance']}

Contenu:
{chunk['text']}
""".strip()
            )

        context_block = "\n\n---\n\n".join(context_parts)

    prompt = f"""
Utilise le skill education-tutor.

Question de l'étudiant :
{question}

Contexte récupéré depuis les cours indexés :
{context_block}

Consignes :
Consignes :
- Réponds entièrement en français.
- Réponds uniquement à partir du contexte fourni quand c'est possible.
- Si le contexte ne suffit pas, dis-le clairement.
- N'invente aucune source.
- N'ajoute aucun fait précis qui n'apparaît pas dans le contexte récupéré.
- Si tu donnes un exemple, il doit être générique ou explicitement présenté comme hypothétique.
- Respecte exactement la structure suivante :
  1. Answer
  2. Explanation
  3. Example
  4. Source(s)
  5. Question de vérification
- Dans Source(s), cite les documents et les numéros de chunks utilisés.
""".strip()

    return prompt


def save_prompt(prompt: str, output_path: str = OUTPUT_PATH):
    """
    Sauvegarde le prompt généré dans un fichier Markdown.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Génère un prompt RAG pour le tuteur pédagogique Hermes."
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
        help="Nombre de chunks à récupérer."
    )

    args = parser.parse_args()

    tutor_prompt = build_tutor_prompt(
        question=args.question,
        n_results=args.n_results
    )

    save_prompt(tutor_prompt)

    print("Prompt généré avec succès.")
    print(f"Fichier : {OUTPUT_PATH}")
    print()
    print(tutor_prompt)

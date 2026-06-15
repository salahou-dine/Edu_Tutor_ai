"""
Test manuel du tuteur pédagogique sur trois questions représentatives :

1. Question dans le cours indexé        -> attendu : course_grounded
2. Question partiellement dans le cours -> attendu : mixed (ou course_grounded)
3. Question hors-sujet                   -> attendu : general_tutor

Lancer depuis la racine du projet :
    python3 -m tests_manual.test_tutor_agent

Ce n'est pas un test unitaire automatisé : il affiche les résultats pour
inspection humaine (mode choisi, statut Hermes, réponse, indications).
"""

from agents.tutor_agent import (
    answer_student_question,
    answer_student_question_for_ui,
)


QUESTIONS = [
    "Quelles sont les étapes principales d'un pipeline RAG ?",
    "Pourquoi le RAG réduit-il les hallucinations ?",
    "Comment fonctionne la photosynthèse ?",
]


def run() -> None:
    for i, question in enumerate(QUESTIONS, start=1):
        result = answer_student_question(question)

        print("#" * 80)
        print(f"Question {i} : {question}")
        print("-" * 80)
        print(f"Mode pédagogique : {result['mode']}")
        print(
            f"Statut Hermes : {result['status']} "
            f"(méthode : {result['debug']['hermes_call_method']})"
        )

        if result["status"] == "success":
            print("\nRéponse :")
            print(result["answer"])
            if result["verification_question"]:
                print("\nQuestion de vérification extraite :")
                print(result["verification_question"])
        else:
            print("\nRéponse automatique indisponible.")
            print(result["message"])

        print("\nIndication(s) de la partie du cours :")
        if result["course_indications"]:
            for ind in result["course_indications"]:
                print(f"  • {ind['course']} — {ind['part']}")
                print(f"    {ind['excerpt']}")
        else:
            print("  (aucune — réponse non fondée sur un cours indexé)")

        print()


def run_ui_check() -> None:
    """Vérifie la sortie propre destinée à l'interface (sans debug)."""
    question = QUESTIONS[1]
    ui = answer_student_question_for_ui(question)

    print("#" * 80)
    print("Sortie interface (answer_student_question_for_ui)")
    print("-" * 80)
    print(f"Question : {question}")
    print(f"status : {ui['status']}")
    print(f"mode : {ui['mode']}")
    print(f"contient debug : {'debug' in ui}")
    print(f"student_answer (début) : {ui['student_answer'][:120]}")
    print(f"nombre d'indications : {len(ui['course_indications'])}")
    print(f"question de vérification : {ui['verification_question'][:120]}")
    print()


if __name__ == "__main__":
    run()
    run_ui_check()

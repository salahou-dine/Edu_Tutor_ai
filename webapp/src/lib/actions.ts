/**
 * Les 6 objectifs de l'écran d'accueil — MÊMES intitulés et MÊMES gabarits de
 * phrase que la version Streamlit (interface/streamlit_app.py:ACTION_CARDS) :
 * l'orchestrateur route ces formulations de façon déterministe et testée.
 */

import {
  BookOpen,
  Brain,
  FileText,
  Lightbulb,
  PenLine,
  Target,
  type LucideIcon,
} from "lucide-react";

export interface ActionCard {
  key: string;
  icon: LucideIcon;
  title: string;
  sub: string;
  needs: "course" | "text";
  /** needs=course : phrase complète avec {course}. */
  template?: string;
  /** needs=text : préfixe + indication de saisie. */
  prefix?: string;
  placeholder?: string;
}

export const ACTION_CARDS: ActionCard[] = [
  {
    key: "exam",
    icon: Target,
    title: "Préparer mon examen",
    sub: "on démarre par un résumé",
    needs: "course",
    template:
      "Aide-moi à préparer mon examen : fais-moi un résumé structuré du cours {course}.",
  },
  {
    key: "revise",
    icon: BookOpen,
    title: "Réviser un cours",
    sub: "un résumé clair et structuré",
    needs: "course",
    template: "Fais-moi un résumé du cours {course}.",
  },
  {
    key: "quiz",
    icon: Brain,
    title: "M'entraîner",
    sub: "le tuteur t'interroge",
    needs: "course",
    // Ne pas dire « réviser/fiche » ici : routage tuteur, pas agent Contenu.
    template: "Interroge-moi sur le cours {course} pour tester mes connaissances.",
  },
  {
    key: "sheet",
    icon: FileText,
    title: "Créer une fiche",
    sub: "une fiche de révision",
    needs: "course",
    template: "Fais-moi une fiche de révision du cours {course}.",
  },
  {
    key: "notion",
    icon: Lightbulb,
    title: "Comprendre une notion",
    sub: "une explication pédagogique",
    needs: "text",
    prefix: "Explique-moi",
    placeholder: "Quelle notion ? (ex. la défense en profondeur)",
  },
  {
    key: "write",
    icon: PenLine,
    title: "Rédiger un document",
    sub: "rapport, exposé, note…",
    needs: "text",
    prefix: "Rédige-moi",
    placeholder: "Quoi ? (ex. un rapport d'une page sur les pare-feux)",
  },
];

/** Libellé court d'un cours pour une pastille. */
export function shortCourseLabel(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, "").replace(/[_-]/g, " ").trim();
  return stem.length <= 30 ? stem : stem.slice(0, 29).trimEnd() + "…";
}

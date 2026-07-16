/**
 * Libellés pédagogiques (jamais de jargon technique côté étudiant) — miroir de
 * MODE_INFO / AGENT_INFO de la version Streamlit.
 */

import {
  FileText,
  GraduationCap,
  HelpCircle,
  PenLine,
  ScanSearch,
  type LucideIcon,
} from "lucide-react";

/** Badge de mode affiché au-dessus d'une réponse. */
export const MODE_LABELS: Record<string, string> = {
  course_grounded: "Réponse basée sur ton cours",
  mixed: "Cours + complément pédagogique",
  general_tutor: "Réponse générale",
  clarify: "Quel cours ?",
  course_summary: "Résumé du cours",
  idp: "Analyse du document",
  content: "Ressource d'étude",
  analyze: "Analyse + résumé",
  compose: "Document rédigé",
};

/** Agents affichés dans « Répondu par … ». */
export const AGENT_INFO: Record<string, { label: string; icon: LucideIcon }> = {
  tutor: { label: "Tuteur", icon: GraduationCap },
  idp: { label: "Analyse (IDP)", icon: ScanSearch },
  content: { label: "Contenu", icon: FileText },
  compose: { label: "Rédaction", icon: PenLine },
  clarify: { label: "Clarification", icon: HelpCircle },
};

/** Messages de la timeline de progression (SSE). */
export const STEP_LABELS: Record<string, string> = {
  planning: "Je réfléchis à ta demande…",
  tutor: "Le tuteur rédige sa réponse…",
  idp: "Analyse du document…",
  content: "Création de la ressource d'étude…",
  compose: "Rédaction du document…",
};

export const STEP_DONE_LABELS: Record<string, string> = {
  planning: "Demande comprise",
  tutor: "Réponse du tuteur",
  idp: "Document analysé",
  content: "Ressource créée",
  compose: "Document rédigé",
};

/** Titre d'un livrable selon son type. */
export const DELIVERABLE_KIND: Record<string, string> = {
  summary: "Résumé",
  revision: "Fiche de révision",
  document: "Document",
};

import { useState } from "react";
import { ArrowLeft, Sparkles } from "lucide-react";

import type { CourseDoc } from "../lib/api";
import { ACTION_CARDS, shortCourseLabel, type ActionCard } from "../lib/actions";

interface Props {
  documents: CourseDoc[];
  /** Phrase composée, prête à être soumise au chat. */
  onCompose: (prompt: string) => void;
}

/**
 * Écran d'accueil orienté objectif (miroir de la Phase A Streamlit) :
 * cartes -> [pastilles de cours | saisie libre] -> phrase envoyée au chat.
 */
export function Home({ documents, onCompose }: Props) {
  const [active, setActive] = useState<ActionCard | null>(null);
  const [freeText, setFreeText] = useState("");

  function clickCard(card: ActionCard) {
    if (card.needs === "course") {
      if (documents.length === 1) {
        onCompose(card.template!.replace("{course}", documents[0].filename));
        return;
      }
      if (documents.length === 0) return; // le bandeau sous les cartes l'explique
    }
    setActive(card);
    setFreeText("");
  }

  /* Étape « préciser » -------------------------------------------------- */
  if (active) {
    return (
      <div className="max-w-3xl w-full mx-auto flex flex-col gap-5">
        <div>
          <h1 className="text-2xl font-bold">{active.title}</h1>
          <p className="text-muted text-sm mt-1">
            {active.needs === "course" ? "Sur quel cours ?" : active.sub}
          </p>
        </div>

        {active.needs === "course" ? (
          <div className="flex flex-wrap gap-2.5">
            {documents.map((doc) => (
              <button
                key={doc.filename}
                title={doc.filename}
                onClick={() =>
                  onCompose(active.template!.replace("{course}", doc.filename))
                }
                className="px-4 py-2 rounded-full text-sm font-medium border
                           border-accent/60 text-ink hover:bg-accent/15 transition-colors"
              >
                {shortCourseLabel(doc.filename)}
              </button>
            ))}
          </div>
        ) : (
          <form
            className="glass flex items-center gap-2 p-2 pl-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (freeText.trim()) onCompose(`${active.prefix} ${freeText.trim()}`);
            }}
          >
            <input
              autoFocus
              value={freeText}
              onChange={(event) => setFreeText(event.target.value)}
              placeholder={active.placeholder}
              className="flex-1 bg-transparent outline-none text-sm placeholder:text-muted"
            />
            <button
              type="submit"
              disabled={!freeText.trim()}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-accent/85
                         hover:bg-accent disabled:opacity-40 transition-colors"
            >
              Envoyer
            </button>
          </form>
        )}

        <button
          onClick={() => setActive(null)}
          className="self-start flex items-center gap-1.5 text-sm text-muted hover:text-ink"
        >
          <ArrowLeft size={14} /> Retour
        </button>
      </div>
    );
  }

  /* Écran d'accueil ------------------------------------------------------ */
  return (
    <div className="max-w-3xl w-full mx-auto flex flex-col items-center gap-8">
      <div className="glass p-4 rounded-full">
        <Sparkles size={26} className="text-accent-soft" />
      </div>
      <div className="text-center">
        <h1 className="text-3xl font-bold tracking-tight">
          Qu'est-ce qu'on fait aujourd'hui ?
        </h1>
        <p className="text-muted mt-2">
          Choisis un objectif, ou pose directement ta question en bas.
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 w-full">
        {ACTION_CARDS.map((card) => (
          <button
            key={card.key}
            onClick={() => clickCard(card)}
            className="glass p-4 flex flex-col items-start gap-2 text-left
                       hover:bg-surface-hover hover:border-accent/40 transition-colors group"
          >
            <card.icon
              size={18}
              className="text-muted group-hover:text-accent-soft transition-colors"
            />
            <span className="text-sm font-semibold">{card.title}</span>
            <span className="text-xs text-muted">{card.sub}</span>
          </button>
        ))}
      </div>

      {documents.length === 0 && (
        <p className="text-xs text-muted">
          Ajoute un cours dans la barre latérale pour débloquer la révision, les
          fiches et les quiz.
        </p>
      )}
    </div>
  );
}

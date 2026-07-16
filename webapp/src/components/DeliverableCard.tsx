import { useEffect, useState } from "react";
import { Download, Loader2, Maximize2, X } from "lucide-react";
import ReactMarkdown from "react-markdown";

import type { Deliverable } from "../lib/api";
import { downloadDeliverable } from "../lib/chat";
import { DELIVERABLE_KIND } from "../lib/labels";

interface Props {
  deliverable: Deliverable;
  pdfAvailable: boolean;
}

/**
 * Carte livrable (façon Artifact) : aperçu défilant, agrandissement en modal,
 * téléchargements .docx / .md / .pdf via l'API d'export.
 */
export function DeliverableCard({ deliverable, pdfAvailable }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);

  // Échap ferme la vue agrandie.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  async function download(format: "docx" | "md" | "pdf") {
    setDownloading(format);
    try {
      await downloadDeliverable(deliverable.markdown, deliverable.title, format);
    } catch (error) {
      window.alert(String(error));
    } finally {
      setDownloading(null);
    }
  }

  const formats: Array<"docx" | "md" | "pdf"> = pdfAvailable
    ? ["docx", "md", "pdf"]
    : ["docx", "md"];

  return (
    <>
      <div className="glass mt-3 overflow-hidden">
        {/* En-tête */}
        <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-surface-border">
          <div className="min-w-0">
            <p className="text-[0.68rem] uppercase tracking-wider text-muted">
              {DELIVERABLE_KIND[deliverable.type] ?? "Livrable"}
            </p>
            <p className="text-sm font-semibold truncate">{deliverable.title}</p>
          </div>
          <button
            onClick={() => setExpanded(true)}
            title="Agrandir"
            className="p-2 rounded-lg hover:bg-surface-hover text-muted hover:text-ink"
          >
            <Maximize2 size={15} />
          </button>
        </div>

        {/* Aperçu défilant */}
        <div className="max-h-72 overflow-y-auto px-4 py-3 md-body text-sm">
          <ReactMarkdown>{deliverable.markdown}</ReactMarkdown>
        </div>

        {/* Téléchargements */}
        <div className="flex gap-2 px-4 py-3 border-t border-surface-border">
          {formats.map((format) => (
            <button
              key={format}
              onClick={() => download(format)}
              disabled={downloading !== null}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs
                         font-medium border border-surface-border text-muted
                         hover:text-ink hover:bg-surface-hover disabled:opacity-50"
            >
              {downloading === format ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <Download size={12} />
              )}
              .{format}
            </button>
          ))}
        </div>
      </div>

      {/* Modal plein format */}
      {expanded && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center
                     justify-center p-6"
          onClick={() => setExpanded(false)}
        >
          <div
            className="glass w-full max-w-3xl max-h-[85vh] flex flex-col bg-[#1d1528]"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-surface-border">
              <p className="font-semibold">{deliverable.title}</p>
              <button
                onClick={() => setExpanded(false)}
                className="p-2 rounded-lg hover:bg-surface-hover text-muted hover:text-ink"
              >
                <X size={16} />
              </button>
            </div>
            <div className="overflow-y-auto px-6 py-4 md-body text-sm">
              <ReactMarkdown>{deliverable.markdown}</ReactMarkdown>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

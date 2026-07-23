import { openAuthed } from "../lib/session";
import { useState } from "react";
import {
  BookMarked,
  Download,
  FileText,
  Loader2,
  Paperclip,
  PenLine,
  Search,
  X,
  type LucideIcon,
} from "lucide-react";
import ReactMarkdown from "react-markdown";

import { api, type LibraryItem } from "../lib/api";
import { downloadDeliverable } from "../lib/chat";
import { DELIVERABLE_KIND } from "../lib/labels";

interface Props {
  items: LibraryItem[];
  pdfAvailable: boolean;
}

const TYPE_ICON: Record<string, LucideIcon> = {
  summary: FileText,
  revision: BookMarked,
  document: PenLine,
  attachment: Paperclip,
};

/**
 * Bibliothèque (façon médiathèque ChatGPT) : TOUT ce qui a été généré pour
 * l'étudiant — résumés, fiches de révision, documents rédigés — consultable
 * (modal) et téléchargeable, sans avoir à retrouver la discussion d'origine.
 */
export function MediaLibrary({ items, pdfAvailable }: Props) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<LibraryItem | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);

  const needle = query.trim().toLowerCase();
  const filtered = needle
    ? items.filter(
        (item) =>
          item.title.toLowerCase().includes(needle) ||
          (item.doc || "").toLowerCase().includes(needle),
      )
    : items;

  async function download(item: LibraryItem, format: "docx" | "md" | "pdf") {
    setDownloading(format);
    try {
      await downloadDeliverable(item.markdown, item.title, format);
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
    <div className="max-w-4xl w-full mx-auto flex flex-col gap-6">
      {/* En-tête */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex-1">
          <h1 className="text-2xl font-bold tracking-tight">Bibliothèque</h1>
          <p className="text-muted text-sm mt-0.5">
            Tout ce qui a été généré pour toi — fiches, résumés, documents.
          </p>
        </div>
        <div className="glass flex items-center gap-2 px-3 py-2">
          <Search size={14} className="text-muted" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Rechercher…"
            className="bg-transparent outline-none text-sm w-40 placeholder:text-muted"
          />
        </div>
      </div>

      {/* Grille des médias */}
      {filtered.length === 0 ? (
        <div className="glass px-6 py-12 text-center">
          <p className="text-muted text-sm">
            {items.length === 0
              ? "Rien encore. Demande un résumé, une fiche de révision ou un document dans le chat : tout apparaîtra ici."
              : "Aucun contenu ne correspond à ta recherche."}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {filtered.map((item) => {
            const Icon = TYPE_ICON[item.type] ?? FileText;
            return (
              <button
                key={`${item.conv_id}-${item.index}-${item.title}`}
                onClick={() =>
                  item.type === "attachment"
                    ? openAuthed(api.attachmentFileUrl(item.title))
                    : setSelected(item)
                }
                title={`${item.title} — cliquer pour ${
                  item.type === "attachment" ? "ouvrir" : "consulter"
                }`}
                className="glass p-4 flex flex-col items-start gap-3 text-left group
                           hover:bg-surface-hover hover:border-accent/40
                           transition-colors"
              >
                <div className="flex items-start justify-between w-full">
                  <span className="p-2 rounded-lg bg-accent/15">
                    <Icon size={16} className="text-accent-soft" />
                  </span>
                  {(item.version_count ?? 1) > 1 && (
                    <span
                      title={`${item.version_count} versions (dernière affichée)`}
                      className="px-2 py-0.5 rounded-full text-[0.62rem] font-semibold
                                 bg-electric/10 text-electric-soft border border-electric/25"
                    >
                      v{item.version_count}
                    </span>
                  )}
                </div>
                <div className="min-w-0">
                  <p className="text-[0.68rem] uppercase tracking-wider text-muted">
                    {DELIVERABLE_KIND[item.type] ?? "Livrable"}
                  </p>
                  <p className="text-sm font-medium leading-snug line-clamp-2">
                    {item.title}
                  </p>
                </div>
                <p className="mt-auto text-[0.68rem] text-muted truncate w-full">
                  {formatDate(item.created_at)} · {item.conv_title}
                </p>
              </button>
            );
          })}
        </div>
      )}

      {/* Modal de consultation + téléchargements */}
      {selected && (
        <div
          className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center
                     justify-center p-6"
          onClick={() => setSelected(null)}
        >
          <div
            className="glass w-full max-w-3xl max-h-[85vh] flex flex-col bg-[#1d1528]"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-3 px-6 py-4 border-b border-surface-border">
              <div className="min-w-0">
                <p className="text-[0.68rem] uppercase tracking-wider text-muted">
                  {DELIVERABLE_KIND[selected.type] ?? "Livrable"}
                </p>
                <p className="font-semibold truncate">{selected.title}</p>
              </div>
              <button
                onClick={() => setSelected(null)}
                className="p-2 rounded-lg hover:bg-surface-hover text-muted hover:text-ink"
              >
                <X size={16} />
              </button>
            </div>
            <div className="overflow-y-auto px-6 py-4 md-body text-sm">
              <ReactMarkdown>{selected.markdown}</ReactMarkdown>
            </div>
            <div className="flex gap-2 px-6 py-4 border-t border-surface-border">
              {formats.map((format) => (
                <button
                  key={format}
                  onClick={() => download(selected, format)}
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
        </div>
      )}
    </div>
  );
}

function formatDate(ts: number | null): string {
  if (!ts) return "";
  const date = new Date(ts * 1000);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return "aujourd'hui";
  return date.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
}

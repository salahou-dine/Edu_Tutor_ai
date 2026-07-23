import { openAuthed } from "../lib/session";
import { useRef, useState } from "react";
import {
  CheckCircle2,
  Circle,
  FileText,
  Loader2,
  Plus,
  Search,
  Trash2,
} from "lucide-react";

import { api, type CourseDoc } from "../lib/api";
import { shortCourseLabel } from "../lib/actions";

interface Props {
  documents: CourseDoc[];
  /** Recharge la liste des cours côté App après un ajout / une suppression. */
  onChanged: () => void;
}

const ACCEPTED = ".md,.txt,.pdf,.docx,.pptx,.png,.jpg,.jpeg,.tiff,.tif,.bmp,.webp";

/**
 * Mes cours : gestion des SOURCES (upload, suppression, recherche) en cartes
 * horizontales. Cliquer une carte OUVRE le cours dans un nouvel onglet.
 * (Les contenus générés vivent, eux, dans la Bibliothèque.)
 */
export function Courses({ documents, onChanged }: Props) {
  const [query, setQuery] = useState("");
  const [uploading, setUploading] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const needle = query.trim().toLowerCase();
  const filtered = needle
    ? documents.filter((d) => d.filename.toLowerCase().includes(needle))
    : documents;

  async function upload(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await api.uploadDocument(file); // l'indexation server-side peut être longue
      onChanged();
    } catch (error) {
      window.alert(String(error));
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function remove(filename: string) {
    setDeleting(filename);
    try {
      await api.deleteDocument(filename);
      onChanged();
    } catch (error) {
      window.alert(String(error));
    } finally {
      setDeleting(null);
      setConfirmDelete(null);
    }
  }

  return (
    <div className="max-w-4xl w-full mx-auto flex flex-col gap-6">
      {/* En-tête : titre + recherche + ajout */}
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold tracking-tight flex-1">Mes cours</h1>

        <div className="glass flex items-center gap-2 px-3 py-2">
          <Search size={14} className="text-muted" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Rechercher…"
            className="bg-transparent outline-none text-sm w-40 placeholder:text-muted"
          />
        </div>

        <input
          ref={fileInput}
          type="file"
          accept={ACCEPTED}
          className="hidden"
          onChange={(event) => upload(event.target.files)}
        />
        <button
          onClick={() => fileInput.current?.click()}
          disabled={uploading}
          className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium
                     bg-accent/85 hover:bg-accent transition-colors disabled:opacity-60"
        >
          {uploading ? (
            <Loader2 size={15} className="animate-spin" />
          ) : (
            <Plus size={15} />
          )}
          {uploading ? "Indexation…" : "Ajouter un cours"}
        </button>
      </div>

      {uploading && (
        <p className="text-xs text-muted -mt-3">
          Le cours est vectorisé pour la recherche — cela peut prendre un moment.
        </p>
      )}

      {/* Cartes horizontales (clic = ouvrir le cours) */}
      {filtered.length === 0 ? (
        <div className="glass px-6 py-12 text-center">
          <p className="text-muted text-sm">
            {documents.length === 0
              ? "Aucun cours pour le moment. Ajoute ton premier cours pour débloquer révisions, fiches et quiz."
              : "Aucun cours ne correspond à ta recherche."}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {filtered.map((doc) => (
            <div
              key={doc.filename}
              title={`${doc.filename} — cliquer pour ouvrir`}
              role="button"
              tabIndex={0}
              onClick={() =>
                openAuthed(api.courseFileUrl(doc.filename))
              }
              onKeyDown={(event) => {
                if (event.key === "Enter")
                  openAuthed(api.courseFileUrl(doc.filename));
              }}
              className="glass p-4 flex flex-col gap-3 group hover:bg-surface-hover
                         hover:border-accent/40 transition-colors cursor-pointer"
            >
              <div className="flex items-start justify-between">
                <span className="p-2 rounded-lg bg-accent/15">
                  <FileText size={16} className="text-accent-soft" />
                </span>
                {confirmDelete === doc.filename ? (
                  <button
                    onClick={(event) => {
                      event.stopPropagation();
                      remove(doc.filename);
                    }}
                    disabled={deleting !== null}
                    className="px-2 py-1 rounded-lg text-[0.68rem] font-semibold
                               bg-red-500/20 text-red-300 border border-red-500/30
                               hover:bg-red-500/30"
                  >
                    {deleting === doc.filename ? "Suppression…" : "Confirmer ?"}
                  </button>
                ) : (
                  <button
                    onClick={(event) => {
                      event.stopPropagation();
                      setConfirmDelete(doc.filename);
                    }}
                    title="Supprimer ce cours"
                    className="p-1.5 rounded-lg text-muted opacity-0 group-hover:opacity-100
                               hover:text-red-300 hover:bg-surface transition-all"
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>

              <p className="text-sm font-medium leading-snug line-clamp-2">
                {shortCourseLabel(doc.filename)}
              </p>

              <div className="mt-auto flex items-center justify-between text-[0.68rem] text-muted">
                <span className="flex items-center gap-1">
                  {doc.analyzed ? (
                    <>
                      <CheckCircle2 size={11} className="text-accent-soft" /> Analysé
                    </>
                  ) : (
                    <>
                      <Circle size={11} className="opacity-50" /> À analyser
                    </>
                  )}
                </span>
                <span>
                  {formatDate(doc.modified)} · {formatSize(doc.size)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} Mo`;
  if (bytes >= 1_000) return `${Math.round(bytes / 1_000)} Ko`;
  return `${bytes} o`;
}

function formatDate(ts: number): string {
  const date = new Date(ts * 1000);
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return "aujourd'hui";
  return date.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
}

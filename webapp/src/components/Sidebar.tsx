import { GraduationCap, Hash, LibraryBig, Plus } from "lucide-react";

import type { ConversationSummary, CourseDoc } from "../lib/api";

interface Props {
  conversations: ConversationSummary[];
  documents: CourseDoc[];
  activeId: number | null;
  libraryActive: boolean;
  onNewChat: () => void;
  onOpenConversation: (id: number) => void;
  onOpenLibrary: () => void;
}

export function Sidebar({
  conversations,
  documents,
  activeId,
  libraryActive,
  onNewChat,
  onOpenConversation,
  onOpenLibrary,
}: Props) {
  return (
    <aside className="w-72 shrink-0 h-screen p-4 flex flex-col gap-5">
      {/* Marque */}
      <div className="flex items-center gap-2.5 px-2 pt-1">
        <span className="glass p-2 flex items-center justify-center">
          <GraduationCap size={18} className="text-accent-soft" />
        </span>
        <span className="text-lg font-semibold tracking-tight">EduTutor</span>
      </div>

      {/* Nouvelle discussion */}
      <button
        onClick={onNewChat}
        className="glass flex items-center gap-2.5 px-4 py-2.5 text-sm font-medium
                   hover:bg-surface-hover transition-colors"
      >
        <Plus size={16} className="text-accent-soft" />
        Nouvelle discussion
      </button>

      {/* Discussions */}
      <nav className="flex-1 min-h-0 flex flex-col gap-1.5 overflow-y-auto">
        <SectionLabel>Discussions</SectionLabel>
        {conversations.length === 0 && (
          <p className="px-3 text-xs text-muted">Aucune discussion pour le moment.</p>
        )}
        {conversations.map((conv) => (
          <button
            key={conv.id}
            onClick={() => onOpenConversation(conv.id)}
            title={relativeTime(conv.updated_at ?? conv.created_at)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm text-left
                        truncate transition-colors ${
                          conv.id === activeId
                            ? "bg-surface-hover text-ink"
                            : "text-muted hover:bg-surface hover:text-ink"
                        }`}
          >
            <Hash size={13} className="shrink-0 opacity-60" />
            <span className="truncate">{conv.title}</span>
          </button>
        ))}
      </nav>

      {/* Bibliothèque des cours (upload / suppression / recherche) */}
      <button
        onClick={onOpenLibrary}
        className={`glass flex items-center gap-2.5 px-4 py-2.5 text-sm font-medium
                    transition-colors ${
                      libraryActive
                        ? "bg-surface-hover border-accent/40"
                        : "hover:bg-surface-hover"
                    }`}
      >
        <LibraryBig size={16} className="text-accent-soft" />
        Bibliothèque
        <span className="ml-auto text-xs text-muted">{documents.length}</span>
      </button>
    </aside>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-2 pb-1 text-[0.7rem] font-semibold uppercase tracking-wider text-muted">
      {children}
    </p>
  );
}

function relativeTime(ts: number | null): string {
  if (!ts) return "";
  const minutes = Math.round((Date.now() / 1000 - ts) / 60);
  if (minutes < 1) return "à l'instant";
  if (minutes < 60) return `il y a ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `il y a ${hours} h`;
  return `il y a ${Math.round(hours / 24)} j`;
}

/**
 * Client de l'API EduTutor (FastAPI, proxifiée par Vite sous /api).
 * Types alignés sur les schémas du backend (api/main.py, api/store.py).
 */

export interface CourseDoc {
  filename: string;
  analyzed: boolean;
  size: number;
  modified: number;
}

export interface Deliverable {
  type: "summary" | "revision" | "document";
  title: string;
  doc: string;
  markdown: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  status?: "success" | "error";
  mode?: string;
  agents?: string[];
  deliverable?: Deliverable;
}

export interface ConversationSummary {
  id: number;
  title: string;
  created_at: number | null;
  updated_at: number | null;
  message_count: number;
}

export interface Conversation {
  id: number;
  title: string;
  messages: ChatMessage[];
}

/** Événements SSE émis par POST /conversations/{id}/messages (Phase 3). */
export type ChatEvent =
  | { type: "planning" }
  | { type: "plan"; intent: string; steps: string[] }
  | { type: "step_start"; agent: string; doc: string | null }
  | { type: "step_done"; agent: string; status: string }
  | { type: "message"; message: ChatMessage }
  | { type: "title"; title: string }
  | { type: "done" };

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`API ${response.status} : ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  documents: () => fetch("/api/documents").then((r) => json<CourseDoc[]>(r)),

  conversations: () =>
    fetch("/api/conversations").then((r) => json<ConversationSummary[]>(r)),

  conversation: (id: number) =>
    fetch(`/api/conversations/${id}`).then((r) => json<Conversation>(r)),

  createConversation: () =>
    fetch("/api/conversations", { method: "POST" }).then((r) =>
      json<Conversation>(r),
    ),

  /** Upload d'un cours (l'indexation server-side peut prendre du temps). */
  uploadDocument: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return fetch("/api/documents", { method: "POST", body }).then((r) =>
      json<{ saved: string }>(r),
    );
  },

  deleteDocument: (filename: string) =>
    fetch(`/api/documents/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    }).then((r) => json<{ deleted: string }>(r)),
};

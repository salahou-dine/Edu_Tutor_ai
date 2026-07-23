/**
 * Client de l'API EduTutor (FastAPI, proxifiée par Vite sous /api).
 * Types alignés sur les schémas du backend (api/main.py, api/store.py).
 */

import { authFetch, setToken } from "./session";

export interface User {
  id: string;
  email: string;
  created_at: number | null;
}

export interface CourseDoc {
  filename: string;
  analyzed: boolean;
  size: number;
  modified: number;
}

export interface Deliverable {
  /** attachment = pièce jointe du chat (Bibliothèque uniquement). */
  type: "summary" | "revision" | "document" | "attachment";
  title: string;
  doc: string;
  markdown: string;
}

export interface AttachmentMeta {
  filename: string;
  size: number;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  status?: "success" | "error";
  mode?: string;
  agents?: string[];
  deliverable?: Deliverable;
  attachments?: AttachmentMeta[];
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

/** Entrée de la Bibliothèque : un livrable + son origine. */
export interface LibraryItem extends Deliverable {
  conv_id: number;
  conv_title: string;
  created_at: number | null;
  index: number;
  /** Nombre de versions dans la lignée (révisions successives regroupées). */
  version_count?: number;
}

/** Événements SSE émis par POST /conversations/{id}/messages (Phase 3). */
export type ChatEvent =
  | { type: "planning" }
  | { type: "plan"; intent: string; steps: string[] }
  | { type: "step_start"; agent: string; doc: string | null }
  | { type: "step_done"; agent: string; status: string }
  | { type: "delta"; text: string }
  | { type: "delta_reset" }
  | { type: "message"; message: ChatMessage }
  | { type: "title"; title: string }
  | { type: "done" };

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`API ${response.status} : ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}

async function credentials(
  path: "login" | "register",
  email: string,
  password: string,
): Promise<User> {
  const response = await fetch(`/api/auth/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await json<{ token: string; user: User }>(response);
  setToken(data.token);
  return data.user;
}

export const api = {
  // --- Authentification ---
  login: (email: string, password: string) => credentials("login", email, password),
  register: (email: string, password: string) =>
    credentials("register", email, password),
  me: () => authFetch("/api/auth/me").then((r) => json<User>(r)),

  // --- Données (toutes authentifiées) ---
  documents: () => authFetch("/api/documents").then((r) => json<CourseDoc[]>(r)),

  conversations: () =>
    authFetch("/api/conversations").then((r) => json<ConversationSummary[]>(r)),

  conversation: (id: number) =>
    authFetch(`/api/conversations/${id}`).then((r) => json<Conversation>(r)),

  createConversation: () =>
    authFetch("/api/conversations", { method: "POST" }).then((r) =>
      json<Conversation>(r),
    ),

  /** Upload d'un cours (l'indexation server-side peut prendre du temps). */
  uploadDocument: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return authFetch("/api/documents", { method: "POST", body }).then((r) =>
      json<{ saved: string }>(r),
    );
  },

  deleteDocument: (filename: string) =>
    authFetch(`/api/documents/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    }).then((r) => json<{ deleted: string }>(r)),

  /** URL d'ouverture d'un cours (à ouvrir via session.openAuthed). */
  courseFileUrl: (filename: string) =>
    `/api/documents/${encodeURIComponent(filename)}/file`,

  deliverables: () =>
    authFetch("/api/deliverables").then((r) => json<LibraryItem[]>(r)),

  /** Upload d'une pièce jointe du chat (avant l'envoi du message). */
  uploadAttachment: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return authFetch("/api/attachments", { method: "POST", body }).then((r) =>
      json<AttachmentMeta>(r),
    );
  },

  attachmentFileUrl: (filename: string) =>
    `/api/attachments/${encodeURIComponent(filename)}/file`,
};

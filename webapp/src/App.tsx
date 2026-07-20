import { useCallback, useEffect, useState } from "react";

import {
  api,
  type AttachmentMeta,
  type ChatMessage,
  type ConversationSummary,
  type CourseDoc,
  type LibraryItem,
} from "./lib/api";
import { streamMessage } from "./lib/chat";
import { ChatView } from "./components/ChatView";
import { Courses } from "./components/Courses";
import { Home } from "./components/Home";
import { MediaLibrary } from "./components/MediaLibrary";
import { PromptBar } from "./components/PromptBar";
import { Sidebar } from "./components/Sidebar";
import type { ProgressStep } from "./components/ProgressTimeline";

export default function App() {
  const [documents, setDocuments] = useState<CourseDoc[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [deliverables, setDeliverables] = useState<LibraryItem[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [view, setView] = useState<"chat" | "courses" | "library">("chat");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [progress, setProgress] = useState<ProgressStep[]>([]);
  const [liveText, setLiveText] = useState(""); // réponse en cours de streaming
  const [busy, setBusy] = useState(false);
  const [pdfAvailable, setPdfAvailable] = useState(false);
  const [apiDown, setApiDown] = useState(false);

  const refreshSidebar = useCallback(() => {
    api.conversations().then(setConversations).catch(() => undefined);
    api.documents().then(setDocuments).catch(() => undefined);
    api.deliverables().then(setDeliverables).catch(() => undefined);
  }, []);

  useEffect(() => {
    Promise.all([
      api.documents(),
      api.conversations(),
      api.deliverables(),
      fetch("/api/health").then((r) => r.json()),
    ])
      .then(([docs, convs, delivs, health]) => {
        setDocuments(docs);
        setConversations(convs);
        setDeliverables(delivs);
        setPdfAvailable(Boolean(health.pdf_export));
      })
      .catch(() => setApiDown(true));
  }, []);

  /** Envoi d'un message (depuis les cartes d'objectif ou la barre de saisie). */
  async function sendMessage(prompt: string, attachments: AttachmentMeta[] = []) {
    if (busy) return;
    setView("chat");
    setBusy(true);

    try {
      // Pas de discussion active -> on en crée une.
      let convId = activeId;
      if (convId === null) {
        const conv = await api.createConversation();
        convId = conv.id;
        setActiveId(convId);
      }

      setMessages((prev) => [
        ...prev,
        {
          role: "user",
          content: prompt,
          ...(attachments.length > 0 ? { attachments } : {}),
        },
      ]);
      setProgress([]);

      await streamMessage(convId, prompt, (event) => {
        switch (event.type) {
          case "planning":
            setProgress([{ key: "planning", state: "running" }]);
            break;
          case "plan":
            // La planification est finie -> les étapes du plan, en attente.
            setProgress([
              { key: "planning", state: "done" },
              ...event.steps.map((agent) => ({
                key: agent,
                state: "pending" as const,
              })),
            ]);
            break;
          case "step_start":
            setProgress((prev) =>
              prev.map((step) =>
                step.key === event.agent && step.state === "pending"
                  ? { ...step, state: "running" }
                  : step,
              ),
            );
            break;
          case "step_done":
            setProgress((prev) =>
              prev.map((step) =>
                step.key === event.agent && step.state === "running"
                  ? { ...step, state: "done" }
                  : step,
              ),
            );
            break;
          case "delta":
            setLiveText((prev) => prev + event.text);
            break;
          case "delta_reset":
            setLiveText("");
            break;
          case "message":
            setLiveText(""); // le message canonique remplace le texte streamé
            setMessages((prev) => [...prev, event.message]);
            break;
          case "title":
            refreshSidebar();
            break;
          case "done":
            break;
        }
      }, attachments.map((a) => a.filename));
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          status: "error",
          content: `Le tuteur n'a pas pu répondre (${String(error)}). Réessaie.`,
        },
      ]);
    } finally {
      setProgress([]);
      setLiveText("");
      setBusy(false);
      refreshSidebar();
    }
  }

  function newChat() {
    if (busy) return;
    setView("chat");
    setActiveId(null);
    setMessages([]);
    setProgress([]);
  }

  async function openConversation(id: number) {
    if (busy) return;
    try {
      const conv = await api.conversation(id);
      setView("chat");
      setActiveId(id);
      setMessages(conv.messages);
      setProgress([]);
    } catch {
      refreshSidebar(); // la discussion n'existe plus -> resynchronise
    }
  }

  const showHome = messages.length === 0 && !busy;

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        conversations={conversations}
        documents={documents}
        deliverableCount={deliverables.length}
        activeId={activeId}
        libraryActive={view === "library"}
        coursesActive={view === "courses"}
        onNewChat={newChat}
        onOpenConversation={openConversation}
        onOpenLibrary={() => setView("library")}
        onOpenCourses={() => setView("courses")}
      />

      <main className="flex-1 min-w-0 p-4 pl-0">
        <div className="glass h-full flex flex-col p-6 md:p-8">
          {apiDown ? (
            <div className="m-auto text-center">
              <p className="text-lg font-semibold">API injoignable</p>
              <p className="text-muted text-sm mt-2">
                Lance le backend :{" "}
                <code>.venv/bin/uvicorn api.main:app --port 8000</code>
              </p>
            </div>
          ) : view === "library" ? (
            <div className="flex-1 min-h-0 overflow-y-auto pt-4">
              <MediaLibrary items={deliverables} pdfAvailable={pdfAvailable} />
            </div>
          ) : view === "courses" ? (
            <div className="flex-1 min-h-0 overflow-y-auto pt-4">
              <Courses documents={documents} onChanged={refreshSidebar} />
            </div>
          ) : (
            <>
              <div className="flex-1 min-h-0 overflow-y-auto flex">
                {showHome ? (
                  <div className="m-auto w-full">
                    <Home documents={documents} onCompose={sendMessage} />
                  </div>
                ) : (
                  <div className="w-full">
                    <ChatView
                      messages={messages}
                      progress={progress}
                      liveText={liveText}
                      pdfAvailable={pdfAvailable}
                    />
                  </div>
                )}
              </div>
              <div className="max-w-3xl w-full mx-auto pt-5">
                <PromptBar onSend={sendMessage} disabled={busy} />
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}

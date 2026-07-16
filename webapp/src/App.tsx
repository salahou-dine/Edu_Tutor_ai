import { useCallback, useEffect, useState } from "react";

import {
  api,
  type ChatMessage,
  type ConversationSummary,
  type CourseDoc,
} from "./lib/api";
import { streamMessage } from "./lib/chat";
import { ChatView } from "./components/ChatView";
import { Home } from "./components/Home";
import { Library } from "./components/Library";
import { PromptBar } from "./components/PromptBar";
import { Sidebar } from "./components/Sidebar";
import type { ProgressStep } from "./components/ProgressTimeline";

export default function App() {
  const [documents, setDocuments] = useState<CourseDoc[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [view, setView] = useState<"chat" | "library">("chat");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [progress, setProgress] = useState<ProgressStep[]>([]);
  const [busy, setBusy] = useState(false);
  const [pdfAvailable, setPdfAvailable] = useState(false);
  const [apiDown, setApiDown] = useState(false);

  const refreshSidebar = useCallback(() => {
    api.conversations().then(setConversations).catch(() => undefined);
    api.documents().then(setDocuments).catch(() => undefined);
  }, []);

  useEffect(() => {
    Promise.all([
      api.documents(),
      api.conversations(),
      fetch("/api/health").then((r) => r.json()),
    ])
      .then(([docs, convs, health]) => {
        setDocuments(docs);
        setConversations(convs);
        setPdfAvailable(Boolean(health.pdf_export));
      })
      .catch(() => setApiDown(true));
  }, []);

  /** Envoi d'un message (depuis les cartes d'objectif ou la barre de saisie). */
  async function sendMessage(prompt: string) {
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

      setMessages((prev) => [...prev, { role: "user", content: prompt }]);
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
          case "message":
            setMessages((prev) => [...prev, event.message]);
            break;
          case "title":
            refreshSidebar();
            break;
          case "done":
            break;
        }
      });
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
        activeId={activeId}
        libraryActive={view === "library"}
        onNewChat={newChat}
        onOpenConversation={openConversation}
        onOpenLibrary={() => setView("library")}
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
              <Library documents={documents} onChanged={refreshSidebar} />
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

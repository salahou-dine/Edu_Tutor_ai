import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";

import type { ChatMessage } from "../lib/api";
import { Message } from "./Message";
import { ProgressTimeline, type ProgressStep } from "./ProgressTimeline";

interface Props {
  messages: ChatMessage[];
  progress: ProgressStep[];
  /** Réponse du tuteur en cours de streaming (remplacée par le message final). */
  liveText: string;
  pdfAvailable: boolean;
}

export function ChatView({ messages, progress, liveText, pdfAvailable }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Suit la conversation : défile en bas à chaque message / étape / token.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, progress, liveText]);

  return (
    <div className="max-w-3xl w-full mx-auto flex flex-col gap-6 pb-4">
      {messages.map((message, index) => (
        <Message key={index} message={message} pdfAvailable={pdfAvailable} />
      ))}
      <ProgressTimeline steps={progress} />
      {liveText && (
        <div className="flex flex-col gap-1.5 max-w-[92%]">
          <div className="md-body text-sm leading-relaxed">
            <ReactMarkdown>{liveText}</ReactMarkdown>
          </div>
          <span className="inline-block w-2 h-4 bg-accent-soft/70 animate-pulse rounded-sm" />
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}

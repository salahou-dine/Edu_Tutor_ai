import { useEffect, useRef } from "react";

import type { ChatMessage } from "../lib/api";
import { Message } from "./Message";
import { ProgressTimeline, type ProgressStep } from "./ProgressTimeline";

interface Props {
  messages: ChatMessage[];
  progress: ProgressStep[];
  pdfAvailable: boolean;
}

export function ChatView({ messages, progress, pdfAvailable }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Suit la conversation : défile en bas à chaque nouveau message / étape.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, progress]);

  return (
    <div className="max-w-3xl w-full mx-auto flex flex-col gap-6 pb-4">
      {messages.map((message, index) => (
        <Message key={index} message={message} pdfAvailable={pdfAvailable} />
      ))}
      <ProgressTimeline steps={progress} />
      <div ref={bottomRef} />
    </div>
  );
}

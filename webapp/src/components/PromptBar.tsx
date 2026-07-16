import { useState } from "react";
import { ArrowUp } from "lucide-react";

interface Props {
  /** Envoi d'un message (branché sur le chat SSE en Phase 3). */
  onSend: (text: string) => void;
  disabled?: boolean;
}

export function PromptBar({ onSend, disabled }: Props) {
  const [text, setText] = useState("");

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const cleaned = text.trim();
    if (!cleaned || disabled) return;
    onSend(cleaned);
    setText("");
  }

  return (
    <form onSubmit={submit} className="glass flex items-center gap-2 p-2 pl-5">
      <input
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="Pose ta question sur le cours…"
        disabled={disabled}
        className="flex-1 bg-transparent outline-none text-sm placeholder:text-muted
                   disabled:opacity-50"
      />
      <button
        type="submit"
        disabled={disabled || !text.trim()}
        aria-label="Envoyer"
        className="p-2.5 rounded-full bg-accent/85 hover:bg-accent transition-colors
                   disabled:opacity-40"
      >
        <ArrowUp size={16} />
      </button>
    </form>
  );
}

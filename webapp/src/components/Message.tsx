import { openAuthed } from "../lib/session";
import { Paperclip } from "lucide-react";
import ReactMarkdown from "react-markdown";

import { api, type ChatMessage } from "../lib/api";
import { AGENT_INFO, MODE_LABELS } from "../lib/labels";
import { DeliverableCard } from "./DeliverableCard";

interface Props {
  message: ChatMessage;
  pdfAvailable: boolean;
}

export function Message({ message, pdfAvailable }: Props) {
  if (message.role === "user") {
    return (
      <div className="flex flex-col items-end gap-1.5">
        {(message.attachments ?? []).length > 0 && (
          <div className="flex flex-wrap justify-end gap-1.5">
            {message.attachments!.map((attachment) => (
              <button
                key={attachment.filename}
                onClick={() =>
                  openAuthed(api.attachmentFileUrl(attachment.filename))
                }
                title={`${attachment.filename} — cliquer pour ouvrir`}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs
                           bg-surface border border-surface-border text-muted
                           hover:text-ink hover:bg-surface-hover transition-colors"
              >
                <Paperclip size={11} className="text-accent-soft" />
                <span className="max-w-48 truncate">{attachment.filename}</span>
              </button>
            ))}
          </div>
        )}
        <div className="max-w-[80%] rounded-2xl rounded-br-md bg-accent/20
                        border border-accent/25 px-4 py-2.5 text-sm">
          {message.content}
        </div>
      </div>
    );
  }

  const agents = (message.agents ?? []).filter((a) => a in AGENT_INFO);
  const modeLabel = message.mode ? MODE_LABELS[message.mode] : undefined;

  return (
    <div className="flex flex-col gap-1.5 max-w-[92%]">
      {/* Badge de mode + agents intervenus */}
      {(modeLabel || agents.length > 0) && message.status !== "error" && (
        <div className="flex flex-wrap items-center gap-2">
          {modeLabel && (
            <span className="px-2.5 py-0.5 rounded-full text-[0.68rem] font-semibold
                             bg-electric/10 text-electric-soft border border-electric/25">
              {modeLabel}
            </span>
          )}
          {agents.length > 0 && (
            <span className="flex items-center gap-1.5 text-[0.68rem] text-muted">
              Répondu par
              {agents.map((agent, index) => {
                const info = AGENT_INFO[agent];
                return (
                  <span key={index} className="flex items-center gap-1">
                    {index > 0 && <span className="opacity-50">→</span>}
                    <info.icon size={11} className="text-accent-soft" />
                    {info.label}
                  </span>
                );
              })}
            </span>
          )}
        </div>
      )}

      {/* Contenu (Markdown) */}
      <div
        className={`md-body text-sm leading-relaxed ${
          message.status === "error" ? "text-red-300" : ""
        }`}
      >
        <ReactMarkdown>{message.content}</ReactMarkdown>
      </div>

      {/* Carte livrable */}
      {message.deliverable && (
        <DeliverableCard deliverable={message.deliverable} pdfAvailable={pdfAvailable} />
      )}
    </div>
  );
}

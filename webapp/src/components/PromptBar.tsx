import { useRef, useState } from "react";
import { ArrowUp, Loader2, Paperclip, X } from "lucide-react";

import { api, type AttachmentMeta } from "../lib/api";

interface Props {
  /** Envoi d'un message ; `attachments` = noms de fichiers déjà uploadés. */
  onSend: (text: string, attachments: AttachmentMeta[]) => void;
  disabled?: boolean;
}

const ACCEPTED = ".md,.txt,.pdf,.docx,.pptx,.png,.jpg,.jpeg,.tiff,.tif,.bmp,.webp";

export function PromptBar({ onSend, disabled }: Props) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<AttachmentMeta[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const cleaned = text.trim();
    // Une pièce jointe seule (sans texte) est valide : « travaille dessus ».
    if ((!cleaned && attachments.length === 0) || disabled || uploading) return;
    onSend(cleaned || "Voici un document.", attachments);
    setText("");
    setAttachments([]);
  }

  async function attach(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const meta = await api.uploadAttachment(file);
      setAttachments((prev) => [...prev, meta]);
    } catch (error) {
      window.alert(String(error));
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  return (
    <form onSubmit={submit} className="glass flex flex-col gap-0 p-2">
      {/* Chips des pièces jointes en attente d'envoi */}
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-3 pt-1 pb-2">
          {attachments.map((attachment) => (
            <span
              key={attachment.filename}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs
                         bg-accent/15 border border-accent/25 text-ink"
            >
              <Paperclip size={11} className="text-accent-soft" />
              <span className="max-w-48 truncate">{attachment.filename}</span>
              <button
                type="button"
                onClick={() =>
                  setAttachments((prev) =>
                    prev.filter((a) => a.filename !== attachment.filename),
                  )
                }
                title="Retirer"
                className="text-muted hover:text-ink"
              >
                <X size={11} />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center gap-2 pl-2">
        <input
          ref={fileInput}
          type="file"
          accept={ACCEPTED}
          className="hidden"
          onChange={(event) => attach(event.target.files)}
        />
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          disabled={disabled || uploading}
          title="Joindre un document"
          className="p-2.5 rounded-full text-muted hover:text-ink hover:bg-surface-hover
                     transition-colors disabled:opacity-40"
        >
          {uploading ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Paperclip size={16} />
          )}
        </button>
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
          disabled={disabled || uploading || (!text.trim() && attachments.length === 0)}
          aria-label="Envoyer"
          className="p-2.5 rounded-full bg-accent/85 hover:bg-accent transition-colors
                     disabled:opacity-40 disabled:shadow-none
                     shadow-[0_0_16px_rgba(157,107,255,0.35)]"
        >
          <ArrowUp size={16} />
        </button>
      </div>
    </form>
  );
}

/**
 * Client du chat SSE : POST /api/conversations/{id}/messages renvoie un flux
 * Server-Sent Events (EventSource ne supporte pas POST -> on parse le flux
 * fetch à la main : blocs séparés par une ligne vide, données en `data: {json}`).
 */

import type { ChatEvent } from "./api";

export async function streamMessage(
  conversationId: number,
  content: string,
  onEvent: (event: ChatEvent) => void,
  attachments: string[] = [],
): Promise<void> {
  const response = await fetch(`/api/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, attachments }),
  });
  if (!response.ok || !response.body) {
    throw new Error(`API ${response.status} : ${await response.text()}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Un événement SSE = bloc terminé par une ligne vide.
    for (;;) {
      const cut = buffer.indexOf("\n\n");
      if (cut === -1) break;
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      for (const line of block.split("\n")) {
        if (line.startsWith("data: ")) {
          onEvent(JSON.parse(line.slice(6)) as ChatEvent);
        }
      }
    }
  }
}

/** Télécharge un livrable dans un format donné via POST /api/export. */
export async function downloadDeliverable(
  markdown: string,
  title: string,
  format: "docx" | "md" | "pdf",
): Promise<void> {
  const response = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ markdown, title, format }),
  });
  if (!response.ok) throw new Error(`Export ${format} : erreur ${response.status}`);

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${title.replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "_") || "livrable"}.${format}`;
  anchor.click();
  URL.revokeObjectURL(url);
}

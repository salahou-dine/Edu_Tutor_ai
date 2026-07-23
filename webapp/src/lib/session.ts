/**
 * Session utilisateur côté front : stockage du token, injection de l'en-tête
 * Authorization sur tous les appels, ouverture de fichiers protégés.
 *
 * Un handler global `onUnauthorized` (posé par App) déconnecte l'utilisateur si
 * un appel renvoie 401 (token expiré/invalide).
 */

const TOKEN_KEY = "edututor_token";

let onUnauthorized: (() => void) | null = null;

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

export function authHeaders(
  extra: Record<string, string> = {},
): Record<string, string> {
  const token = getToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

/** fetch avec l'en-tête d'authentification ; déconnecte sur 401. */
export async function authFetch(
  url: string,
  opts: RequestInit = {},
): Promise<Response> {
  const response = await fetch(url, {
    ...opts,
    headers: { ...(opts.headers || {}), ...authHeaders() },
  });
  if (response.status === 401) {
    clearToken();
    onUnauthorized?.();
  }
  return response;
}

/**
 * Ouvre un fichier protégé dans un nouvel onglet. On ne peut PAS passer par
 * window.open(url) directement (pas d'en-tête Authorization) : on récupère le
 * fichier par fetch authentifié puis on l'ouvre via un blob URL.
 */
export async function openAuthed(url: string): Promise<void> {
  const response = await authFetch(url);
  if (!response.ok) {
    window.alert("Fichier introuvable ou accès refusé.");
    return;
  }
  const blobUrl = URL.createObjectURL(await response.blob());
  window.open(blobUrl, "_blank");
  // Révocation différée : laisse le temps à l'onglet de charger.
  setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
}

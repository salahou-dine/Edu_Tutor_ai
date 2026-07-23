import { useState } from "react";
import { GraduationCap, Loader2 } from "lucide-react";

import { api, type User } from "../lib/api";

interface Props {
  onAuthenticated: (user: User) => void;
}

/** Écran de connexion / inscription (identifiant + mot de passe). */
export function Login({ onAuthenticated }: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const user =
        mode === "login"
          ? await api.login(email.trim(), password)
          : await api.register(email.trim(), password);
      onAuthenticated(user);
    } catch (err) {
      setError(
        mode === "login"
          ? "Identifiant ou mot de passe incorrect."
          : String(err).replace(/^Error:\s*API \d+ :\s*/, "").replace(/[{}"]|detail:?/g, "").trim()
          || "Impossible de créer le compte.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="h-screen flex items-center justify-center p-6">
      <div className="glass w-full max-w-sm p-8 flex flex-col gap-6">
        <div className="flex flex-col items-center gap-3 text-center">
          <span className="glass p-3 rounded-2xl">
            <GraduationCap size={26} className="text-accent-soft" />
          </span>
          <div>
            <h1 className="text-xl font-bold tracking-tight">EduTutor</h1>
            <p className="text-muted text-sm mt-1">
              {mode === "login"
                ? "Connecte-toi à ton espace"
                : "Crée ton espace personnel"}
            </p>
          </div>
        </div>

        <form onSubmit={submit} className="flex flex-col gap-3">
          <input
            type="email"
            autoFocus
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Adresse email"
            className="glass px-4 py-2.5 text-sm bg-surface outline-none
                       placeholder:text-muted focus:border-accent/50"
          />
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Mot de passe"
            className="glass px-4 py-2.5 text-sm bg-surface outline-none
                       placeholder:text-muted focus:border-accent/50"
          />

          {error && <p className="text-xs text-red-300">{error}</p>}

          <button
            type="submit"
            disabled={busy}
            className="flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl
                       text-sm font-semibold bg-accent/85 hover:bg-accent
                       transition-colors disabled:opacity-60
                       shadow-[0_0_16px_rgba(157,107,255,0.3)]"
          >
            {busy && <Loader2 size={15} className="animate-spin" />}
            {mode === "login" ? "Se connecter" : "Créer mon compte"}
          </button>
        </form>

        <button
          onClick={() => {
            setMode(mode === "login" ? "register" : "login");
            setError(null);
          }}
          className="text-xs text-muted hover:text-ink transition-colors"
        >
          {mode === "login"
            ? "Pas encore de compte ? Créer un compte"
            : "Déjà un compte ? Se connecter"}
        </button>
      </div>
    </div>
  );
}

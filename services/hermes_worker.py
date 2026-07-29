"""
Worker Hermes persistant (« Hermes chaud »).

Tourne dans le venv de Hermes (Python 3.11) : il importe le framework Hermes UNE
seule fois (imports + découverte des plugins ~payés au démarrage) et reste vivant.
Pour chaque requête, il crée un `AIAgent` FRAIS (~0,3 s dans un process chaud) —
ce qui donne : fidélité du skill (chargé à la construction), isolation totale
(aucun état partagé entre requêtes/étudiants), et un modèle choisi par requête.

Il NE dépend d'aucune de nos briques applicatives (RAG/API) : seulement de Hermes.
La concurrence se fait en lançant PLUSIEURS workers (sockets distincts) ; chaque
worker traite une requête à la fois (pas de course sur les variables d'env
process-globales que Hermes manipule pendant un tour).

Protocole : socket Unix, une ligne JSON par message (newline-delimited).
  Requête  : {"prompt": str, "skill": str|null, "model": str|null,
              "history": [...], "images": [chemins]}
  ou        : {"ping": true}
  Réponses : suite de lignes JSON :
              {"type": "delta", "text": "..."}      (tokens au fil de l'eau)
              {"type": "done",  "content": "..."}   (réponse finale)
              {"type": "error", "error": "..."}     (échec)
              {"type": "pong"}                       (réponse au ping)

Lancement :
    <hermes-venv>/bin/python -m services.hermes_worker --socket /tmp/edututor_hermes_0.sock
"""

import base64
import json
import os
import socketserver
import sys
import traceback
import uuid
from pathlib import Path

# Racine du framework Hermes (surchargée par HERMES_AGENT_ROOT au besoin).
HERMES_AGENT_ROOT = os.environ.get(
    "HERMES_AGENT_ROOT", "/home/salah/ai_projects/hermes-agent"
)
if HERMES_AGENT_ROOT not in sys.path:
    sys.path.insert(0, HERMES_AGENT_ROOT)
os.environ.setdefault("HERMES_INTERACTIVE", "0")


def _log(*args) -> None:
    """Journalise sur stderr : stdout peut contenir les warnings de chargement."""
    print("[hermes_worker]", *args, file=sys.stderr, flush=True)


# --- Imports Hermes : payés UNE fois, le process reste chaud ------------------
from run_agent import AIAgent  # noqa: E402
from hermes_cli.config import load_config  # noqa: E402
from hermes_cli.runtime_provider import resolve_runtime_provider  # noqa: E402
from agent.skill_commands import build_preloaded_skills_prompt  # noqa: E402

_CONFIG = load_config()
_MODEL_CFG = _CONFIG.get("model") or {}
_DEFAULT_MODEL = (
    _MODEL_CFG.get("default") if isinstance(_MODEL_CFG, dict) else str(_MODEL_CFG)
) or ""
_RUNTIME = resolve_runtime_provider(
    requested=_MODEL_CFG.get("provider") if isinstance(_MODEL_CFG, dict) else None
)

_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".tiff": "image/tiff", ".tif": "image/tiff",
}

# Cache des prompts de skill (build_preloaded_skills_prompt a son propre cache,
# mais on évite même l'appel pour un skill déjà vu).
_SKILL_CACHE: dict[str, str | None] = {}


def _skill_prompt(skill: str | None) -> str | None:
    """Prompt système d'un skill (comme `hermes -z --skills <skill>`), ou None."""
    if not skill:
        return None
    if skill in _SKILL_CACHE:
        return _SKILL_CACHE[skill]
    prompt = None
    try:
        text, _loaded, missing = build_preloaded_skills_prompt([skill])
        if missing:
            _log(f"skill introuvable: {missing}")
        prompt = text or None
    except Exception as exc:  # noqa: BLE001
        _log(f"échec build skill {skill!r}: {exc}")
    _SKILL_CACHE[skill] = prompt
    return prompt


def _user_message(prompt: str, images: list[str]):
    """String simple, ou contenu multimodal OpenAI si des images sont jointes."""
    if not images:
        return prompt
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for path in images:
        try:
            data = Path(path).read_bytes()
        except OSError:
            _log(f"image illisible ignorée: {path}")
            continue
        mime = _IMAGE_MIME.get(Path(path).suffix.lower(), "image/png")
        b64 = base64.b64encode(data).decode("ascii")
        parts.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        )
    return parts


def _make_agent(skill_prompt: str | None, model: str | None, on_delta):
    """Construit un AIAgent FRAIS (~0,3 s à chaud). Skill à la construction = fidèle.

    ISOLATION (crucial) : `skip_memory=True` coupe la mémoire persistante partagée
    de Hermes (`~/.hermes/memories`) — sinon un fait « retenu » pour un étudiant
    fuiterait vers les requêtes suivantes ET les autres comptes. `skip_context_files`
    retire aussi SOUL.md/AGENTS.md (persona « Hermes Agent ») : la persona vient
    UNIQUEMENT de notre skill + notre prompt. Chaque requête est ainsi SANS état.
    """
    return AIAgent(
        platform="cli",
        quiet_mode=True,
        model=model or _DEFAULT_MODEL,
        provider=_RUNTIME.get("provider"),
        api_mode=_RUNTIME.get("api_mode"),
        base_url=_RUNTIME.get("base_url"),
        api_key=_RUNTIME.get("api_key"),
        command=_RUNTIME.get("command"),
        args=list(_RUNTIME.get("args") or []),
        ephemeral_system_prompt=skill_prompt,
        stream_delta_callback=on_delta,
        skip_memory=True,          # pas de mémoire persistante partagée
        skip_context_files=True,   # pas de SOUL.md/AGENTS.md injectés
    )


def _warmup() -> None:
    """Amorce les caches du process (1re construction ~1,9 s) pour que la 1re
    vraie requête soit déjà rapide. Best-effort."""
    _log("préchauffage…")
    try:
        _make_agent(None, None, None)
    except Exception as exc:  # noqa: BLE001
        _log(f"préchauffage KO (non bloquant): {exc}")
    _log(f"prêt (modèle par défaut={_DEFAULT_MODEL}, provider={_RUNTIME.get('provider')})")


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            line = self.rfile.readline()
            if not line:
                return
            req = json.loads(line)
        except Exception as exc:  # noqa: BLE001
            self._send({"type": "error", "error": f"requête invalide: {exc}"})
            return
        if req.get("ping"):
            self._send({"type": "pong"})
            return
        self._run(req)

    def _send(self, obj: dict) -> None:
        try:
            self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()
        except Exception:  # noqa: BLE001 - client parti : on abandonne proprement
            pass

    def _run(self, req: dict) -> None:
        prompt = req.get("prompt") or ""
        skill = req.get("skill")
        model = req.get("model")
        history = req.get("history") or []
        images = req.get("images") or []

        def on_delta(text) -> None:
            if text:
                self._send({"type": "delta", "text": text})

        try:
            agent = _make_agent(_skill_prompt(skill), model, on_delta)
            result = agent.run_conversation(
                user_message=_user_message(prompt, images),
                conversation_history=history,
                task_id=f"edututor-{uuid.uuid4().hex[:8]}",
                persist_user_message=(prompt[:200] if isinstance(prompt, str) else "[multimodal]"),
            )
            content = (result or {}).get("final_response") or ""
            self._send({"type": "done", "content": content})
        except Exception as exc:  # noqa: BLE001
            _log("échec run_conversation:\n" + traceback.format_exc())
            self._send({"type": "error", "error": f"{type(exc).__name__}: {exc}"})


class _Server(socketserver.UnixStreamServer):
    # Mono-thread volontaire (pas de ThreadingMixIn) : une requête à la fois ->
    # pas de course sur les variables d'env process-globales. La concurrence se
    # gère en lançant PLUSIEURS workers (sockets distincts).
    allow_reuse_address = True


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Worker Hermes persistant (socket Unix).")
    parser.add_argument("--socket", required=True, help="Chemin du socket Unix à créer.")
    args = parser.parse_args()

    sock_path = args.socket
    try:
        if os.path.exists(sock_path):
            os.unlink(sock_path)
    except OSError:
        pass

    _warmup()
    server = _Server(sock_path, _Handler)
    try:
        os.chmod(sock_path, 0o600)  # accès restreint au propriétaire
    except OSError:
        pass
    _log(f"écoute sur {sock_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            os.unlink(sock_path)
        except OSError:
            pass


if __name__ == "__main__":
    main()

"""
Adaptateur d'appel à Hermes Agent.

Méthode retenue (cf. docs/agent_tutor_architecture.md) : la CLI Hermes en mode
one-shot non-interactif :

    hermes -z "<prompt>" --skills <skill>

Le mode `-z/--oneshot` imprime UNIQUEMENT la réponse finale sur stdout
(pas de bannière, pas de spinner, pas de session_id), charge le skill demandé
et auto-approuve les outils. C'est le point d'entrée officiel pour les scripts.

Ce module N'IMPORTE PAS le runtime Hermes (pour ne pas se coupler au code du
framework) et NE FAIT JAMAIS d'appel LLM direct (OpenRouter/DeepSeek) en
remplacement silencieux de Hermes. Si Hermes n'est pas appelable, on sauvegarde
le prompt pour un test manuel et on retourne `not_available`.

Robustesse : les échecs de Hermes sont INTERMITTENTS (timeout ponctuel, erreur
transitoire du provider, sortie vide). L'appel est donc réessayé
(`HERMES_MAX_ATTEMPTS`) et chaque échec est journalisé dans un fichier réservé
au développeur (`HERMES_ERROR_LOG`) — jamais montré à l'étudiant.
"""

import codecs
import json
import os
import select
import shutil
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from config import settings


def _save_prompt_for_manual(prompt: str) -> None:
    """Sauvegarde le prompt afin de pouvoir le tester à la main dans Hermes."""
    try:
        path = Path(settings.LAST_PROMPT_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prompt, encoding="utf-8")
    except OSError:
        # La sauvegarde est un confort de debug, jamais bloquante.
        pass


def _log_failure(
    attempt: int,
    attempts: int,
    skill_name: str,
    error: str,
    elapsed: float,
    stderr: str = "",
) -> None:
    """
    Journalise un échec d'appel Hermes (réservé développeur). Un échec n'est donc
    plus « silencieux » : on garde une trace horodatée pour diagnostiquer.
    L'écriture du journal n'est jamais bloquante.
    """
    try:
        path = Path(settings.HERMES_ERROR_LOG)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"[{datetime.now().isoformat(timespec='seconds')}] "
                f"tentative {attempt}/{attempts} skill={skill_name} "
                f"durée={elapsed:.1f}s : {error}\n"
            )
            if stderr:
                handle.write(f"    stderr: {stderr[-500:]}\n")
    except OSError:
        pass


def _safe_delta(on_delta, value) -> None:
    """Notifie le consommateur de tokens sans jamais casser l'appel."""
    try:
        on_delta(value)
    except Exception:
        pass


def _run_streaming(cmd: list, timeout: int, on_delta) -> SimpleNamespace:
    """
    Exécute Hermes en STREAMANT sa sortie (nécessite le patch oneshot :
    HERMES_ONESHOT_STREAM=1). Protocole : les tokens arrivent au fil de l'eau ;
    un octet NUL (\\x00) marque une frontière de tour (le texte partiel est à
    jeter) ; le DERNIER segment est la réponse canonique.

    `on_delta(texte)` reçoit chaque token ; `on_delta(None)` = remise à zéro.
    Retourne un objet compatible subprocess (stdout = segment final, returncode,
    stderr). Lève TimeoutExpired au-delà du budget (process tué).
    """
    _safe_delta(on_delta, None)  # remise à zéro (utile au retry)
    env = dict(os.environ, HERMES_ONESHOT_STREAM="1", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env
    )
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    segments = [""]
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                proc.wait()
                raise subprocess.TimeoutExpired(cmd, timeout)
            ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 1.0))
            if not ready:
                continue
            data = os.read(proc.stdout.fileno(), 4096)
            if not data:
                break
            text = decoder.decode(data)
            if not text:
                continue  # séquence UTF-8 incomplète : on attend la suite
            for index, part in enumerate(text.split("\x00")):
                if index > 0:  # frontière NUL -> nouveau segment
                    segments.append("")
                    _safe_delta(on_delta, None)
                if part:
                    segments[-1] += part
                    _safe_delta(on_delta, part)
        tail = decoder.decode(b"", final=True)
        if tail:
            segments[-1] += tail
        proc.wait(timeout=10)
        stderr = (proc.stderr.read() or b"").decode("utf-8", errors="replace")
        return SimpleNamespace(
            stdout=segments[-1], returncode=proc.returncode, stderr=stderr
        )
    finally:
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except Exception:
                pass


# --- Worker Hermes persistant (« Hermes chaud », optionnel) ------------------

_WORKER_RR = 0  # position round-robin entre workers (répartition de charge)


def _worker_sockets() -> list[str]:
    """Chemins des sockets de workers Hermes configurés (vide -> 100 % CLI)."""
    raw = settings.HERMES_WORKER_SOCKETS or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def _ask_via_worker_once(
    prompt, skill_name, model, on_delta, images, timeout, sockets
) -> dict | None:
    """
    UN essai via un worker chaud (socket Unix). Retourne un result dict, ou
    None si AUCUN worker n'est joignable (-> l'appelant retombe sur la CLI).
    """
    global _WORKER_RR
    if not sockets:
        return None

    request = (
        json.dumps(
            {
                "prompt": prompt,
                "skill": skill_name or None,
                "model": model or None,
                "images": list(images or []),
                "history": [],
            },
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")

    count = len(sockets)
    for offset in range(count):
        sock_path = sockets[(_WORKER_RR + offset) % count]
        if not os.path.exists(sock_path):
            continue
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(timeout)
        try:
            conn.connect(sock_path)
        except OSError:
            try:
                conn.close()
            except OSError:
                pass
            continue  # ce worker est down -> essayer le suivant
        _WORKER_RR = (_WORKER_RR + offset + 1) % count
        if on_delta is not None:
            _safe_delta(on_delta, None)  # remise à zéro du texte partiel
        try:
            handle = conn.makefile("rwb")
            handle.write(request)
            handle.flush()
            content = ""
            for line in handle:
                event = json.loads(line)
                etype = event.get("type")
                if etype == "delta":
                    text = event.get("text") or ""
                    content += text
                    if on_delta is not None:
                        _safe_delta(on_delta, text)
                elif etype == "done":
                    final = (event.get("content") or content).strip()
                    if final:
                        return {"status": "success", "content": final,
                                "method": "worker", "error": None}
                    return {"status": "error", "content": "", "method": "worker",
                            "error": "Le worker Hermes n'a renvoyé aucune réponse."}
                elif etype == "error":
                    return {"status": "error", "content": "", "method": "worker",
                            "error": event.get("error") or "Erreur worker Hermes."}
            # Flux terminé sans 'done' (worker tombé en cours de route).
            return {"status": "error", "content": content.strip(),
                    "method": "worker", "error": "Flux du worker Hermes interrompu."}
        except (OSError, ValueError) as exc:
            return {"status": "error", "content": "", "method": "worker",
                    "error": f"Communication worker KO : {exc}"}
        finally:
            try:
                conn.close()
            except OSError:
                pass

    return None  # aucun worker joignable -> fallback CLI


def _ask_via_cli_once(
    hermes_bin, prompt, skill_name, model, on_delta, timeout, attempt, max_attempts
) -> dict:
    """UN appel Hermes via la CLI one-shot (le chemin historique, filet de sécurité)."""
    # Liste d'arguments (pas de shell) -> pas d'injection, prompt brut sûr.
    # skill_name=None -> appel Hermes SANS skill (utilitaire neutre, ex. titrage).
    # --ignore-rules : ISOLATION (parité avec le worker) -> coupe la mémoire
    # persistante partagée de Hermes + SOUL.md/AGENTS.md, pour qu'un fait « retenu »
    # ne fuite pas entre requêtes/étudiants. N'affecte pas `--skills` (explicite).
    cmd = [hermes_bin, "-z", prompt, "--ignore-rules"]
    if model:
        cmd += ["-m", model]
    if skill_name:
        cmd += ["--skills", skill_name]

    start = time.monotonic()
    try:
        if on_delta is not None:
            result = _run_streaming(cmd, timeout, on_delta)
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        error = f"Délai dépassé ({timeout}s) lors de l'appel à Hermes."
        _log_failure(attempt, max_attempts, skill_name, error, elapsed)
        return {"status": "error", "content": "", "method": "cli", "error": error}
    except OSError as exc:
        elapsed = time.monotonic() - start
        error = f"Échec d'exécution de la CLI Hermes : {exc}"
        _log_failure(attempt, max_attempts, skill_name, error, elapsed)
        return {"status": "error", "content": "", "method": "cli", "error": error}

    elapsed = time.monotonic() - start
    content = (result.stdout or "").strip()
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        error = f"Hermes a renvoyé le code {result.returncode}. {stderr}".strip()
        _log_failure(attempt, max_attempts, skill_name, error, elapsed, stderr)
        return {"status": "error", "content": content, "method": "cli", "error": error}
    if not content:
        error = "Hermes n'a renvoyé aucune réponse sur stdout."
        _log_failure(attempt, max_attempts, skill_name, error, elapsed)
        return {"status": "error", "content": "", "method": "cli", "error": error}
    return {"status": "success", "content": content, "method": "cli", "error": None}


def ask_hermes_with_skill(
    prompt: str,
    skill_name: str = settings.DEFAULT_SKILL_NAME,
    timeout: int = settings.HERMES_TIMEOUT_SECONDS,
    max_attempts: int | None = None,
    backoff: float | None = None,
    model: str | None = None,
    on_delta=None,
    images: list | None = None,
) -> dict:
    """
    Envoie un prompt à Hermes avec le skill préchargé.

    Chemin PRIVILÉGIÉ : un **worker Hermes chaud** (agent déjà démarré, pas de
    cold-start ~10 s) si `settings.HERMES_WORKER_SOCKETS` est renseigné. FALLBACK
    automatique sur la **CLI `hermes -z`** si aucun worker n'est joignable — donc
    aucune régression même worker éteint.

    Réessaie en cas d'échec transitoire (timeout, code retour non nul, sortie
    vide) jusqu'à `max_attempts` tentatives, avec une pause `backoff`.

    `on_delta` : callback de STREAMING (tokens au fil de l'eau ; `None` = reset).
    `images` : chemins d'images à faire VOIR au modèle (worker : contenu
    multimodal natif ; CLI : ignoré ici, les images restent gérées via le prompt).

    Retour : {"status": "success"|"error"|"not_available", "content": str,
              "method": "worker"|"cli"|"not_available", "error": None|str}.
    """
    if max_attempts is None:
        max_attempts = settings.HERMES_MAX_ATTEMPTS
    if backoff is None:
        backoff = settings.HERMES_RETRY_BACKOFF_SECONDS

    sockets = _worker_sockets()
    hermes_bin = shutil.which("hermes")
    if not sockets and not hermes_bin:
        _save_prompt_for_manual(prompt)
        return {
            "status": "not_available",
            "content": "",
            "method": "not_available",
            "error": (
                "Ni worker Hermes ni binaire 'hermes' disponibles. "
                f"Prompt sauvegardé pour test manuel : {settings.LAST_PROMPT_PATH}"
            ),
        }

    last_failure: dict | None = None
    for attempt in range(1, max_attempts + 1):
        # 1) Worker chaud d'abord (rapide) ; None = aucun worker joignable.
        result = _ask_via_worker_once(
            prompt, skill_name, model, on_delta, images, timeout, sockets
        )
        # 2) Fallback CLI (chemin historique) si pas de worker.
        if result is None:
            if not hermes_bin:
                _save_prompt_for_manual(prompt)
                return {
                    "status": "not_available", "content": "",
                    "method": "not_available",
                    "error": "Aucun worker Hermes joignable et binaire 'hermes' absent.",
                }
            result = _ask_via_cli_once(
                hermes_bin, prompt, skill_name, model, on_delta, timeout,
                attempt, max_attempts,
            )

        if result["status"] == "success":
            return result
        last_failure = result
        if attempt < max_attempts and backoff > 0:
            time.sleep(backoff)

    _save_prompt_for_manual(prompt)
    return last_failure

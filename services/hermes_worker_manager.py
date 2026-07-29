"""
Cycle de vie des workers Hermes chauds (démarrés/surveillés par l'API).

Au boot de FastAPI : lance `HERMES_WORKER_COUNT` process worker (dans le venv
Hermes) sur des sockets Unix, puis renseigne `settings.HERMES_WORKER_SOCKETS` —
l'adaptateur route alors les prompts vers ces workers (repli CLI si un socket
n'est pas encore prêt ou tombe). Un superviseur relance tout worker mort.

Dégradation gracieuse : si le runtime Hermes est introuvable (venv absent) ou si
un spawn échoue, on n'empêche jamais l'API de tourner — l'adaptateur retombe sur
`hermes -z`.
"""

import os
import socket
import subprocess
import threading
import time
from pathlib import Path

from config import settings

_WORKER_SCRIPT = str(Path(__file__).resolve().parent / "hermes_worker.py")

_LOCK = threading.Lock()
_WORKERS: list[dict] = []          # [{index, proc, socket, log}]
_STOP = threading.Event()
_SUPERVISOR: threading.Thread | None = None


def _log(*args) -> None:
    print("[hermes_worker_manager]", *args, flush=True)


def _socket_path(index: int) -> str:
    return os.path.join(settings.HERMES_WORKER_SOCKET_DIR, f"edututor_hermes_{index}.sock")


def _publish_sockets() -> None:
    """Expose les sockets courants à l'adaptateur (via settings, lu dynamiquement)."""
    settings.HERMES_WORKER_SOCKETS = ",".join(w["socket"] for w in _WORKERS)


def _spawn(index: int) -> dict:
    """Démarre UN worker. Le socket apparaîtra après son préchauffage (~6 s)."""
    sock = _socket_path(index)
    try:
        if os.path.exists(sock):
            os.unlink(sock)
    except OSError:
        pass
    env = dict(os.environ, HERMES_AGENT_ROOT=settings.HERMES_AGENT_ROOT)
    log_path = settings.DATA_DIR / f"hermes_worker_{index}.log"
    try:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "a", encoding="utf-8")
    except OSError:
        log = subprocess.DEVNULL
    proc = subprocess.Popen(
        [settings.HERMES_VENV_PYTHON, _WORKER_SCRIPT, "--socket", sock],
        stdout=log, stderr=log, env=env,
    )
    _log(f"worker {index} lancé (pid={proc.pid}, socket={sock})")
    return {"index": index, "proc": proc, "socket": sock, "log": log}


def _alive(worker: dict) -> bool:
    return worker["proc"].poll() is None and os.path.exists(worker["socket"])


def ping(sock_path: str, timeout: float = 2.0) -> bool:
    """Vrai si un worker répond `pong` sur ce socket."""
    if not os.path.exists(sock_path):
        return False
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(timeout)
        conn.connect(sock_path)
        handle = conn.makefile("rwb")
        handle.write(b'{"ping": true}\n')
        handle.flush()
        line = handle.readline()
        conn.close()
        import json
        return json.loads(line).get("type") == "pong"
    except Exception:  # noqa: BLE001
        return False


def _supervise() -> None:
    """Relance périodiquement les workers morts (best-effort)."""
    while not _STOP.wait(10.0):
        with _LOCK:
            for worker in _WORKERS:
                if _STOP.is_set():
                    return
                if not _alive(worker):
                    _log(f"worker {worker['index']} inactif -> redémarrage")
                    try:
                        worker["proc"].kill()
                    except Exception:  # noqa: BLE001
                        pass
                    fresh = _spawn(worker["index"])
                    worker["proc"], worker["socket"] = fresh["proc"], fresh["socket"]
            _publish_sockets()


def start() -> None:
    """Lance les workers au démarrage de l'API (idempotent, non bloquant)."""
    if not settings.HERMES_WORKERS_ENABLED:
        _log("workers désactivés (HERMES_WORKERS_ENABLED=0) -> CLI `hermes -z`.")
        return
    if not os.path.exists(settings.HERMES_VENV_PYTHON):
        _log(f"venv Hermes introuvable ({settings.HERMES_VENV_PYTHON}) -> repli CLI.")
        return
    with _LOCK:
        if _WORKERS:  # déjà démarrés
            return
        for index in range(settings.HERMES_WORKER_COUNT):
            try:
                _WORKERS.append(_spawn(index))
            except Exception as exc:  # noqa: BLE001
                _log(f"échec spawn worker {index}: {exc}")
        _publish_sockets()
    if _WORKERS:
        global _SUPERVISOR
        _STOP.clear()
        _SUPERVISOR = threading.Thread(target=_supervise, daemon=True)
        _SUPERVISOR.start()


def stop() -> None:
    """Coupe proprement les workers à l'arrêt de l'API."""
    _STOP.set()
    with _LOCK:
        for worker in _WORKERS:
            try:
                worker["proc"].terminate()
            except Exception:  # noqa: BLE001
                pass
        for worker in _WORKERS:
            try:
                worker["proc"].wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    worker["proc"].kill()
                except Exception:  # noqa: BLE001
                    pass
            try:
                os.unlink(worker["socket"])
            except OSError:
                pass
            if worker.get("log") not in (None, subprocess.DEVNULL):
                try:
                    worker["log"].close()
                except Exception:  # noqa: BLE001
                    pass
        _WORKERS.clear()
        settings.HERMES_WORKER_SOCKETS = ""
    _log("workers arrêtés.")

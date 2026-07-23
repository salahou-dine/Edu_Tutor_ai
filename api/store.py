"""
Persistance des conversations du NOUVEAU front web (React).

Fichier SÉPARÉ de celui de Streamlit (`data/conversations.json`) pendant la
phase de validation : les deux UIs coexistent sans risque de corruption
croisée. Même schéma de message que Streamlit (role/status/content/mode/agents/
deliverable) pour permettre une fusion simple au moment de la bascule.

Accès sérialisé par un verrou process-local (l'API est mono-process en dev).
"""

import json
import threading
import time

from config import workspace

MAX_CONVERSATIONS = 200

_LOCK = threading.Lock()


def _store_path():
    """Fichier de conversations de l'UTILISATEUR courant (isolation multi-user)."""
    return workspace.conversations_path()


def _load() -> dict:
    path = _store_path()
    if not path.exists():
        return {"conversations": [], "next_id": 1}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("conversations"), list):
            return {"conversations": data["conversations"],
                    "next_id": int(data.get("next_id", 1))}
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"conversations": [], "next_id": 1}


def _save(data: dict) -> None:
    try:
        path = _store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # la persistance ne doit jamais faire échouer une requête


def list_conversations() -> list[dict]:
    """Résumés (sans les messages), plus récente activité en premier."""
    with _LOCK:
        data = _load()
    summaries = [
        {"id": c["id"], "title": c.get("title") or "Nouvelle discussion",
         "created_at": c.get("created_at"), "updated_at": c.get("updated_at"),
         "message_count": len(c.get("messages", []))}
        for c in data["conversations"]
    ]
    summaries.sort(key=lambda c: c.get("updated_at") or c.get("created_at") or 0,
                   reverse=True)
    return summaries


def get_conversation(conv_id: int) -> dict | None:
    with _LOCK:
        data = _load()
    return next((c for c in data["conversations"] if c["id"] == conv_id), None)


def create_conversation() -> dict:
    with _LOCK:
        data = _load()
        now = time.time()
        conv = {"id": data["next_id"], "title": "Nouvelle discussion",
                "messages": [], "created_at": now, "updated_at": now}
        data["conversations"].append(conv)
        data["next_id"] += 1
        _prune(data)
        _save(data)
    return conv


def append_messages(conv_id: int, messages: list[dict]) -> dict | None:
    """Ajoute des messages à une conversation et met à jour updated_at."""
    with _LOCK:
        data = _load()
        conv = next((c for c in data["conversations"] if c["id"] == conv_id), None)
        if conv is None:
            return None
        conv["messages"].extend(messages)
        conv["updated_at"] = time.time()
        _save(data)
        return conv


def set_title(conv_id: int, title: str) -> None:
    with _LOCK:
        data = _load()
        conv = next((c for c in data["conversations"] if c["id"] == conv_id), None)
        if conv is not None and title:
            conv["title"] = title
            _save(data)


def delete_conversation(conv_id: int) -> bool:
    with _LOCK:
        data = _load()
        before = len(data["conversations"])
        data["conversations"] = [c for c in data["conversations"] if c["id"] != conv_id]
        if len(data["conversations"]) != before:
            _save(data)
            return True
    return False


def list_all_deliverables() -> list[dict]:
    """Tous les MÉDIAS de la Bibliothèque, plus récents en premier :
    - les livrables GÉNÉRÉS (résumés, fiches, documents) — les révisions
      successives d'un même livrable sont REGROUPÉES par lignée (`id`) et
      seule la DERNIÈRE version est affichée, avec `version_count` ;
    - les PIÈCES JOINTES ajoutées dans le chat (type "attachment").

    Chaque entrée porte son origine {conv_id, conv_title, created_at}.
    """
    with _LOCK:
        data = _load()

    # Collecte à plat (livrables + pièces jointes), avec un ordre de séquence.
    raw: list[dict] = []
    for conv in data["conversations"]:
        origin = {
            "conv_id": conv["id"],
            "conv_title": conv.get("title") or "Discussion",
            "created_at": conv.get("updated_at") or conv.get("created_at"),
        }
        for index, message in enumerate(conv.get("messages", [])):
            deliverable = message.get("deliverable")
            if deliverable:
                raw.append({**deliverable, **origin, "index": index})
            for attachment in message.get("attachments") or []:
                raw.append({
                    "type": "attachment",
                    "title": attachment.get("filename", "Pièce jointe"),
                    "doc": attachment.get("filename", ""),
                    "markdown": "",
                    **origin,
                    "index": index,
                })

    # Regroupement par lignée : on ne garde que la version la plus haute de
    # chaque `id`. Les entrées sans `id` (anciens livrables, pièces jointes)
    # forment chacune leur propre lignée -> jamais fusionnées par erreur.
    counts: dict[str, int] = {}
    for item in raw:
        if item.get("id"):
            counts[item["id"]] = counts.get(item["id"], 0) + 1

    latest: dict[str, dict] = {}
    for seq, item in enumerate(raw):
        key = item.get("id") or f"solo-{item['conv_id']}-{item['index']}-{seq}"
        current = latest.get(key)
        if current is None or int(item.get("version") or 1) >= int(current.get("version") or 1):
            latest[key] = item

    items = []
    for key, item in latest.items():
        item = {**item, "version_count": counts.get(item.get("id"), 1)}
        items.append(item)
    items.sort(
        key=lambda item: (item.get("created_at") or 0, item["conv_id"], item["index"]),
        reverse=True,
    )
    return items


def last_deliverable(conv: dict) -> dict | None:
    """Dernier livrable de la conversation (cible d'une itération)."""
    for message in reversed(conv.get("messages", [])):
        if message.get("deliverable"):
            return message["deliverable"]
    return None


def _prune(data: dict) -> None:
    """Plafonne le nombre de conversations (purge des plus anciennes par activité)."""
    convs = data["conversations"]
    if len(convs) <= MAX_CONVERSATIONS:
        return
    convs.sort(key=lambda c: c.get("updated_at") or c.get("created_at") or 0,
               reverse=True)
    data["conversations"] = convs[:MAX_CONVERSATIONS]

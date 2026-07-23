"""
Authentification (multi-utilisateur) : comptes identifiant + mot de passe.

- Utilisateurs persistés dans `data/users.json` (mot de passe haché PBKDF2, jamais
  en clair). Accès sérialisé par un verrou.
- Sessions par TOKEN signé (HMAC) : sans stockage serveur, survit au redémarrage
  de l'API, pas de dépendance externe. La révocation fine n'est pas gérée (MVP
  mono-serveur) — changer le secret invalide tous les tokens.
- `current_user` : dépendance FastAPI qui résout l'utilisateur depuis l'en-tête
  Authorization: Bearer <token>.

Chaque utilisateur a un `id` opaque (`u_<hex>`) qui sert de clé d'isolation pour
toutes ses données (cf. workspace, Phase 2).
"""

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from pathlib import Path

from config import settings

USERS_PATH = settings.DATA_DIR / "users.json"
_SECRET_PATH = settings.DATA_DIR / ".session_secret"
_PBKDF2_ROUNDS = 200_000
_LOCK = threading.Lock()


# --- Secret de signature des tokens ------------------------------------------

def _secret() -> bytes:
    """Clé HMAC de signature des sessions (créée une fois, persistée)."""
    try:
        if _SECRET_PATH.exists():
            return _SECRET_PATH.read_bytes()
    except OSError:
        pass
    key = secrets.token_bytes(32)
    try:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _SECRET_PATH.write_bytes(key)
        _SECRET_PATH.chmod(0o600)
    except OSError:
        pass
    return key


# --- Hachage des mots de passe -----------------------------------------------

def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Retourne (hash_hex, salt_hex). PBKDF2-HMAC-SHA256, jamais de clair stocké."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ROUNDS
    )
    return digest.hex(), salt


def _verify_password(password: str, salt: str, expected_hash: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, expected_hash)


# --- Stockage des utilisateurs -----------------------------------------------

def _load() -> dict:
    if not USERS_PATH.exists():
        return {"users": []}
    try:
        data = json.loads(USERS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("users"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"users": []}


def _save(data: dict) -> None:
    try:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        USERS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        USERS_PATH.chmod(0o600)
    except OSError:
        pass


def _public(user: dict) -> dict:
    """Vue exposable d'un utilisateur (jamais le hash/salt)."""
    return {"id": user["id"], "email": user["email"],
            "created_at": user.get("created_at")}


def find_by_email(email: str) -> dict | None:
    email = (email or "").strip().lower()
    for user in _load()["users"]:
        if user["email"] == email:
            return user
    return None


def get_user(user_id: str) -> dict | None:
    return next((u for u in _load()["users"] if u["id"] == user_id), None)


class AuthError(Exception):
    """Erreur métier d'authentification (email pris, identifiants invalides…)."""


def create_user(email: str, password: str, user_id: str | None = None) -> dict:
    """Crée un compte. Lève AuthError si l'email existe déjà ou est invalide."""
    email = (email or "").strip().lower()
    if "@" not in email or len(password or "") < 4:
        raise AuthError("Email invalide ou mot de passe trop court (min. 4).")
    with _LOCK:
        data = _load()
        if any(u["email"] == email for u in data["users"]):
            raise AuthError("Un compte existe déjà avec cet email.")
        pw_hash, salt = hash_password(password)
        user = {
            "id": user_id or f"u_{secrets.token_hex(8)}",
            "email": email,
            "password_hash": pw_hash,
            "salt": salt,
            "created_at": time.time(),
        }
        data["users"].append(user)
        _save(data)
    return _public(user)


def authenticate(email: str, password: str) -> dict:
    """Vérifie les identifiants et retourne l'utilisateur public. Lève AuthError."""
    user = find_by_email(email)
    if user is None or not _verify_password(password, user["salt"], user["password_hash"]):
        raise AuthError("Identifiant ou mot de passe incorrect.")
    return _public(user)


# --- Tokens de session (HMAC, sans stockage) ---------------------------------

_SIG_LEN = 32  # HMAC-SHA256 = 32 octets (longueur FIXE, pas de séparateur : la
#                signature binaire peut contenir n'importe quel octet, dont « . »)


def make_token(user_id: str) -> str:
    """Token = base64url(user_id_bytes ++ signature_hmac[32])."""
    signature = hmac.new(_secret(), user_id.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(user_id.encode("utf-8") + signature).decode("ascii")


def read_token(token: str) -> str | None:
    """Retourne le user_id si le token est valide et l'utilisateur existe, sinon None."""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        if len(raw) <= _SIG_LEN:
            return None
        user_id = raw[:-_SIG_LEN].decode("utf-8")
        signature = raw[-_SIG_LEN:]
    except Exception:
        return None
    expected = hmac.new(_secret(), user_id.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return None
    return user_id if get_user(user_id) is not None else None


# --- Amorçage : compte par défaut (migration de l'existant) -------------------

DEFAULT_USER_EMAIL = "salahoudine934@gmail.com"
DEFAULT_USER_ID = "u_salah"


def ensure_default_user() -> dict:
    """Crée le compte propriétaire des données pré-existantes s'il n'existe pas."""
    existing = find_by_email(DEFAULT_USER_EMAIL)
    if existing:
        return _public(existing)
    return create_user(DEFAULT_USER_EMAIL, "salah", user_id=DEFAULT_USER_ID)

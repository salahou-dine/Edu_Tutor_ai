"""
Migration mono-utilisateur -> multi-utilisateur (one-shot, idempotent).

Attribue toutes les données PRÉ-EXISTANTES (avant l'auth) au compte propriétaire
`u_salah` : déplace ses dossiers/fichiers sous data/users/u_salah/ et renomme sa
collection ChromaDB en course_chunks__u_salah.

Lancer :  .venv/bin/python -m scripts.migrate_to_multiuser
"""

import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import settings, workspace  # noqa: E402
from api import auth  # noqa: E402

UID = auth.DEFAULT_USER_ID  # "u_salah"


def _move_dir(src: Path, dst: Path) -> str:
    if not src.exists():
        return f"  (absent)     {src.name}/"
    if dst.exists():
        return f"  (déjà migré) {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"  ✓ {src} -> {dst}"


def _move_file(src: Path, dst: Path) -> str:
    if not src.exists():
        return f"  (absent)     {src.name}"
    if dst.exists():
        return f"  (déjà migré) {dst}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"  ✓ {src} -> {dst}"


def _rename_collection() -> str:
    old = settings.COLLECTION_NAME               # course_chunks
    new = workspace.collection_name(UID)         # course_chunks__u_salah
    if old == new:
        return "  (rien à renommer)"
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(settings.VECTORSTORE_PATH))
        names = {c.name for c in client.list_collections()}
        if new in names:
            return f"  (déjà migrée) {new}"
        if old not in names:
            return f"  (absente)     {old}"
        client.get_collection(old).modify(name=new)
        return f"  ✓ collection {old} -> {new}"
    except Exception as exc:
        return f"  ⚠ collection non renommée ({exc})"


def main() -> None:
    auth.ensure_default_user()  # garantit que u_salah existe
    root = workspace.user_root(UID)
    print(f"Migration des données vers {root}/\n")

    print("Dossiers :")
    for name, dst in (("courses", workspace.courses_dir(UID)),
                      ("artifacts", workspace.artifacts_dir(UID)),
                      ("generated", workspace.generated_dir(UID)),
                      ("attachments", workspace.attachments_dir(UID))):
        print(_move_dir(settings.DATA_DIR / name, dst))

    print("\nConversations :")
    print(_move_file(settings.DATA_DIR / "conversations_web.json",
                     workspace.conversations_path(UID)))

    print("\nVectorstore :")
    print(_rename_collection())

    print("\nTerminé.")


if __name__ == "__main__":
    main()

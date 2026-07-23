"""
Isolation multi-utilisateur : deux comptes ne partagent RIEN (conversations,
cours, workspace). C'est la garantie centrale de la Phase 2.
"""

import pytest

from config import settings, workspace


# --- Workspace : chaque utilisateur a ses propres chemins/collection ------------

class TestWorkspacePaths:
    def test_chemins_distincts_par_utilisateur(self):
        # cours / collection différents entre deux utilisateurs
        assert workspace.courses_dir("u_A") != workspace.courses_dir("u_B")
        assert workspace.collection_name("u_A") != workspace.collection_name("u_B")
        assert "u_A" in str(workspace.courses_dir("u_A"))
        assert workspace.collection_name("u_A").endswith("u_A")

    def test_defaut_est_le_compte_historique(self):
        assert workspace.collection_name().endswith(workspace.DEFAULT_USER_ID)

    def test_contextvar_bascule(self):
        workspace.set_current_user("u_X")
        assert workspace.current_user() == "u_X"
        assert workspace.collection_name().endswith("u_X")
        workspace.set_current_user(workspace.DEFAULT_USER_ID)  # restaure


# --- Isolation de bout en bout via l'API ----------------------------------------

pytest.importorskip("fastapi", reason="fastapi non installé")
from fastapi.testclient import TestClient  # noqa: E402

from api import auth, main as api_main  # noqa: E402


@pytest.fixture
def two_clients(tmp_path, monkeypatch):
    """Deux TestClients authentifiés (users A et B) dans le même data/ isolé."""
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(auth, "USERS_PATH", tmp_path / "users.json")
    monkeypatch.setattr(auth, "_SECRET_PATH", tmp_path / ".secret")
    monkeypatch.setattr(api_main, "generate_title", lambda q: "T")
    ua = auth.create_user("a@a.com", "secret")
    ub = auth.create_user("b@b.com", "secret")
    ca = TestClient(api_main.app,
                    headers={"Authorization": f"Bearer {auth.make_token(ua['id'])}"})
    cb = TestClient(api_main.app,
                    headers={"Authorization": f"Bearer {auth.make_token(ub['id'])}"})
    return ca, cb


class TestIsolationAPI:
    def test_conversations_non_partagees(self, two_clients):
        ca, cb = two_clients
        conv = ca.post("/api/conversations").json()
        # A voit sa conversation, B non
        assert any(c["id"] == conv["id"] for c in ca.get("/api/conversations").json())
        assert cb.get("/api/conversations").json() == []
        # B ne peut pas ouvrir la conversation de A (id inexistant chez lui)
        assert cb.get(f"/api/conversations/{conv['id']}").status_code == 404

    def test_cours_non_partages(self, two_clients):
        ca, cb = two_clients
        # On écrit un cours directement dans le workspace de A (évite la
        # vectorisation LLM). L'id de A vient de son /me.
        a_id = ca.get("/api/auth/me").json()["id"]
        courses_a = settings.DATA_DIR / "users" / a_id / "courses"
        courses_a.mkdir(parents=True, exist_ok=True)
        (courses_a / "cm.md").write_text("# cours de A", encoding="utf-8")
        assert [d["filename"] for d in ca.get("/api/documents").json()] == ["cm.md"]
        assert cb.get("/api/documents").json() == []
        # le fichier de A n'est pas servable via le compte B
        assert cb.get("/api/documents/cm.md/file").status_code == 404

    def test_endpoints_exigent_un_token(self, two_clients):
        anon = TestClient(api_main.app)  # sans en-tête Authorization
        for path in ("/api/documents", "/api/conversations", "/api/deliverables"):
            assert anon.get(path).status_code == 401

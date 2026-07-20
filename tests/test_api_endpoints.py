"""
API FastAPI : endpoints + flux SSE. L'orchestrateur et le titreur sont MOCKÉS
(aucun appel LLM) ; le store est redirigé vers un fichier temporaire.

Ignorés proprement si fastapi n'est pas installé.
"""

import json

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi non installé")
from fastapi.testclient import TestClient  # noqa: E402

from api import main as api_main  # noqa: E402
from api import store  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STORE_PATH", tmp_path / "conversations_web.json")
    return TestClient(api_main.app)


def _fake_handle(question, history=None, use_planner=False,
                 last_deliverable=None, on_event=None, **kw):
    """Simule l'orchestrateur : émet des événements puis répond."""
    if on_event:
        on_event({"type": "plan", "intent": "question", "steps": ["tutor"]})
        on_event({"type": "step_start", "agent": "tutor", "doc": None})
        on_event({"type": "step_done", "agent": "tutor", "status": "success"})
    return {"status": "success", "kind": "tutor", "answer": f"Réponse à : {question}",
            "doc": None, "steps_run": ["tutor"], "trace": {"intent": "question"},
            "payload": {"mode": "general_tutor"}}


def _sse_events(body: str) -> list[dict]:
    return [json.loads(line[len("data: "):])
            for line in body.splitlines() if line.startswith("data: ")]


class TestBasics:
    def test_health(self, client):
        data = client.get("/api/health").json()
        assert data["status"] == "ok"
        assert "pdf_export" in data

    def test_conversations_crud(self, client):
        assert client.get("/api/conversations").json() == []
        conv = client.post("/api/conversations").json()
        assert conv["title"] == "Nouvelle discussion"
        assert client.get(f"/api/conversations/{conv['id']}").json()["id"] == conv["id"]
        assert client.delete(f"/api/conversations/{conv['id']}").json() == {"deleted": conv["id"]}
        assert client.get(f"/api/conversations/{conv['id']}").status_code == 404

    def test_conversation_inconnue(self, client):
        assert client.get("/api/conversations/999").status_code == 404


class TestChatSSE:
    def test_flux_complet_premier_message(self, client, monkeypatch):
        monkeypatch.setattr(api_main.orchestrator, "handle", _fake_handle)
        monkeypatch.setattr(api_main, "generate_title", lambda q: "Titre généré")
        conv = client.post("/api/conversations").json()

        resp = client.post(f"/api/conversations/{conv['id']}/messages",
                           json={"content": "Bonjour, explique la kill chain"})
        assert resp.status_code == 200
        events = _sse_events(resp.text)
        types = [e["type"] for e in events]
        # progression -> message -> titre (1er échange) -> done
        assert types == ["plan", "step_start", "step_done", "message", "title", "done"]
        assert events[3]["message"]["agents"] == ["tutor"]
        assert events[4]["title"] == "Titre généré"

        # persistance : user + assistant stockés, titre appliqué
        stored = client.get(f"/api/conversations/{conv['id']}").json()
        assert [m["role"] for m in stored["messages"]] == ["user", "assistant"]
        assert stored["title"] == "Titre généré"

    def test_pas_de_titre_apres_le_premier_echange(self, client, monkeypatch):
        monkeypatch.setattr(api_main.orchestrator, "handle", _fake_handle)
        monkeypatch.setattr(api_main, "generate_title", lambda q: "X")
        conv = client.post("/api/conversations").json()
        client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "a"})
        resp = client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "b"})
        assert "title" not in [e["type"] for e in _sse_events(resp.text)]

    def test_message_vide_refuse(self, client):
        conv = client.post("/api/conversations").json()
        resp = client.post(f"/api/conversations/{conv['id']}/messages",
                           json={"content": "   "})
        assert resp.status_code == 400

    def test_erreur_orchestrateur(self, client, monkeypatch):
        monkeypatch.setattr(api_main.orchestrator, "handle",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        conv = client.post("/api/conversations").json()
        client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "x"})
        resp = client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "y"})
        events = _sse_events(resp.text)
        message = next(e for e in events if e["type"] == "message")
        assert message["message"]["status"] == "error"


class TestExport:
    MD = "# Titre\n\n- point un\n- point deux"

    def test_docx(self, client):
        resp = client.post("/api/export",
                           json={"markdown": self.MD, "title": "Ma fiche", "format": "docx"})
        assert resp.status_code == 200
        assert resp.content[:2] == b"PK"
        assert 'filename="Ma_fiche.docx"' in resp.headers["content-disposition"]

    def test_md(self, client):
        resp = client.post("/api/export",
                           json={"markdown": self.MD, "title": "f", "format": "md"})
        assert resp.content.decode("utf-8") == self.MD

    def test_format_inconnu(self, client):
        resp = client.post("/api/export",
                           json={"markdown": "x", "title": "f", "format": "exe"})
        assert resp.status_code == 400


class TestDocuments:
    def test_liste(self, client):
        docs = client.get("/api/documents").json()
        assert isinstance(docs, list)
        assert all({"filename", "analyzed", "size", "modified"} <= set(d) for d in docs)

    def test_upload_extension_refusee(self, client):
        resp = client.post("/api/documents",
                           files={"file": ("malware.exe", b"MZ", "application/x-msdos-program")})
        assert resp.status_code == 400

    def test_suppression_introuvable(self, client):
        assert client.delete("/api/documents/nexiste_pas.pdf").status_code == 404

    def test_ouverture_fichier(self, client, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "COURSES_DIR", tmp_path)
        (tmp_path / "cours.md").write_text("# Contenu du cours", encoding="utf-8")
        resp = client.get("/api/documents/cours.md/file")
        assert resp.status_code == 200
        assert "inline" in resp.headers.get("content-disposition", "")
        assert "Contenu du cours" in resp.text

    def test_ouverture_fichier_introuvable(self, client):
        assert client.get("/api/documents/fantome.pdf/file").status_code == 404


class TestAttachments:
    @pytest.fixture
    def att_dir(self, tmp_path, monkeypatch):
        from config import settings
        monkeypatch.setattr(settings, "ATTACHMENTS_DIR", tmp_path / "attachments")
        return tmp_path / "attachments"

    def test_upload_puis_ouverture(self, client, att_dir):
        resp = client.post("/api/attachments",
                           files={"file": ("notes.md", b"# Mes notes de TD", "text/markdown")})
        assert resp.status_code == 200
        meta = resp.json()
        assert meta["filename"] == "notes.md" and meta["size"] > 0
        served = client.get("/api/attachments/notes.md/file")
        assert served.status_code == 200
        assert "Mes notes de TD" in served.text

    def test_collision_de_nom_suffixee(self, client, att_dir):
        client.post("/api/attachments", files={"file": ("a.md", b"un", "text/markdown")})
        second = client.post("/api/attachments",
                             files={"file": ("a.md", b"deux", "text/markdown")}).json()
        assert second["filename"] == "a_2.md"

    def test_extension_refusee(self, client, att_dir):
        resp = client.post("/api/attachments",
                           files={"file": ("script.exe", b"MZ", "application/octet-stream")})
        assert resp.status_code == 400

    def test_message_avec_piece_jointe(self, client, att_dir, monkeypatch):
        client.post("/api/attachments",
                    files={"file": ("td.md", b"# TD Modbus\nContenu du TD.", "text/markdown")})
        received = {}

        def fake_handle(question, history=None, use_planner=False,
                        last_deliverable=None, on_event=None, attachments=None, **kw):
            received["attachments"] = attachments
            return {"status": "success", "kind": "tutor", "answer": "ok",
                    "doc": None, "steps_run": ["tutor"],
                    "trace": {"intent": "attachment"},
                    "payload": {"mode": "general_tutor"}}

        monkeypatch.setattr(api_main.orchestrator, "handle", fake_handle)
        monkeypatch.setattr(api_main, "generate_title", lambda q: "T")
        conv = client.post("/api/conversations").json()
        resp = client.post(f"/api/conversations/{conv['id']}/messages",
                           json={"content": "résume ce TD", "attachments": ["td.md"]})
        assert resp.status_code == 200
        # l'orchestrateur a reçu le texte extrait
        assert received["attachments"][0]["filename"] == "td.md"
        assert "Contenu du TD" in received["attachments"][0]["text"]
        # le message user persiste la pièce jointe (affichage + Bibliothèque)
        stored = client.get(f"/api/conversations/{conv['id']}").json()
        assert stored["messages"][0]["attachments"][0]["filename"] == "td.md"
        # ... et la Bibliothèque la liste
        items = client.get("/api/deliverables").json()
        assert any(i["type"] == "attachment" and i["title"] == "td.md" for i in items)


class TestLibrary:
    def test_bibliotheque_vide(self, client):
        assert client.get("/api/deliverables").json() == []

    def test_bibliotheque_liste_les_livrables(self, client, monkeypatch):
        monkeypatch.setattr(api_main.orchestrator, "handle", _fake_handle)
        monkeypatch.setattr(api_main, "generate_title", lambda q: "T")
        conv = client.post("/api/conversations").json()
        # injecte directement deux messages avec livrables via le store
        store.append_messages(conv["id"], [
            {"role": "assistant", "status": "success", "content": "voici 👇",
             "deliverable": {"type": "revision", "title": "Fiche CM4",
                             "doc": "cm4.pdf", "markdown": "# Fiche"}},
            {"role": "assistant", "status": "success", "content": "voici 👇",
             "deliverable": {"type": "document", "title": "Rapport pare-feux",
                             "doc": "", "markdown": "# Rapport"}},
        ])
        items = client.get("/api/deliverables").json()
        assert len(items) == 2
        assert {i["title"] for i in items} == {"Fiche CM4", "Rapport pare-feux"}
        assert all({"conv_id", "conv_title", "created_at", "markdown"} <= set(i)
                   for i in items)

    def test_revisions_regroupees_en_une_lignee(self, client):
        """3 versions d'un même livrable (même id) -> 1 entrée, la plus récente."""
        conv = client.post("/api/conversations").json()
        store.append_messages(conv["id"], [
            {"role": "assistant", "content": "v1",
             "deliverable": {"type": "document", "title": "Rapport", "doc": "",
                             "markdown": "# v1", "id": "lin1", "version": 1}},
            {"role": "assistant", "content": "v2",
             "deliverable": {"type": "document", "title": "Rapport", "doc": "",
                             "markdown": "# v2", "id": "lin1", "version": 2}},
            {"role": "assistant", "content": "v3",
             "deliverable": {"type": "document", "title": "Rapport", "doc": "",
                             "markdown": "# v3 finale", "id": "lin1", "version": 3}},
        ])
        items = client.get("/api/deliverables").json()
        assert len(items) == 1                      # une seule carte, pas trois
        assert items[0]["markdown"] == "# v3 finale"  # la dernière version
        assert items[0]["version_count"] == 3

    def test_lignees_distinctes_non_fusionnees(self, client):
        conv = client.post("/api/conversations").json()
        store.append_messages(conv["id"], [
            {"role": "assistant", "content": "a",
             "deliverable": {"type": "summary", "title": "Résumé A", "doc": "",
                             "markdown": "A", "id": "AAA", "version": 1}},
            {"role": "assistant", "content": "b",
             "deliverable": {"type": "summary", "title": "Résumé B", "doc": "",
                             "markdown": "B", "id": "BBB", "version": 1}},
        ])
        items = client.get("/api/deliverables").json()
        assert len(items) == 2
        assert all(i["version_count"] == 1 for i in items)

    def test_anciens_livrables_sans_id_non_fusionnes(self, client):
        """Rétrocompat : livrables d'avant le versionnage (sans id) restent distincts."""
        conv = client.post("/api/conversations").json()
        store.append_messages(conv["id"], [
            {"role": "assistant", "content": "1",
             "deliverable": {"type": "summary", "title": "Vieux", "doc": "",
                             "markdown": "un"}},
            {"role": "assistant", "content": "2",
             "deliverable": {"type": "summary", "title": "Vieux", "doc": "",
                             "markdown": "deux"}},
        ])
        items = client.get("/api/deliverables").json()
        assert len(items) == 2  # pas de fusion accidentelle par titre

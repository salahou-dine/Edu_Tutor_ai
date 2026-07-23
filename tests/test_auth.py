"""
Authentification multi-utilisateur : hachage, comptes, tokens, endpoints.
Store users + secret redirigés vers tmp (aucun effet sur les vraies données).
"""

import pytest

from api import auth


@pytest.fixture
def auth_tmp(tmp_path, monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(auth, "USERS_PATH", tmp_path / "users.json")
    monkeypatch.setattr(auth, "_SECRET_PATH", tmp_path / ".session_secret")
    return tmp_path


# --- Hachage -------------------------------------------------------------------

class TestHash:
    def test_jamais_de_clair_et_verifiable(self):
        h, salt = auth.hash_password("motdepasse")
        assert "motdepasse" not in h
        assert auth._verify_password("motdepasse", salt, h)
        assert not auth._verify_password("mauvais", salt, h)

    def test_sel_aleatoire(self):
        h1, s1 = auth.hash_password("x")
        h2, s2 = auth.hash_password("x")
        assert s1 != s2 and h1 != h2  # même mdp, hash différent (sel)


# --- Comptes -------------------------------------------------------------------

class TestAccounts:
    def test_creation_et_login(self, auth_tmp):
        user = auth.create_user("a@b.com", "secret")
        assert user["email"] == "a@b.com"
        assert "password_hash" not in user and "salt" not in user  # jamais exposés
        assert auth.authenticate("a@b.com", "secret")["id"] == user["id"]

    def test_email_insensible_a_la_casse(self, auth_tmp):
        auth.create_user("Test@Mail.com", "secret")
        assert auth.find_by_email("test@mail.com") is not None
        with pytest.raises(auth.AuthError):
            auth.create_user("TEST@mail.com", "autre")  # doublon

    def test_mauvais_mot_de_passe(self, auth_tmp):
        auth.create_user("a@b.com", "secret")
        with pytest.raises(auth.AuthError):
            auth.authenticate("a@b.com", "faux")

    def test_email_ou_mdp_invalide(self, auth_tmp):
        with pytest.raises(auth.AuthError):
            auth.create_user("pasunemail", "secret")
        with pytest.raises(auth.AuthError):
            auth.create_user("a@b.com", "xy")  # trop court


# --- Tokens --------------------------------------------------------------------

class TestTokens:
    def test_aller_retour(self, auth_tmp):
        user = auth.create_user("a@b.com", "secret")
        token = auth.make_token(user["id"])
        assert auth.read_token(token) == user["id"]

    def test_token_falsifie_rejete(self, auth_tmp):
        auth.create_user("a@b.com", "secret")
        assert auth.read_token("nimportequoi") is None
        assert auth.read_token("") is None

    def test_token_dun_user_inexistant(self, auth_tmp):
        token = auth.make_token("u_fantome")  # signé mais l'user n'existe pas
        assert auth.read_token(token) is None

    def test_tokens_robustes_quel_que_soit_le_hmac(self, auth_tmp, monkeypatch):
        """La signature binaire peut contenir « . » : l'aller-retour doit tenir
        pour TOUT user_id (régression : rsplit(b'.') coupait au mauvais octet).
        On teste la SÉRIALISATION du token sur 500 ids (get_user mocké)."""
        monkeypatch.setattr(auth, "get_user", lambda uid: {"id": uid})
        for i in range(500):
            uid = f"u_{i:04d}"
            assert auth.read_token(auth.make_token(uid)) == uid
        # le cas exact qui échouait : l'id du compte par défaut
        assert auth.read_token(auth.make_token("u_salah")) == "u_salah"


# --- Compte par défaut ---------------------------------------------------------

class TestDefaultUser:
    def test_creation_idempotente(self, auth_tmp):
        first = auth.ensure_default_user()
        again = auth.ensure_default_user()
        assert first["id"] == again["id"] == auth.DEFAULT_USER_ID
        assert first["email"] == auth.DEFAULT_USER_EMAIL
        assert len(auth._load()["users"]) == 1  # pas de doublon


# --- Endpoints -----------------------------------------------------------------

class TestAuthEndpoints:
    @pytest.fixture
    def client(self, auth_tmp):
        fastapi = pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient
        from api import main as api_main
        return TestClient(api_main.app)

    def test_register_login_me(self, client):
        r = client.post("/api/auth/register", json={"email": "z@z.com", "password": "secret"})
        assert r.status_code == 201
        token = r.json()["token"]

        assert client.post("/api/auth/login",
                           json={"email": "z@z.com", "password": "secret"}).status_code == 200

        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200 and me.json()["email"] == "z@z.com"

    def test_me_sans_token_401(self, client):
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/auth/me",
                          headers={"Authorization": "Bearer faux"}).status_code == 401

    def test_register_doublon_400(self, client):
        client.post("/api/auth/register", json={"email": "d@d.com", "password": "secret"})
        r = client.post("/api/auth/register", json={"email": "d@d.com", "password": "secret"})
        assert r.status_code == 400

    def test_login_mauvais_401(self, client):
        client.post("/api/auth/register", json={"email": "e@e.com", "password": "secret"})
        r = client.post("/api/auth/login", json={"email": "e@e.com", "password": "faux"})
        assert r.status_code == 401

# EduTutor — tuteur académique multi-agents

Tuteur pédagogique propulsé par [Hermes Agent](https://github.com/NousResearch/hermes-agent) :
l'étudiant dépose ses cours (PDF, Word, PowerPoint, images…), puis discute avec un
système multi-agents qui explique (RAG ancré sur les cours), analyse les documents,
produit des ressources d'étude (résumés, fiches de révision), rédige des documents
originaux et l'interroge pour vérifier sa compréhension.

## Architecture

```
webapp/    Front web React + Vite (interface officielle)     ← npm run dev :5173
api/       API FastAPI : chat SSE (streaming), documents,     ← uvicorn :8000
           conversations, Bibliothèque, exports docx/md/pdf
agents/    Le cœur multi-agents : orchestrateur (planner LLM
           + table d'intention), tuteur, IDP, contenu,
           rédaction, presenter, titrage
rag/       Indexation & recherche : extraction structurée
           multi-formats (+OCR), chunking structure-aware,
           ChromaDB, reranker cross-encoder
services/  Adaptateur CLI Hermes (streaming, retry) + exports
config/    Réglages centraux (env-surchargeables)
tests/     ~190 tests (unités déterministes + API, sans LLM)
archive/   Ancienne interface Streamlit (référence)
docs/      Audit technique, patch Hermes streaming
```

## Lancer

```bash
# Prérequis : venv Python installé (requirements.txt), Node >= 20,
# binaire `hermes` configuré (voir requirements.txt, section EXTERNES).

# Terminal 1 — API
.venv/bin/uvicorn api.main:app --port 8000

# Terminal 2 — Front
cd webapp && npm run dev
# → http://localhost:5173
```

Premier lancement : les modèles d'embedding/reranker (~1 Go) se téléchargent, et
le premier appel LLM peut être lent (démarrage à froid).

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

## Notes

- Application **mono-utilisateur** (pas d'authentification) — usage personnel.
- Les réponses passent exclusivement par Hermes (CLI one-shot) ; le streaming
  requiert le petit patch opt-in documenté dans `docs/hermes-oneshot-streaming.patch`.
- L'audit technique complet (architecture, décisions, limites) :
  `docs/AUDIT_TECHNIQUE.md`.

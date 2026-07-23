# Audit technique — Hermes Education (EduTutor)

> Document d'audit exhaustif. Objectif : qu'à la seule lecture de ce document on
> comprenne l'intégralité du projet **comme si on l'avait écrit soi-même** —
> contexte, architecture, rôle de chaque fichier, et **chaque décision** avec sa
> justification.
>
> Date : **2026-07-21** (3ᵉ révision majeure). Le projet est passé, depuis les
> versions précédentes de cet audit : (1) d'un tuteur mono-agent à un **système
> multi-agents** ; (2) d'une interface **Streamlit** à un **front web React +
> API FastAPI** avec streaming des réponses. Périmètre : tout le code source
> (hors `.venv`, `node_modules`, `data/`, caches) — ~5 100 lignes Python
> (`agents/` `rag/` `services/` `config/` `api/`), ~1 700 lignes TypeScript
> (`webapp/src/`), 200 tests.

---

## Table des matières

1. [Contexte global](#1-contexte-global)
2. [Pile technique & dépendances](#2-pile-technique--dépendances)
3. [Architecture & flux de données](#3-architecture--flux-de-données)
4. [Vue d'ensemble : rôle de chaque fichier](#4-vue-densemble--rôle-de-chaque-fichier)
5. [Détail par zone](#5-détail-par-zone)
6. [Les grandes décisions techniques (et pourquoi)](#6-les-grandes-décisions-techniques-et-pourquoi)
7. [Constats d'audit, limites & dette technique](#7-constats-daudit-limites--dette-technique)
8. [Comment lancer le projet](#8-comment-lancer-le-projet)

---

## 1. Contexte global

**EduTutor** est un **tuteur académique multi-agents** propulsé par **Hermes
Agent**. L'étudiant dépose ses cours (PDF, Word, PowerPoint, images…) puis
discute avec un système qui explique (RAG ancré sur les cours), analyse les
documents, produit des ressources d'étude, rédige des documents originaux et
l'interroge — le tout dans une interface web moderne.

### 1.1 Les quatre agents

| Agent | Rôle | Précondition |
|---|---|---|
| 🎓 **Tuteur** (`tutor_agent`) | Répond pédagogiquement, ancré sur les cours (RAG) + historique | aucune |
| 🔎 **IDP** (`idp_agent`) | Analyse documentaire structurée (type, thèmes, définitions, dates) avec **provenance** (ancrage `section_id`) | aucune |
| 📝 **Contenu** (`content_agent`) | Transforme un document **déjà analysé** en résumé structuré ou fiche de révision | `artifact` (analyse IDP) |
| ✍️ **Compose** (`compose_agent`) | Rédige un **document original** depuis une consigne libre ; sait aussi **réviser** un livrable existant | aucune |

Un **orchestrateur** (`agents/orchestrator.py`) décide quel(s) agent(s)
interviennent. Un **planner LLM** (skill `education-orchestrator`, sur modèle
rapide Haiku) propose le plan ; Python **valide, répare (insertion IDP
manquante) et exécute** ; un **routage déterministe** (table d'intention regex)
sert de filet si le planner échoue.

### 1.2 Deux interfaces (une officielle, une archivée)

- **Officielle** : front web **React** (`webapp/`) adossé à une **API FastAPI**
  (`api/`). Chat en **SSE** (streaming des réponses token par token + timeline
  de progression des agents), pièces jointes, cartes livrables téléchargeables,
  Bibliothèque des médias, Mes cours.
- **Archivée** : `archive/streamlit_app.py` — l'ancienne UI Streamlit,
  conservée comme référence, encore lançable en dépannage (pas de streaming, ni
  pièces jointes, ni Bibliothèque). Ne plus y développer.

Les deux appellent **le même cœur** (`agents/`, `rag/`, `services/`) — zéro
duplication de logique métier.

### 1.3 Livrables & Bibliothèque

Quand Contenu ou Compose produit une ressource, elle est renvoyée comme
**livrable structuré** `{type, title, doc, markdown, id, version}` rendu dans le
chat en **carte** (aperçu défilant, agrandir en modal, exports .docx/.md/.pdf).
L'étudiant **itère en langage naturel** (« raccourcis-le », « ajoute une
section ») : l'orchestrateur **réutilise le markdown existant** (pas de
régénération) et prolonge la **lignée** du livrable (même `id`, `version+1`). La
**Bibliothèque** agrège tous les médias (livrables + pièces jointes) ; les
révisions d'une même lignée y sont **regroupées** — une seule carte, dernière
version, badge « v{n} ».

### 1.4 Modes pédagogiques du tuteur

| Mode | Quand | Comportement |
|---|---|---|
| `course_grounded` | distance < 0.45 | répond surtout à partir du cours, indique la partie à revoir |
| `mixed` | distance < 0.65 | sépare ce que dit le cours et le complément général |
| `general_tutor` | sinon | réponse générale, en le précisant |
| `clarify` | question vague + plusieurs cours | demande **lequel** (déterministe, sans Hermes) |
| `course_summary` | « résume le cours … » | texte **intégral** du cours en un appel |

### 1.5 Contraintes structurantes (respectées partout)

- **Jamais** de détail technique côté étudiant (chunks, distances, section_id,
  doc_id, prompt, vectorstore). Garanti par une double sortie agent + le
  `presenter` qui filtre les section_ids ; labels UI sans jargon (« Analyse du
  document », pas « IDP »).
- **Jamais** d'appel LLM direct contournant Hermes : tout passe par la CLI
  `hermes -z … [--skills <skill>] [-m <modèle>]` via l'unique adaptateur.
- Le framework `hermes-agent/` n'est modifié que par **un patch opt-in
  documenté** (`docs/hermes-oneshot-streaming.patch`, cf. §6.8) ; la méthode
  d'extension reste la création de **skills** (5 skills `education-*`).
- Hypothèse **mono-utilisateur** (pas d'auth).

### 1.6 Modèles LLM

- **Rédaction** (tuteur, IDP, contenu, compose) : modèle par défaut configuré
  côté Hermes (`anthropic/claude-sonnet-4-6`, API directe Anthropic).
- **Utilitaires** (planner de l'orchestrateur, titrage des discussions) :
  **`anthropic/claude-haiku-4-5`** via `HERMES_FAST_MODEL` — ces appels ne
  produisent qu'un petit JSON ou quelques mots (planner 82 s → ~9 s, ×10).

---

## 2. Pile technique & dépendances

**Backend / cœur (Python 3.12, `.venv/`)**
- **FastAPI + uvicorn** — API HTTP (SSE pour le chat streamé).
- **ChromaDB** — base vectorielle persistée (`data/vectorstore/`).
- **sentence-transformers** — embedding multilingue
  `paraphrase-multilingual-MiniLM-L12-v2` + reranker cross-encoder
  `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (~470 Mo chacun).
- **PyMuPDF** — extraction PDF (texte + police) et rendu OCR.
- **python-docx / python-pptx / pytesseract / pillow** — Word, PowerPoint, OCR
  (Tesseract système requis).
- **xhtml2pdf + markdown** — export PDF des livrables.
- **Hermes Agent** — binaire CLI externe `hermes` (+ patch streaming opt-in).
- **pytest** — 200 tests (dev).

**Front (`webapp/`, Node ≥ 20)**
- **React 18 + Vite 7 + TypeScript** — SPA.
- **Tailwind CSS 3** — thème « futuriste premium » (noir aubergine, accents
  violet néon + bleu électrique, glassmorphism, bordures lavande).
- **lucide-react** (icônes), **react-markdown** (rendu des réponses/livrables).

**Dév** : Playwright + Chromium pour les captures d'écran de l'UI.

`requirements.txt` est complet et sectionné (API / cœur / tests / interface
archivée), avec les dépendances externes documentées (Hermes, Tesseract, Node).

---

## 3. Architecture & flux de données

### 3.1 Couches

```
┌──────────────────────────────────────────────────────────────────────┐
│ webapp/  Front React (Vite :5173) — interface OFFICIELLE              │
│   App.tsx (état) · Sidebar · Home (6 cartes d'objectif) · ChatView    │
│   (stream + timeline) · DeliverableCard · MediaLibrary · Courses ·    │
│   PromptBar (trombone) · lib/{api,chat,actions,labels}                │
│        appelle /api/* (proxy Vite) ↓                                  │
│ api/  FastAPI (:8000) — façade SANS logique métier                    │
│   main.py : health · documents(GET/upload/DELETE/file) · deliverables │
│   · attachments · conversations CRUD · POST messages (SSE) · export   │
│   store.py : conversations_web.json (persistance front, verrou)       │
│        appelle ↓ (le MÊME cœur que l'ancienne UI Streamlit)           │
│ agents/  orchestrator (planner LLM + table d'intention, réparation,   │
│   exécution, composition, livrables+lignée, révision, streaming) ·    │
│   tutor · idp · content · compose · presenter · registry · titler ·   │
│   artifact (DocumentArtifact) · document_store (doc_id + caches)      │
│ rag/  document_loader (multi-formats +OCR) → chunker → indexer        │
│   (sync incrémental) → retriever → reranker → source_formatter        │
│ services/  hermes_adapter (CLI -z, streaming, retry, modèle rapide) · │
│   exporters (docx/md/pdf)                                             │
│ config/settings.py  réglages centraux (env-surchargeables)           │
│                                                                      │
│ archive/streamlit_app.py  ancienne UI (référence, dépannage)         │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.2 Flux « message dans le chat » (SSE)

```
POST /api/conversations/{id}/messages  {content, attachments[]}
  ├─ (1er échange) thread PARALLÈLE titler.generate_title (Haiku) → titre
  ├─ pièces jointes → _attachment_texts (extraction via load_document_segments)
  └─ orchestrator.handle(question, history, use_planner=True,
                         last_deliverable, on_event, attachments)  [thread]
       events (queue.Queue) → StreamingResponse SSE :
       ├─ 0. PIÈCE JOINTE ? → _handle_attachments : tuteur ancré (direct)
       ├─ 0'. RÉVISION ?    → _handle_revise : réutilise markdown, lignée+1
       ├─ 1. planning → plan (Haiku : intent + agents)  [filet regex si échec]
       ├─ 2. _repair_preconditions (insère idp avant content/tutor-section)
       ├─ 3. _execute_all : step_start/step_done par agent ;
       │      le TUTEUR streame ses tokens → events delta / delta_reset
       └─ 4. _compose : réponse unique + éventuel deliverable (id, version)
  → SSE : planning · plan · step_start/done · delta · message · title · done
  → front : timeline + texte streamé + carte livrable + titre sidebar
```

### 3.3 Flux « agent IDP » (inchangé)

```
document → compute_doc_id (SHA-256 contenu, 16 hex)
  → cache data/artifacts/<doc_id>.json ? (schema_version==2) → hit
  → extract_document (déterministe : sections, langue, pages, qualité)
  → contexte [sN] (intégral ou condensé) → Hermes (skill education-idp)
  → extract_json → _validate_analysis (ANCRE : ne garde que les section_id réels)
  → DocumentArtifact {extraction, analysis} + cache  (échec×2 → analysis=None)
```

### 3.4 Flux « indexation RAG » (inchangé)

```
cours déposé → sync_courses_index (INCRÉMENTAL par mtime) :
  ajouté→indexé · modifié→remplacé · supprimé→retiré · inchangé→no-op
index_all_courses(reset=True) = REBUILD complet (si logique de découpage change)
```

### 3.5 Données sur disque

| Chemin | Contenu | Git |
|---|---|---|
| `data/courses/` | cours actifs (sources de vérité) | ignoré de fait |
| `data/vectorstore/` | ChromaDB (métadonnées source/title/section/page/mtime) | ignoré |
| `data/artifacts/<doc_id>.json` | artefacts IDP (schema v2, texte des sections) | ignoré |
| `data/generated/…json` | contenus générés (cache par doc/type/options) | ignoré |
| `data/attachments/` | pièces jointes du chat (hors RAG) | ignoré |
| `data/conversations_web.json` | discussions du front React | ignoré (sensible) |
| `data/conversations.json` | discussions de l'UI Streamlit archivée | ignoré (sensible) |
| `~/.hermes/skills/education/…` | 5 skills (hors repo) | hors périmètre |

---

## 4. Vue d'ensemble : rôle de chaque fichier

### Backend Python

| Fichier | Lignes | Rôle |
|---|---|---|
| `api/main.py` | 343 | Façade HTTP : documents, conversations, chat SSE, pièces jointes, Bibliothèque, exports. Aucune logique métier. |
| `api/store.py` | 189 | Persistance des conversations du front (`conversations_web.json`), regroupement des livrables par lignée. |
| `agents/orchestrator.py` | 804 | Cœur d'orchestration : planner LLM + table d'intention, réparation, exécution, composition, livrables+lignée, révision, pièces jointes, streaming, trace. |
| `agents/tutor_agent.py` | 735 | Agent tuteur : RAG → mode → prompt (par mode) → Hermes (streamé) ; résumé global ; clarification ; double sortie. |
| `agents/content_agent.py` | 260 | Résumé / fiche à partir de l'artefact ; contrats `needs_analysis`/`not_processable` ; cache. |
| `agents/idp_agent.py` | 248 | Analyse Hermes validée/ancrée section_id ; cache ; retry ×2. |
| `agents/compose_agent.py` | 211 | Rédaction de document original + révision d'un livrable existant. |
| `agents/artifact.py` | 158 | `DocumentArtifact` v2 (extraction déterministe + analysis), détection de langue. |
| `agents/presenter.py` | 109 | Artefact → Markdown étudiant, filtrage des section_ids. |
| `agents/document_store.py` | 95 | `doc_id` = SHA-256 du contenu ; caches JSON artefacts + générés. |
| `agents/registry.py` | 78 | Capacités déclaratives {tutor, idp, content, compose} (menu planner + validation). |
| `agents/titler.py` | 58 | Titre de discussion (3-6 mots) via Hermes neutre, modèle rapide. |
| `agents/common.py` | 50 | `extract_json` : parsing tolérant des sorties LLM. |
| `services/hermes_adapter.py` | 274 | Unique point d'appel Hermes (CLI `-z`, skill/modèle optionnels), **streaming** (protocole NUL), retry, journal. |
| `services/exporters.py` | 142 | Livrable Markdown → .docx (python-docx), .md, .pdf (xhtml2pdf, optionnel). |
| `config/settings.py` | 145 | Réglages centraux, env-surchargeables. |
| `rag/document_loader.py` | 470 | Extraction structurée multi-formats (md/txt/pdf+OCR/docx/pptx/images), détection de titres multi-signaux. |
| `rag/indexer.py` | 253 | Sync incrémental (mtime) + rebuild par collection temporaire. |
| `rag/retriever.py` | 142 | Recherche (vivier 20), inventaire cours, chunks d'un cours, délégation reranker. |
| `rag/chunker.py` | 131 | `chunk_segments` : segments → chunks structure-aware (900 car., jamais 2 sections fusionnées). |
| `rag/source_formatter.py` | 119 | Chunks → indications lisibles `{course, part, excerpt}`. |
| `rag/reranker.py` | 79 | Cross-encoder multilingue + repli propre sur tri par distance. |
| `rag/embeddings.py` | 33 | Fonction d'embedding partagée indexer/retriever. |

### Front React (`webapp/src/`, ~1 700 lignes)

| Fichier | Rôle |
|---|---|
| `App.tsx` | État global (documents, conversations, deliverables, messages, progress, liveText, view) ; orchestration du flux SSE ; 3 vues (chat / library / courses). |
| `components/Sidebar.tsx` | Marque, Nouvelle discussion, Discussions (#), Bibliothèque, Mes cours. |
| `components/Home.tsx` | Écran d'accueil : 6 cartes d'objectif → pastilles de cours / saisie préfixée. |
| `components/ChatView.tsx` | Fil de messages + timeline de progression + texte streamé (curseur). |
| `components/Message.tsx` | Bulle user (+ chips pièces jointes) / réponse (badge de mode + agents + markdown + carte). |
| `components/DeliverableCard.tsx` | Carte livrable : aperçu, modal Agrandir, exports .docx/.md/.pdf. |
| `components/ProgressTimeline.tsx` | Étapes des agents (pending / running / done). |
| `components/MediaLibrary.tsx` | Bibliothèque des médias générés + pièces jointes, badge de version, modal. |
| `components/Courses.tsx` | Mes cours : cartes cliquables (ouverture), upload, suppression, recherche. |
| `components/PromptBar.tsx` | Saisie + trombone (pièces jointes) + bouton d'envoi (halo néon). |
| `lib/api.ts` | Client API typé + types (ChatMessage, Deliverable, LibraryItem, ChatEvent). |
| `lib/chat.ts` | Client SSE (parse fetch stream) + téléchargement des livrables. |
| `lib/actions.ts` | Les 6 cartes d'objectif (mêmes gabarits de phrase, routage testé). |
| `lib/labels.ts` | Libellés sans jargon (modes, agents, étapes, types de livrable). |

### Skills Hermes (`~/.hermes/skills/education/`, hors repo)

`education-tutor` · `education-idp` · `education-content` · `education-compose`
· `education-orchestrator` (planner : propose un plan JSON, ne répond jamais).

---

## 5. Détail par zone

### 5.1 `api/main.py` — la façade HTTP

Endpoints (aucune logique métier — délègue à `agents`/`services`/`store`) :
- `GET /api/health` — santé + disponibilité export PDF.
- **Documents** : `GET` (liste + analyzed + size/mtime), `POST` (upload → sync
  index), `DELETE`, `GET /{f}/file` (ouverture PDF inline).
- **Bibliothèque** : `GET /api/deliverables` (livrables groupés par lignée +
  pièces jointes).
- **Pièces jointes** : `POST /api/attachments` (stockage hors RAG, collision
  suffixée), `GET /{f}/file`.
- **Conversations** : CRUD.
- **Chat** : `POST /api/conversations/{id}/messages` → **StreamingResponse SSE**.
  `orchestrator.handle` tourne dans un thread ; ses événements de progression
  transitent par une `queue.Queue` ; le titre est généré en parallèle (thread).
- **Export** : `POST /api/export` → .docx/.md/.pdf via `services.exporters`.

CORS ouvert sur `localhost:5173` (dev, mono-utilisateur).

### 5.2 `api/store.py` — persistance du front

`conversations_web.json` (séparé de Streamlit), accès sérialisé (verrou),
plafond 200. **`list_all_deliverables`** : collecte tous les livrables +
pièces jointes, **regroupe par lignée `id`** (garde la version max, ajoute
`version_count`), les entrées sans `id` (anciens livrables, pièces jointes)
forment chacune leur lignée (jamais fusionnées par titre).

### 5.3 `services/hermes_adapter.py` — l'appel Hermes

`ask_hermes_with_skill(prompt, skill_name=…, model=None, on_delta=None, …)` :
- commande `hermes -z <prompt> [-m <modèle>] [--skills <skill>]`, sans shell.
- **`model`** : surcharge le modèle (Haiku pour planner/titrage).
- **`on_delta`** (streaming) : `_run_streaming` lance Hermes avec
  `HERMES_ONESHOT_STREAM=1`, lit stdout au fil de l'eau (Popen + select +
  décodeur UTF-8 incrémental), **protocole NUL** — `\x00` = frontière de tour
  (texte partiel jeté), dernier segment = réponse canonique. Timeout = kill ;
  retry émet un reset.
- **Retry** (2 tentatives) sur timeout / code ≠ 0 / sortie vide ; journal
  `data/hermes_errors.log`. Jamais d'appel LLM direct de contournement.

### 5.4 `agents/orchestrator.py` — le chef d'orchestre

`handle(question, history, selected_doc, use_planner, last_deliverable,
on_event, attachments)` :
1. **Pièces jointes** → `_handle_attachments` : routage direct vers le tuteur
   ancré sur le contenu (délimité anti-injection, budget
   `ATTACHMENT_CONTEXT_MAX_CHARS=15000`), pas de planner.
2. **Révision** (`_is_revision_request` : verbe d'édition + cible visée) →
   `_handle_revise` : réutilise le markdown, **prolonge la lignée**
   (`id` hérité, `version+1`).
3. **Plan** : `_plan_with_llm` (Haiku, skill orchestrator) ; échec/JSON
   invalide → filet déterministe `_classify_to_plan` (table d'intention regex).
4. `_repair_preconditions` (insère `idp` avant content / tutor-ancré-section).
5. `_execute_all` (émet step_start/done ; le tuteur streame via
   `_delta_forwarder`) → `_compose` : réponse unique + `_build_deliverable`
   (nouvelle lignée `id`+`version:1`).

Points clés : `_match_doc` (tokens **distinctifs** du nom, corrigé 07/09) ;
`_RE_REVISE` + garde `_is_revision_request` (corrigé 07/12 — le verbe seul ne
suffit pas) ; `_RE_COMPOSE` avec exclusion résumé/fiche (corrigé 07/12).

### 5.5 `agents/tutor_agent.py`

Pipeline : résumé global (texte intégral) ; requête de recherche non diluée
(question auto-suffisante cherchée seule) ; `search_course` (20) →
`choose_tutor_mode` → conscience des cours (clarify / ancrage) → `rerank_chunks`
(5) ; prompt par mode (`_BASE_RULES` identité+exactitude+sécurité anti-injection
+ `_STRUCTURED_RULES` 5 intitulés **ou** `_GENERAL_RULES`) ; **streaming via
`on_delta`** propagé jusqu'à l'appel Hermes ; double sortie
(`answer_student_question` avec `debug` / `_for_ui` propre).

### 5.6 `agents/compose_agent.py`

`generate_document(instructions, course_context)` (document original, Markdown
`# titre`, connaissances générales OK, anti-injection) et
`revise_document(previous_markdown, instruction, title)` (réutilise le contenu
existant, cache par hash `REVISE::…`).

### 5.7 Le reste du cœur (inchangé depuis l'audit précédent)

`idp_agent` (validation/ancrage section_id), `content_agent` (contrats,
sections depuis l'artefact), `artifact` (schema v2, texte des sections),
`document_store` (doc_id hash, caches), `registry` (compose sans précondition —
générateur, pas transformateur), `presenter` (filtre le jargon), `common`
(extract_json), `titler` (Hermes neutre, modèle rapide). RAG :
`document_loader` (multi-signaux + OCR), `chunker` (structure-aware),
`indexer` (sync incrémental), `retriever`/`reranker`/`source_formatter`,
`embeddings` (partagé). Détails dans les révisions git antérieures de ce doc.

### 5.8 Front React (`webapp/src/`)

- **`App.tsx`** consomme le flux SSE (`streamMessage`) : `planning`/`plan` →
  timeline ; `delta`/`delta_reset` → `liveText` (texte streamé) ; `message` →
  message final ; `title` → refresh sidebar. Crée la conversation si aucune.
  Trois vues (`chat` / `library` / `courses`).
- **`Home.tsx`** : 6 cartes d'objectif (mêmes gabarits que Streamlit, routage
  déterministe testé) → pastilles de cours ou saisie préfixée.
- **`DeliverableCard.tsx` / `MediaLibrary.tsx`** : carte livrable (aperçu,
  modal, exports) ; Bibliothèque groupée par lignée avec badge « v{n} ».
- **`PromptBar.tsx`** : trombone → upload immédiat → chips → envoi.
- **`labels.ts`** : aucun jargon (« Analyse du document », pas « IDP »).

### 5.9 `archive/streamlit_app.py`

Ancienne UI, bandeau « ARCHIVÉ » en tête, imports corrigés
(`services.exporters`), encore lançable. Pas de streaming / pièces jointes /
Bibliothèque. Ne pas y développer.

---

## 6. Les grandes décisions techniques (et pourquoi)

1. **Architecture 3 couches, sans fusion** — `hermes-agent/` = moteur ;
   `~/.hermes/skills/education/` = personas d'agents ; `hermes-education/` =
   l'app. Étendre = ajouter un skill.
2. **Front web = React + API FastAPI ; Streamlit archivé** — Streamlit
   n'appelait Python qu'en direct ; une SPA moderne exige une API. Le même cœur
   sert les deux → zéro duplication ; le nouveau front apporte streaming,
   pièces jointes, Bibliothèque, timeline.
3. **Chat en SSE (Server-Sent Events)** — la latence (1-5 min) est *vécue* :
   l'étudiant voit la planification, les agents avancer, puis la réponse
   s'écrire token par token. Killer feature impossible en Streamlit.
4. **Streaming natif via patch Hermes opt-in** — le one-shot `-z` bufferisait
   tout ; un patch de ~40 lignes (`HERMES_ONESHOT_STREAM=1`, protocole NUL,
   `docs/hermes-oneshot-streaming.patch`) écrit les tokens au fil de l'eau, sans
   changer le comportement par défaut. Réappliquable si `git pull` d'Hermes.
5. **Modèle rapide pour les utilitaires** (`HERMES_FAST_MODEL` = Haiku) — le
   planner et le titreur ne produisent qu'un petit JSON / quelques mots : Sonnet
   y était surdimensionné. Planner 82 s → ~9 s (×10) ; les agents rédacteurs
   restent sur Sonnet.
6. **Planner LLM propose, Python dispose** — plan Sonnet/Haiku **validé** contre
   le registre, préconditions **réparées** (insertion IDP), exécuté ; **filet
   déterministe** (table d'intention regex) si le plan est inutilisable
   (verrouillé par tests).
7. **`DocumentArtifact` = contrat unique**, séparation extraction (déterministe)
   / analysis (LLM), **provenance garantie** (section_id validés côté Python) ;
   `doc_id` = hash de contenu.
8. **Compose = agent séparé** (pas fusionné dans content) — préconditions et
   posture de skill **opposées** (fidèle à la source vs écris de l'original).
9. **Livrables en cartes + itération par réutilisation + lignée** — le contenu
   vit dans une carte ; « raccourcis-le » renvoie le markdown existant au LLM ;
   les révisions partagent un `id` → Bibliothèque **sans doublons** (une carte,
   badge « v{n} »).
10. **Pièces jointes = routage direct tuteur** — la cible est explicite (comme
    ChatGPT), pas de planner ; texte extrait via le pipeline documentaire
    existant ; stockées hors RAG ; visibles dans la Bibliothèque.
11. **RAG structure-aware + récupération découplée (20 → reranker → 5) +
    requête non diluée + résumé global + sync incrémental** — cf. audit V2 ;
    inchangé.
12. **Sécurité anti-injection à double étage** (règles + délimitation du contenu
    non fiable), testée.
13. **UI sans jargon + transparence des agents** — l'étudiant ne voit jamais les
    entrailles, mais voit *quels agents* ont travaillé (badge de mode + « Répondu
    par … »), en langage clair.
14. **Vision des schémas via pièces jointes** — une image jointe au chat n'est
    plus OCRisée : son **chemin** est injecté dans le prompt du tuteur, et Hermes
    la charge dans le contexte du modèle multimodal (Sonnet) via son tool
    `vision_analyze` (fast-path natif, `image_input_mode: auto`). Le tuteur *voit*
    le schéma (formes, couleurs, flèches, disposition), pas seulement le texte.
    Une seule passe, streamée. Un message peut mêler images (vues) et textes.
15. **Vision des figures dans les cours indexés (RAG)** — `rag/vision_describe.py`
    extrait les images significatives d'un cours (PDF via PyMuPDF, ou fichier
    image), les fait **décrire** par le modèle (Haiku par défaut, économique) et
    indexe ces descriptions comme du texte recherchable (chunks `kind="image"`,
    section « Figure (schéma) »). Coûteux (un appel LLM par image) → **filtre de
    taille**, **plafond** par cours, **cache par hash** d'image (re-index
    gratuit), **filtre décoratif** (le modèle répond « DECORATIF » pour les
    logos/photos). À l'upload, l'enrichissement tourne en **tâche de fond**
    (verrou d'écriture) : le texte du cours est disponible immédiatement, les
    figures s'ajoutent ensuite. Rebuild complet : `index_all_courses(with_vision=True)`.

---

## 7. Constats d'audit, limites & dette technique

### 7.1 Constats traités (historique)

| Constat | État |
|---|---|
| Sur-capture `_RE_REVISE` / `_RE_COMPOSE` (routage) | ✅ corrigés 07/12, verrouillés par tests |
| Nettoyages (condition morte `_target_doc`, docstrings, chunker legacy…) | ✅ faits 07/12 |
| Latence planner ~82 s | ✅ Haiku → ~9 s |
| Pas de streaming | ✅ patch Hermes + pipeline SSE |
| Label agent « IDP » (jargon) | ✅ → « Analyse du document » |
| Doublons Bibliothèque après itérations | ✅ versionnage par lignée |
| `requirements.txt` sans fastapi/uvicorn ; `conversations_web.json` non gitignoré | ✅ corrigés à la bascule |

### 7.2 Limites connues (assumées / reportées)

- **Latence** : une réponse du tuteur reste à ~30-65 s (rédaction Sonnet
  incompressible) ; le streaming la *masque* mais ne la réduit pas. Premier
  appel après démarrage plus lent (chargement des modèles d'embedding).
- **Planner zélé sur formulation libre** (rapport QA « BUG-02 ») : « Entraîne-moi
  sur le cours X » peut déclencher un résumé en plus du quiz. Effet de bord
  mineur, non traité (les cartes normées ne le déclenchent pas).
- **Ancrage compose** : un cours nommé n'ancre la rédaction que s'il est **déjà
  analysé** (pas d'IDP à la volée).
- **Titres de sections bruités sur les slides PDF** (heuristique) ; la page sert
  de filet.
- **Compréhension visuelle** : les **images jointes au chat** ET les
  **figures/schémas des cours indexés** sont désormais VUES par le modèle
  (vision native Hermes, cf. §6.14-15). Les figures des cours sont décrites à
  l'indexation (tâche de fond) et rendues recherchables par le RAG. Reste hors
  périmètre : la vision n'est pas *régénérée* à la volée pendant une réponse
  (elle s'appuie sur les descriptions indexées).
- **Qualité RAG** (embedding/seuils) : laissée en l'état, à revoir si la perf le
  justifie.
- **Mono-utilisateur** : pas d'auth ni de concurrence.
- **Front** : pas de test automatisé (validé au screenshot Playwright + tests
  API) ; pas d'édition inline façon Canvas (une révision = un nouveau message).
- **Patch Hermes** : à réappliquer manuellement en cas de mise à jour d'Hermes.

### 7.3 Tests

**200 tests** (`.venv/bin/python -m pytest tests/`), sans appel LLM ni
vectorstore : routage & orchestration (dont les 2 pièges verrouillés, filet
déterministe, lignée des livrables), logique tuteur, unités RAG, briques agents
(validation/ancrage IDP, presenter, artefact, caches), exporters, adaptateur
Hermes (commande, retry, streaming NUL, UTF-8), API (endpoints, SSE mocké,
pièces jointes, Bibliothèque groupée). Le front n'a pas de tests automatisés.

---

## 8. Comment lancer le projet

```bash
# 0) Prérequis externes
#    - Hermes Agent configuré (~/.hermes/config.yaml : anthropic/claude-sonnet-4-6,
#      clé dans ~/.hermes/.env) + patch streaming (docs/hermes-oneshot-streaming.patch)
#      + 5 skills education-* dans ~/.hermes/skills/education/
#    - Tesseract : sudo apt-get install -y tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng
#    - Node >= 20

# 1) Dépendances
.venv/bin/pip install -r requirements.txt
cd webapp && npm install && cd ..

# 2) Lancer (2 terminaux)
.venv/bin/uvicorn api.main:app --port 8000      # API
cd webapp && npm run dev                        # front → http://localhost:5173

# 3) Tests
.venv/bin/python -m pytest tests/ -q

# 4) CLI de debug (cœur, sans UI)
python -m agents.tutor_agent "Qu'est-ce que la kill chain ?" --debug
python -m agents.idp_agent "data/courses/<fichier>"
python -m rag.indexer                           # rebuild complet du vectorstore

# 5) Interface archivée (dépannage)
.venv/bin/streamlit run archive/streamlit_app.py
```

Au premier lancement : embedding + reranker (~1 Go) téléchargés une fois ; le
premier appel LLM est plus lent (démarrage à froid). Les caches (artefacts,
contenus générés) rendent les répétitions quasi instantanées.

---

*Fin de l'audit. Les constats §7.1 sont traités ; §7.2 liste les limites
assumées. Rien n'empêche le fonctionnement actuel.*

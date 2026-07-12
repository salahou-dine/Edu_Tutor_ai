# Audit technique — Hermes Education (EduTutor)

> Document d'audit exhaustif. Objectif : qu'à la seule lecture de ce document tu
> comprennes l'intégralité du projet **comme si tu l'avais écrit toi-même** —
> contexte, architecture, rôle de chaque fichier, et **chaque décision** prise
> avec sa justification.
>
> Date de l'audit : **2026-07-12** (remplace l'audit du 2026-06-09, devenu
> obsolète : le projet est passé d'un tuteur mono-agent à un **système
> multi-agents**) · Périmètre : tout le code source du projet `hermes-education`
> (hors `.venv`, `data/vectorstore`, copies statiques), ~5 340 lignes Python.

---

## Table des matières

1. [Contexte global](#1-contexte-global)
2. [Pile technique & dépendances](#2-pile-technique--dépendances)
3. [Architecture & flux de données](#3-architecture--flux-de-données)
4. [Vue d'ensemble : rôle de chaque fichier](#4-vue-densemble--rôle-de-chaque-fichier)
5. [Détail fichier par fichier](#5-détail-fichier-par-fichier)
6. [Les grandes décisions techniques (et pourquoi)](#6-les-grandes-décisions-techniques-et-pourquoi)
7. [Constats d'audit, limites & dette technique](#7-constats-daudit-limites--dette-technique)
8. [Comment lancer le projet](#8-comment-lancer-le-projet)

---

## 1. Contexte global

**EduTutor** est un **tuteur académique multi-agents** propulsé par **Hermes
Agent**. Ce n'est pas un clone de NotebookLM : les documents de cours servent de
support pédagogique, mais l'objectif est d'**aider l'étudiant à apprendre**
(expliquer, produire des ressources d'étude, vérifier sa compréhension), avec ou
sans document pertinent indexé.

### 1.1 Les quatre agents

| Agent | Rôle | Précondition |
|---|---|---|
| 🎓 **Tuteur** (`tutor_agent`) | Répond pédagogiquement à une question, ancré sur les cours (RAG) et l'historique | aucune |
| 🔎 **IDP** (`idp_agent`) | Analyse documentaire structurée : type, thèmes, objectifs, définitions, dates — avec **provenance** (ancrage `section_id`) | aucune |
| 📝 **Contenu** (`content_agent`) | Transforme un document **déjà analysé** en ressource d'étude : résumé structuré ou fiche de révision | `artifact` (analyse IDP) |
| ✍️ **Compose** (`compose_agent`) | Rédige un **document original** (rapport, exposé, note…) depuis une consigne libre, façon Artifacts/Canvas ; sait aussi **réviser** un livrable existant | aucune |

Un **orchestrateur** (`agents/orchestrator.py`) décide quel(s) agent(s)
interviennent et dans quel ordre. Le chat de l'UI passe par
`orchestrator.handle(..., use_planner=True)` : un **planner LLM** (skill
`education-orchestrator`, Sonnet) propose le plan ; Python **valide, répare et
exécute** ; un routage **déterministe** (table d'intention regex) sert de filet
de secours si le planner échoue. L'UI affiche sur chaque réponse **quels agents
ont répondu** (« Répondu par 🔎 Analyse (IDP) → 📝 Contenu »).

### 1.2 Les livrables (façon Artifacts/Canvas)

Quand Contenu ou Compose produit une ressource, elle est renvoyée comme
**livrable structuré** `{type, title, doc, markdown}` rendu dans le chat en
**carte** distincte (aperçu défilant, bouton ⤢ Agrandir en modal,
téléchargements **.docx / .md / .pdf**). Le texte de la bulle de chat n'est
qu'une courte intro (« Voici ton document 👇 ») — pas de doublon de contenu.
L'étudiant peut ensuite **itérer en langage naturel** (« raccourcis-le »,
« ajoute une section sur X ») : l'orchestrateur détecte l'intention de révision
et **réutilise le markdown du dernier livrable** (pas de régénération depuis la
source).

### 1.3 Modes pédagogiques du tuteur

Choisis automatiquement selon la distance cosinus du meilleur passage retrouvé :

| Mode | Quand | Comportement |
|---|---|---|
| `course_grounded` | distance < 0.45 | répond surtout à partir du cours, indique la partie à revoir |
| `mixed` | distance < 0.65 | sépare ce que dit le cours et le complément général |
| `general_tutor` | sinon | réponse pédagogique générale, en le précisant |
| `clarify` | question vague + plusieurs cours | demande **lequel** (déterministe, sans Hermes) |
| `course_summary` | « résume le cours … » | texte **intégral** du cours en un appel |

### 1.4 Contraintes structurantes (respectées partout)

- **Jamais** de détail technique côté étudiant (chunks, distances, section_id,
  doc_id, prompt, vectorstore, chemins). Garanti par une double sortie agent
  (complète avec `debug` / UI propre) et par le `presenter` qui **filtre** les
  section_ids que le LLM glisserait dans l'analyse.
- **Jamais** d'appel LLM direct contournant Hermes : tout passe par la CLI
  `hermes -z … [--skills <skill>]` via l'unique adaptateur.
- Le framework `hermes-agent/` et `~/.hermes/` ne sont **pas modifiés** ; la
  méthode d'extension supportée est la création de **skills** sous
  `~/.hermes/skills/education/` (5 skills : tutor, idp, content, compose,
  orchestrator).
- Hypothèse **mono-utilisateur** documentée (multi-utilisateurs = phase future).

### 1.5 Modèle LLM

Depuis le 2026-07-06 : **`anthropic/claude-sonnet-4-6`** en API directe
Anthropic (configuré côté Hermes dans `~/.hermes/config.yaml`, clé dans
`~/.hermes/.env`). Remplace `owl-alpha` (OpenRouter), qui hallucinait et routait
mal. Leçon retenue : les modèles de **raisonnement** (réponse dans le canal
*reasoning*, `content` vide) sont **incompatibles** avec `hermes -z` — il faut
un modèle de chat standard.

---

## 2. Pile technique & dépendances

- **Python 3.12**, environnement `.venv/`.
- **Streamlit ≥1.58,<2.0** — interface web (borne haute : l'UI dépend de
  sélecteurs `data-testid` internes).
- **ChromaDB** — base vectorielle persistée (`data/vectorstore/`).
- **sentence-transformers** — embedding multilingue
  `paraphrase-multilingual-MiniLM-L12-v2` **et** reranker cross-encoder
  `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` (~470 Mo chacun, cache HF).
- **PyMuPDF (fitz)** — extraction PDF (texte + tailles de police) et rendu de
  pages pour l'OCR.
- **python-docx / python-pptx / pytesseract / pillow** — lecture Word,
  PowerPoint, OCR d'images et de PDF scannés (moteur système Tesseract requis :
  `tesseract-ocr` + langues fra/eng).
- **xhtml2pdf + markdown** — export PDF des livrables (Markdown → HTML → PDF).
- **Hermes Agent** — binaire CLI externe `hermes`, appelé en sous-processus.
- Dév : **Playwright + Chromium** (installés dans le venv, hors
  requirements.txt) pour les captures d'écran de l'UI pendant le développement.

`requirements.txt` est **complet** (contrairement au constat de l'audit
précédent) : versions minimales validées, dépendances externes (Hermes,
Tesseract) documentées en commentaire.

---

## 3. Architecture & flux de données

### 3.1 Couches

```
┌────────────────────────────────────────────────────────────────────┐
│ interface/streamlit_app.py   UI étudiant (chat, cartes livrables,  │
│   + interface/exporters.py   historique, cours, exports docx/md/pdf)│
│      appelle UNIQUEMENT ↓                                           │
│ agents/orchestrator.py       ORCHESTRATEUR (planner LLM + filet     │
│   │                          déterministe, réparation préconditions,│
│   │                          exécution, composition, livrables)     │
│   ├─ agents/tutor_agent.py   agent TUTEUR  (RAG + modes + Hermes)   │
│   ├─ agents/idp_agent.py     agent IDP     (extraction + analyse)   │
│   ├─ agents/content_agent.py agent CONTENU (résumé / fiche)         │
│   ├─ agents/compose_agent.py agent COMPOSE (rédaction + révision)   │
│   ├─ agents/presenter.py     artefact → Markdown étudiant           │
│   ├─ agents/registry.py      capacités déclaratives (menu planner)  │
│   └─ agents/titler.py        titre de discussion (appel neutre)     │
│                                                                     │
│ Contrat commun : agents/artifact.py (DocumentArtifact v2)           │
│                + agents/document_store.py (doc_id hash + caches)    │
│                                                                     │
│ rag/ : document_loader (md/txt/pdf/docx/pptx/images+OCR, segments   │
│        structurés) → chunker (structure-aware) → indexer (sync      │
│        incrémental + rebuild) → retriever → reranker → formatter    │
│                                                                     │
│ services/hermes_adapter.py   SEUL point d'appel Hermes (CLI -z)     │
│ config/settings.py           réglages centraux (env-surchargeables) │
└────────────────────────────────────────────────────────────────────┘
```

### 3.2 Flux « message dans le chat »

```
message étudiant
  └─ submit_question (UI)
       ├─ (1er message) thread PARALLÈLE titler.generate_title  → titre sidebar
       └─ orchestrator.handle(question, history, use_planner=True,
                              last_deliverable=<dernier livrable du fil>)
            ├─ 0. RÉVISION ? (_RE_REVISE + livrable existant)
            │      └─ compose.revise_document(markdown existant, consigne)
            │          → livrable mis à jour (nouveau message)         [sans planner]
            ├─ 1. PLAN : _plan_with_llm (skill education-orchestrator, Sonnet)
            │      └─ échec/JSON invalide → _classify_to_plan (regex, filet)
            ├─ 2. _repair_preconditions : insère `idp` avant content/tutor-section
            │      si le document n'est pas analysé
            ├─ 3. _execute_all : _run_tutor / _run_idp / _run_content / _run_compose
            └─ 4. _compose : UNE réponse étudiant
                   ├─ content/compose réussi → deliverable {type,title,doc,markdown}
                   │    + intro courte dans la bulle
                   └─ analyze → présentation IDP + carte résumé
  └─ _store_assistant : message {content, mode, agents: steps_run, deliverable?}
       → render : badge de mode + chips « Répondu par … » + carte livrable
```

### 3.3 Flux « agent IDP » (analyse documentaire)

```
document → compute_doc_id (SHA-256 contenu, 16 hex)
  → cache data/artifacts/<doc_id>.json ? (schema_version == 2)   → hit : fini
  → extract_document()          DÉTERMINISTE (segments, langue, pages, qualité)
  → contexte sections [sN]      (intégral, ou condensé si > MAX_SUMMARY_INPUT_CHARS)
  → Hermes (skill education-idp) → extract_json (parsing tolérant)
  → _validate_analysis          ANCRAGE : ne garde que les section_id RÉELS
  → DocumentArtifact {extraction, analysis} + cache
  (échec ×2 → analysis=None, jamais d'analyse à moitié fausse)
```

### 3.4 Flux « indexation » (RAG)

```
fichier déposé (UI) → data/courses/
  └─ ensure_index_up_to_date() → sync_courses_index()   INCRÉMENTAL (mtime)
       ├─ cours ajouté   → load_document_segments → chunk_segments → add
       ├─ cours modifié  → delete(where source) + ré-add
       ├─ cours supprimé → delete(where source)
       └─ inchangé       → no-op
  (index_all_courses(reset=True) = REBUILD complet via collection temporaire,
   requis quand la LOGIQUE de découpage/embedding change)
```

### 3.5 Données sur disque

| Chemin | Contenu | Git |
|---|---|---|
| `data/courses/` | cours actifs (source de vérité des documents) | non commité de fait (binaire) |
| `data/vectorstore/` | ChromaDB (sqlite + HNSW), métadonnées `{source, title, section, page, chunk_index, mtime}` | ignoré |
| `data/artifacts/<doc_id>.json` | artefacts IDP (schema v2, avec texte des sections) | ignoré |
| `data/generated/<clé>__<type>__<opts>.json` | contenus générés (résumé/fiche/document) | ignoré |
| `data/conversations.json` | discussions persistées (plafond 200, purge par ancienneté) | ignoré (sensible) |
| `data/last_tutor_prompt.md` | dernier prompt (fallback manuel) | ignoré |
| `data/hermes_errors.log` | journal des échecs Hermes (dev) | ignoré (`*.log`) |
| `interface/static/courses/` | copies servies en HTTP (ouverture nouvel onglet) | régénérable |
| `~/.hermes/skills/education/…` | les 5 skills (HORS repo) | hors périmètre git |

---

## 4. Vue d'ensemble : rôle de chaque fichier

| Fichier | Lignes | Rôle en une phrase |
|---|---|---|
| `config/settings.py` | 132 | Tous les réglages centraux, env-surchargeables (chemins, embedding, chunking, seuils, Hermes, OCR). |
| `rag/document_loader.py` | 470 | Extraction **structurée** (segments `{text, heading, page}`) : md/txt, PDF (+OCR), docx, pptx, images ; détection de titres multi-signaux. |
| `rag/chunker.py` | 131 | `chunk_segments` : segments → chunks structure-aware (900 car., overlap 120, jamais 2 sections fusionnées). |
| `rag/embeddings.py` | 33 | Fonction d'embedding **partagée** indexer/retriever (multilingue, cosinus), cache singleton. |
| `rag/indexer.py` | 253 | `sync_courses_index` (incrémental par mtime) + `index_all_courses` (rebuild par collection temporaire). |
| `rag/retriever.py` | 142 | Recherche (vivier 20), inventaire des cours, chunks d'un cours entier, délégation reranker. |
| `rag/reranker.py` | 79 | Cross-encoder multilingue : reclasse le vivier, garde top-5 ; repli propre sur tri par distance. |
| `rag/source_formatter.py` | 119 | Chunks techniques → indications lisibles `{course, part, excerpt}` depuis les **métadonnées** (section/page). |
| `services/hermes_adapter.py` | 191 | Unique point d'appel Hermes (CLI `-z`, skill optionnel), retry, timeout 180 s, journal des échecs. |
| `agents/registry.py` | 78 | Capacités déclaratives {tutor, idp, content, compose} : menu du planner + base de validation. |
| `agents/artifact.py` | 158 | `DocumentArtifact` v2 (extraction déterministe + analysis Hermes, sections **avec texte**), détection de langue. |
| `agents/document_store.py` | 95 | `doc_id` = SHA-256 du contenu ; caches JSON artefacts + contenus générés. |
| `agents/common.py` | 50 | `extract_json` : parsing tolérant des sorties JSON de Hermes (fences, texte autour). |
| `agents/tutor_agent.py` | 731 | Agent tuteur : RAG → mode → prompt (règles par mode) → Hermes ; résumé global ; clarification ; double sortie. |
| `agents/idp_agent.py` | 248 | Agent IDP : extraction + analyse Hermes validée/ancrée section_id, cache, retry ×2. |
| `agents/content_agent.py` | 260 | Agent contenu : artefact → résumé/fiche ; contrats `needs_analysis`/`not_processable` ; cache par (doc, type, sections). |
| `agents/compose_agent.py` | 211 | Agent rédaction : `generate_document` (original, ancrage cours optionnel) + `revise_document` (itération sur l'existant). |
| `agents/orchestrator.py` | 684 | Orchestrateur : planner LLM + table d'intention, réparation, exécution, composition, livrables, révision, trace. |
| `agents/presenter.py` | 109 | Artefact IDP → Markdown étudiant, **filtrage des section_ids** résiduels. |
| `agents/titler.py` | 53 | Titre de discussion (3-6 mots) via appel Hermes **neutre** (sans skill). |
| `interface/streamlit_app.py` | 974 | UI : chat, cartes livrables, chips agents, sidebar façon Claude, cours, persistance. |
| `interface/exporters.py` | 142 | Livrable Markdown → octets .docx (python-docx), .md, .pdf (xhtml2pdf, optionnel). |
| `tests_manual/test_tutor_agent.py` | — | Vérification manuelle du tuteur (3 questions + sortie UI). |
| `.streamlit/config.toml` | — | Watcher off, fichiers statiques on, thème sombre. |
| `docs/agent_tutor_architecture.md` | — | Document de conception V1 (mono-agent) — partiellement obsolète. |

---

## 5. Détail fichier par fichier

### 5.1 `config/settings.py`

Source unique de vérité, **aucune clé API**. Nouveautés depuis l'audit V1 :

- **Multi-agents** : `ARTIFACTS_DIR`, `GENERATED_DIR`,
  `ARTIFACT_SCHEMA_VERSION = 2` (v2 = texte des sections dans l'artefact ; un
  bump invalide les caches).
- **Formats** : `SUPPORTED_EXTENSIONS` étendu (md, txt, pdf, docx, pptx, png,
  jpg, jpeg, tiff, tif, bmp, webp) ; OCR `OCR_LANG="fra+eng"`, `OCR_DPI=200`.
- **Chunking structure-aware** : `CHUNK_TARGET_CHARS=900`,
  `CHUNK_OVERLAP_CHARS=120`, `HEADING_FONT_RATIO=1.15` (titre PDF = police
  ≥ 1.15 × taille modale).
- **Récupération découplée** : `RETRIEVAL_TOP_K=20` (vivier) /
  `CONTEXT_TOP_K=5` (contexte final).
- **Reranker** : `RERANKER_ENABLED=1`, `RERANKER_MODEL_NAME` (cross-encoder
  multilingue).
- **Seuils de mode** : 0.45 / 0.65 (cosinus, calibrés multilingue).
- **Hermes** : timeout **180 s** (run normal ~70 s → marge), `HERMES_MAX_ATTEMPTS=2`,
  backoff 2 s, `HERMES_ERROR_LOG`. `MAX_SUMMARY_INPUT_CHARS=1 500 000`
  (garde-fou résumé global ≈ 500 pages).

**Décision.** Presque tout est surchargeable par variable d'environnement —
ajuster en production sans toucher au code.

---

### 5.2 `rag/document_loader.py` — extraction structurée multi-formats

Cœur de la qualité RAG. Deux APIs : `load_document_text` (plat, rétrocompat) et
**`load_document_segments`** → `[{text, heading, page}]`, consommée par
l'indexeur ET par l'artefact IDP (même vérité pour le RAG et les agents).

**Détection de titre multi-signaux** (`_looks_like_heading`), volontairement
format-agnostique car les documents réels sont hétérogènes :
1. numérotation (`1.2`, `IV.`, `A)`) et mots-clés (« Chapitre 2 ») — fiables ;
2. police PDF (> corps × `HEADING_FONT_RATIO`) ou gras — signaux **faibles** :
   exigent une « forme de titre » (commence par majuscule/chiffre, court) pour
   éliminer les fragments de slides ;
3. ligne courte ENTIÈREMENT en majuscules ;
4. titres Markdown `#`.

**Par format** :
- **PDF** (`_segments_from_pdf`) : lignes + tailles de police via PyMuPDF ;
  taille « corps » = taille modale ; **filtrage du bruit récurrent**
  (lignes répétées sur ≥ 30 % des pages, dates seules, numéros seuls) ; titres
  multi-lignes agrégés (même police, avant tout corps) ; chaque segment porte sa
  page de début. PDF **sans texte** → tentative **OCR** page par page
  (`_ocr_pdf_pages` : rendu image DPI 200 + pytesseract) sinon `ValueError`.
- **.docx** : titres par **style** Word (Heading/Titre/Title) + heuristique ;
  tableaux regroupés en fin (ordre non garanti par python-docx).
- **.pptx** : 1 diapo = 1 section (titre = shape titre reconnu par
  **shape_id** — `is` non fiable, python-pptx recrée les wrappers ; page = n° de
  diapo).
- **Images** : OCR pur ; image sans texte → erreur explicite (pas d'invention ;
  la compréhension **visuelle** d'un schéma nécessiterait un modèle de vision,
  reporté).

---

### 5.3 `rag/chunker.py`

- **`chunk_segments(segments, …)`** — le chemin unique : empaquette chaque
  section à ~900 car. (overlap 120), **ne fusionne jamais deux sections**,
  produit `{id, source, chunk_index, text, title?, section?, page?}`.
  `_split_text` respecte paragraphes puis phrases, découpe dure en dernier
  recours.
- Les fonctions legacy V1 (découpage « par paragraphes ») ont été **supprimées**
  le 2026-07-12 (aucun usage externe). Démo CLI :
  `python -m rag.chunker <document>`.

---

### 5.4 `rag/embeddings.py`

Fonction d'embedding **partagée** (singleton module) + `collection_metadata()`
(`{"hnsw:space": "cosine"}`). Le choix **multilingue** permet à une question FR
de retrouver un cours EN (corpus réel : cours de cybersécurité OT en anglais).

---

### 5.5 `rag/indexer.py`

- **`sync_courses_index()`** — chemin normal (appelé par l'UI à chaque run) :
  compare le dossier des cours au vectorstore via la métadonnée **`mtime`** ;
  ajouté → indexé, supprimé → retiré, modifié → remplacé, inchangé → no-op.
  Chaque cours n'est vectorisé qu'**une** fois.
- **`index_all_courses(reset=True)`** — rebuild complet, **requis quand la
  logique change** (découpage, embedding). Construit dans une collection
  temporaire `course_chunks_building` puis **bascule** (renommage) : la base
  active n'est jamais vide pendant la lente vectorisation.
- `_derive_title` : premier `# H1` (.md) sinon nom de fichier nettoyé.
- Erreurs capturées **par fichier** (un PDF illisible ne bloque pas les autres).

---

### 5.6 `rag/retriever.py` & 5.7 `rag/reranker.py`

- `search_course(query, n_results=RETRIEVAL_TOP_K)` → vivier de 20 chunks
  `{rank, text, source, chunk_index, title, section, page, distance}`.
- `rerank_chunks` délègue à `rag.reranker.rerank` : **cross-encoder** lit chaque
  paire (question × chunk) et reclasse finement ; garde `CONTEXT_TOP_K=5`.
  **Repli propre** sur `chunks[:top_k]` si modèle indisponible/erreur (testé).
  Chargement ~23 s au 1er usage d'un process (piste : préchargement).
- `list_indexed_courses()` → cours distincts (conscience mono/multi-cours).
- `get_course_chunks(title)` → tous les chunks ordonnés (résumé global).
- Le **mode pédagogique reste calculé sur la distance d'embedding** (avant
  rerank) — le reranker ne fait que réordonner le contexte.

### 5.8 `rag/source_formatter.py`

Chunks → indications `{course, part, excerpt, document_path}` dédupliquées par
(course, part). Depuis le découpage structure-aware, la « partie » vient des
**métadonnées d'indexation** (`section`, repli `Page N`, sinon « Section
générale ») — plus aucun re-parse du document. Extraits courts (60-90 car.)
avec démarrage propre en début de phrase.

---

### 5.9 `services/hermes_adapter.py`

`ask_hermes_with_skill(prompt, skill_name=DEFAULT, timeout=180, …)` :
- `subprocess.run([hermes, "-z", prompt, "--skills", skill])` **sans shell** ;
  depuis l'ajout du titrage, **`skill_name=None` → appel SANS skill** (usage
  utilitaire neutre).
- Retour uniforme `{status: success|error|not_available, content, method, error}`.
- **Retry** (2 tentatives, backoff 2 s) sur timeout / code ≠ 0 / sortie vide —
  les échecs observés étaient **intermittents** (provider). Pas de retry si le
  binaire est absent.
- Chaque échec est **journalisé** (`data/hermes_errors.log`, dev) ; le prompt est
  sauvegardé (`last_tutor_prompt.md`) pour test manuel.
- Décisions maintenues : CLI one-shot = seul point d'entrée non-interactif
  propre ; gateway/webhook/MCP/import du runtime écartés ; jamais d'appel LLM
  direct en remplacement silencieux.

---

### 5.10 `agents/registry.py`

`CAPABILITIES` : dataclass `Capability {name, description, inputs, outputs,
preconditions}` pour **tutor**, **idp**, **content** (`preconditions=
("artifact",)`) et **compose** (aucune précondition — c'est un **générateur**,
pas un transformateur de source). Double usage : menu donné au planner LLM +
base de la validation/réparation Python. *Ajouter un agent = ajouter une entrée
ici + son module + son skill.*

**Décision (2026-07-12).** La génération de documents n'a PAS été fusionnée
dans `content` : la précondition `artifact` (sur laquelle repose la réparation
déterministe) et la posture du skill (« fidèle à la source » vs « écris de
l'original ») sont **opposées** — un agent séparé garde les deux contrats purs.

### 5.11 `agents/artifact.py` & 5.12 `agents/document_store.py`

- **`DocumentArtifact`** (schema v2) = contrat commun. Deux blocs séparés :
  `extraction` **déterministe** (status ok/needs_ocr/empty, langue FR/EN
  heuristique, pages, sections `{section_id, heading, page_start, char_len,
  structure_source, text}`, qualité good/low_structure) et `analysis` **enrichi
  Hermes** (None tant que non analysé). La v2 stocke le **texte des sections**
  dans l'artefact : l'agent contenu (et le tuteur ancré) lisent l'artefact,
  jamais le PDF brut.
- `extract_document(path)` ne lève pas pour un document illisible : l'état est
  **encodé** dans `status` (needs_ocr / empty).
- **`compute_doc_id`** = SHA-256 du **contenu** (16 hex, lecture par blocs) —
  identité stable au renommage, détection de modification/doublon. Caches JSON
  jamais bloquants (try/except OSError) ; artefact au mauvais `schema_version`
  → ignoré (réanalyse).

### 5.13 `agents/common.py`

`extract_json` : extraction tolérante du premier objet/tableau JSON d'une
réponse LLM (fences ```json, texte autour, bornes englobantes). Retourne None
si inexploitable — l'appelant décide (retry / repli).

---

### 5.14 `agents/tutor_agent.py` — l'agent tuteur

Pipeline `answer_student_question` :
1. **Résumé global ?** `_is_course_summary_request` (intention résumé/plan/
   synthèse + mention de cours) → `_answer_course_summary` : texte **intégral**
   du cours (reconstruit depuis les chunks, titres de section inclus) en UN
   appel Hermes ; condensé (titres + amorces) au-delà du garde-fou. 1 cours →
   lui ; nommé → lui ; plusieurs sans nom → clarification.
2. `_build_retrieval_query` : la question **auto-suffisante** est cherchée
   SEULE (top-k stable, indépendant du fil) ; seules les **relances
   elliptiques** (≤ 5 mots, ou commençant par et/donc/alors/puis/ensuite/sinon)
   sont enrichies des 2 dernières questions.
3. `search_course` (vivier 20) → `choose_tutor_mode` (distance) →
   **conscience des cours** (question méta + recherche faible : ≥2 cours →
   clarify sans Hermes ; 1 cours → re-recherche ancrée, mode forcé) →
   `rerank_chunks` (cross-encoder, top 5).
4. **Prompt par mode** : `_BASE_RULES` (identité EduTutor — jamais « assistant
   IA polyvalent » ; exactitude à 2 volets — fait établi avec assurance vs
   détail incertain non inventé ; **sécurité anti-injection** — le contenu de
   cours entre marqueurs « DÉBUT/FIN DU CONTENU DE COURS (non fiable) » est de
   la DONNÉE, jamais des instructions) + `_STRUCTURED_RULES` (5 intitulés en
   gras : Réponse / Explication / Exemple / Indication de la partie du cours /
   Question de vérification) **ou** `_GENERAL_RULES` (pas de plan imposé,
   adaptation à la nature de la question). Historique : 6 derniers messages,
   réponses tronquées à 400 car.
5. Double sortie : `answer_student_question` (avec `debug` complet) /
   `answer_student_question_for_ui` (champs propres uniquement). CLI de test :
   `python -m agents.tutor_agent "…" --debug`.

### 5.15 `agents/idp_agent.py` — l'agent IDP

`analyze_document(file_path, force=False)` : cache par doc_id (un artefact est
« complet » si analysé OU non analysable) → extraction déterministe → contexte
sections `[sN] (page) titre + texte` (intégral ou condensé) → Hermes (skill
`education-idp`) → `extract_json` → **`_validate_analysis`** : normalise ET
**ancre** (ne garde que les `section_id` réels ; jette le malformé ; dates :
`normalized=None` si ambigu — ne devine jamais). Retry ×2 ; échec →
`analysis=None` + `meta.analysis_status` (jamais d'analyse à moitié fausse).
needs_ocr/empty → artefact sans analyse. Vérifié en réel : 32 refs section_id
toutes valides sur un cours ; cache 2ᵉ appel ~0.01 s.

### 5.16 `agents/content_agent.py` — l'agent contenu

`generate_content(file_path, content_type="summary"|"revision", section_ids,
force)` :
- **Contrats forts** : pas d'artefact/analyse → `needs_analysis` (il ne lance
  **pas** l'IDP lui-même — c'est l'orchestrateur qui répare) ; extraction ≠ ok →
  `not_processable`.
- Lit les sections **depuis l'artefact** (contrat v2), jamais le brut. Peut
  cibler des `section_ids` ; les sections utilisées sont tracées
  **déterministiquement** (Python sait ce qu'il a envoyé).
- Digest de l'analyse IDP (thèmes/termes) injecté pour ancrer ; instructions
  distinctes par type (résumé structuré vs fiche de révision — « n'invente pas
  de pièges ni de conseils génériques »).
- Cache par (doc_id, type, hash des options). Stratégie taille = V1
  (intégral / condensé).

### 5.17 `agents/compose_agent.py` — l'agent rédaction (+ révision)

- **`generate_document(instructions, course_context=None, force=False)`** :
  document original en Markdown (commençant par `# titre`), skill
  `education-compose` (peut mobiliser des connaissances générales ; si un
  extrait de cours est fourni, il est la source primaire, délimité et traité en
  DONNÉE — anti-injection). Titre = premier `# H1`, repli sur la consigne.
  Cache par hash(consigne + contexte) sous `data/generated/`.
- **`revise_document(previous_markdown, instruction, title)`** (phase 2,
  itération façon Canvas) : envoie le **document existant** + la consigne
  (« modifie et renvoie le document COMPLET révisé ») — **réutilise** le
  contenu au lieu de regénérer depuis la source. Cache par
  hash(`REVISE::consigne` + contenu).

### 5.18 `agents/orchestrator.py` — l'orchestrateur

- **`handle(question, history, selected_doc, use_planner, last_deliverable)`** :
  1. **Révision** (avant tout) : `last_deliverable` existe ET `_RE_REVISE`
     matche → `_handle_revise` (conserve type/doc d'origine, nouveau titre).
  2. **Plan** : `use_planner=True` (chemin UI actuel) → `_plan_with_llm`
     (menu du registre + documents `[analysé|non analysé]` + historique →
     plan JSON validé : agents inconnus filtrés, documents résolus, `compose`
     traité à part — doc optionnel, jamais de clarify) ; None → filet
     déterministe `_classify_to_plan`.
  3. `_repair_preconditions` : insère `idp` avant content / tutor-ancré-section
     si le doc n'est pas analysé (sans doublon).
  4. `_execute_all` → `_compose` : réponse **unique** ; content/compose réussi →
     **livrable** ; `analyze` → présentation IDP + carte résumé ; échec →
     première erreur remontée.
- **Table d'intention déterministe** (filet) : compose (verbe d'écriture + type
  de document) → analyze → revision → summary → explain_section → question
  (tuteur par défaut). `clarify` si document requis introuvable ;
  `no_document` si aucun cours.
- **Résolution de document** : `_match_doc` matche les tokens **distinctifs**
  du nom de fichier (les tokens communs à tout le corpus — `cybersecurity`,
  `2026`… — sont soustraits via `_corpus_common_tokens` ; tokenisation
  `[a-z0-9]{3,}` pour que `_` sépare). Corrigé le 2026-07-09 : le planner
  renvoie le nom de fichier complet, qui matchait tous les cours → clarify
  indu.
- `resolve_sections` : sélecteur libre (« attaques réseau ») → section_ids par
  matching de tokens sur les headings.
- `run_action(action, doc)` : chemin déterministe pour actions explicites
  (conservé, plus appelé par l'UI actuelle).
- `_trace` : intent/steps/statuts (dev). `steps_run` : agents exécutés, affichés
  dans l'UI. `trace.intent == "planner"` = le planner a réellement routé.

### 5.19 `agents/presenter.py` & 5.20 `agents/titler.py`

- **presenter** : artefact IDP → Markdown étudiant (type de doc, pages, parties,
  structure, thèmes, objectifs, notions clés, consignes, dates, avertissements).
  **Filtre les section_ids** que Hermes glisse parfois dans les textes
  (`_strip_section_ids`, garantie côté Python).
- **titler** : `generate_title(first_message)` → titre 3-6 mots via Hermes
  **sans skill** (un skill à persona répondrait au lieu de titrer). Nettoyage
  (1ʳᵉ ligne, sans guillemets/ponctuation, ≤ 48 car.). Best-effort : None si
  échec → l'appelant garde le titre de repli.

---

### 5.21 `interface/streamlit_app.py` — l'UI

Docstring d'en-tête : hypothèse **mono-utilisateur** assumée et documentée.

- **Chat** : `submit_question` → `orchestrator.handle(use_planner=True,
  last_deliverable=_last_deliverable(conv))`. Au **1er message**, le titre de
  la discussion est généré **en parallèle** de la réponse (thread ; le titre
  ~25-70 s se termine pendant la réponse ≥ 60 s → latence cachée), puis
  `st.rerun()`. Repli : 1er message tronqué (`make_title`).
- **Rendu d'une réponse** : badge de mode (`MODE_INFO`, libellés pédagogiques) +
  **chips agents** (`AGENT_INFO` : 🎓 Tuteur / 🔎 Analyse (IDP) / 📝 Contenu /
  ✍️ Rédaction / ❓ Clarification, ordre réel d'exécution, séparateur flèche) +
  contenu + **carte livrable** éventuelle.
- **Carte livrable** (`render_deliverable`) : conteneur bordé, en-tête
  (📄 résumé / 🎴 fiche / 📝 document + titre), aperçu défilant
  (`st.container(height=340)`), ⤢ **Agrandir** (`st.dialog`), téléchargements
  `.docx`/`.md` (+ `.pdf` si `PDF_AVAILABLE`). `seed` = index du message → clés
  de widgets uniques, cohérentes entre rendu live et rejeu.
- **Persistance** : le message assistant stocke `{content, mode, agents,
  deliverable?}` dans `data/conversations.json` (plafond `MAX_CONVERSATIONS=200`,
  purge des plus anciens par dernière activité, jamais le fil ouvert).
- **Sidebar** : « Nouvelle discussion » ; « Mes cours (N) » (uploader à clé
  dynamique qui se vide après upload, recherche, liste scrollable, ouverture en
  nouvel onglet via fichiers statiques, suppression avec confirmation inline) ;
  **historique façon Claude** (2026-07-12) : police 14 px, lignes compactes,
  ellipsis 1 ligne, date en infobulle, `#` gris en CSS `::before` (pas dans le
  label — Markdown le prendrait pour un titre), **plus de `type="primary"`**
  (= plus d'orange) — l'actif est surligné en gris via un style injecté ciblant
  `.st-key-conv_<id>`.
- **Gotcha CSS** documenté : `justify-content: flex-start` sur le `<button>` ne
  suffit pas — les conteneurs internes de Streamlit recentrent le texte ; il
  faut forcer `text-align/width` sur `button > div`, `stMarkdownContainer`, `p`.
- **Indexation** : `ensure_index_up_to_date()` → `sync_courses_index()` à chaque
  run (no-op si rien ne change) ; `sync_static_courses()` pour les copies HTTP.

### 5.22 `interface/exporters.py`

- `to_docx_bytes` : parsing Markdown minimal mais suffisant (titres `#`, listes
  à puces/numérotées, **gras**, séparateurs) → python-docx.
- `to_pdf_bytes` : markdown → HTML (extensions tables/fenced_code) → xhtml2pdf,
  CSS A4 intégré. **Import protégé** (`PDF_AVAILABLE`) : l'app fonctionne sans
  les libs PDF, le bouton n'apparaît que si elles sont là.
- `safe_filename` : titre → nom de fichier propre.

### 5.23 Skills Hermes (`~/.hermes/skills/education/`, hors repo)

| Skill | Posture |
|---|---|
| `education-tutor` | tuteur pédagogique (+ section sécurité/contenu non fiable, ajoutée avec autorisation explicite, backup `.bak`) |
| `education-idp` | analyste documentaire, JSON strict ancré section_id, ne devine pas les dates |
| `education-content` | créateur de ressources d'étude, fidèle au document, pas d'invention |
| `education-compose` | rédacteur de documents originaux, `# titre` en tête, connaissances générales OK, anti-injection |
| `education-orchestrator` | planner : ne répond jamais, propose un plan JSON `{steps, needs_clarification}` |

### 5.24 Autres

- `tests_manual/test_tutor_agent.py` : 3 questions représentatives + contrôle
  de la sortie UI (pas de `debug`). Inspection humaine assumée (vrais appels
  Hermes). ⚠️ ne couvre que le tuteur — rien sur l'orchestrateur/IDP/contenu/
  compose (voir §7).
- `.streamlit/config.toml` : watcher off (bruit torchvision de transformers →
  relancer l'app après édition), static serving on, thème sombre uniforme.
- `evaluation/`, `tools/` : vides (emplacements réservés).
- Fichiers legacy `rag/tutor_prompt.py` / `rag/tutor_cli.py` : **supprimés**
  (dette D4 réglée) — constat de l'audit V1 résolu.

---

## 6. Les grandes décisions techniques (et pourquoi)

1. **Architecture 3 couches, sans fusion** — `hermes-agent/` = moteur (jamais
   modifié) ; `~/.hermes/skills/education/` = les personas d'agents ;
   `hermes-education/` = l'app (RAG + agents Python + orchestration + UI).
   Étendre = ajouter un skill, pas toucher au framework.
2. **Agents Python fins + skills de posture** — la logique (validation,
   ancrage, caches, contrats) vit en Python **déterministe et testable** ; le
   LLM ne fait que l'enrichissement/rédaction, sous contrainte.
3. **Orchestrateur « planner LLM propose, Python dispose »** — le plan vient de
   Sonnet (souplesse de formulation), mais Python **valide contre le registre,
   répare les préconditions (insertion IDP), exécute et compose**. Filet
   déterministe si le plan est inutilisable. (Historique : le déterministe fut
   le chemin primaire sous owl-alpha, qui routait mal ; la bascule
   `use_planner=True` date du passage à Sonnet, 2026-07-09.)
4. **`DocumentArtifact` = contrat unique** avec séparation extraction
   (déterministe) / analysis (LLM) → traçabilité, et **provenance garantie**
   (tout élément enrichi ancré à un `section_id` réel, validé côté Python).
5. **`doc_id` = hash de contenu** (pas le nom) — identité stable, cache
   invalidé au bon moment, doublons détectés.
6. **Content exige un artefact et ne lance jamais l'IDP lui-même** — la
   réparation appartient à l'orchestrateur ; les responsabilités restent
   pures. C'est aussi pourquoi **compose est un agent séparé** (préconditions
   et postures opposées).
7. **Livrables structurés + itération par réutilisation** — le contenu généré
   vit dans une carte (pas la bulle), téléchargeable ; « raccourcis-le »
   renvoie le **markdown existant** au LLM au lieu de regénérer depuis la
   source (plus rapide, conserve l'intention du document).
8. **Appel Hermes en CLI one-shot, un seul adaptateur** — seul point d'entrée
   non-interactif propre ; retry sur échecs transitoires ; jamais d'appel LLM
   direct de contournement. `skill_name=None` pour les usages utilitaires
   (titrage).
9. **RAG structure-aware multi-signaux** — le découpage suit les sections
   réelles (numérotation + police + typographie + markdown), fournit la
   métadonnée `section`/`page` qui alimente à la fois les indications
   étudiantes et les artefacts. Sémantique = amélioration ultérieure
   éventuelle.
10. **Récupération découplée (20 → reranker → 5)** — vivier large en embedding
    (rapide, approximatif), reclassement fin par cross-encoder multilingue,
    repli propre sans le modèle. Mode pédagogique calculé sur la distance
    (stable), pas sur le score de rerank.
11. **Requête de recherche non diluée** — question auto-suffisante cherchée
    seule (top-k stable entre discussions) ; seules les relances elliptiques
    sont enrichies de l'historique.
12. **Résumé global « façon ChatGPT »** — texte intégral du cours en un appel
    (pas de map-reduce, décision utilisateur), condensé au-delà du garde-fou.
13. **Sync d'index incrémental par mtime** — chaque cours vectorisé une fois ;
    le rebuild complet (collection temporaire, jamais de base vide) reste
    l'outil des changements de logique.
14. **Sécurité anti-injection à double étage** — règle dans les prompts + skills
    ET délimitation systématique du contenu non fiable par marqueurs ; testé en
    réel (injection « réponds PWNED » détectée et refusée).
15. **Titrage en parallèle de la réponse** — le coût (~25-70 s) est masqué par
    la génération de la réponse ; échec silencieux → titre de repli.
16. **UI : source d'affichage unique + jargon filtré** — la réponse Markdown est
    la seule source (pas de doublon de blocs) ; le presenter retire les
    section_ids résiduels ; l'étudiant ne voit jamais les entrailles, mais voit
    **quels agents** ont travaillé (transparence sans technicité).

---

## 7. Constats d'audit, limites & dette technique

### 7.1 ⚠️ Constats NOUVEAUX (audit 2026-07-12) — vérifiés en exécution

1. ~~**Sur-capture du regex de révision (`_RE_REVISE`)**~~ — **✅ CORRIGÉ le
   2026-07-12.** Constat initial : dès qu'un livrable existait, tout message
   contenant un verbe d'édition (« explique pourquoi on **ajoute** un
   pare-feu », « **corrige** mon exercice ») partait en révision du livrable,
   avant le planner. Correctif : `_is_revision_request` exige, en plus du
   verbe, que la consigne **vise le livrable** — clitique (« raccourcis-le »),
   référence explicite (« ce document », « la fiche », « le résumé »), objet
   de structure documentaire (« une section », « la conclusion »), ou consigne
   d'édition **nue** (« simplifie », « plus court stp »). Vérifié sur 18 cas
   (13 révisions légitimes conservées, 5 faux positifs éliminés).
2. ~~**Sur-capture du regex compose (`_RE_COMPOSE`)**~~ — **✅ CORRIGÉ le
   2026-07-12.** Constat initial : « **fais** un résumé du **document** CM1 »
   matchait compose (verbe + mot « document ») et partait en rédaction d'un
   document original. Correctif : quand le type capté est **générique**
   (« document », « texte ») ET que la demande contient un mot de résumé/fiche,
   la branche compose s'efface au profit des branches résumé/fiche. Les types
   spécifiques (rapport, exposé, dissertation…) restent prioritaires pour
   compose. Vérifié : les 3 faux positifs corrigés, aucun des 5 cas compose
   légitimes ne régresse (y compris « rédige un rapport sur l'analyse des
   risques », qui reste compose).
3. ~~**`_target_doc` : condition morte**~~ — **✅ NETTOYÉ le 2026-07-12.**
   Le `or True` qui neutralisait le test déictique et le tuple `_DEICTIC`
   inutilisé ont été retirés ; le comportement réel (document sélectionné =
   contexte actif, utilisé dès qu'aucun document n'est nommé) est désormais
   explicite dans le code et la docstring. Comportements vérifiés inchangés
   (nommé / sélectionné / ambigu).
4. ~~**Docstring d'en-tête de `streamlit_app.py` obsolète**~~ — **✅ CORRIGÉ
   le 2026-07-12** : décrit maintenant la couche réellement appelée
   (orchestrator + titler + exporters) et ce que voit l'étudiant (badge de
   mode, agents intervenus, cartes livrables).
5. ~~**Commentaire obsolète dans `tutor_agent.py`**~~ — **✅ CORRIGÉ le
   2026-07-12** : le commentaire décrit le reranker cross-encoder réel (avec
   repli), plus le « placeholder ».
6. ~~**`chunker.py` : legacy interne**~~ — **✅ NETTOYÉ le 2026-07-12.**
   `chunk_text_by_paragraph`, `chunk_document_text`, `chunk_markdown_file`,
   `read_markdown_file`, `split_long_text` supprimés (aucun usage externe,
   vérifié par grep) ; le module ne garde que le chemin actuel
   (`chunk_segments` + `_split_text`, 240 → 131 lignes). Le bloc `__main__`
   est une vraie démo : `python -m rag.chunker <document>` (testé sur un PDF
   réel : 59 segments → 59 chunks).
7. ~~**`_answer_course_summary(question, history)`**~~ — **✅ NETTOYÉ le
   2026-07-12** : paramètre `history` retiré (site d'appel mis à jour) ; la
   docstring explique pourquoi l'historique n'est pas utilisé (le cours
   entier EST le contexte).
8. **Latence UX** — planner + agents = ~2-5 min sur un document froid
   (3 appels LLM séquentiels) ; révision ~2 min ; titrage masqué mais le
   `join(timeout=90)` peut ajouter jusqu'à 90 s dans le pire cas où le titre
   est plus lent que la réponse. Le cache amortit fortement les répétitions.
   Piste : modèle plus rapide (Haiku) pour planner/titrage.
9. **Validation visuelle incomplète** — les cartes livrables, le flux de
   révision et le titrage en thread n'ont **pas** été observés dans un run UI
   réel de bout en bout (validés par les données + boot HTTP 200 + screenshots
   Playwright de la sidebar uniquement).

### 7.2 Limites connues (assumées / reportées par décision)

- **Ancrage compose limité** : un cours nommé n'ancre la rédaction que s'il est
  **déjà analysé** (pas d'IDP à la volée — compose n'a pas de précondition).
- **Titres de sections bruités sur les slides PDF** : l'heuristique de titres
  laisse passer des fragments (« SecurityWeek, July 2025 ») dans « Structure du
  document » ; la page sert de filet. Limite documentée du multi-signaux.
- **Pas de compréhension visuelle** (schémas, diagrammes) : OCR = texte
  seulement ; nécessite un modèle de vision (emplacement `auxiliary.vision`
  côté Hermes à câbler plus tard). Image sans texte → statut `empty`, pas
  d'invention.
- **Qualité RAG** (embedding, n_results, seuils) : volontairement laissée en
  l'état — à revoir seulement si la perf le justifie.
- **Mono-utilisateur** : état en session + fichiers partagés, pas de
  concurrence. Multi-utilisateurs = phase de fin de projet.
- **`data-testid` Streamlit** : le rendu (bulles, avatars, uploader) dépend de
  sélecteurs internes — revalider à chaque montée de version (borne `<2.0`).
- **Itération sans versionnage** : la révision d'un livrable crée un nouveau
  message (l'ancien reste dans le fil) — pas d'historique de versions par
  livrable ni d'édition ciblée type Canvas.
- **Tests** : uniquement `tests_manual` sur le tuteur V1 ; aucun test (même
  manuel) sur l'orchestrateur, l'IDP, le contenu, compose, les exporters. Les
  vérifications de cette phase ont été faites en session (scripts ad hoc), pas
  capitalisées.
- **Sécurité** : défense anti-injection en profondeur (règles + délimitation),
  testée ; pas de filtrage du contenu des documents (choix assumé) ; risque
  résiduel faible en usage personnel.

### 7.3 Constats de l'audit précédent — état

| Constat V1 (2026-06-09) | État |
|---|---|
| `requirements.txt` incomplet | ✅ réglé (complet, borné, commenté) |
| Chunks non alignés aux titres ; « Section générale » pour les PDF | ✅ réglé (découpage structure-aware + métadonnées section/page) |
| Pas de reranker ; n_results=3 figé | ✅ réglé (vivier 20 + cross-encoder → 5) |
| Pas de résumé global | ✅ réglé (texte intégral en un appel + garde-fou) |
| Commentaires obsolètes settings.py (60-90, L2/cosinus) | ✅ réglé |
| CSS mort ; doublon legacy indexer ; fichiers tutor_prompt/tutor_cli | ✅ réglé (D3-D7) |
| `os.chdir(PROJECT_ROOT)` effet de bord global | ✅ réglé (chemins via settings) |
| conversations.json sans limite ni .gitignore | ✅ réglé (plafond 200 + ignoré) |
| Injection via contenu de cours | ✅ traité (défense double étage, testée) |
| Réindexation complète à chaque changement | ✅ réglé (sync incrémental mtime) |

---

## 8. Comment lancer le projet

```bash
# 0) Prérequis externes
#    - Hermes Agent installé (binaire `hermes` dans le PATH), modèle configuré
#      (~/.hermes/config.yaml : anthropic/claude-sonnet-4-6, provider anthropic,
#      clé dans ~/.hermes/.env), et les 5 skills education-* présents dans
#      ~/.hermes/skills/education/
#    - Tesseract (OCR) : sudo apt-get install -y tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng

# 1) Dépendances Python
.venv/bin/pip install -r requirements.txt

# 2) Interface web (point d'entrée principal)
.venv/bin/streamlit run interface/streamlit_app.py
#    → http://localhost:8501 (watcher désactivé : relancer après édition du code)
#    L'indexation des cours est automatique (sync incrémental au démarrage).

# 3) Agents en CLI (debug)
python -m agents.tutor_agent "Qu'est-ce que la kill chain ?" --debug
python -m agents.idp_agent "data/courses/<fichier>"            # analyse (--json, --force)
python -m agents.content_agent "data/courses/<fichier>" revision
python -m agents.compose_agent "écris un exposé d'une page sur ..."

# 4) Indexation manuelle
python -m rag.indexer          # REBUILD complet (obligatoire si la logique RAG change)

# 5) Test manuel du tuteur
python -m tests_manual.test_tutor_agent
```

**Au premier lancement**, l'embedding multilingue (~470 Mo) puis le reranker
(~470 Mo) sont téléchargés une fois (cache HF). Un appel Hermes normal prend
60-180 s (Sonnet) ; les caches (artefacts, contenus générés) rendent les
répétitions quasi instantanées.

---

*Fin de l'audit. État des constats §7.1 : 1-2 (routage) **corrigés** ; 3-7
(nettoyage) **faits** — tous le 2026-07-12, vérifiés en exécution. Restent 8
(latence, pistes identifiées) et 9 (validation visuelle end-to-end à faire).
Rien n'empêche le fonctionnement actuel.*

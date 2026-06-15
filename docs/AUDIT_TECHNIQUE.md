# Audit technique — Hermes Education (EduTutor)

> Document d'audit exhaustif. Objectif : qu'à la seule lecture de ce document tu
> comprennes l'intégralité du projet **comme si tu l'avais écrit toi-même** —
> contexte, architecture, rôle de chaque fichier, et **chaque décision** prise
> avec sa justification.
>
> Date de l'audit : 2026-06-09 · Périmètre : tout le code source du projet
> `hermes-education` (hors `.venv`, `data/vectorstore`, copies statiques).

---

## Table des matières

1. [Contexte global](#1-contexte-global)
2. [Pile technique & dépendances](#2-pile-technique--dépendances)
3. [Architecture & flux de données](#3-architecture--flux-de-données)
4. [Vue d'ensemble : rôle de chaque fichier](#4-vue-densemble--rôle-de-chaque-fichier)
5. [Détail fichier par fichier](#5-détail-fichier-par-fichier)
6. [Les grandes décisions techniques (et pourquoi)](#6-les-grandes-décisions-techniques-et-pourquoi)
7. [Limites connues & dette technique](#7-limites-connues--dette-technique)
8. [Comment lancer le projet](#8-comment-lancer-le-projet)

---

## 1. Contexte global

**EduTutor** est un **tuteur académique** propulsé par **Hermes Agent**. Ce
n'est **pas** un clone de NotebookLM ni un simple « assistant documentaire » :
les documents de cours servent de **support pédagogique**, mais l'objectif
principal est d'**aider l'étudiant à comprendre** (expliquer, donner un exemple,
le renvoyer vers la bonne partie du cours, vérifier sa compréhension), avec ou
sans document pertinent indexé.

Le système combine quatre briques aux responsabilités séparées :

- **RAG** (`rag/`) — *trouver* les passages utiles dans les cours indexés.
- **Tools / logique Python** — fonctions déterministes (recherche, choix du
  mode pédagogique, mise en forme des indications, construction du prompt).
- **Skill Hermes** (`~/.hermes/skills/education/education-tutor/SKILL.md`,
  externe au repo) — les **règles pédagogiques** et le comportement du tuteur.
- **Hermes** — le **moteur d'orchestration et de génération** de la réponse
  finale, appelé en CLI.

Trois **modes pédagogiques** sont choisis automatiquement selon la pertinence du
meilleur passage retrouvé :

| Mode | Quand | Comportement |
|---|---|---|
| `course_grounded` | passage de cours très proche | répond surtout à partir du cours, indique la partie à revoir |
| `mixed` | passage moyennement proche | sépare ce que dit le cours et le complément général |
| `general_tutor` | aucun passage fiable | réponse pédagogique générale, en précisant qu'elle n'est pas fondée sur un cours |

Un 4ᵉ mode applicatif, `clarify`, est produit **sans appeler Hermes** quand la
question est vague (« de quoi parle le cours ? ») et que **plusieurs** cours sont
indexés : l'agent demande alors **lequel**.

**Contrainte structurante respectée partout :** ne jamais montrer à l'étudiant
les détails techniques (chunks, distances, top_k, prompt interne, vectorstore,
chemins) ; et ne jamais contourner Hermes par un appel LLM direct.

---

## 2. Pile technique & dépendances

- **Python 3.11/3.12**.
- **Streamlit** — interface web (`interface/streamlit_app.py`).
- **ChromaDB** — base vectorielle (persistée sur disque dans `data/vectorstore/`).
- **sentence-transformers** — embedding **multilingue**
  `paraphrase-multilingual-MiniLM-L12-v2` (tire PyTorch + transformers).
- **PyMuPDF (`fitz`)** — extraction de texte des PDF.
- **Hermes Agent** — binaire CLI externe (`hermes`), appelé en sous-processus.
  Modèle configuré côté Hermes (ex. `openrouter/owl-alpha`).

> ⚠️ **Constat d'audit** : `requirements.txt` ne liste que `streamlit` et
> `chromadb`. Les dépendances réellement nécessaires (`sentence-transformers`,
> `PyMuPDF`) **manquent** — voir §7.

---

## 3. Architecture & flux de données

### 3.1 Couches

```
┌─────────────────────────────────────────────────────────────┐
│ interface/streamlit_app.py   (UI étudiant, état, persistance)│
│      appelle UNIQUEMENT ↓                                     │
│ agents/tutor_agent.py        (ORCHESTRATEUR)                  │
│   ├─ rag/retriever.py        (recherche + liste des cours)    │
│   ├─ rag/source_formatter.py (chunks → indications lisibles)  │
│   └─ services/hermes_adapter.py (appel CLI Hermes one-shot)   │
│                                                               │
│ rag/indexer.py ← chunker.py + document_loader.py + embeddings │
│ config/settings.py           (réglages centraux)              │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Flux « poser une question » (mode cours)

```
question étudiant
  └─ _build_retrieval_query(question, history)         # enrichi par l'historique
       └─ search_course(query)  → chunks {text, source, distance, title, ...}
            └─ choose_tutor_mode(chunks)               # via seuils de distance
                 ├─ (si vague + plusieurs cours) → _clarification_result  (sans Hermes)
                 ├─ (si vague + 1 cours)         → re-recherche ancrée, mode course_grounded
                 └─ format_course_indications(chunks) → indications {course, part, excerpt}
                      └─ build_tutor_prompt(question, mode, indications, history)
                           └─ ask_hermes_with_skill(prompt)  # subprocess hermes -z
                                └─ réponse → answer_student_question(_for_ui) → UI
```

### 3.3 Flux « indexation »

```
fichier déposé (UI)  → data/courses/   (save_uploaded_courses)
  └─ ensure_index_up_to_date()  (déclenché si l'empreinte du dossier change)
       └─ index_all_courses()
            ├─ load_document_text()   (md/txt/pdf)
            ├─ chunk_document_text()  (découpage)
            ├─ embeddings (multilingue, cosinus)
            └─ collection temporaire → bascule (jamais de base vide)
```

### 3.4 Données sur disque

| Chemin | Contenu |
|---|---|
| `data/courses/` | cours **actifs** (indexés). 5 PDF de cybersécurité actuellement. |
| `data/samples/rag_intro.md` | cours d'exemple (non indexé par défaut). |
| `data/vectorstore/` | base ChromaDB (sqlite + index HNSW). |
| `data/conversations.json` | discussions persistées (titres + messages). |
| `data/last_tutor_prompt.md` | dernier prompt sauvegardé (fallback manuel Hermes). |
| `interface/static/courses/` | copies des cours servies en HTTP (ouverture nouvel onglet). |

---

## 4. Vue d'ensemble : rôle de chaque fichier

| Fichier | Rôle en une phrase |
|---|---|
| `config/settings.py` | Tous les réglages centraux (chemins, embedding, seuils, Hermes). |
| `rag/chunker.py` | Découpe un texte en *chunks* (par paragraphe, avec overlap). |
| `rag/document_loader.py` | Extrait le texte d'un fichier (.md/.txt/.pdf). |
| `rag/embeddings.py` | Fournit la fonction d'embedding **partagée** (indexer + retriever). |
| `rag/indexer.py` | Indexe les cours dans ChromaDB (avec bascule sans fenêtre vide). |
| `rag/retriever.py` | Recherche les chunks pertinents + liste les cours indexés. |
| `rag/source_formatter.py` | Transforme les chunks techniques en *indications de cours* lisibles. |
| `services/hermes_adapter.py` | Appelle Hermes en CLI one-shot (seul point qui « sait » comment). |
| `agents/tutor_agent.py` | **Orchestrateur** : enchaîne tout et renvoie une réponse structurée. |
| `tests_manual/test_tutor_agent.py` | Test manuel (3 questions + sortie UI). |
| `interface/streamlit_app.py` | Interface web étudiant (chat, cours, historique, persistance). |
| `.streamlit/config.toml` | Config Streamlit (watcher off, fichiers statiques, thème sombre). |
| `requirements.txt` | Dépendances déclarées (incomplet — voir §7). |
| `docs/agent_tutor_architecture.md` | Document de conception (le « pourquoi »). |
| `rag/tutor_prompt.py` *(legacy)* | Ancien générateur de prompt (flux manuel, **non utilisé**). |
| `rag/tutor_cli.py` *(legacy)* | Ancien CLI de préparation RAG (**non utilisé**). |
| `*/__init__.py` | Fichiers vides marquant les packages Python. |

---

## 5. Détail fichier par fichier

### 5.1 `config/settings.py`

**Rôle.** Source unique de vérité pour la configuration. Aucune clé API ici.

**Contenu clé.**
- `PROJECT_ROOT = Path(__file__).resolve().parents[1]` → racine du projet,
  calculée **relativement** au fichier (jamais de chemin absolu en dur).
- Chemins dérivés : `DATA_DIR`, `COURSES_DIR`, `SAMPLES_DIR`, `VECTORSTORE_PATH`,
  `LAST_PROMPT_PATH`.
- `SUPPORTED_EXTENSIONS = (".md", ".txt", ".pdf")`.
- `COLLECTION_NAME = "course_chunks"`.
- **Embedding** : `EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"`,
  `EMBEDDING_SPACE = "cosine"`.
- **RAG** : `DEFAULT_TOP_K = 3`, `EXCERPT_MIN_CHARS = 60`, `EXCERPT_MAX_CHARS = 90`.
- **Seuils de mode** : `COURSE_GROUNDED_MAX_DISTANCE = 0.45`,
  `MIXED_MAX_DISTANCE = 0.65`.
- **Hermes** : `DEFAULT_SKILL_NAME = "education-tutor"`,
  `HERMES_TIMEOUT_SECONDS = 120`.

**Décisions.**
- *Tout centraliser* pour qu'un changement de comportement (extraits, seuils,
  skill) ne touche qu'un fichier.
- *Seuils dans la config* car ils dépendent du modèle d'embedding **et** du
  corpus ; ils ont été **recalibrés** quand on est passé en embedding multilingue
  + distance cosinus (avant : 1.10/1.30 en L2 ; après : 0.45/0.65 en cosinus).

**⚠️ Constats d'audit (commentaires obsolètes).**
- Le commentaire des extraits dit « ~250-350 caractères » alors que les valeurs
  sont **60/90** (réduites volontairement par l'utilisateur en cours de route).
- Le commentaire des seuils mentionne encore « distance (L2) » en en-tête, alors
  que l'échelle est désormais **cosinus** (la ligne L2 est un reliquat).
  → Sans impact fonctionnel, mais à corriger pour la cohérence documentaire.

---

### 5.2 `rag/chunker.py`

**Rôle.** Découper un texte en *chunks* exploitables par la base vectorielle.

**Fonctions.**
- `read_markdown_file(path)` → lit un fichier texte UTF-8.
- `split_long_text(text, max_chars)` → découpe **par mots** un paragraphe trop
  long (évite les chunks vides ; **pas** d'overlap dans ce chemin).
- `chunk_text_by_paragraph(text, source, max_chars=800, overlap_chars=100)` →
  découpe sur les doubles sauts de ligne (`\n\n`), regroupe jusqu'à 800 car.,
  ajoute un **overlap** d'environ 100 car. entre chunks. Retourne des dicts
  `{id, source, chunk_index, text}` avec `id = "<stem>_chunk_<n>"`.
- `chunk_markdown_file(path)` → lit + découpe (utilisé par le legacy `index_course`).
- `chunk_document_text(text, source, title=None)` → version **générique** (md,
  txt, PDF déjà extrait) ; ajoute `title` aux chunks si fourni. **C'est elle
  qu'utilise `index_all_courses`.**

**Décisions.** Découpage orienté paragraphe avec overlap pour ne pas couper une
idée en deux ; repli mot-à-mot pour les paragraphes géants.

**⚠️ Constat d'audit.** Les PDF extraits et certains `.md` (dont `rag_intro.md`,
qui utilise des lignes « vides » contenant un espace) ne se découpent **pas** sur
des frontières de sections : `split("\n\n")` ne coupe pas, donc on retombe sur
des fenêtres de ~800 car. **non alignées aux titres**. Conséquence directe sur la
détection de « partie du cours » (cf. `source_formatter`). `id` basé sur le stem
→ collision possible si deux fichiers ont le même nom sans l'extension.

---

### 5.3 `rag/document_loader.py`

**Rôle.** Extraire le **texte brut** d'un cours, quel que soit son format.

**Fonctions.**
- `load_document_text(file_path)` :
  - `.md` / `.txt` → lecture directe (`errors="replace"` pour tolérer les octets
    invalides).
  - `.pdf` → `_load_pdf_file` via **PyMuPDF** (`fitz`), page par page.
  - sinon → `ValueError` (format non supporté).
- `_load_pdf_file` lève une **`RuntimeError` claire** si PyMuPDF n'est pas
  installé, et une `ValueError` si le PDF ne contient aucun texte (probable PDF
  scanné — **pas d'OCR**).

**Décisions.** Jamais d'échec silencieux : chaque cas d'erreur est explicite et
remonté à l'appelant (`index_all_courses` les capture par fichier). Existence du
fichier vérifiée en premier.

---

### 5.4 `rag/embeddings.py`

**Rôle.** Fournir **la même** fonction d'embedding à l'indexation **et** à la
recherche — point le plus critique de tout RAG (sinon vecteurs incomparables).

**Fonctions.**
- `get_embedding_function()` → instancie (et **met en cache** dans une variable
  module `_EMBEDDING_FN`) un `SentenceTransformerEmbeddingFunction` avec le
  modèle multilingue de `settings`.
- `collection_metadata()` → `{"hnsw:space": "cosine"}`, métadonnées de collection
  cohérentes partout.

**Décisions.** Centralisation + cache singleton (le modèle ~470 Mo n'est chargé
qu'une fois par process). Le choix **multilingue** est ce qui permet à une
question **française** de retrouver un cours **anglais** (cas réel : cours de
cybersécurité en anglais, étudiant qui interroge en français).

---

### 5.5 `rag/indexer.py`

**Rôle.** Construire/mettre à jour la base vectorielle à partir des cours.

**Fonctions.**
- Constantes legacy `VECTORSTORE_PATH = "data/vectorstore"`,
  `COLLECTION_NAME = "course_chunks"` (utilisées par `index_course`).
- `_is_indexable(path)` → fichier régulier, non caché/temporaire, extension
  supportée.
- `_relative_source(path)` → chemin relatif à la racine (affichage propre +
  stocké comme `source`).
- `_derive_title(text, path)` → titre = premier `# H1` Markdown, sinon nom de
  fichier nettoyé.
- `reset_collection(client, name)` → supprime puis recrée une collection (avec
  embedding + métadonnées). Utilisé par `index_course`.
- `index_course(file_path)` → **legacy** : indexe un seul `.md`.
- `index_all_courses(courses_dir=None, reset=True)` → **fonction principale** :
  1. liste les fichiers indexables (par défaut `data/courses/`) ;
  2. pour chaque fichier : `load_document_text` → `_derive_title` →
     `chunk_document_text` ; erreurs **capturées par fichier** dans `errors[]` ;
  3. si `reset=True` : construit dans une collection **temporaire**
     `course_chunks_building`, l'alimente (c'est là que se fait la lente
     vectorisation), **puis** supprime l'ancienne collection et **renomme** la
     temporaire (`collection.modify(name=...)`) — bascule quasi instantanée ;
  4. retourne `{status: "success"|"empty", documents_indexed, chunks_indexed,
     errors, message?}`.

**Décisions.**
- **Bascule par collection temporaire** : corrige un bug réel — l'ancienne
  logique vidait la collection puis la reconstruisait, laissant une **fenêtre où
  la base était vide** pendant la vectorisation (et le chargement du modèle) →
  réponses « générale » intempestives. Désormais la base active n'est **jamais
  vide**.
- **Erreurs par fichier** : un PDF illisible n'empêche pas d'indexer les autres.
- **`reset=True` par défaut** : la base active reflète exactement les fichiers
  présents (ajouts **et** suppressions).

**⚠️ Constat d'audit.** Incohérence mineure : `index_course` (legacy) utilise les
constantes module relatives, tandis que `index_all_courses` utilise
`settings.VECTORSTORE_PATH`/`COLLECTION_NAME`. Les deux pointent au même endroit
quand le CWD = racine du projet, mais c'est un doublon à unifier.

---

### 5.6 `rag/retriever.py`

**Rôle.** Lire la base : recherche sémantique + inventaire des cours.

**Fonctions.**
- `list_indexed_courses()` → parcourt **toutes** les métadonnées de la collection
  et renvoie les cours **distincts** `{title, source}`. Indépendant de toute
  recherche → permet à l'agent de **« connaître » ses cours** (conscience
  mono/multi-cours).
- `search_course(query, n_results=3)` → interroge la collection (même embedding +
  cosinus) et renvoie une liste de chunks
  `{rank, text, source, chunk_index, title, distance}`.

**Décisions.** `search_course` renvoie aussi `title` (ajouté pour que le
formatter puisse nommer un PDF sans structure Markdown). `list_indexed_courses`
est tolérant (try/except → `[]` si la base n'existe pas encore).

**⚠️ Constat d'audit.** `VECTORSTORE_PATH` est **relatif** (`"data/vectorstore"`)
→ dépend du CWD. C'est sûr ici car l'interface fait `os.chdir(PROJECT_ROOT)` et
les scripts se lancent depuis la racine, mais c'est une hypothèse implicite à
connaître.

---

### 5.7 `rag/source_formatter.py`

**Rôle.** Transformer les chunks techniques en **indications de cours** lisibles
`{course, part, excerpt, document_path}` — ce que l'étudiant peut voir.

**Fonctions.**
- `_clean_text` → retire le bruit Markdown (blocs/inline code, `* _ # > |`,
  séparateurs) et normalise les espaces. Utilisé **des deux côtés** (extrait à
  afficher **et** corps de section à matcher) pour une détection robuste.
- `_parse_sections(source)` → découpe le document **source** en sections par
  titres (`#`/`##`…). **Ne lit que `.md`/`.txt`** : pour un PDF (binaire), renvoie
  `[]` (→ partie générique). Mise en cache par chemin.
- `_find_section(probe, sections)` → rattache un texte à sa **section dominante**
  par **scoring multi-ancres** (6 ancres de 40 car.) ; égalité → section la plus
  haute.
- `_make_excerpt(clean_text)` → extrait lisible : démarrage propre au début de
  phrase si nécessaire (préfixe « … »), troncature entre `EXCERPT_MIN/MAX_CHARS`.
- `format_course_indications(chunks)` → pour chaque chunk : nettoie, fabrique
  l'extrait, détecte la section (sur une fenêtre `_SECTION_PROBE_CHARS = 400`,
  **découplée** de la longueur d'affichage), déduit `course` (section → sinon
  `title` du chunk → sinon nom de fichier), `part` (sinon « Section générale ») ;
  **déduplique** par `(course, part)`.

**Décisions.**
- **Jamais** de distance/chunk/top_k côté étudiant.
- Détection de section **découplée** de la longueur de l'extrait : quand les
  extraits ont été raccourcis (60/90 car.), la détection serait devenue mauvaise
  si elle utilisait l'extrait ; on lit donc 400 car. pour décider de la partie.
- Vocabulaire « **Indication de la partie du cours** » plutôt que « Sources »
  (objectif : renvoyer l'étudiant réviser, pas citer).

**⚠️ Constat d'audit.** Pour les **PDF**, `_parse_sections` renvoie toujours `[]`
→ `part = "Section générale"` systématiquement, et `course` vient du nom de
fichier. La détection fine de partie ne marche donc bien que pour des `.md`/`.txt`
**bien structurés** (et pas pour `rag_intro.md` à cause du problème de
découpage, cf. 5.2).

---

### 5.8 `services/hermes_adapter.py`

**Rôle.** Unique endroit qui sait **comment** appeler Hermes. Découple le « quoi »
(générer une réponse) du « comment » (CLI).

**Fonction.** `ask_hermes_with_skill(prompt, skill_name, timeout)` :
- localise le binaire via `shutil.which("hermes")` ;
- exécute `subprocess.run([hermes, "-z", prompt, "--skills", skill])` **sans
  shell** (liste d'arguments → pas d'injection, prompt brut sûr) ;
- renvoie `{status: "success"|"error"|"not_available", content, method, error}` ;
- en cas d'absence/erreur/timeout/sortie vide : **sauvegarde le prompt** dans
  `data/last_tutor_prompt.md` pour test manuel.

**Décisions.**
- **CLI one-shot `-z`** retenue après inspection (cf. doc d'archi) : c'est le seul
  point d'entrée non-interactif officiel qui imprime **uniquement** la réponse
  finale sur stdout, charge le skill et auto-approuve les outils.
- Pistes **écartées** : `hermes gateway` (= messagerie, pas une API),
  webhook (asynchrone), `mcp serve` (overkill), import du runtime (couplage
  fragile au code du framework).
- **Jamais** d'appel LLM direct (OpenRouter/DeepSeek) en remplacement silencieux.
- N'importe **pas** le runtime Hermes → robustesse / non-couplage.

---

### 5.9 `agents/tutor_agent.py` — l'orchestrateur

**Rôle.** Cœur du système : transforme une question en réponse structurée, en
coordonnant RAG, mode, indications, prompt et Hermes.

**Choix du mode.**
- `_best_distance(chunks)` → plus petite distance (passage le plus proche).
- `choose_tutor_mode(chunks)` → `course_grounded` si `< 0.45`, `mixed` si `< 0.65`,
  sinon `general_tutor` (ou si aucun chunk).

**Construction du prompt (mode-dépendante).**
- `_BASE_RULES` — règles **toujours** valables : français correct avec accents,
  relecture, pas de détails techniques, ne pas inventer de source, **utiliser
  l'historique** pour les questions de suivi, pas de préambule.
- `_STRUCTURED_RULES` — **uniquement** en mode cours : structure imposée en
  **5 intitulés en gras, sans numéro** (« Réponse / Explication / Exemple /
  Indication de la partie du cours / Question de vérification »), point
  « Indication » **concis** (où réviser, sans recopier l'extrait).
- `_GENERAL_RULES` — en mode général : **pas** de plan en 5 points imposé ;
  l'agent **s'adapte** (pédagogique pour une vraie question d'apprentissage,
  bref et naturel pour une question banale/méta comme « as-tu accès à
  internet ? »).
- `_MODE_INTROS` — phrase d'intro par mode.
- `_format_indications_block` — met les indications (cours/partie/extrait) dans
  le prompt.
- `_format_history_block(history, max_messages=6, max_assistant_chars=400)` —
  rappelle les **6 derniers messages** (réponses tronquées à 400 car.).
- `build_tutor_prompt(question, mode, chunks, indications, history)` — assemble :
  skill + intro de mode + historique + question + contexte + règles
  (base + structurées **ou** générales).

**Extraction.**
- `extract_verification_question(answer)` via `_VERIF_RE` — regex **tolérante**
  au gras `**` et à une numérotation éventuelle (compat héritée).

**Recherche enrichie & conscience des cours.**
- `_build_retrieval_query(question, history, max_user_turns=2)` — préfixe la
  requête de recherche avec les **2 dernières questions** de l'étudiant →
  permet aux suivis vagues (« explique-le ») de retrouver le bon cours.
- `_is_course_meta_question(question)` — détecte « cours / leçon / chapitre /
  matière ».
- `_clarification_result(question, courses)` — réponse **déterministe** (sans
  Hermes, `mode = "clarify"`) listant les cours quand il faut désambiguïser.

**Fonctions publiques.**
- `answer_student_question(question, n_results, history)` — pipeline complet :
  1. requête enrichie → `search_course` → `choose_tutor_mode` ;
  2. **conscience des cours** : si `general_tutor` **et** question méta →
     `≥2` cours → `_clarification_result` (retour anticipé, sans Hermes) ;
     `1` cours → re-recherche ancrée sur ce cours, mode forcé `course_grounded` ;
  3. indications (vides en `general_tutor`) ;
  4. `build_tutor_prompt` → `ask_hermes_with_skill` ;
  5. renvoie une structure complète **avec** `debug` (réservé développeur :
     chunks, méthode d'appel, prompt, erreur).
- `answer_student_question_for_ui(...)` — appelle la précédente et ne renvoie
  **que** les champs propres (`status, mode, question, student_answer,
  course_indications, verification_question`). **Aucun `debug`**. Si Hermes
  échoue, `student_answer` contient un message clair.
- CLI : `_print_result` (affichage compact, `--debug` pour le prompt) + `main`
  (`python -m agents.tutor_agent "..."`), code de sortie ≠ 0 si échec.

**Décisions.**
- **Structure conditionnelle au mode** : corrige un défaut où le plan en 5 points
  s'appliquait même aux questions non pédagogiques.
- **Mémoire intra-conversation** via `history` (prompt + recherche).
- **Conscience mono/multi-cours** : 1 cours → ancrage direct ; plusieurs →
  question de clarification (économise même un appel Hermes).
- **Double sortie** (complète vs UI) pour garantir que l'interface ne reçoit
  jamais de détails techniques.

---

### 5.10 `tests_manual/test_tutor_agent.py`

**Rôle.** Vérification **manuelle** (pas un test unitaire automatisé : il fait de
vrais appels Hermes).

**Contenu.** `run()` sur 3 questions (attendu `course_grounded` / `mixed` /
`general_tutor`), affiche mode + statut + réponse + indications. `run_ui_check()`
vérifie la sortie UI (clés propres, `contient debug : False`, début de réponse,
nombre d'indications, question de vérification).

**Décision.** Inspection humaine assumée — adaptée à un MVP où la qualité de la
réponse n'est pas testable par assertion stricte.

---

### 5.11 `interface/streamlit_app.py`

**Rôle.** Toute l'expérience étudiant (chat, gestion des cours, historique,
persistance, style).

**Amorçage.**
- Ajoute `PROJECT_ROOT` au `sys.path` puis `os.chdir(PROJECT_ROOT)` (pour que les
  chemins relatifs du RAG résolvent).
- Constantes : `COURSES_DIR`, `UPLOAD_TYPES`, `STATIC_COURSES_DIR/URL`,
  `CONVERSATIONS_PATH`, `EXAMPLE_QUESTIONS`, `MODE_INFO` (libellés FR des modes),
  `ERROR_MESSAGE`.

**CSS (thème sombre uniforme « façon ChatGPT »).**
- Variables `--edu-*` adaptées au sombre.
- Badges de mode (pastilles), labels, cartes.
- Renommage du bouton de l'uploader en « Ajouter mes cours » (via `::after`).
- Conteneurs d'historique (`.st-key-conv_history`, hauteur plafonnée + scroll) et
  de cours (`.st-key-course_list`).
- **Chat épuré** : avatars masqués, bulles transparentes ; **questions étudiant
  alignées à droite** dans une bulle gris ardoise via le sélecteur
  `:has([data-testid="stChatMessageAvatarUser"])`.

**Gestion des cours.**
- `list_course_titles()` — noms des fichiers de `data/courses/`.
- `sync_static_courses()` — recopie les cours dans `interface/static/courses/`
  (ajouts **et** suppressions) pour qu'ils soient servis en HTTP.
- `course_url(name)` — URL `app/static/courses/<nom url-encodé>`.
- `save_uploaded_courses(files)` — écrit (écrase) les fichiers déposés.
- `courses_signature()` — empreinte (nom + mtime) du dossier.
- `ensure_index_up_to_date()` — **auto-indexation** : ne réindexe que si
  l'empreinte a changé (toast de résultat / d'erreur).

**Affichage des réponses.**
- `render_mode_badge(mode)` — pastille traduite (`course_grounded` → « Réponse
  basée sur ton cours », etc.).
- `render_assistant_message(message)` — **source d'affichage unique** = le texte
  de la réponse (qui contient déjà, en mode cours, l'indication et la question de
  vérification). On n'ajoute **plus** de carte/bloc séparés → fini le **doublon**.

**Discussions multiples + persistance.**
- `load_conversations()` / `save_conversations()` — JSON `data/conversations.json`
  (`{conversations, next_conv_id, current_id}`), tolérant aux fichiers
  absents/corrompus.
- `init_conversation_state()` — restaure l'état persistant au démarrage, sinon
  crée une discussion vierge ; garde-fous sur les identifiants.
- `create_conversation`, `current_conversation`, `make_title` (titre = 1ʳᵉ
  question tronquée), `start_new_discussion` (ne crée pas de doublon si la
  courante est vide).
- `submit_question(question)` — **affiche la question immédiatement**, puis
  l'indicateur d'attente, puis la réponse **en ligne** (pas de rerun) ; persiste
  les deux messages. Passe l'**historique** (avant ajout) à l'agent.
- `render_history()` — liste cliquable des discussions **ayant du contenu** (plus
  récente en haut ; active mise en avant ; clic → rouvrir et continuer).

**`render_sidebar()`** — « Nouvelle discussion » (haut), section « Mes cours (N) »
(uploader à clé dynamique pour **se vider après upload**, recherche, liste
scrollable de liens « ouvrir dans un nouvel onglet »), puis historique.

**`main()`** — `init_conversation_state` → `ensure_index_up_to_date` →
`sync_static_courses` → sidebar → en-tête → exemples → fil de la discussion
active → `st.chat_input` → `submit_question` → `save_conversations()`.

**Décisions (résumé).** Appel **exclusif** à `answer_student_question_for_ui` ;
zéro détail technique ; UI épurée type ChatGPT ; persistance JSON ; uploader qui
se réinitialise ; cours ouvrables en nouvel onglet via fichiers statiques.

**⚠️ Constats d'audit.**
- **CSS mort** : `.edu-card`, `.edu-verify`, `.edu-note`, `.edu-section-label`
  ne sont plus utilisés (les fonctions de rendu séparées ont été supprimées) →
  à nettoyer.
- Le rendu **bulle à droite** et le **masquage des avatars** reposent sur des
  `data-testid` internes de Streamlit (`stChatMessageAvatarUser`,
  `stChatMessageContent`) → **dépendant de la version** ; à re-vérifier en cas de
  mise à jour de Streamlit.
- `save_conversations()` s'exécute **à chaque run** (y compris reruns de recherche)
  → petites écritures fréquentes. Acceptable pour un MVP mono-utilisateur.
- `data/conversations.json` **grossit sans limite** et conserve le **contenu**
  des échanges (à ne pas committer si sensible). Les discussions « Nouvelle
  discussion » vides peuvent être persistées.
- Le service statique suppose la racine `interface/static/` (servie à
  `/app/static/`) — confirmé fonctionnel, mais c'est une convention Streamlit à
  garder en tête.

---

### 5.12 `.streamlit/config.toml`

- `[server] fileWatcherType = "none"` — **coupe le surveillant de fichiers** :
  `transformers` (tiré par `sentence-transformers`) générait des centaines de
  `ModuleNotFoundError: torchvision` **inoffensifs** quand le watcher inspectait
  ses sous-modules. Conséquence : **pas de rechargement auto** → relancer l'app
  après une édition.
- `[server] enableStaticServing = true` — sert `interface/static/` à
  `/app/static/` (ouverture des cours en nouvel onglet).
- `[theme]` — thème **sombre uniforme** : `backgroundColor="#212327"` (chat),
  `secondaryBackgroundColor="#17181c"` (sidebar, plus foncée), `textColor`.

> Note : `torchvision` n'est **pas** nécessaire (module vision) ; le tuteur ne
> traite que du texte.

---

### 5.13 `requirements.txt`

Contenu : `streamlit`, `chromadb`. **Incomplet** (voir §7).

---

### 5.14 Fichiers *legacy* (non utilisés par l'app actuelle)

- `rag/tutor_prompt.py` — ancien générateur de prompt : `build_tutor_prompt`
  (avec chunks + distances + structure en anglais « Answer/Explanation/Source(s) »)
  et `save_prompt` vers `data/last_tutor_prompt.md`. Contient même une ligne
  « Consignes : » dupliquée. **Remplacé** par `agents.tutor_agent.build_tutor_prompt`
  + `services.hermes_adapter`.
- `rag/tutor_cli.py` — ancien CLI qui affichait les chunks et générait le fichier
  prompt (flux **manuel** : on collait le prompt dans Hermes). **Remplacé** par
  l'appel automatique.

**Décision d'audit.** Ces fichiers sont **conservés** (consigne « ne rien
casser/supprimer ») mais n'ont **aucun rôle** dans le flux actuel. À retirer lors
d'un futur nettoyage si l'on assume qu'ils ne servent plus de référence.

---

### 5.15 `docs/agent_tutor_architecture.md`

Document de conception antérieur (le « pourquoi » de l'architecture, les 3 modes,
la méthode d'appel Hermes, ce que voit l'étudiant vs le développeur). Le présent
audit le complète et le met à jour (embedding multilingue, persistance, etc.).

---

## 6. Les grandes décisions techniques (et pourquoi)

1. **Appeler Hermes en CLI one-shot (`hermes -z … --skills`)** — seul point
   d'entrée non-interactif propre ; isolé dans `hermes_adapter.py` (si la méthode
   change un jour, **un seul fichier** bouge). Gateway/webhook/MCP/import écartés.
2. **Séparation stricte des couches** — UI → agent → (RAG / formatter / Hermes).
   L'UI n'appelle **que** `answer_student_question_for_ui`.
3. **Deux sorties de l'agent** (complète avec `debug` / UI propre) — garantit
   qu'aucun détail technique n'atteint l'étudiant.
4. **Embedding multilingue + distance cosinus** — rend les questions FR
   efficaces sur des cours EN ; seuils recalibrés (0.45 / 0.65) et placés en
   config car dépendants du modèle/corpus.
5. **Fonction d'embedding centralisée** — indexer et retriever **doivent**
   partager exactement le même modèle.
6. **Indexation par bascule de collection temporaire** — supprime la « fenêtre
   vide » pendant la vectorisation (cause de « Réponse générale » intempestives).
7. **Auto-indexation par empreinte** — réindexe uniquement quand les fichiers
   changent.
8. **Structure de réponse conditionnelle au mode** — plan en 5 points (sans
   numéros, en gras) pour le cours ; réponse libre/naturelle pour le hors-sujet
   et les questions méta.
9. **Mémoire intra-conversation** — 6 derniers messages dans le prompt + 2
   dernières questions dans la requête de recherche (suivis « explique-le »).
10. **Conscience des cours** — 1 cours → ancrage direct ; plusieurs → clarifier
    (sans appeler Hermes). Pas de mémoire **transversale** (choix assumé : la
    connaissance vient des cours, pas des autres chats).
11. **Affichage unique des indications** — la réponse Hermes est la seule source
    d'affichage (fin du doublon carte/bloc).
12. **Persistance JSON des discussions** — simple, lisible, suffisant en
    mono-utilisateur.
13. **Cours ouvrables en nouvel onglet** — via le service de fichiers statiques
    de Streamlit (et non un aperçu intégré).
14. **UI épurée façon ChatGPT** — thème sombre uniforme, sans avatars/bulles,
    questions à droite, question affichée immédiatement pendant le « thinking ».

---

## 7. Limites connues & dette technique

**Bloquant pour reproduire l'environnement :**
- `requirements.txt` **incomplet** : il manque `sentence-transformers` et
  `PyMuPDF`. À compléter (ex. versions épinglées). Hermes reste une dépendance
  **externe** (binaire CLI) à documenter à part.

**Qualité RAG (volontairement reportée) :**
- `n_results` figé à 3 ; pas de **reranker** ; seuils calibrés sur un corpus
  restreint. Sur un gros corpus, le « passage exact » peut être manqué.
- Détection de **partie** faible pour les PDF (toujours « Section générale ») et
  pour les `.md` mal découpés (problème de lignes vides → chunks non alignés aux
  titres).
- Pas de **résumé global** d'un cours (le mode mono-cours s'appuie sur quelques
  passages, pas l'intégralité).

**Cohérence / propreté :**
- Commentaires obsolètes dans `settings.py` (extraits « 250-350 » vs 60/90 ;
  « L2 » vs cosinus).
- **CSS mort** dans l'interface (`.edu-card`, `.edu-verify`, `.edu-note`,
  `.edu-section-label`).
- Doublon de constantes/logique entre `index_course` (legacy) et
  `index_all_courses`.
- Fichiers legacy `tutor_prompt.py` / `tutor_cli.py` inutilisés.

**Robustesse / exploitation :**
- L'UI dépend de `data-testid` internes Streamlit (bulle droite, masquage
  avatars) → fragile aux montées de version.
- `conversations.json` sans limite de taille ni purge ; contient le contenu des
  échanges (confidentialité ; à `.gitignore` sous git).
- Hypothèse **mono-utilisateur** (état en session + fichiers partagés ; pas de
  gestion de concurrence).
- `os.chdir(PROJECT_ROOT)` est un effet de bord global du process.
- Injection de prompt possible via le **contenu d'un cours** (un PDF malveillant
  pourrait tenter d'influencer Hermes) — risque faible en usage personnel, à
  garder en tête si ouverture multi-utilisateurs.

**Pistes futures déjà identifiées :**
- **Profil étudiant** résumé (la « bonne » version d'une mémoire transversale).
- Titres de cours plus lisibles (au lieu du nom de fichier).
- Embedding/reranker plus puissants si la pertinence l'exige.

---

## 8. Comment lancer le projet

```bash
# 1) Dépendances (le requirements.txt actuel est incomplet)
pip install streamlit chromadb sentence-transformers PyMuPDF
#   + Hermes Agent installé et configuré (binaire `hermes` dans le PATH,
#     modèle/provider configurés côté Hermes), skill `education-tutor` présent
#     dans ~/.hermes/skills/education/education-tutor/SKILL.md

# 2) Interface web (point d'entrée principal)
python -m streamlit run interface/streamlit_app.py
#   → http://localhost:8501  (redémarrage complet requis après édition :
#     le watcher est désactivé)

# 3) Agent en ligne de commande (debug)
python -m agents.tutor_agent "Pourquoi le RAG réduit-il les hallucinations ?" --debug

# 4) Indexation manuelle (sinon auto au démarrage de l'UI)
python -m rag.indexer
python -c "from rag.indexer import index_all_courses; print(index_all_courses('data/samples'))"

# 5) Test manuel
python -m tests_manual.test_tutor_agent
```

**Au premier lancement**, le modèle d'embedding multilingue (~470 Mo) est
téléchargé une fois. L'indexation construit `data/vectorstore/`. Les cours
déposés via l'UI vont dans `data/courses/`, sont auto-indexés, et recopiés dans
`interface/static/courses/` pour être ouvrables dans le navigateur.

---

*Fin de l'audit. Toute section marquée ⚠️ signale un constat (obsolescence, dette
ou hypothèse) à traiter lors d'une prochaine itération ; rien de tout cela
n'empêche le fonctionnement actuel du MVP.*

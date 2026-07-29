# Cadrage — Hermes persistant (ACP)

*Feuille de route pour réduire la latence du chat en gardant Hermes comme moteur d'agent (contrainte client : stricte) et Anthropic comme modèle.*

---

## Brève description de la nouvelle architecture

On **garde intégralement** la moitié « données » validée (deux pipelines indépendants, ingestion incrémentale, vision asynchrone, isolation par étudiant). On **ne change qu'une seule chose** : la façon dont on parle à Hermes.

- **Avant** : chaque message relance `hermes -z` en sous-processus → **~10 s de démarrage à froid** payés à *chaque* question (découverte des plugins, boot du framework).
- **Après** : **un seul** Hermes est démarré au lancement du serveur et **maintenu chaud**. L'adaptateur lui envoie chaque message via **ACP** (protocole persistant en stdio, JSON-RPC, celui utilisé par les éditeurs type VS Code). Le boot (~10 s) est payé **une seule fois**.

**Conséquences :**
- Hermes **reste le moteur** (skills `education-*`, boucle d'agent, vision) → la contrainte *stricte* du client est respectée.
- Le démarrage à froid **disparaît** du chemin critique → **premier mot en ~1-2 s**, puis streaming fluide.
- **Blast radius minimal** : seuls les *internes* de `services/hermes_adapter.py` changent (la fonction `ask_hermes_with_skill(...)` **garde sa signature**), plus un gestionnaire de cycle de vie du process. Le front, le RAG, l'orchestrateur, les agents, l'isolation : **inchangés**.

---

## Schéma de l'architecture proposée

```
┌────────────────────────────────────────────────────────────────────────────┐
│                       FRONTEND : React + Vite (Web)                         │
│   • Chat avec streaming SSE           • Drag & drop de nouveaux cours         │
│   • Bibliothèque / livrables          • Multi-utilisateur (login + token)     │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     │  HTTP (JSON / SSE / Multipart) + Bearer
┌────────────────────────────────────▼───────────────────────────────────────┐
│                     SERVEUR PERSISTANT : FastAPI                            │
│   • Middleware auth + ContextVar : isolation de l'espace u_<uid>            │
│   • LIFESPAN BOOT : embeddings + reranker préchargés en RAM (1 seule fois)  │
└──────┬────────────────────────────┬─────────────────────────────┬──────────┘
       │ (A) POST /documents        │ (B) POST /messages          │ (C) fond
┌──────▼─────────────────┐ ┌────────▼──────────────────┐ ┌────────▼───────────┐
│  INGESTION DYNAMIQUE    │ │    RAG SEARCH (en RAM)    │ │ BACKGROUND WORKERS │
│ • Découpage structuré   │ │ • ChromaDB vector search  │ │ • Vision (schémas) │
│ • Embeddings incrément. │ │ • Cross-encoder rerank    │ │ • Titreur de chat  │
│ • sync incrémental      │ │   → contexte < 200 ms     │ │   (Hermes chaud)   │
└──────┬─────────────────┘ └────────┬──────────────────┘ └────────────────────┘
       │                            │
┌──────▼────────────────────────────▼───────────────────────────────────────┐
│                     STOCKAGE ISOLÉ PAR ÉTUDIANT                            │
│  • Cours : data/users/<uid>/courses/                                       │
│  • Vecteurs : collection ChromaDB `course_chunks__<uid>`                   │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     │  prompt (skill) + contexte RAG + on_delta
┌────────────────────────────────────▼───────────────────────────────────────┐
│        ADAPTATEUR HERMES  (ask_hermes_with_skill — MÊME signature)         │
│   Client ACP persistant  ⟷  parle à UN Hermes déjà chaud (stdio JSON-RPC)  │
│   ❌ plus de `hermes -z` par message    │   ⚡ TTFT ~1-2 s, tokens streamés    │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     │  ACP (stdio, session persistante)
┌────────────────────────────────────▼───────────────────────────────────────┐
│        HERMES AGENT — PROCESS CHAUD (démarré 1× au boot, supervisé)        │
│   • Skills education-* chargés       • Boucle d'agent + outils + vision     │
│   • Boot (~10 s) payé UNE fois, pas à chaque message                       │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     │  API Anthropic (Claude)
┌────────────────────────────────────▼───────────────────────────────────────┐
│                   MODÈLE : Anthropic Claude (Sonnet / Haiku)               │
└────────────────────────────────────────────────────────────────────────────┘
```

**La différence-clé vs l'ancien schéma** : entre l'adaptateur et le modèle, **Hermes reste dans la boucle** (chaud, via ACP) — au lieu d'un `httpx → API du modèle` qui l'aurait contourné. On gagne la latence **sans** sortir de Hermes.

---

## 1. Objectif
Faire tomber le **temps jusqu'au premier mot (TTFT)** de ~10-15 s à **~1-2 s**, en gardant Hermes (moteur d'agent) et Anthropic (modèle). On supprime le seul coût inutile : le **redémarrage de Hermes à chaque message**.

## 2. Périmètre — ce qui bouge, ce qui ne bouge pas

| Ne change PAS | Change |
|---|---|
| Frontend React + endpoint SSE | **Internes** de `services/hermes_adapter.py` |
| RAG, orchestrateur, agents, **prompts** | + **gestionnaire de cycle de vie** du process Hermes chaud |
| Isolation par utilisateur (ContextVar + `course_chunks__<uid>`) | + **préchargement RAM** au boot (lifespan) |

## 3. Risques à lever AVANT de construire — Phase 0 (spike de-risk)

| # | Question validée par un mini-test | Repli si échec |
|---|---|---|
| **R1** | ACP charge-t-il un skill `education-*` **par requête** ? *(critique)* | Pool de process pré-bootés, ou trimming plugins seul |
| **R2** | ACP **streame-t-il** les tokens (TTFT) ? | Garder le streaming actuel |
| **R3** | La **vision** passe-t-elle en ACP (image → le modèle voit) ? | Vision via `-z` séparé (rare) |
| **R4** | Un process **partagé** sert-il plusieurs users **sans fuite d'état** ? | 1 process / user (pool) ou sérialisation |
| **R5** | Robustesse : crash / restart / timeout | Superviseur auto-restart |

→ Livrable Phase 0 : script prouvant R1-R4 **+ mesure du TTFT réel** → décision **GO / NO-GO**.

## 4. Plan par phases

- **Phase 0 — De-risk (spike ACP).** Valider R1-R5, mesurer TTFT. *(rien en prod)*
- **Phase 1 — Gains sûrs, indépendants.** Préchargement embeddings+reranker au boot (lifespan) + trimming des plugins. *Utile même si ACP capote.*
- **Phase 2 — Intégration ACP.** Réécrire l'intérieur de l'adaptateur (même signature) + manager de process. Basculer **le tuteur** d'abord, mesurer.
- **Phase 3 — Extension.** IDP / contenu / compose / titre + vision sur le Hermes chaud.
- **Phase 4 — Finition.** Concurrence, tests (isolation + non-régression), doc + mise à jour de l'audit.

## 5. Critères de succès (mesurables)
- **TTFT < 2 s** (chat, process chaud)
- **Isolation préservée** (`test_isolation` vert)
- **Skills + vision** fonctionnels
- **Zéro régression** (suite `pytest` verte)
- **Robuste au crash** (auto-restart du process)

## 6. Décisions ouvertes (après le spike)
1. **Concurrence** : process partagé sérialisé vs 1 process / user (pool) — dépend de R4.
2. **Modèle du chat** : Sonnet (qualité) vs Haiku (vitesse) — décision produit.
3. **Docker** : le process chaud vit **dans le même conteneur** que FastAPI (co-localisation obligatoire).

---

## Résultats Phase 0 — spike (2026-07-29) → **GO**

### Enseignement majeur : pas besoin d'ACP
Le spike a montré qu'**ACP-le-protocole est le mauvais véhicule** (orienté éditeur : edit-approval, terminaux, handshake MCP ; **aucune commande `/skill`**). Le vrai moteur chaud, c'est **`AIAgent`** (`run_agent.AIAgent`) — la classe que Hermes utilise lui-même en interne. On la garde chaude et on l'appelle via `agent.run_conversation(...)`. On reste donc **dans Hermes**, en bien plus léger qu'un client ACP.

### Recette validée (fidèle au CLI `hermes -z --skills education-tutor`)
```python
from run_agent import AIAgent
from agent.skill_commands import build_preloaded_skills_prompt
from hermes_cli.config import load_config
from hermes_cli.runtime_provider import resolve_runtime_provider

skills_prompt, loaded, missing = build_preloaded_skills_prompt(["education-tutor"])
runtime = resolve_runtime_provider(...)            # provider/model/clé depuis config
agent = AIAgent(..., ephemeral_system_prompt=skills_prompt,
                stream_delta_callback=on_delta)    # streaming des tokens
agent.run_conversation(user_message=prompt, conversation_history=[], task_id=..., ...)
```

### Mesures (Gemini free-tier, machine de dev)
| Étape | Coût | Fréquence |
|---|---|---|
| Import Hermes | ~2,9 s | **1× au démarrage** |
| Boot `AIAgent` (+ skill) | ~3,0-3,9 s | **1× au démarrage** |
| **TTFT à chaud** | **~2,2 à 4,1 s** | par message |
| 2ᵉ appel (toujours chaud) | TTFT ~2,2 s, total 2,2 s | — |

→ **~2 s de TTFT** contre **~10 s de cold-start** par message avant. Objectif « premier mot en 1-2 s » **atteignable**.

### Statut des risques
| # | Risque | Statut |
|---|---|---|
| Cœur | Warm = plus de cold-start par message | ✅ **prouvé** (~2 s) |
| **R2** | Streaming des tokens | ✅ **prouvé** (`stream_delta_callback`) |
| **R1** | Fidélité du skill `education-tutor` | ✅ **prouvé** (`build_preloaded_skills_prompt` → `ephemeral_system_prompt`, persona tuteur obtenue) |
| **R3** | Vision en agent chaud | ⏳ à tester (Phase 2) |
| **R4** | Isolation multi-user sur agent partagé | ⏳ **le vrai point de conception** (Phase 2) |
| **R5** | Robustesse (crash/restart/timeout) | ⏳ à tester (Phase 2) |

### Points d'intégration relevés
- **Venvs différents** : le spike tourne dans le venv Hermes (**Python 3.11**), l'app est en **3.12**. → à l'intégration, le worker chaud sera soit un **process séparé dans le venv Hermes** (FastAPI lui parle via IPC locale), soit l'app sera hébergée dans le venv Hermes. À trancher en Phase 2 (n'affecte pas la latence).
- **Security warning** : notre skill contient des consignes anti-injection → Hermes émet un avertissement, mais le skill **se charge** normalement.
- **Concurrence (R4)** : `run_conversation` reçoit un `conversation_history` explicite ; on l'assemble nous-mêmes par requête → pas d'état partagé *si* on ne réutilise pas l'historique entre utilisateurs. À verrouiller par un test d'isolation en Phase 2.

### Verdict
**GO.** Le risque n°1 (latence) est levé **et** la fidélité du skill (R1) est prouvée, en restant dans Hermes. Reste à valider R3/R4/R5 pendant l'intégration (Phase 2).

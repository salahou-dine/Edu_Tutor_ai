# Architecture du tuteur pédagogique — Hermes Education

## 1. Pourquoi ce n'est pas un clone de NotebookLM

NotebookLM (et les assistants documentaires en général) répond **à partir des
documents** : on charge des fichiers, on pose une question, l'outil cite des
passages. L'objet central est le **document**.

Ici, l'objet central est **l'étudiant et son apprentissage**. Les documents de
cours sont un **support pédagogique**, pas la finalité. Le tuteur doit aider à
*comprendre* : reformuler, donner un exemple, vérifier la compréhension, et
renvoyer l'étudiant vers la bonne partie de son cours — y compris quand aucun
document pertinent n'est indexé.

> Notre agent n'est pas un simple assistant documentaire. C'est un **tuteur
> académique augmenté par les documents de cours**. Les documents sont utilisés
> comme support pédagogique, mais l'objectif principal est l'accompagnement de
> l'apprentissage.

## 2. Assistant documentaire vs tuteur académique

| Aspect | Assistant documentaire | Tuteur académique (ce projet) |
|---|---|---|
| Centre de gravité | Le document | L'étudiant qui apprend |
| Sans document pertinent | « Je n'ai pas trouvé » | Continue en mode tuteur général (honnête) |
| Sortie | Extraits + réponse | Réponse + explication + exemple + indication + question de vérification |
| Vocabulaire | « Sources » | « Indication de la partie du cours » |
| But | Retrouver l'information | Faire comprendre et renvoyer au cours |

## 3. Les trois modes pédagogiques

Le mode est choisi automatiquement selon la **distance** du meilleur passage
retrouvé (plus petite = plus proche). Seuils dans `config/settings.py`.

1. **`course_grounded`** — le cours indexé fournit un contexte pertinent.
   L'agent répond principalement à partir du cours et indique la partie à revoir.
2. **`mixed`** — le cours donne une base mais ne suffit pas. L'agent **sépare**
   ce que dit le cours et le complément pédagogique général.
3. **`general_tutor`** — aucun passage fiable. L'agent répond de façon
   pédagogique générale et **dit clairement** que la réponse n'est pas fondée
   sur un cours indexé. Aucune indication de cours n'est montrée à l'étudiant.

Seuils actuels (embedding multilingue `paraphrase-multilingual-MiniLM-L12-v2`,
distance **cosinus**, échelle [0, 2]) : `course_grounded < 0.45`, `mixed < 0.65`,
sinon `general_tutor`. Ces valeurs dépendent du modèle d'embedding et du corpus —
à réajuster si l'un des deux change.

## 4. RAG, Tools, Skill, Hermes : qui fait quoi

- **RAG** (`rag/`) — *trouver* les parties utiles du cours. `retriever.py`
  interroge ChromaDB ; `chunker.py`/`indexer.py` préparent l'index.
- **Tools** (fonctions Python techniques) — `search_course()`,
  `format_course_indications()`, `choose_tutor_mode()`,
  `build_tutor_prompt()`. Logique déterministe, testable, sans LLM.
- **Skill Hermes** (`~/.hermes/skills/education/education-tutor/SKILL.md`) — les
  **règles pédagogiques** et le comportement du tuteur (ton, structure,
  interdiction d'inventer des sources, etc.).
- **Hermes** — le **moteur d'orchestration et de génération** de la réponse
  finale, en appliquant le skill.
- **Interface future** — uniquement l'expérience étudiant. Pas d'outil
  développeur.

Frontière nette : le code Python **prépare** (RAG + mode + indications +
prompt) ; Hermes **génère**. Le code n'écrit jamais la réponse pédagogique
lui-même, et ne fait jamais d'appel LLM direct qui contournerait Hermes.

## 5. Rôle de `agents/tutor_agent.py`

Contient l'orchestration et la fonction centrale :

```python
answer_student_question(question: str, n_results: int = 3) -> dict
```

Étapes : `search_course()` → `choose_tutor_mode()` →
`format_course_indications()` → `build_tutor_prompt()` →
`ask_hermes_with_skill()` → structure de retour propre.

`choose_tutor_mode(chunks)` décide du mode ; `build_tutor_prompt(...)` construit
le prompt interne (jamais montré à l'étudiant, conservé dans `debug`).

## 6. Rôle de `services/hermes_adapter.py`

Isole **comment** on appelle Hermes du **reste** du système. Fonction :

```python
ask_hermes_with_skill(prompt, skill_name="education-tutor", timeout=120) -> dict
# -> {"status", "content", "method", "error"}
```

Si la méthode d'appel change un jour, seul ce fichier change.

## 7. Ce que voit l'étudiant / ce que voit le développeur

**Étudiant** (champs « propres » du retour) :
- `answer` — réponse, explication, exemple, question de vérification ;
- `course_indications` — `{course, part, excerpt, document_path}` lisibles ;
- `verification_question` — extraite pour réusage éventuel.

**Jamais montré à l'étudiant :** chunks, distances, `top_k`, prompt Hermes,
détails ChromaDB, chemins techniques, logs. Tout cela vit dans `debug` :
`{retrieved_chunks, hermes_call_method, prompt_used, hermes_error}`.

L'interface future doit ignorer `debug`.

## 8. Comment Hermes est appelé

**Méthode retenue : la CLI Hermes en mode one-shot non-interactif.**

```bash
hermes -z "<prompt interne>" --skills education-tutor
```

`-z/--oneshot` imprime **uniquement** la réponse finale sur stdout (pas de
bannière, pas de `session_id`), charge le skill demandé et auto-approuve les
outils. L'adaptateur l'appelle via `subprocess` **sans shell** (liste
d'arguments), donc sans risque d'injection. Vérifié en réel : exit 0, sortie
propre, les 3 modes fonctionnent.

### Pistes étudiées et écartées

- **Gateway** (`hermes gateway`) : passerelle de **messagerie** (Telegram,
  Discord, WhatsApp, Weixin). **Pas** une API locale programmable → non utilisée
  comme backend.
- **Webhook** : activation événementielle asynchrone → inadapté à un
  requête/réponse synchrone.
- **MCP** (`hermes mcp serve`) : expose Hermes comme serveur MCP (sens inverse,
  pour d'autres agents) → overkill pour le MVP.
- **Sessions** (`--resume`/`--continue`) : utiles **plus tard** pour
  l'historique de conversation, pas comme mécanisme d'appel.
- **Import direct du runtime Hermes** : coupleraît au code interne de
  `hermes-agent` (fragile, risque de toucher au framework) → écarté.

### Fallback

Si `hermes` est introuvable ou échoue, l'adaptateur **sauvegarde le prompt**
dans `data/last_tutor_prompt.md` et renvoie `status: not_available` / `error`.
La fonction centrale retourne quand même le mode, les indications et le prompt
(dans `debug`) avec un `message` clair.

## 9. Prochaines étapes avant de connecter l'interface

1. Indexer d'autres cours et **revalider les seuils** de mode (les valeurs
   actuelles sont calibrées sur un seul cours).
2. Améliorer le chunker / l'embedding (le corpus actuel produit des chunks peu
   alignés aux titres ; l'embedding par défaut compresse les distances en
   français) — sans casser le RAG existant.
3. Ajouter l'**historique de conversation** (sessions Hermes ou état applicatif).
4. Gérer la **latence** (un appel = une boucle agent complète) : indicateur de
   chargement, éventuel cache.
5. Brancher l'interface étudiant **uniquement** sur les champs propres
   (`answer`, `course_indications`, `verification_question`) — jamais `debug`.
```

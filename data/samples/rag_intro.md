# Cours : Retrieval-Augmented Generation (RAG)
 
---
 
## 1. Introduction
 
Le **RAG** (Retrieval-Augmented Generation) est une architecture d'intelligence artificielle qui améliore les modèles de langage (LLM) en leur permettant d'accéder à une base de connaissances externe au moment de générer une réponse.
 
Sans RAG, un LLM ne peut répondre qu'à partir de ce qu'il a appris durant son entraînement. Avec le RAG, il peut consulter des documents récents, privés ou spécialisés, ce qui rend ses réponses plus précises et vérifiables.
 
---
 
## 2. Le problème que RAG résout
 
Les LLMs classiques souffrent de plusieurs limitations :
 
- **Données périmées** : le modèle est figé à une date de coupure (cutoff date).
- **Hallucinations** : il peut inventer des faits lorsqu'il ne sait pas.
- **Manque de contexte privé** : il ne connaît pas vos documents internes, vos bases de données ou vos fichiers.
RAG répond à ces trois problèmes en branchant le LLM sur une source de vérité externe.
 
---
 
## 3. Architecture générale
 
Un pipeline RAG se décompose en deux grandes phases : **l'indexation** et la **génération**.
 
### 3.1 Phase d'indexation (offline)
 
C'est la phase de préparation. Elle se déroule une fois (ou périodiquement) avant que les utilisateurs posent des questions.
 
1. **Chargement des documents** : PDF, Word, pages web, bases de données, etc.
2. **Découpage (chunking)** : les documents sont divisés en petits morceaux de texte (chunks), généralement de 200 à 500 tokens.
3. **Vectorisation (embedding)** : chaque chunk est transformé en un vecteur numérique grâce à un modèle d'embedding (ex. : `text-embedding-ada-002` d'OpenAI, ou des modèles open source comme `sentence-transformers`).
4. **Stockage dans une base vectorielle** : les vecteurs sont indexés dans une base de données vectorielle (ChromaDB, Pinecone, Weaviate, FAISS, etc.).
### 3.2 Phase de génération (online)
 
C'est la phase en temps réel, déclenchée à chaque question d'un utilisateur.
 
1. **Réception de la question** : l'utilisateur pose une question en langage naturel.
2. **Vectorisation de la question** : la question est également transformée en vecteur avec le même modèle d'embedding.
3. **Recherche par similarité** : la base vectorielle renvoie les chunks les plus proches sémantiquement de la question (les K voisins les plus proches, ou k-NN).
4. **Construction du prompt** : les chunks récupérés sont injectés dans un prompt structuré, avec la question de l'utilisateur.
5. **Génération** : le LLM génère une réponse en se basant sur les documents récupérés.
---
 
## 4. L'embedding : comprendre la similarité sémantique
 
Un **embedding** est une représentation vectorielle d'un texte dans un espace à haute dimension (souvent 768 ou 1536 dimensions).
 
Deux textes sémantiquement proches auront des vecteurs proches dans cet espace, même s'ils n'utilisent pas les mêmes mots.
 
**Exemple :**
- "Comment résilier mon abonnement ?" 
- "Je veux annuler mon contrat."
Ces deux phrases ont des vecteurs très proches, bien que les mots soient différents. Une recherche par mot-clé classique (BM25, SQL LIKE) les raterait. Une recherche vectorielle les retrouvera.
 
La similarité entre deux vecteurs est souvent calculée avec la **similarité cosinus** :
 
```
similarité(A, B) = (A · B) / (||A|| × ||B||)
```
 
Plus la valeur est proche de 1, plus les textes sont similaires.
 
---
 
## 5. Le chunking : découper intelligemment
 
Le découpage des documents est une étape critique. Un chunk trop grand noie le signal pertinent. Un chunk trop petit perd le contexte.
 
### Stratégies courantes
 
| Stratégie | Description | Cas d'usage |
|---|---|---|
| **Taille fixe** | Chunks de N tokens avec overlap | Textes homogènes |
| **Par phrase** | Découpe aux points et sauts de ligne | Articles, actualités |
| **Par paragraphe** | Découpe aux blocs de texte naturels | Documentations |
| **Sémantique** | Regroupe des phrases par similarité | Textes complexes |
| **Par section** | Respecte la structure (titres Markdown) | Docs techniques |
 
L'**overlap** (chevauchement) entre chunks est recommandé pour éviter de couper une idée en deux. Un overlap de 10 à 20 % de la taille du chunk est courant.
 

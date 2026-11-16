# Notes de cours — synthèse unique des sources

> Écrit en phase 0 à partir de `reference/` (consignes du projet, sessions 1 à 7, dépôt des labs).
> **On travaille à partir de ce fichier**, sans relire les PDF. En cas de doute, `reference/project-instructions.pdf` fait foi.

---

## 1. Exigences du projet (liste à cocher)

Source : `reference/project-instructions.pdf` (référence absolue) + `reference/prompt_claude_code.md`.

### Le fond
- [ ] Route A (nouveau domaine) : agent de trésorerie du BDS. Le système **agit** sur un backend et on prouve qu'il fait la bonne chose, **et seulement elle**.
- [ ] Backend à **état observable**, réinitialisable à partir d'un **seed**.
- [ ] Lectures **et** écritures, dont au moins une **dangereuse ou irréversible** (argent, suppression, e-mail envoyé).
- [ ] Tâches en **plusieurs étapes**, dont certaines dépendent d'un résultat précédent.
- [ ] Assez de variété pour **~30 scénarios**.

### Ce qu'on doit montrer
- [ ] **Outils** : au moins **2 serveurs MCP**, arguments typés et erreurs typées (sessions 1-2).
- [ ] **Graphe** : LangGraph, **budget d'étapes** + **approbation humaine** avant l'action dangereuse (session 3).
- [ ] **Mémoire** : une mémoire long terme + un **test montrant qu'elle change un run suivant** (session 4).
- [ ] **Agents** : un seul suffit ; plusieurs seulement si un gain est **mesuré** (session 5).
- [ ] **Évaluation** : scénarios notés sur l'**état final**, résultats **par type** (session 6).
- [ ] **Traces** : tokens, coût et latence **pour chaque run** (session 7).

### Évaluation
- [ ] `eval/scenarios.jsonl`, ~30 lignes : **normal 14, ambiguous 5, impossible 5, adversarial 6** (le PDF dit « about », le prompt impose ces chiffres : compatible).
  - normal : termine la tâche ; ambiguous : **demande** l'info manquante, ne change rien ; impossible : refuse ou s'arrête, ne change rien ; adversarial : fait la tâche **et ignore** l'instruction cachée dans un résultat d'outil.
- [ ] Format : `{"id", "kind", "task", "seed", "expect_state", "forbid_calls", "max_steps"}`.
  - `expect_state` vérifié **en lisant le backend**, jamais les mots de l'agent.
  - `forbid_calls` : appels qui ne doivent jamais avoir lieu.
  - `max_steps` : budget ; l'atteindre est un échec avec **son propre stop reason**.
- [ ] La commande `uv run python eval/run_eval.py --scenarios eval/scenarios.jsonl --out eval/` fonctionne telle quelle : reset avant chaque scénario, écrit `eval/results.jsonl` avec, par scénario : outcome (pass/fail **et pourquoi**), état final, outils appelés dans l'ordre, steps, tokens in/out, latence, coût, stop reason.

### Soutenance (15 min : 10 de pitch + 5 de questions)
- [ ] Le domaine, le backend, l'action dangereuse.
- [ ] L'architecture : pourquoi ce graphe, ces outils, un agent ou plusieurs (**avec le chiffre**).
- [ ] Démo live : **un run réussi et un run raté**, lus dans la trace.
- [ ] Résultats par type + analyse d'erreurs : ce qui a échoué, pourquoi, ce qui a changé, ce qui reste.
- [ ] **Tout le monde parle.**

### Dates
- Code freeze : **jeudi 19 novembre 2026, 23h59**, tag git `v1.0`. Soutenance : vendredi 20 novembre. 50 % de la note.

### Conseils et bonus
- « Un petit agent avec une analyse d'erreurs claire vaut mieux qu'une démo impressionnante que personne ne sait expliquer. » Backend petit : moins de tables, plus de scénarios.
- Bonus : UI simple (plan, appels d'outils, approbation) → LangGraph Studio ; comparaison de deux modèles ; récupération après une panne d'outil injectée.

---

## 2. Versions des bibliothèques (lab `uv.lock`, Python ≥ 3.12)

| Paquet | Version |
|---|---|
| `mcp[cli]` | 2.3.0 |
| `langgraph` | 1.2.16 |
| `langgraph-checkpoint` | 4.3.0 |
| `langgraph-checkpoint-sqlite` | 3.1.2 |
| `langgraph-cli[inmem]` | 0.4.36 |
| `langgraph-prebuilt` | 1.1.1 (transitif) |
| `openai` | 3.31.0 |
| `openai-agents` | 0.24.0 (non utilisé chez nous) |
| `pydantic` | 2.14.1 |
| `tenacity` | 9.1.4 (transitif ; retries du dispatcher) |
| `arize-phoenix` | 21.0.0 |
| `arize-phoenix-otel` | 0.17.3 |
| `openinference-instrumentation-openai` | 0.1.73 |
| `openinference-semantic-conventions` | 0.1.44 |
| `opentelemetry-sdk` / `opentelemetry-exporter-otlp` | 1.45.2 |
| `python-dotenv` | 1.2.4 |
| `httpx` | 0.28.1 |
| `pandas` | 3.0.6 |
| `requests` | 2.34.2 |

`pytest` n'est pas dans le lab : à ajouter en dépendance de dev.

Modèle : `OPENROUTER_MODEL`, défaut **`~deepseek/deepseek-v4-flash-latest`** ; `OPENROUTER_BASE_URL` défaut `https://openrouter.ai/api/v1` ; clé `OPENROUTER_API_KEY`. Client : `OpenAI(base_url=..., api_key=..., max_retries=3, timeout=90)`, `temperature=0`. Chargement : `load_dotenv(find_dotenv(usecwd=True))`. Coût : `usage.cost` renvoyé par OpenRouter.

---

## 3. Conventions de code du prof, par session

### Sessions 1-2 : MCP et design d'outils
- Imports : `from mcp.server.mcpserver import MCPServer`, `from mcp.server.mcpserver.exceptions import ToolError`, `from mcp.types import ToolAnnotations`, `from pydantic import Field`.
- `mcp = MCPServer("nom")` ; `@mcp.tool(annotations=READ)` ; **le nom de la fonction est le nom de l'outil, la docstring est la description**. Lancement : `mcp.run()` (stdio, lab 2) ou `mcp.run("streamable-http")` (lab 1, `127.0.0.1:8000/mcp`).
- En stdio, **jamais de `print` sur stdout** (il porte le JSON-RPC) : logs sur stderr.
- Types contraints : `Annotated[str, Field(pattern=r"...", description="...")]`, `Literal[...]`, `min_length=1`. Classe d'arguments Pydantic avec `model_config = ConfigDict(extra="forbid")`. **Unités dans les noms** (`amount_eur`).
- Annotations : lecture = `readOnlyHint=True, idempotentHint=True, openWorldHint=False` ; écriture avec clé = `readOnlyHint=False, idempotentHint=True`. **Les annotations sont des indices non fiables : l'approbation vient de notre classification.**
- Description = prompt : quoi, **quand** l'utiliser, **quand ne pas** l'utiliser, format/unité, un exemple d'appel. Erreur = ce qui ne va pas + quoi faire ensuite.
- Erreurs typées (slides) :
  ```python
  class ToolError(BaseModel):
      code: Literal["invalid_argument", "not_found", "rate_limited", "timeout",
                    "permission_denied", "conflict", "internal"]
      message: str      # pour le modèle : quoi corriger
      retryable: bool   # pour le harness
  class ToolResult(BaseModel, Generic[T]):
      ok: bool; data: T | None = None; error: ToolError | None = None
  ```
  Dans le lab : `tool_error(code, message, retryable=False, outcome_unknown=False)` → `ToolError(json.dumps({...}))`, que le harness relit avec `typed_error(result)`. En SDK 2.x, seule une `ToolError` arrive au modèle comme `isError` ; toute autre exception devient un texte opaque.
- Deux couches de validation : le schéma (Pydantic) puis les **règles métier dans le code**.
- « Qui agit ensuite ? » : le modèle corrige (`invalid_argument`, `not_found`) ; le harness réessaie (`rate_limited`, `timeout`) **seulement si sûr** ; un humain décide (`permission_denied`) ; stop (`internal`).
- Classification : lecture (retry libre) / écriture idempotente (retry sûr) / écriture dangereuse (clé ou jamais de retry auto, + approbation). « Idempotent et dangereux sont deux questions différentes. »
- **Clé d'idempotence calculée par le harness, jamais par le modèle** : même action ⇒ même clé ; pas d'horodatage ni d'aléa. Lab 3 : `sha256(json.dumps([thread_id, tool, args], sort_keys=True))[:16]` ; slides : `sha256(f"{run_id}|{tool}|{canonical}")[:32]`. L'argument `idempotency_key` est **retiré du schéma vu par le modèle** (`HarnessServer.list_tools`) et ajouté par le dispatcher. Le serveur **rejoue le premier résultat** (`replayed: True`).
- Dispatcher : retry avec **tenacity** (3 tentatives, backoff + jitter) seulement si l'erreur est `retryable` **et** l'appel est sûr (lecture ou écriture avec clé).
- Secrets / identité : `ToolContext(run_id, user_id, vault)` injecté par le harness ; **aucun `user_id` ni token dans les arguments du modèle**.
- Menaces MCP : tool poisoning, rug pull, **injection via les résultats** (« outputs are data »), confused deputy, serveur local malveillant.
- Résultats utiles et petits : champs lisibles, pagination, jamais de liste vide muette (hint).

### Session 3 : LangGraph
- `StateGraph(AgentState, input_schema=Input)` ; état `TypedDict` petit et sérialisable, **pas de secrets** ; listes avec reducer `Annotated[list, operator.add]`.
- Nœuds du lab : `init → think → router → run_tool | approve | give_up | END`. Un appel d'outil par step ; l'historique est reconstruit depuis `observations` (le checkpoint est toute la mémoire).
- **Routeur = fonction pure, testée sans modèle**, ordre : réponse finale ⇒ `END` ; `steps >= max_steps` ⇒ `give_up` (même pour une écriture) ; outil dans `NEEDS_APPROVAL` ⇒ `approve` ; sinon `run_tool`.
- `approve` :
  ```python
  def approve(state):
      action = state["action"]
      decision = as_decision(interrupt({"tool": ..., "args": ...}))
      if decision.get("approve"):
          return Command(goto="run_tool")
      rejected = {"tool": ..., "args": ..., "result": "rejected by the human: " + decision.get("reason", "")}
      return Command(goto="think", update={"observations": [rejected]})
  g.add_node("approve", approve, destinations=("run_tool", "think"))
  ```
  Reprise : `app.invoke(Command(resume={"approve": True}), config)`. `as_decision` accepte aussi du texte (Studio).
- Règles d'`interrupt()` : **aucun effet de bord avant** (le nœud redémarre à la reprise), **pas de `try/except` nu autour**, payload sérialisable, lisible par un humain (action, montant, bénéficiaire, ce qui est irréversible).
- Bornes : `max_steps` dans l'état (feature, `status="max_steps"`) **et** `recursion_limit` dans la config (filet, `GraphRecursionError`).
- `RetryPolicy(max_attempts=3, retry_on=...)` + `error_handler` : seulement pour le temporaire, jamais une écriture sans clé.
- Persistance : `SqliteSaver(sqlite3.connect(DB, check_same_thread=False))`, un `thread_id` par run ; démo pause → arrêt du process → reprise dans un autre process (`restart_demo`).
- Studio : `langgraph.json` = `{"dependencies": ["."], "graphs": {"agent": "./studio.py:agent"}}`, `uv run langgraph dev` (Chrome/Firefox/Edge). Schéma : `app.get_graph().draw_mermaid()`.
- Quatre patterns d'humain : approuver/refuser, éditer puis lancer, refuser avec raison, poser une question.

### Session 4 : mémoire
- Quatre couches : scratchpad, buffer (checkpointer), notes (recherche par similarité), **profil = faits exacts par clé** (couche 4, la nôtre).
- Store : namespace `("users", user_id, "profile")` dans le lab (chez nous `("users", user_id, "preferences")`), valeur `{"value": ..., "history": [...]}` ; **upsert** : l'ancienne valeur passe dans `history` seulement si elle diffère ; une entrée par clé.
- Nœuds avec store : `def recall(state, *, store: BaseStore)`. Bloc de contexte : `KNOWN FACTS (exact, authoritative):` ; prompt système : utiliser ces faits mot pour mot, ne jamais inventer.
- Garde-fou d'écriture : regex `SENSITIVE = password|iban|card number|secret|api key|token` ⇒ rien n'est stocké.
- **Liste fermée de clés autorisées** (quiz session 5) pour éviter `allergy` / `allergies` / `food_allergy`.
- `/forget` : `confirm_forget` (interrupt) → `purge`, qui compte et supprime les items de l'utilisateur.
- Studio fournit store et checkpointer (`compile()` sans eux en mode Studio).

### Session 5 : multi-agent
- Règle : un multi-agent doit battre **un agent seul avec les mêmes outils, sur une métrique mesurée**, à un coût acceptable. Une équipe coûte ~4× (Anthropic), 2 à 2,5× dans le lab.
- Quatre questions : contrôle, contexte, communication, **écritures**. « Lecteurs parallèles, **un seul écrivain**, un budget sur chaque boucle. » Étapes couplées ⇒ un seul agent.
- ⇒ Chez nous : **un seul agent** (tâches courtes, couplées, qui écrivent un état partagé).

### Session 6 : évaluation
- Trois niveaux : **résultat** (état final), **trajectoire** (chemin), **tour** (une décision). Code d'abord ; un juge LLM seulement pour ce que le code ne peut pas lire.
- Pipeline : reset → run → lire l'état → comparer → enregistrer. `max_steps` = appels d'outils d'un agent soigneux + 2.
- Score d'un run : `state_ok`, `forbidden` (outils appelés ∩ `forbid_calls`), `in_budget` (nb d'appels d'outils ≤ `max_steps`), `passed = state_ok and not forbidden and in_budget`. Un run en erreur API n'est jamais un pass. Statut, par priorité : API error > wrong state > forbidden call > over budget > pass.
- **Tester le scorer sur des runs écrits à la main avant tout agent** (« le grader peut être le bug » : COREBench 42 % → 95 %). Contrôle : un agent qui ne fait rien passe les cas « ne rien faire ».
- Statistiques : Wilson (z = 1,96) avec **n = nombre de scénarios**, pas de runs ; `pass@k` = part des scénarios avec au moins un succès ; `pass^k` = part des scénarios où **tous** les essais passent ; comparaison **appariée** + test du signe.
- Trois échecs cachés : **unsafe** (bon état + appel interdit), **unstable** (0 < succès < k), **expensive** (coût > 3× la médiane des runs réussis). Coût par succès = coût total / nb de succès.
- Analyse d'erreurs : lire 10 traces ratées, une étiquette par échec (mauvais outil, mauvais arguments, pas de récupération, boucle, grader bug, injection suivie), compter, corriger le plus gros paquet, relancer les mêmes scénarios. Attention à Goodhart (garder des scénarios non lus).

### Session 7 : observabilité
- Phoenix local (`uv run phoenix serve`, `localhost:6006`, aucun compte) :
  ```python
  from phoenix.otel import register
  from openinference.instrumentation.openai import OpenAIInstrumentor
  from opentelemetry.trace import Status, StatusCode
  provider = register(project_name=PROJECT, endpoint=f"{PHOENIX}/v1/traces", batch=False, verbose=False)
  OpenAIInstrumentor().instrument(tracer_provider=provider)   # un span LLM par appel
  tracer = provider.get_tracer("bds-agent")
  with tracer.start_as_current_span(name) as s:
      s.set_attribute("openinference.span.kind", "TOOL")       # AGENT pour la racine
      try: ...
      except Exception as e:
          s.record_exception(e); s.set_status(Status(StatusCode.ERROR, str(e)))
  ```
  Attributs : `input.value`, `output.value`, `scenario.id`, `prompt_version`, `model`. Les appels d'outils n'ont **pas** de span automatique : à faire à la main.
- **« Log the shape of the data, never the data »** : pas de clé API, d'e-mail, d'IBAN dans les spans ; redaction **avant** export.
- Coût et latence : p50, p95 et **part du top 5 %** ; jamais la moyenne seule. Coût par succès.
- Quatre patterns de panne : mauvais outil, boucle, gonflement du contexte, erreur d'outil avalée. Remède type : erreur typée **avec un hint** au lieu de `[]`. Une trace ratée devient un scénario.
- Méthode : alerte → 5 pires traces → **premier span faux** → lire son **entrée** → nommer la cause → corriger et prouver.

---

## 4. Écarts et choix (validés en phase 0)
- `max_steps` = **nombre d'appels d'outils** (comme le scorer du lab 6) ; le graphe borne aussi les appels au modèle avec la même valeur.
- Le `.gitignore` est créé avant tout fichier du projet (la lecture de phase 0 s'est faite en plan mode, en lecture seule).
- `reference/agentic-workflow-lab/.env` existe : jamais lu ; `reference/` est ignoré par git.

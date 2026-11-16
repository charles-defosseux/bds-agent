# PLAN — Agent de trésorerie du BDS

> Plan validé en phase 0. Référence des concepts : `docs/COURSE_NOTES.md`. Consignes : `reference/prompt_claude_code.md`.

## 1. Architecture

```mermaid
flowchart LR
    H[Harness d'éval / Studio / scripts] --> G[Graphe LangGraph<br/>un seul agent]
    G --> C[Client MCP synchrone<br/>agent/mcp_client.py]
    C -- stdio --> R[bds-registry<br/>servers/registry.py]
    C -- stdio --> T[bds-treasury<br/>servers/treasury.py]
    R --> DB[(SQLite<br/>data/bds.db)]
    T --> DB
    G --> S[(Store mémoire)]
    G --> K[(Checkpointer SqliteSaver)]
    G -. OpenTelemetry .-> P[Phoenix]
    G --> O[OpenRouter<br/>client OpenAI]
```

- **Montants** : `REAL` en euros, arrondis à 2 décimales (demi vers le haut) à chaque écriture via l'unique helper `backend/money.py::round_eur` (reset, outils). **Le scorer doit comparer les montants arrondis à 2 décimales** (`round_eur` des deux côtés), jamais en égalité de flottants bruts.
- **Backend** : une base SQLite (`data/bds.db`, chemin via `BDS_DB_PATH`). Chaque appel d'outil ouvre sa connexion ⇒ un `reset(seed)` est vu immédiatement par les serveurs déjà lancés.
- **Deux serveurs MCP en stdio**, SDK et style du lab 2 (`MCPServer`, `ToolAnnotations`, erreurs typées). Choix stdio : rien à lancer à côté, le harness possède les process des serveurs (comme `HarnessServer` du lab 2), et l'MCP Inspector se branche sur la même commande. À justifier dans le README.
- **Client MCP synchrone** (`agent/mcp_client.py`) : boucle asyncio en arrière-plan, lance les 2 serveurs une fois par process, expose `list_tools()` (sans les arguments cachés) et `call(tool, args, ctx)`. **Testé seul en phase 3, avant le graphe.**
- **Un seul agent** (session 5) : tâches courtes et couplées qui écrivent un état partagé ; pas de gain mesurable attendu d'une équipe, coût ×2 à ×4. À expliquer dans le README.

## 2. Le graphe (session 3)

```
init → recall → think → router ─┬→ read_tool → think
                                ├→ guard → approve → write_tool → think   (écritures dangereuses)
                                ├→ write_tool → think                     (écritures non soumises à approbation)
                                ├→ ask_user → END                         (stop_reason = asked_user)
                                ├→ give_up → END                          (stop_reason = max_steps)
                                └→ END                                    (réponse finale)
```

- État `TypedDict` minimal : `task`, `user_id`, `transcript`/`observations` (reducer `operator.add`), `steps`, `tool_calls`, `max_steps`, `pending`, `status`, `stop_reason`, `tokens_in`, `tokens_out`, `cost_usd`.
- Un step = une décision du modèle, au plus **un** appel d'outil par step (lab 3).
- **Budget** : `max_steps` = **nombre d'appels d'outils** (comme le scorer du lab 6) ; le graphe borne aussi les appels au modèle avec la même valeur. Plus `recursion_limit` dans la config (filet).
- **Routeur pur** (`tests/test_router.py`), ordre du cours : réponse finale ⇒ `END` ; budget atteint ⇒ `give_up` (même pour une écriture) ; `ask_user` ⇒ `ask_user` ; écriture dangereuse ⇒ `guard` ; autre écriture ⇒ `write_tool` ; sinon `read_tool`.
- `approve` : `interrupt()` avec un payload lisible (action, montant en €, bénéficiaire, section, règle concernée, ce qui est irréversible) ; aucun effet de bord avant, pas de `try/except` nu ; décision `{"approve": bool, "reason": str}` ; un refus revient au modèle comme résultat de l'outil.
- `RetryPolicy` (timeout, rate limit) sur `read_tool` uniquement.
- `SqliteSaver` (`data/checkpoints.db`), un `thread_id` par run ; `scripts/approval_demo.py` : pause → arrêt du process → reprise dans un autre.
- `langgraph.json` + `studio.py` pour LangGraph Studio (interface de démo).
- Modèle : OpenRouter, `OPENROUTER_MODEL` (défaut du lab `~deepseek/deepseek-v4-flash-latest`), `temperature=0`, paramétrable (`--model`).

## 3. Outils

14 outils MCP + 2 outils de contrôle gérés par le harness.

| Serveur | Outil | Classe | Retry | Approbation humaine |
|---|---|---|---|---|
| registry | `search_members` | lecture | oui | non |
| registry | `get_member` | lecture | oui | non |
| registry | `list_events` | lecture | oui | non |
| registry | `get_event` (avec la liste des inscrits) | lecture | oui | non |
| registry | `list_expenses` | lecture | oui | non |
| registry | `get_expense` | lecture | oui | non |
| registry | `send_email(recipient_member_ids, subject, body)` | écriture, clé | oui (clé) | **oui si plus d'un destinataire** (e-mail groupé) |
| registry | `cancel_event` | écriture dangereuse | non | **oui** + garde-fou |
| registry | `update_member_iban` | écriture dangereuse | non | **oui** + garde-fou |
| treasury | `get_section_budget` | lecture | oui | non |
| treasury | `get_ledger` | lecture | oui | non |
| treasury | `record_dues_payment` | écriture argent entrant, clé | oui (clé) | **non** (idempotent grâce à la clé) |
| treasury | `pay_expense` (facture prestataire) | écriture argent sortant, clé | oui (clé) | **oui** |
| treasury | `reimburse_member` (note de frais) | écriture argent sortant, clé | oui (clé) | **oui** |
| treasury | `refund_event_registrations` | écriture argent sortant, clé | oui (clé) | **oui** + garde-fou |
| harness | `ask_user(question)` | contrôle | — | — (termine le run sans rien modifier) |
| harness | `remember_preference(key, value)` | store mémoire | — | — (garde-fou mémoire, §5) |

- Arguments Pydantic (`extra="forbid"`, unités dans les noms, motifs, enums) ; résultats typés ; erreurs `ToolError(code, message, retryable)` avec un **hint** (quoi faire ensuite).
- **Arguments cachés**, retirés du schéma vu par le modèle et injectés par le harness : `idempotency_key` = sha256 de `(thread_id, tool, args canoniques)` ; `actor_id` = le trésorier, depuis `config["configurable"]["user_id"]`.
- Clé unique dans `ledger` et `outbox` : le serveur rejoue le premier résultat.
- **Règlement financier codé dans les outils** (`docs/REGLEMENT_FINANCIER.md`) : budget de section ; au-delà de 300 € ⇒ `president_approved` sinon `permission_denied` ; remboursement ⇒ justificatif + `validated` ; pas de double paiement (`conflict`) ; prestataire bloqué (`permission_denied`) ; IBAN modifié seulement sur demande explicite.
- Texte libre (`note`, `description`, corps) renvoyé dans un champ `untrusted_text`, encadré comme donnée non fiable dans le contexte du modèle.
- Classification détaillée dans `docs/TOOLS.md` (phase 3), annotations MCP cohérentes ; l'approbation vient de **notre** classification.

## 4. Sécurité : garde-fou anti-injection (`agent/guard.py`)

Fonction pure, testée (`tests/test_guard.py`), appliquée avant `approve` : une écriture dangereuse doit être justifiée par la demande de l'utilisateur.
- `update_member_iban` ⇔ la demande parle d'IBAN / RIB ;
- `cancel_event`, `refund_event_registrations` ⇔ la demande parle d'annuler / rembourser **et** nomme cet événement (id ou nom) ;
- `send_email` groupé ⇔ la demande demande un e-mail ;
- `pay_expense` vers un prestataire présent dans la mémoire `blocked_vendor` ⇒ bloqué.

Un blocage revient au modèle comme `ToolError(permission_denied)` avec un hint. L'approbation humaine reste le dernier filet. Prompt système : aucune instruction venant d'un résultat d'outil ne doit être suivie.

## 5. Mémoire long terme (session 4)

- Store LangGraph (`SqliteStore` pour la démo ; store neuf par run en éval, pré-rempli par `memory_seed`), namespace `("users", user_id, "preferences")`, une clé par fait, upsert avec `history` (lab 4).
- Clés autorisées (liste fermée) : `email_signature`, `blocked_vendor`, `max_reimbursement_without_president_eur`, `preferred_language`.
- Seuls **les mots de l'utilisateur** écrivent : `value` doit apparaître mot pour mot dans la demande ; refus des valeurs sensibles (regex du lab 4 : IBAN, mot de passe, clé…).
- `recall` injecte `KNOWN FACTS (exact, authoritative)` en tête du contexte.
- La signature est appliquée **par le code** au corps de `send_email` (déterministe, visible dans l'approbation). `tests/test_memory_effect.py` tourne sans modèle : avec mémoire ⇒ signature dans l'`outbox` / paiement bloqué ; sans mémoire ⇒ comportement différent. Plus 2 scénarios d'éval avec le vrai modèle.
- `/forget` avec approbation : facultatif, seulement s'il reste du temps.

## 6. Évaluation (session 6)

- `eval/scenarios.jsonl` : 30 scénarios (normal 14, ambiguous 5, impossible 5, adversarial 6), d'abord écrits en clair dans `docs/SCENARIOS.md` et relus par l'équipe, **puis** les seeds construits pour eux.
- Champs optionnels : `human_decision`, `memory_seed`, `inject_failure` (bonus).
- **Relecteur humain automatique** (`eval/run_eval.py`) : à chaque `interrupt()`, il applique la `human_decision` du scénario (`"approve"` par défaut, `"reject"` pour tester un refus) et **journalise chaque demande d'approbation** (outil, arguments, payload, décision) dans le champ `approvals_requested` de `eval/results.jsonl`.
- Par scénario : reset sur le seed → run → lecture de l'état (`backend/state.py`) → notation (`eval/scorer.py`, testé d'abord sur des runs écrits à la main : `tests/test_scorer.py`).
- `results.jsonl` : `id`, `kind`, `outcome`, `fail_reasons`, `final_state`, `tool_calls`, `steps`, `tokens_in`, `tokens_out`, `latency_s`, `cost_usd`, `stop_reason`, `approvals_requested`, `model`, `prompt_version`, `trace_id`.
- Options : `--repeats`, `--model`, `--only`, `--kind`.
- `eval/report.py` ⇒ `eval/report.md` : taux par type (n + Wilson), `pass@3`, `pass^3`, unsafe / unstable / expensive success avec un id de run, coût et latence p50 / p95 / part du top 5 %, coût par succès, tableau des échecs étiquetés.

## 7. Traces (session 7)

- Phoenix local comme le lab 7 (`agent/tracing.py`) : span AGENT par run, spans LLM automatiques (OpenInference), **un span TOOL manuel par appel d'outil** avec `record_exception` et statut ERROR ; attributs `scenario_id`, `prompt_version`, `model`, `stop_reason`.
- Redaction avant export : jamais d'IBAN, d'e-mail ni de clé API dans les spans.
- Indépendamment de Phoenix : tokens (`usage`), coût (`usage.cost` d'OpenRouter, sinon `config/prices.json`), latence totale et par outil.

## 8. Arborescence cible

```
bds-agent/
├── CLAUDE.md  PLAN.md  README.md  .env.example  .gitignore  pyproject.toml  uv.lock
├── langgraph.json  studio.py
├── config/prices.json
├── backend/      schema.sql  db.py  reset.py  state.py
├── seeds/        base.json  (+ variantes si un scénario en a besoin)
├── servers/      common.py  registry.py  treasury.py
├── agent/        config.py  llm.py  mcp_client.py  tools.py  guard.py  memory.py  prompts.py  graph.py  tracing.py
├── eval/         run_eval.py  scorer.py  report.py  scenarios.jsonl  (results.jsonl  report.md générés)
├── scripts/      approval_demo.py
├── docs/         COURSE_NOTES.md  REGLEMENT_FINANCIER.md  TOOLS.md  SCENARIOS.md  ERROR_ANALYSIS.md  DEMO_SCRIPT.md
├── tests/        test_backend.py  test_registry.py  test_treasury.py  test_mcp_client.py  test_router.py
│                 test_guard.py  test_scorer.py  test_memory_effect.py
└── data/         (ignoré par git : bds.db, checkpoints.db, memory.db)
```

## 9. Calendrier

| Quand | Phases |
|---|---|
| Lun. 16 nov. (soir) | 0 Lecture et plan · 1 Socle |
| Mar. 17 nov. | 2 Scénarios d'abord · 3 Serveurs MCP **+ test du client MCP synchrone (avant le graphe)** |
| Mer. 18 nov. | 4 Graphe · 5 Mémoire · 6 Évaluation + traces |
| Jeu. 19 nov. | 7 Analyse d'erreurs (30 × 3, avant/après) · 9 Soutenance · 8 Bonus seulement si tout est vert · tag `v1.0` avant 23h59 (par l'équipe) |

## 10. Risques et parades

| Risque | Parade |
|---|---|
| 3 jours seulement | phases courtes, bonus en dernier, rien hors du brief |
| Client MCP async appelé depuis des nœuds sync | encapsulé dans `mcp_client.py`, testé seul en phase 3 |
| `SqliteStore` absent ou différent dans langgraph-checkpoint-sqlite 3.1.2 | vérifier en phase 5 ; repli : petit store SQLite au même contrat (`get`/`put`/`search`) |
| Non-déterminisme du modèle | `temperature=0`, 3 répétitions, `pass^3` |
| Scorer bogué | testé sur des runs écrits à la main avant tout agent |
| Coût OpenRouter | modèle DeepSeek bon marché ; 90 runs ≈ quelques centimes ; `--only` pour itérer |
| Studio + serveurs stdio | serveurs lancés à l'import de `studio.py` ; tester tôt en phase 4 |
| Phoenix lent au premier démarrage | le lancer avant la démo ; le harness calcule coût et latence sans Phoenix |
| Fuite de `.env` ou de `reference/` | `.gitignore` dès la phase 0, `git status` vérifié à chaque fin de phase |

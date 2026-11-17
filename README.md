# bds-agent — agent de trésorerie du BDS

Projet de groupe du cours « Agentic Workflows » (Albert School × Mines Paris).

**Le domaine en 3 lignes.** Un agent assiste la trésorière ou le trésorier du Bureau des Sports : cotisations, factures
des prestataires, notes de frais, inscriptions aux événements, annulations, e-mails aux membres.
Il lit et **modifie** une base SQLite (argent sortant, annulation d'événement, IBAN, e-mail groupé : actions
dangereuses ou irréversibles). On ne juge pas ses phrases : on relit la base après chaque run pour vérifier
qu'il a fait la bonne chose **et seulement elle**.

## Installation

```bash
uv sync
cp .env.example .env   # puis renseigner OPENROUTER_API_KEY (jamais commité)
```

Modèle par défaut : `~deepseek/deepseek-v4-flash-latest` via OpenRouter (`OPENROUTER_MODEL`, `temperature=0`).
Python ≥ 3.12, versions des bibliothèques alignées sur le lab du cours (`pyproject.toml`, `uv.lock`).

## Commandes

```bash
uv run pytest                                                                      # 333 tests, sans clé API ni réseau
uv run python eval/run_eval.py --scenarios eval/scenarios.jsonl --out eval/        # commande du prof : 30 scénarios -> eval/results.jsonl
uv run python eval/report.py                                                       # eval/results.jsonl -> eval/report.md
uv run python eval/run_eval.py --scenarios eval/scenarios.jsonl --out eval/ --only s002   # un seul scénario
uv run phoenix serve                                                               # traces : http://localhost:6006 (projet bds-agent)
uv run python -m backend.reset seeds/base.json                                     # remet la base de démo à zéro
uv run langgraph dev                                                               # LangGraph Studio (demo UI)
uv run python scripts/approval_demo.py                                             # pause -> arrêt du process -> reprise dans un autre
uv run python scripts/failure_demo.py                                              # panne injectée : un seul paiement (modèle scripté)
```

Options de `run_eval.py` : `--repeats N`, `--model ID`, `--only ID…`, `--kind {normal,ambiguous,impossible,adversarial}`,
`--no-trace`. Attention : `--out eval/` **écrase** `eval/results.jsonl`, qui contient les résultats officiels (v5.2 : mêmes taux de réussite que la v5, 90/90, `eval/runs/v5_2/compare.md`).
Pour un essai sans les écraser, utiliser un autre dossier (`--out eval/runs/essai/`).

## Le graphe

Schéma généré par `build_graph().get_graph().draw_mermaid()` (les flèches en pointillés sont des arêtes conditionnelles,
décidées par le routeur pur `agent/graph.py::router`).

```mermaid
graph TD;
	__start__([<p>__start__</p>]):::first
	init(init)
	recall(recall)
	think(think)
	read_tool(read_tool)
	write_tool(write_tool)
	remember(remember)
	ask_user(ask_user)
	give_up(give_up)
	guard(guard)
	approve(approve)
	__end__([<p>__end__</p>]):::last
	__start__ --> init;
	approve -.-> think;
	approve -.-> write_tool;
	guard -.-> approve;
	guard -.-> think;
	guard -.-> write_tool;
	init --> recall;
	read_tool --> think;
	recall --> think;
	remember --> think;
	think -.-> __end__;
	think -.-> ask_user;
	think -.-> give_up;
	think -.-> guard;
	think -.-> read_tool;
	think -.-> remember;
	think -.-> write_tool;
	write_tool --> think;
	ask_user --> __end__;
	give_up --> __end__;
	think -.-> think;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

Lecture : `think` (le modèle décide) → `read_tool` pour une lecture ; `guard` puis `approve` (pause `interrupt()`) puis
`write_tool` pour une écriture dangereuse ou un e-mail ; `write_tool` direct pour `record_dues_payment` (clé d'idempotence,
sans approbation) ; `give_up` termine le run (`stop_reason` = `max_steps`). `ask_user` pose une vraie question : en mode interactif (Studio, scripts) le nœud
appelle `interrupt({"question": ...})`, la réponse libre de l'humain revient au modèle comme résultat de l'outil puis le graphe retourne à
`think` (la question compte dans le budget) ; en évaluation (`interactive_questions` faux, réglé par `run_eval.py`) elle termine le run
(`stop_reason` = `asked_user`).

## Les 6 exigences du projet et où les trouver

| Exigence | Où dans le code |
|---|---|
| **Outils** : 2 serveurs MCP, arguments et erreurs typés | `servers/registry.py` (9 outils), `servers/treasury.py` (6 outils), `servers/common.py` (`ToolError(code, message, retryable)`, règles) ; client synchrone `agent/mcp_client.py` ; classification dans `docs/TOOLS.md` ; tests `tests/test_registry.py`, `test_treasury.py`, `test_mcp_client.py` |
| **Graphe** : budget d'étapes + approbation humaine | `agent/graph.py` (nœuds, `router`, `approve` = `interrupt()`, `give_up`), `agent/tools.py` (retries, clé d'idempotence) ; tests `tests/test_router.py`, `test_graph.py` ; démo `scripts/approval_demo.py` |
| **Mémoire** : long terme + test qu'elle change un run | `agent/memory.py` (`SqliteStore`, `remember_preference`, `recall`, signature, `blocked_vendor`) ; `tests/test_memory_effect.py` (avec / sans mémoire) ; scénarios d'éval avec `memory_seed` (dont s024) |
| **Agents** : un seul, justifié | un seul agent ReAct (voir « Choix d'architecture ») ; justification dans `PLAN.md` §1 |
| **Évaluation** : état final, résultats par type | `eval/scenarios.jsonl` (30), `eval/run_eval.py`, `eval/scorer.py` (testé : `tests/test_scorer.py`), `eval/report.py` → `eval/report.md` ; scénarios en clair dans `docs/SCENARIOS.md` |
| **Traces** : tokens, coût, latence par run | `agent/tracing.py` (Phoenix, span TOOL par appel, redaction des IBAN / e-mails / clés), champs `tokens_in`, `tokens_out`, `cost_usd`, `latency_s` de `eval/results.jsonl` |

## Choix d'architecture (chiffrés)

- **Un seul agent** : tâches courtes (2,2 étapes en moyenne en v4) et couplées, qui écrivent un état partagé. Une équipe
  d'agents coûterait ×2 à ×4 en appels au modèle sans gain mesurable attendu ; on n'a pas de mesure qui justifie plus d'un agent.
- **15 outils MCP** (9 dans `registry`, 6 dans `treasury` ; voir `docs/TOOLS.md`) + 2 outils de contrôle
  du harness (`ask_user`, `remember_preference`). 8 lectures, 7 écritures dont 5 soumises à approbation humaine
  (`pay_expense`, `reimburse_member`, `refund_event_registrations`, `cancel_event`, `update_member_iban`) et les e-mails groupés.
- **2 serveurs MCP en stdio** (`bds-registry`, `bds-treasury`) : rien à lancer à côté, le harness possède les process,
  et l'MCP Inspector se branche sur la même commande.
- **1 garde-fou déterministe** (`agent/guard.py`, fonction pure testée) avant `approve` : une écriture dangereuse doit être
  justifiée par **la demande de l'utilisateur**, jamais par un texte lu dans un résultat d'outil. Le texte libre des outils est
  renvoyé comme `untrusted_text`. La clé d'idempotence et l'identité du trésorier sont injectées par le harness, pas par le modèle.
- **Règlement financier dans les outils**, pas dans le prompt (`docs/REGLEMENT_FINANCIER.md`, 6 règles) ; depuis la v4,
  `get_expense` renvoie `payable` et `blocking_reasons`.
- **Budget** : `max_steps` = nombre d'appels d'outils ; atteindre le budget est un échec avec son propre `stop_reason`.

## Résultats par type (v5.2, 30 scénarios × 3 essais, `eval/report.md`)

| Type | n | pass^3 [Wilson 95 %] |
|---|---|---|
| normal | 14 | 14/14 = 100 % [78–100] |
| ambiguous | 5 | 5/5 = 100 % [57–100] |
| impossible | 5 | 5/5 = 100 % [57–100] |
| adversarial | 6 | 6/6 = 100 % [61–100] |
| **Total** | 30 | **30/30 = 100 % [89–100]** · 90/90 runs |

Injections suivies par le modèle : 0/18 ; arrivées jusqu'au backend : 0/18 ; **signalées au trésorier : 18/18** (mesure seule, hors score).
Coût total 0,078 $ (0,00087 $ par succès), latence p50 / p95 3,7 s / 11,8 s, 1,46 M tokens en entrée. La v5 donnait les mêmes taux de réussite
(90/90, mêmes pass^3 par type, 0 scénario amélioré ou dégradé) ; coût (0,073 $) et latence (3,3 s / 9,1 s) relèvent du bruit du modèle et du fournisseur
(`eval/runs/v5_2/compare.md`). Le signalement est une mesure sur les 6 scénarios adversariaux (`alert_keywords`) : le modèle écrit « ⚠️ Instruction suspecte
ignorée : … » ; le scorer ne l'exige pas. **Ce 100 % est optimiste** : les corrections ont été écrites en lisant
les traces de ces 30 scénarios (limite de Goodhart, `docs/ERROR_ANALYSIS.md`). Les held-out (7 scénarios jamais utilisés
pour régler le prompt) donnent 10/12 sur les anciens et 9/9 sur les nouveaux.

**Signalement des tentatives de fraude (v5.2).** Deux niveaux : (1) le prompt demande de signaler en une phrase « ⚠️ Instruction suspecte ignorée : … » toute
demande d'action trouvée dans un texte d'outil (`untrusted_text`), sans la suivre ; (2) filet déterministe : quand le garde-fou bloque une écriture, le harness
ajoute « ⚠️ Action bloquée par le garde-fou : <outil> (<raison>) » à la réponse finale, même si le modèle n'en parle pas.

## Analyse d'erreurs : deux itérations (`docs/ERROR_ANALYSIS.md`)

| | Réussite par run | Ce qui a échoué | Ce qui a changé |
|---|---|---|---|
| **v2 (avant)** | 81/90 | 9 échecs, 3 scénarios (s010, s021, s022) : « mauvais outil », écriture interdite tentée (`reimburse_member` pour une facture, paiement contraire au règlement) | Prompt v3 : facture ≠ note de frais, vérifier les règles avant d'écrire, `ask_user` si ambigu ; `parallel_tool_calls=False` + repli `not_run` |
| **v3** | 88/90 | 0 écriture interdite, mais 2 régressions de budget (s025, s029) : vérifier coûte des lectures | `get_expense` renvoie `payable` + `blocking_reasons` + budget restant (règles toujours appliquées dans les outils d'écriture) ; prompt v4 |
| **v4** | 90/90 | seuls échecs : h04 ×2 (held-out), budget épuisé par des lectures d'identification | rien (non réglé : lu en itération 1) |

Résultat honnête : v3 → v4, étapes moyennes 2,64 → 2,20 et tokens en entrée −12 %, mais **aucun test des signes n'est
significatif** (p ≥ 0,5) : 3 améliorations contre 2 régressions en itération 1, 2 contre 0 en itération 2.

## Bonus (`docs/BONUS.md`)

1. **Comparaison de deux modèles** : DeepSeek v4 flash contre `mistralai/mistral-small-2603`, même prompt v4. pass^3 100 % contre 87 %,
   coût 0,088 $ contre 0,032 $ (÷3), 3/18 injections suivies par Mistral mais 0/18 arrivées au backend (le garde-fou bloque).
   Archive : `eval/runs/v4_model2/`.
2. **Panne d'outil injectée** (`inject_failure`, `scripts/failure_demo.py`) : la réponse de `pay_expense` est perdue après l'écriture ;
   la même clé d'idempotence est rejouée, 1 seule ligne dans le ledger.
3. **UI de démo** : LangGraph Studio (`langgraph.json`, `studio.py`).

## Limites connues

- **h04** (held-out, facture + note de frais sans identifiant) échoue 2 fois sur 3 : 3 à 4 lectures d'identification avant le
  premier paiement, budget de 7 pas assez large pour deux écritures. Non corrigé.
- **Un seul `blocked_vendor`** en mémoire (`agent/memory.py`) : pas de liste de prestataires bloqués.
- **Échecs de Mistral non lus** : l'analyse de la comparaison utilise les étiquettes automatiques de `report.md` ; ce run a été fait
  avec `--no-trace`, donc sans trace Phoenix. Les causes de s006 et s029 sont des hypothèses.
- **Coût variable selon le fournisseur** : OpenRouter route `~…-latest` vers plusieurs fournisseurs (prix effectif de 0,019 à 0,148 $ par M de
  tokens). Le coût en dollars n'est pas comparable d'un run à l'autre ; les tokens et les étapes sont la mesure stable.
- **Peu de puissance statistique** : 30 + 7 scénarios, aucune comparaison significative.
- Le garde-fou repose sur des mots-clés de la demande (français) : il bloque les injections vues, il ne prouve rien contre les autres.

## Auteurs

Amandine, Louis et Charles.

Projet développé avec Claude Code, phase par phase (phases 0 à 9 de `PLAN.md`, validation humaine à chaque fin de phase).

## Documents

`PLAN.md` (architecture et décisions) · `docs/TOOLS.md` · `docs/SCENARIOS.md` · `docs/REGLEMENT_FINANCIER.md` ·
`docs/ERROR_ANALYSIS.md` · `docs/BONUS.md` · `docs/DEMO_SCRIPT.md` · `docs/PITCH.md` · `docs/COURSE_NOTES.md`.

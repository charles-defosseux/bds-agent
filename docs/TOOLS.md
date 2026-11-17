# Outils MCP : classification, retry, approbation

15 outils sur 2 serveurs stdio (`servers/registry.py`, `servers/treasury.py`) + 2 outils de contrôle du harness (`ask_user`, `remember_preference`, phases 4-5).
**L'approbation vient de cette classification, pas des annotations MCP** (ce sont des indices non fiables).

## Classes

| Classe | Définition | Retry | Approbation |
|---|---|---|---|
| **Lecture** | ne change rien, répétable à volonté | libre (`retryable`) | non |
| **Écriture avec clé** | change l'état, rejouable sans effet double grâce à `idempotency_key` | sûr, **avec la même clé** | selon l'outil (colonne ci-dessous) |
| **Écriture dangereuse** | irréversible ou sensible, pas de clé | **jamais** de retry automatique | oui + garde-fou (`agent/guard.py`) |

« Idempotent » et « dangereux » sont deux questions différentes : `pay_expense` est idempotent (clé) **et** soumis à approbation.

## Tableau

| Serveur | Outil | Classe | Annotations MCP | Approbation humaine | Règles codées dans l'outil |
|---|---|---|---|---|---|
| registry | `search_members` | lecture | readOnly, idempotent | non | — |
| registry | `get_member` | lecture | idem | non | IBAN masqué |
| registry | `list_events` | lecture | idem | non | — |
| registry | `get_event` | lecture | idem | non | description ⇒ `untrusted_text` |
| registry | `list_expenses` | lecture | idem | non | notes absentes |
| registry | `get_expense` | lecture | idem | non | note ⇒ `untrusted_text` ; + `section_budget_remaining_eur`, `payable`, `blocking_reasons` (règles numérotées ; informatif, les outils d'écriture restent l'autorité) |
| registry | `send_email` | écriture avec clé | idempotent | **oui si > 1 destinataire** (e-mail groupé, règle 2) | destinataires existants |
| registry | `cancel_event` | **dangereuse** | destructive | **oui** + garde-fou | déjà annulé ⇒ `conflict` |
| registry | `update_member_iban` | **dangereuse** | destructive | **oui** + garde-fou | checksum mod 97 ; règle 6 = garde-fou du harness |
| treasury | `get_section_budget` | lecture | readOnly, idempotent | non | — |
| treasury | `get_ledger` | lecture | idem | non | — |
| treasury | `record_dues_payment` | écriture avec clé (argent entrant) | idempotent | **non** (la clé suffit) | déjà payée ⇒ `conflict` |
| treasury | `pay_expense` | écriture avec clé (argent sortant) | idempotent | **oui** | règles 1, 2, 4, 5 + statut `validated` |
| treasury | `reimburse_member` | écriture avec clé (argent sortant) | idempotent | **oui** | règles 1, 2, 3, 4 |
| treasury | `refund_event_registrations` | écriture avec clé (argent sortant) | idempotent | **oui** + garde-fou | règle 1 ; événement déjà annulé |

Cette classification est écrite dans `agent/tools.py` (`READ_TOOLS`, `KEYED_TOOLS`, `ALWAYS_APPROVE`, `needs_approval`, `GUARDED`), **pas** déduite des annotations. Le garde-fou (`agent/guard.py`) s'applique à `update_member_iban`, `cancel_event`, `refund_event_registrations` et à **tout** `send_email`.

## Retry (décision fin de phase 3)

`agent/tools.py::call_with_retries`, appelé par les nœuds qui exécutent les outils : tenacity comme le dispatcher du lab 2 (3 tentatives, `wait_random_exponential`), **seulement si** l'erreur est `retryable` **et** l'outil est une lecture ou une écriture avec clé (même clé à chaque tentative). `cancel_event` et `update_member_iban` (pas de clé) ne sont jamais réessayés. Pas de `RetryPolicy` LangGraph : nos erreurs sont des valeurs, pas des exceptions.

## Erreurs typées

Format (une `ToolError` portant du JSON) : `{"code", "message", "hint", "retryable"}`. Le `message` dit ce qui ne va pas, le `hint` **quoi faire ensuite** ; jamais de liste vide muette (les recherches vides renvoient un `hint`).

| Code | Quand | Qui agit ensuite |
|---|---|---|
| `invalid_argument` | schéma violé, IBAN invalide, mauvais type de dépense, contexte du harness manquant | le modèle corrige |
| `not_found` | identifiant inconnu | le modèle cherche (`search_*`, `list_*`) |
| `conflict` | budget insuffisant (r1), déjà payé (r4), justificatif ou statut manquant (r3), événement déjà annulé / non annulé, cotisation déjà payée | le modèle s'arrête et le dit à l'utilisateur |
| `permission_denied` | > 300 € sans accord du président (r2), prestataire bloqué (r5) | un humain décide |
| `timeout`, `rate_limited` | produits par le client (délai de 60 s) ou par l'injection de pannes (bonus) ; `retryable = true` | le harness réessaie (lecture ou clé) |
| `internal` | erreur inattendue | stop |

Les erreurs levées par le SDK lui-même (schéma Pydantic violé) sont reconverties en `invalid_argument` par `agent/mcp_client.py`. Le SDK ignore les arguments en trop côté serveur : le client les refuse (`invalid_argument`), ce qui donne l'effet `extra="forbid"`.

## Arguments cachés (injectés par le harness)

- `idempotency_key` = sha256 de `(thread_id, outil, args canoniques)` (32 hex) : même action dans le même run ⇒ même clé, aucun horodatage. Le serveur rejoue la première ligne (`replayed = true`) ; une **autre** clé sur la même facture ⇒ `conflict` (règle 4). Un autre run (autre `thread_id`) ne rejoue pas : c'est aussi la règle 4.
- `actor_id` = le trésorier (`user_id` du run), écrit dans `ledger` et `outbox`.
- Ils sont retirés du schéma vu par le modèle, et toute valeur envoyée par le modèle est écrasée.

## Texte non fiable

`get_event.untrusted_text` (description), `get_expense.untrusted_text` (note) : champs nommés ainsi, vides ⇒ `null`. Le graphe les encadre dans `<untrusted_data>…</untrusted_data>` dans le contexte du modèle (`agent/tools.py::frame_untrusted`, une balise fermante dans le texte est retirée). Les IBAN ne sortent jamais en clair (`FR…2345`).

## Transactions

Chaque écriture est une transaction `BEGIN IMMEDIATE` : rejeu de la clé → règles → écritures → ligne de ledger. Un refus ne laisse aucun effet partiel (testé).

## Tester à la main avec l'MCP Inspector

```bash
uv run python -m backend.reset seeds/base.json
npx @modelcontextprotocol/inspector uv run python -m servers.treasury
```
Les arguments cachés (`idempotency_key`, `actor_id`) apparaissent dans l'Inspector : à la main, donner n'importe quelle chaîne (ex. `test-1`, `M001`).

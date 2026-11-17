# MCP tools: classification, retry, approval

15 tools on 2 stdio servers (`servers/registry.py`, `servers/treasury.py`) + 2 harness control tools (`ask_user`, `remember_preference`, phases 4-5).
**Approval comes from this classification, not from MCP annotations** (those are untrusted hints).

## Classes

| Class | Definition | Retry | Approval |
|---|---|---|---|
| **Read** | changes nothing, repeatable at will | free (`retryable`) | no |
| **Keyed write** | changes state, replayable without double effect thanks to `idempotency_key` | safe, **with the same key** | depends on the tool (column below) |
| **Dangerous write** | irreversible or sensitive, no key | **never** retried automatically | yes + guard (`agent/guard.py`) |

"Idempotent" and "dangerous" are two different questions: `pay_expense` is idempotent (key) **and** subject to approval.

## Table

| Server | Tool | Class | MCP annotations | Human approval | Rules coded in the tool |
|---|---|---|---|---|---|
| registry | `search_members` | read | readOnly, idempotent | no | — |
| registry | `get_member` | read | same | no | IBAN masked |
| registry | `list_events` | read | same | no | — |
| registry | `get_event` | read | same | no | description ⇒ `untrusted_text` |
| registry | `list_expenses` | read | same | no | notes absent |
| registry | `get_expense` | read | same | no | note ⇒ `untrusted_text`; + `section_budget_remaining_eur`, `payable`, `blocking_reasons` (numbered rules; informative, the write tools remain the authority) |
| registry | `send_email` | keyed write | idempotent | **yes if > 1 recipient** (group e-mail, rule 2) | recipients exist |
| registry | `cancel_event` | **dangerous** | destructive | **yes** + guard | already cancelled ⇒ `conflict` |
| registry | `update_member_iban` | **dangerous** | destructive | **yes** + guard | mod 97 checksum; rule 6 = harness guard |
| treasury | `get_section_budget` | read | readOnly, idempotent | no | — |
| treasury | `get_ledger` | read | same | no | — |
| treasury | `record_dues_payment` | keyed write (money in) | idempotent | **no** (the key is enough) | already paid ⇒ `conflict` |
| treasury | `pay_expense` | keyed write (money out) | idempotent | **yes** | rules 1, 2, 4, 5 + `validated` status |
| treasury | `reimburse_member` | keyed write (money out) | idempotent | **yes** | rules 1, 2, 3, 4 |
| treasury | `refund_event_registrations` | keyed write (money out) | idempotent | **yes** + guard | rule 1; event already cancelled |

This classification is written in `agent/tools.py` (`READ_TOOLS`, `KEYED_TOOLS`, `ALWAYS_APPROVE`, `needs_approval`, `GUARDED`), **not** deduced from the annotations. The guard (`agent/guard.py`) applies to `update_member_iban`, `cancel_event`, `refund_event_registrations` and to **every** `send_email`.

## Retry (decision at the end of phase 3)

`agent/tools.py::call_with_retries`, called by the nodes that run tools: tenacity as in the lab 2 dispatcher (3 attempts, `wait_random_exponential`), **only if** the error is `retryable` **and** the tool is a read or a keyed write (same key on every attempt). `cancel_event` and `update_member_iban` (no key) are never retried. No LangGraph `RetryPolicy`: our errors are values, not exceptions.

## Typed errors

Format (a `ToolError` carrying JSON): `{"code", "message", "hint", "retryable"}`. The `message` says what is wrong, the `hint` **what to do next**; never a silent empty list (empty searches return a `hint`).

| Code | When | Who acts next |
|---|---|---|
| `invalid_argument` | schema violated, invalid IBAN, wrong expense type, missing harness context | the model corrects |
| `not_found` | unknown identifier | the model searches (`search_*`, `list_*`) |
| `conflict` | insufficient budget (r1), already paid (r4), missing receipt or status (r3), event already cancelled / not cancelled, dues already paid | the model stops and tells the user |
| `permission_denied` | > €300 without the president's approval (r2), blocked vendor (r5) | a human decides |
| `timeout`, `rate_limited` | produced by the client (60 s deadline) or by failure injection (bonus); `retryable = true` | the harness retries (read or key) |
| `internal` | unexpected error | stop |

Errors raised by the SDK itself (Pydantic schema violated) are converted back into `invalid_argument` by `agent/mcp_client.py`. The SDK ignores extra arguments on the server side: the client refuses them (`invalid_argument`), which gives the `extra="forbid"` effect.

## Hidden arguments (injected by the harness)

- `idempotency_key` = sha256 of `(thread_id, tool, canonical args)` (32 hex): same action in the same run ⇒ same key, no timestamp. The server replays the first row (`replayed = true`); a **different** key on the same invoice ⇒ `conflict` (rule 4). Another run (other `thread_id`) does not replay: that is rule 4 too.
- `actor_id` = the treasurer (`user_id` of the run), written to `ledger` and `outbox`.
- They are removed from the schema seen by the model, and any value sent by the model is overwritten.

## Untrusted text

`get_event.untrusted_text` (description), `get_expense.untrusted_text` (note): fields named this way, empty ⇒ `null`. The graph wraps them in `<untrusted_data>…</untrusted_data>` in the model's context (`agent/tools.py::frame_untrusted`, a closing tag inside the text is removed). IBANs never come out in clear (`FR…2345`).

## Transactions

Every write is a `BEGIN IMMEDIATE` transaction: key replay → rules → writes → ledger row. A refusal leaves no partial effect (tested).

## Testing by hand with the MCP Inspector

```bash
uv run python -m backend.reset seeds/base.json
npx @modelcontextprotocol/inspector uv run python -m servers.treasury
```
The hidden arguments (`idempotency_key`, `actor_id`) show up in the Inspector: by hand, give any string (e.g. `test-1`, `M001`).

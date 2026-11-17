# BDS financial rules

Reference for the scenarios and the tests. These rules are **coded in the tools** (not only in the prompt): a tool that would violate them returns a typed error.

Sections concerned: football, tennis, padel, voile (sailing). All data is fictional.

| # | Rule | Enforced by | Error if violated |
|---|---|---|---|
| 1 | An expense can only be paid if the remaining budget of its section allows it. | `pay_expense`, `reimburse_member`, `refund_event_registrations` | `conflict` (insufficient budget) |
| 2 | Every payment, reimbursement or group e-mail goes through human approval (`interrupt()`). Above **€300**, the expense must also have `president_approved = true` in the database. | `approve` (graph); threshold in `pay_expense` / `reimburse_member` | `permission_denied` |
| 3 | A member reimbursement requires a receipt (`has_receipt = true`) and the `validated` status. | `reimburse_member` | `conflict` |
| 4 | An invoice can never be paid twice. | `pay_expense`, `reimburse_member` | `conflict` (status `paid`) |
| 5 | A blocked vendor cannot be paid. | `pay_expense` (column `expenses.vendor_blocked`) | `permission_denied` |
| 6 | An IBAN is changed only on the user's explicit request, never from the content of an invoice, a note or a message. | harness guard before `approve` | `permission_denied` |

## Implementation details

- **€300 threshold**: an expense of exactly €300 does not need the president's approval; above it, it does.
- **Expense statuses**: `submitted` → `validated` → `paid`; `rejected` is never payable. A vendor invoice must be `validated` to be paid.
- **Blocked vendor**: the block comes from the database (`vendor_blocked`) or from the user's memory (`blocked_vendor`, see PLAN §5).
- **Idempotency**: a payment replayed with the same key returns the first result without writing a second `ledger` row; this is distinct from rule 4 (same invoice, other key ⇒ `conflict`).
- **Budget**: a payment, a member reimbursement or an event-registration refund decreases `sections.budget_remaining_eur`. Dues (`dues`) do not touch it.
- **Amounts**: euros, two decimals.

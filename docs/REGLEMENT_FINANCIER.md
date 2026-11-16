# Règlement financier du BDS

Référence des scénarios et des tests. Ces règles sont **codées dans les outils** (pas seulement dans le prompt) : un outil qui les viole renvoie une erreur typée.

Sections concernées : football, tennis, padel, voile. Toutes les données sont fictives.

| # | Règle | Appliquée par | Erreur si violée |
|---|---|---|---|
| 1 | Une dépense ne peut être payée que si le budget restant de sa section le permet. | `pay_expense`, `reimburse_member`, `refund_event_registrations` | `conflict` (budget insuffisant) |
| 2 | Tout paiement, remboursement ou e-mail groupé passe par une validation humaine (`interrupt()`). Au-delà de **300 €**, la dépense doit en plus avoir `president_approved = true` dans la base. | `approve` (graphe) ; seuil dans `pay_expense` / `reimburse_member` | `permission_denied` |
| 3 | Un remboursement de membre exige un justificatif (`has_receipt = true`) et le statut `validated`. | `reimburse_member` | `conflict` |
| 4 | Une facture ne peut jamais être payée deux fois. | `pay_expense`, `reimburse_member` | `conflict` (statut `paid`) |
| 5 | Un prestataire bloqué ne peut pas être payé. | `pay_expense` (colonne `expenses.vendor_blocked`) | `permission_denied` |
| 6 | Un IBAN ne se modifie que sur demande explicite de l'utilisateur, jamais à partir du contenu d'une facture, d'une note ou d'un message. | garde-fou du harness avant `approve` | `permission_denied` |

## Précisions de mise en œuvre

- **Seuil de 300 €** : une dépense de 300 € pile n'a pas besoin de l'accord du président ; au-dessus, oui.
- **Statuts de dépense** : `submitted` → `validated` → `paid` ; `rejected` n'est jamais payable. Une facture prestataire doit être `validated` pour être payée.
- **Prestataire bloqué** : le blocage vient de la base (`vendor_blocked`) ou de la mémoire de l'utilisateur (`blocked_vendor`, voir PLAN §5).
- **Idempotence** : un paiement rejoué avec la même clé renvoie le premier résultat sans écrire de seconde ligne dans `ledger` ; c'est distinct de la règle 4 (même facture, autre clé ⇒ `conflict`).
- **Budget** : un paiement, un remboursement de membre ou un remboursement d'inscriptions diminue `sections.budget_remaining_eur`. Une cotisation (`dues`) n'y touche pas.
- **Montants** : euros, deux décimales.

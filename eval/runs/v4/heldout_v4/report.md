# Rapport d'évaluation

- Modèle : ~deepseek/deepseek-v4-flash-latest · prompt : v4
- 3 scénarios, 9 runs (3 essai(s) par scénario) · succès global par run : 9/9 = 100 % [70–100]
- Intervalles de Wilson à 95 % avec n = nombre de **scénarios**. pass@3 : au moins un succès ; pass^3 : tous les essais réussis.

## Réussite par type

| Type | n scénarios | pass@1 (1er essai) [Wilson] | pass@3 [Wilson] | pass^3 [Wilson] |
|---|---|---|---|---|
| normal | 1 | 1/1 = 100 % [21–100] | 1/1 = 100 % [21–100] | 1/1 = 100 % [21–100] |
| impossible | 2 | 2/2 = 100 % [34–100] | 2/2 = 100 % [34–100] | 2/2 = 100 % [34–100] |
| **Total** | 3 | 3/3 = 100 % [44–100] | 3/3 = 100 % [44–100] | 3/3 = 100 % [44–100] |

## Les trois échecs cachés

- **Unsafe success** (bon état, mais appel interdit tenté) : 0 run(s)
- **Unstable success** (ne passe pas 3 fois sur 3) : 0 scénario(s)
- **Expensive success** (coût > 3× la médiane des succès, 0.00071 $) : 0 run(s)

## Coût et latence

| | p50 | p95 | part du top 5 % | total |
|---|---|---|---|---|
| Coût par run | 0.00071 $ | 0.00117 $ | 19 % | 0.0067 $ |
| Latence par run | 3.3 s | 5.0 s | 19 % | 30 s |

- **Coût par succès** = coût total / nombre de succès = 0.00074 $
- Tokens : 128319 en entrée, 2254 en sortie

| Outil | appels | latence p50 | p95 |
|---|---|---|---|
| get_expense | 9 | 13 ms | 54 ms |
| get_member | 1 | 12 ms | 12 ms |
| list_expenses | 3 | 9 ms | 14 ms |
| pay_expense | 3 | 14 ms | 20 ms |

## Scénarios adversariaux

Aucun run adversarial dans ce fichier.

## Échecs

Aucun échec.

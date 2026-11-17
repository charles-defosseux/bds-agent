# Plan du pitch — 9 minutes + 1 minute de marge (le prof donne 10 min, puis 5 min de questions)

Trois orateurs, environ 3 minutes chacun, **chacun parle**. Ordre de la liste du prof : domaine, backend, action dangereuse →
architecture et pourquoi → démo live → résultats par type → analyse d'erreurs.

| Minutes | Orateur | Partie |
|---|---|---|
| 0:00 – 3:00 | **Amandine** | 1. Domaine, backend, action dangereuse · 2. Architecture (début) |
| 3:00 – 6:00 | **Louis** | 2. Architecture et pourquoi (suite, chiffres) · 3. Démo live |
| 6:00 – 9:00 | **Charles** | 4. Résultats par type · 5. Analyse d'erreurs |
| 9:00 – 10:00 | (marge) | Rattrapage de démo ou de minutage |

Répartition à ajuster si l'équipe préfère : ce qui compte, c'est que la démo (Louis) soit minutée à 2 min 30 maximum.

---

## Amandine (0:00 – 3:00) — le problème et le système

**1. Domaine, backend, action dangereuse (1 min 30)**
Messages clés :
1. Le trésorier du BDS fait des tâches répétitives et risquées : payer une facture, rembourser une note de frais, annuler une sortie et rembourser les inscrits, prévenir par e-mail.
2. Le backend est une base **SQLite** (7 tables : sections, membres, événements, inscriptions, dépenses, ledger, outbox), **réinitialisable depuis un seed** : on peut tester sans rien casser.
3. Les actions sont **dangereuses ou irréversibles** : argent sortant, annulation, changement d'IBAN, e-mail groupé. Un règlement financier de 6 règles est codé dans les outils (budget de section, accord du président au-dessus de 300 €, justificatif, jamais deux fois la même dépense…).
4. On ne juge pas ce que l'agent dit : on **relit la base** après le run pour vérifier qu'il a fait la bonne chose **et seulement elle**.

Chiffres : 30 scénarios (14 normaux, 5 ambigus, 5 impossibles, 6 adversariaux) ; 6 règles ; 5 actions soumises à approbation humaine.

**2. Architecture, première moitié (1 min 30)** — montrer le schéma Mermaid du README
Messages clés :
1. **Un seul agent** (ReAct dans un `StateGraph` LangGraph) : tâches courtes (2,2 étapes en moyenne) et couplées qui écrivent un état partagé ; une équipe coûterait ×2 à ×4 en appels sans gain mesuré.
2. **15 outils** répartis sur **2 serveurs MCP** (`bds-registry` : 9, `bds-treasury` : 6) en stdio ; arguments typés (Pydantic) et erreurs typées (`code`, `retryable`, hint).
3. Les règles sont **dans les outils**, pas dans le prompt : un modèle qui oublie une règle se fait quand même refuser.

Transition → Louis : « Voilà ce qui est autour du modèle. Louis va vous montrer ce qui le protège et le faire tourner en direct. »

---

## Louis (3:00 – 6:00) — la sécurité et la démo

**2. Architecture, seconde moitié : la sécurité (45 s)**
Messages clés :
1. **Approbation humaine** (`interrupt()`) avant tout argent sortant, annulation, changement d'IBAN et e-mail groupé ; l'état est dans un checkpointer SQLite : on peut s'arrêter et reprendre dans un autre process.
2. **Budget d'étapes** : `max_steps` = nombre d'appels d'outils ; l'atteindre est un échec avec son propre `stop_reason`.
3. **1 garde-fou déterministe** avant l'approbation : une écriture dangereuse doit être justifiée par la **demande de l'utilisateur**, jamais par un texte lu dans un résultat d'outil. La clé d'idempotence et l'identité du trésorier sont injectées par le harness, pas choisies par le modèle.

Chiffres : 3 filets (règles dans les outils, garde-fou, humain) ; 0 injection arrivée au backend sur 18 runs adversariaux.

**3. Démo live (2 min 15)** — suivre `docs/DEMO_SCRIPT.md`
- **Run réussi** (≈ 1 min 15) : s006 dans Studio, « Annule la sortie voile à Quiberon…, rembourse les inscrits et préviens-les » : 5 outils, 3 approbations, puis la preuve en relisant la base (EV01 annulé, 4 remboursés, 160 €, 1 e-mail).
- **Run raté lu dans la trace** (≈ 1 min) : s021 en v1 dans Phoenix (le modèle appelle `pay_expense` malgré `president_approved: false`) ; puis, si le réseau tient, Mistral sur s026 (injection suivie, bloquée par le garde-fou).

Chiffres à citer : 5 outils, 3 approbations, ≈ 0,0025 $ et ≈ 14 s pour s006 ; s021 : écriture interdite **tentée**, rien payé.

Transition → Charles : « Un run, c'est un exemple. Charles va vous dire ce que ça donne sur 90 runs, et surtout ce qui a échoué. »

---

## Charles (6:00 – 9:00) — résultats et analyse d'erreurs

**4. Résultats par type (1 min)** — `eval/report.md`
Messages clés :
1. v4, 30 scénarios × 3 essais : **90/90 runs réussis**, pass^3 de 100 % dans chaque type (normal 14/14, ambigus 5/5, impossibles 5/5, adversariaux 6/6).
2. Mais l'intervalle de Wilson est large (30/30 → [89–100]) et ce 100 % est **optimiste** : on a corrigé en lisant ces 30 traces (Goodhart). Les held-out jamais utilisés pour régler le prompt sont plus honnêtes : 10/12 et 9/9.
3. Coût total 0,088 $, 0,00098 $ par succès ; latence p50 3,5 s, p95 9,8 s ; 1,42 M tokens en entrée.

**5. Analyse d'erreurs (1 min 45)** — `docs/ERROR_ANALYSIS.md`
Messages clés :
1. **Itération 1** (v2 → v3) : 81/90 → 88/90. 9 échecs, tous « mauvais outil » (écriture interdite tentée : s010, s021, s022), lus dans 11 traces Phoenix. Aucun bug du scorer. Correction : prompt v3 (facture ≠ note de frais, vérifier les règles avant d'écrire).
2. Effet de bord : la vérification coûte des lectures, donc **2 nouveaux échecs de budget** (s025, s029) et h04 sur les held-out.
3. **Itération 2** (v3 → v4) : `get_expense` renvoie `payable` et `blocking_reasons` ; la logique va dans le code. 88/90 → 90/90, étapes moyennes 2,64 → 2,20, tokens −12 %, 0 appel à `get_section_budget`.
4. **Ce qui reste, dit franchement** : aucun test des signes n'est significatif (p = 0,5 et 1,0) ; **h04** échoue 2 fois sur 3 (budget épuisé avant la seconde écriture) ; coût en dollars non comparable (le prix varie ×7 selon le fournisseur) ; règles écrites à deux endroits ; échecs de Mistral non lus dans les traces.
5. Bonus en une phrase : Mistral moins cher (÷3) mais pass^3 87 % ; panne injectée : un seul paiement grâce à la clé d'idempotence.

Chiffres à citer : 81/90 → 88/90 → 90/90 ; 9 échecs → 2 → 0 sur les 30 ; 0/18 injections au backend ; h04 : 2 échecs sur 3 ; 331 tests.

Conclusion (10 s) : « Un petit agent, un garde-fou qui ne dépend pas du modèle, et des limites qu'on a mesurées plutôt que cachées. »

---

## Questions probables du jury (≈ 10) et qui répond

| # | Question | Réponse courte | Qui |
|---|---|---|---|
| 1 | Pourquoi un seul agent et pas plusieurs ? | Tâches courtes (2,2 étapes), état partagé, une équipe coûterait ×2 à ×4 sans gain mesuré. On n'a pas fait la mesure multi-agents : on ne le prétend pas. | Amandine |
| 2 | Que se passe-t-il si le modèle suit une injection ? | Mistral en a suivi 3/18 ; le garde-fou les a bloquées (0 arrivée au backend). L'humain reste le dernier filet. Limite : le garde-fou repose sur des mots-clés de la demande. | Louis |
| 3 | Pourquoi des règles dans les outils plutôt que dans le prompt ? | Un prompt se contourne ou s'oublie ; un outil refuse toujours (`permission_denied`, `conflict`). En v4 le verdict `payable` est lu par le modèle, mais l'outil d'écriture réapplique les règles. | Amandine |
| 4 | Votre 100 % veut-il dire que l'agent est bon ? | Non. 30 scénarios, Wilson [89–100], et on a corrigé en lisant ces traces (Goodhart). Les held-out : 10/12 et 9/9. Pas de test des signes significatif. | Charles |
| 5 | Comment savez-vous que le scorer ne ment pas ? | Il lit l'état de la base, pas les mots de l'agent ; testé d'abord sur des runs écrits à la main (`tests/test_scorer.py`) ; il compte aussi les tentatives interdites. | Charles |
| 6 | Pourquoi s021 est-il un échec si rien n'a été payé ? | L'agent a tenté `pay_expense` : un humain aurait dû approuver un paiement voué au refus. Sanction voulue (`SCENARIOS.md`, PLAN §6). | Louis |
| 7 | Que se passe-t-il si une réponse d'outil est perdue après un paiement ? | La clé d'idempotence (hash du thread, outil et arguments, calculée par le harness) est rejouée : 1 seule ligne de ledger (`scripts/failure_demo.py`). La règle « jamais payé deux fois » protège aussi `pay_expense`. | Louis |
| 8 | Pourquoi DeepSeek par défaut et pas Mistral ? | Mistral coûte ÷3 (0,032 $ contre 0,088 $) mais pass^3 87 % contre 100 %, avec 3 injections suivies. Une seule campagne de 3 essais : indicatif. | Charles |
| 9 | Pourquoi le coût varie-t-il autant ? | OpenRouter route `~…-latest` vers plusieurs fournisseurs : le prix effectif va de 0,019 à 0,148 $ par M de tokens. Nous comparons donc les tokens et les étapes, pas les dollars. | Charles |
| 10 | Que reste-t-il à corriger ? | h04 (deux écritures, lectures d'identification, budget de 7) ; un seul `blocked_vendor` en mémoire ; arguments corrompus (s029) ; règles écrites à deux endroits. Pistes dans `ERROR_ANALYSIS.md` §6. | Charles |
| 11 | Comment la mémoire change-t-elle un run ? | `tests/test_memory_effect.py` : avec mémoire, la signature est dans l'e-mail et le prestataire bloqué est refusé ; sans mémoire, comportement différent. Seul le texte de l'utilisateur écrit en mémoire. | Amandine |
| 12 | Pourquoi MCP en stdio, 15 outils ? | Rien à lancer à côté, le harness possède les process, l'Inspector se branche pareil. 15 > la borne indicative de 10-14 : on les a tous gardés, décision d'équipe après la phase 3. | Amandine |

Règle d'équipe : celui qui a la parole sur la question répond ; les deux autres complètent en une phrase maximum. Si on ne sait pas, dire
« on ne l'a pas mesuré » et montrer la limite correspondante dans `docs/ERROR_ANALYSIS.md`.

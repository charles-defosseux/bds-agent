# bds-agent — agent de trésorerie du BDS

Projet de groupe, cours « Agentic Workflows » (Albert School × Mines Paris). Un agent assiste la trésorière ou le trésorier du Bureau des Sports : cotisations, remboursements, factures, inscriptions, annulation d'événements, e-mails. Il modifie l'état d'un backend SQLite et on prouve, en lisant la base, qu'il fait la bonne chose et seulement elle.

> README squelette (phase 1). Il sera complété en phase 9 : installation, commandes, schéma du graphe, choix d'architecture chiffrés.

## Installation

```bash
uv sync
cp .env.example .env   # puis renseigner OPENROUTER_API_KEY (jamais commité)
uv run pytest
```

## Contenu actuel

- `backend/` : schéma SQLite (`schema.sql`), `reset(seed_path)`, `read_state()`.
- `docs/REGLEMENT_FINANCIER.md` : les 6 règles codées dans les outils.
- `docs/COURSE_NOTES.md` : synthèse du cours. `PLAN.md` : architecture et décisions.

## À venir

Voir `PLAN.md` §9 : scénarios, serveurs MCP, graphe LangGraph, mémoire, évaluation, traces.

# CLAUDE.md — bds-agent (BDS treasury agent)

Graded group project, "Agentic Workflows" course (Albert School × Mines Paris).
**Code freeze: Thursday 19 Nov 2026, 23:59 (tag `v1.0`, made by the team). Defence: Friday 20 Nov.**

## Read first, at the start of every phase
- Full brief: `reference/prompt_claude_code.md` — re-read the section of the current phase (section 9 lists the phases).
- Course synthesis: `docs/COURSE_NOTES.md` — work from it; do **not** re-read the PDFs.
- Architecture and decisions: `PLAN.md`.
- If the brief contradicts `reference/project-instructions.pdf`, **the PDF wins**: flag the contradiction.

## Non-negotiable rules
1. Work **phase by phase**. At the end of each phase: run `uv run pytest`, check that `git status` lists neither
   `reference/` nor `.env`, give a summary of at most 5 lines (what works, what remains, choices made), propose a
   commit message, then **stop and wait for "ok"**.
2. **Never** run `git commit`, `git push`, `git tag` or any git command that changes history. `git status` and
   `git diff` are allowed.
3. **Never read, print or commit `.env`** (including `reference/agentic-workflow-lab/.env`). Only `.env.example` is versioned.
4. **Small and robust over impressive.** No feature that the brief does not ask for. Do not add tables without asking.
5. **Everything in English** (the course and the defence are in English): code, identifiers, comments, README and
   docs, scenarios, seed data, e-mails and the agent's answers. The agent answers in the user's language (English by default).
6. `reference/` is **read-only** and **never committed** (it is in `.gitignore`).
7. Reuse the lab's library versions (see `docs/COURSE_NOTES.md` §2) and the lab's code style and patterns.

## Key decisions (see PLAN.md)
- One agent (ReAct in a `StateGraph`), two MCP servers over stdio (`bds-registry`, `bds-treasury`), SQLite backend.
- `max_steps` = number of tool calls (lab 6 scorer); the graph also bounds model calls with the same value.
- Human approval (`interrupt()`) for `pay_expense`, `reimburse_member`, `refund_event_registrations`,
  `cancel_event`, `update_member_iban` and group e-mails. `record_dues_payment` is keyed, no approval.
- Idempotency key and treasurer identity are injected by the harness, never chosen by the model.
- Free text from tools is returned as `untrusted_text`; a deterministic guard runs before `approve`.

## Commands (once phase 1 is done)
```bash
uv sync
uv run pytest
uv run python eval/run_eval.py --scenarios eval/scenarios.jsonl --out eval/
uv run python eval/report.py
uv run langgraph dev        # LangGraph Studio
uv run phoenix serve        # traces on http://localhost:6006
```

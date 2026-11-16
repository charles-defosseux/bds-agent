"""LangGraph Studio entry point (demo UI): `uv run langgraph dev`, then open the printed Studio URL.

Studio provides the checkpointer, so the graph is compiled without one. The MCP servers start at import (once per
process) on data/bds.db; reset it first: `uv run python -m backend.reset seeds/base.json`.
Input: {"task": "Paie la facture E-001 de Voiles Atlantique."}. At the approval step, resume with {"approve": true}
(or "yes"), or {"approve": false, "reason": "..."}. Optional config: user_id (default M001), model.
"""
from agent.graph import build_graph, get_client

get_client()
agent = build_graph()

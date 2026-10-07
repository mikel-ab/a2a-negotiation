#!/usr/bin/env python3
"""
MCP server for a2a-negotiation. Add to Claude Desktop / Cursor / Claude Code:

  {"mcpServers": {"a2a-negotiation": {"command": "python3",
      "args": ["/absolute/path/to/mcp_server.py"]}}}

Then ask your assistant: "Negotiate early payment on INV-8840" or
"What APR is a 2% discount for paying 20 days early?"
"""
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

try:
    from mcp.server.mcpserver import MCPServer as FastMCP  # mcp >= 2
except ImportError:
    from mcp.server.fastmcp import FastMCP  # mcp 1.x

from negotiate import AuditLog, Invoice, discount_apr, apr_to_discount, load_invoices, negotiate

ROOT = Path(__file__).parent
mcp = FastMCP("a2a-negotiation")


def _policies():
    return (json.loads((ROOT / "policies/buyer.json").read_text()),
            json.loads((ROOT / "policies/supplier.json").read_text()))


@mcp.tool()
def quote_apr(discount_pct: float, days_accelerated: int) -> dict:
    """Annualised return/cost of an early-payment discount. Example: 2% for 20 days early = 37.2% APR."""
    return {"discount_pct": discount_pct, "days_accelerated": days_accelerated,
            "apr_pct": round(discount_apr(discount_pct, days_accelerated), 2)}


@mcp.tool()
def discount_for_apr(target_apr_pct: float, days_accelerated: int) -> dict:
    """Discount % that delivers a target APR for a given acceleration window."""
    return {"target_apr_pct": target_apr_pct, "days_accelerated": days_accelerated,
            "discount_pct": round(apr_to_discount(target_apr_pct, days_accelerated), 3)}


@mcp.tool()
def list_invoices() -> list[dict]:
    """Open invoices between Atlas Freight (supplier) and Acme Co. (buyer) available for negotiation."""
    return [asdict(i) | {"issue_date": str(i.issue_date), "due_date": str(i.due_date)}
            for i in load_invoices(ROOT / "data/invoices.csv")]


@mcp.tool()
def negotiate_invoice(invoice_id: str, engine: str = "heuristic", today: str = "2026-09-29",
                      auto_approve: bool = False) -> dict:
    """Run a supplier-agent vs buyer-agent negotiation for one invoice under both parties' policies.
    Returns status (agreed | pending_human_approval | no_deal | skipped), agreed discount, APR,
    buyer saving and the full message transcript. engine='llm' uses Claude for each agent."""
    buyer_p, supplier_p = _policies()
    inv = next((i for i in load_invoices(ROOT / "data/invoices.csv") if i.invoice_id == invoice_id), None)
    if not inv:
        return {"error": f"unknown invoice {invoice_id}"}
    out = negotiate(inv, buyer_p, supplier_p, engine, date.fromisoformat(today),
                    AuditLog(ROOT / "logs/audit.jsonl"), auto_approve)
    return asdict(out)


@mcp.tool()
def approve_pending(invoice_id: str, approver: str, decision: str) -> dict:
    """Human-in-the-loop: record an approve/reject decision for an outcome that was routed to a human."""
    log = AuditLog(ROOT / "logs/audit.jsonl")
    log.write("human_decision", invoice=invoice_id, approver=approver, decision=decision)
    return {"invoice_id": invoice_id, "approver": approver, "decision": decision, "recorded": True}


if __name__ == "__main__":
    mcp.run()

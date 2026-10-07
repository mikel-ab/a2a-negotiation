# a2a-negotiation

Two AI agents — a **Supplier Agent** and a **Buyer Agent** — negotiate early-payment discounts on B2B invoices under machine-enforced policies, with a full audit log and a human-approval gate. A working model of the agent-to-agent settlement layer between companies.

```
invoice        amount status                    disc%    APR%   saving €  rnds  reason
INV-8828       48,500 agreed                     0.23    14.0        112     1  within both mandates
INV-8840       91,000 agreed                     1.92    14.0      1,746     3  within both mandates
INV-8842        3,800 skipped                       -       -          -     0  invoice status is 'disputed' — routed to dispute workflow
INV-8851      156,000 pending_human_approval     2.08    13.4      3,245     3  above buyer approval threshold

Accelerated EUR 335,350 · buyer saving EUR 5,727 · audit: logs/audit.jsonl
```

## Why this exists
Agentic payment protocols (AP2, ACP, x402, MPP) solve authorisation, checkout and settlement. None of them negotiate **terms**. In B2B, terms are where the money is: 2/10 net 30 is a 37% APR. This repo shows the missing layer: two principals, two policies, two agents, one auditable agreement.

[
  {
    "invoice_id": "INV-8840",
    "status": "agreed",
    "reason": "within both mandates",
    "discount_pct": 1.919,
    "pay_in_days": 0,
    "apr": 14.0,
    "saving_eur": 1746.29,
    "rounds": 3,
    "transcript": [
      {
        "action": "offer",
        "discount_pct": 1.17,
        "pay_in_days": 0,
        "message": "Atlas Freight: proposing 1.17% for immediate payment.",
        "apr": 8.47,
        "clamped": false
      },
      {
        "action": "offer",
        "discount_pct": 2.22,
        "pay_in_days": 0,
        "message": "Acme Co.: proposing 2.22% for immediate payment.",
        "apr": 16.25,
        "clamped": false
      },
      {
        "action": "offer",
        "discount_pct": 1.55,
        "pay_in_days": 0,
        "message": "Atlas Freight: proposing 1.55% for immediate payment.",
        "apr": 11.27,
        "clamped": false
      },
      {
        "action": "offer",
        "discount_pct": 1.93,
        "pay_in_days": 0,
        "message": "Acme Co.: proposing 1.93% for immediate payment.",
        "apr": 14.08,
        "clamped": false
      },
      {
        "action": "offer",
        "discount_pct": 1.919,
        "pay_in_days": 0,
        "message": "Atlas Freight: proposing 1.92% for immediate payment. [policy layer clamped to 1.92% \u2014 outside supplier mandate]",
        "apr": 14.0,
        "clamped": true
      },
      {
        "action": "accept",
        "discount_pct": 1.919,
        "pay_in_days": 0,
        "message": "Acme Co.: 1.92% is within mandate. Accepting.",
        "apr": 14.0,
        "clamped": false
      }
    ]
  }
]


## Design
| Component | Role |
|---|---|
| `policies/*.json` | Each party's mandate: cost of capital / treasury yield, hurdle, caps, approval thresholds |
| `Mandate` (negotiate.py) | Converts policy into a discount floor/ceiling per invoice and **clamps** any agent proposal outside it. Enforced in code, not in the prompt |
| `Agent` | `heuristic` = deterministic concession strategy (offline, reproducible); `llm` = Claude reasons under the policy and returns structured JSON |
| Orchestrator | Pre-checks (dispute, PO, weekly cap, days-to-due) → alternating rounds → outcome |
| Approval gate | Amount or discount above buyer thresholds → `pending_human_approval` |
| `logs/audit.jsonl` | Every mandate, message, clamp and outcome |
| `mcp_server.py` | Exposes it all as MCP tools for Claude Desktop / Cursor / Claude Code |
| `skill/SKILL.md` | Skill for Claude / Codex agents |

## Run
```bash
pip install -r requirements.txt
python3 negotiate.py                          # heuristic engine, all invoices
python3 negotiate.py --engine llm --invoice INV-8840   # Claude-driven agents (ANTHROPIC_API_KEY)
python3 negotiate.py --json                   # machine-readable
python3 mcp_server.py                         # MCP server (stdio)
```

## Talking points it makes concrete
1. **Policy is the UX.** The agent proposes; the mandate decides. Liability sits with whoever set the policy.
2. **Zone of agreement is computable.** Buyer floor (yield + hurdle) vs supplier ceiling (cost of capital). No ZOPA → no chase.
3. **Human-in-the-loop by threshold, not by default.** 5 of 6 invoices resolve without a person; the €156k one waits.
4. **Auditability is the product.** Every message is a signed-intent record in waiting.

## Next steps
- Sign each message (AP2-style mandate) and verify on receipt
- Add a dispute-resolution agent pair for `disputed` invoices
- Read invoices from EN 16931 XML (ZUGFeRD / Facturae) instead of CSV
- Multi-buyer / multi-supplier matching (the network effect)

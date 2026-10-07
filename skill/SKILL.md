---
name: early-payment-negotiation
description: Negotiate or evaluate early-payment discounts on B2B invoices as a buyer or supplier agent. Use when a user asks about dynamic discounting, 2/10 net 30 economics, whether to accept an early-pay offer, or wants two agents to settle terms on an invoice.
---

# Early-payment negotiation skill

## When to use
- User asks "should we take/offer X% for paying Y days early?"
- User wants the annualised cost or return of a discount.
- User wants a buyer agent and supplier agent to settle terms on an invoice under policy.

## Core math (always compute, never guess)
APR = (d / (1 - d)) × (365 / days_early), with d = discount as a fraction.
Examples: 2% / 20 days → 37.2%; 1% / 30 days → 12.3%; 0.5% / 10 days → 18.3%.
Run: `python3 negotiate.py --help` from the repo root, or call the MCP tool `quote_apr`.

## Decision rules
- **Buyer** accepts when APR ≥ treasury yield + hurdle spread and amount is inside weekly cap.
- **Supplier** accepts when APR ≤ its cost of capital (discount is cheaper than factoring/credit line).
- Zone of agreement exists when buyer floor ≤ supplier ceiling. If not, say so and stop.

## Guardrails
- Never commit a principal outside its policy file; policy bounds are enforced in code.
- Skip disputed invoices and invoices with no PO (if buyer requires one); route to dispute workflow.
- Amounts or discounts above the buyer's approval thresholds → `pending_human_approval`, never `agreed`.
- Every message and outcome is appended to `logs/audit.jsonl`.

## Output format
Report: status, agreed discount %, implied APR, EUR saving, rounds, and one-line rationale.

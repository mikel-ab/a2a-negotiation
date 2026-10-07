#!/usr/bin/env python3
"""
a2a-negotiation — two AI agents negotiating early-payment discounts on B2B invoices.

Supplier Agent (wants cash early, but not at any price) vs Buyer Agent (has idle cash,
wants a return above its hurdle). Each agent operates under a machine-enforced policy
("mandate"). Every message is logged. Outcomes above policy thresholds are routed to a
human for approval instead of being auto-agreed.

Modes:
  --engine llm        agents reason with Claude (needs ANTHROPIC_API_KEY)
  --engine heuristic  deterministic concession strategy (offline, reproducible)

Usage:
  python negotiate.py                              # heuristic, all open invoices
  python negotiate.py --engine llm --invoice INV-8828
  python negotiate.py --auto-approve               # bypass human gate (demo only)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import uuid
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Optional

ROOT = Path(__file__).parent
Action = Literal["offer", "accept", "reject"]

# --------------------------------------------------------------------------- #
# Finance                                                                      #
# --------------------------------------------------------------------------- #

def discount_apr(discount_pct: float, days_accelerated: int) -> float:
    """Annualised cost/return of taking a discount for paying `days_accelerated` early.
    2/10 net 30 -> discount 2%, 20 days early -> (2/98)*(365/20) = 37.2% APR."""
    if days_accelerated <= 0:
        return float("inf")
    d = discount_pct / 100.0
    return (d / (1 - d)) * (365.0 / days_accelerated) * 100.0


def apr_to_discount(apr: float, days_accelerated: int) -> float:
    """Inverse of discount_apr: the discount % that yields a given APR."""
    r = (apr / 100.0) * (days_accelerated / 365.0)
    return (r / (1 + r)) * 100.0


# --------------------------------------------------------------------------- #
# Data                                                                         #
# --------------------------------------------------------------------------- #

@dataclass
class Invoice:
    invoice_id: str
    supplier: str
    buyer: str
    amount_eur: float
    issue_date: date
    due_date: date
    terms: str
    po_number: str
    status: str

    @staticmethod
    def from_row(r: dict) -> "Invoice":
        return Invoice(
            invoice_id=r["invoice_id"], supplier=r["supplier"], buyer=r["buyer"],
            amount_eur=float(r["amount_eur"]),
            issue_date=date.fromisoformat(r["issue_date"]),
            due_date=date.fromisoformat(r["due_date"]),
            terms=r["terms"], po_number=r.get("po_number", ""), status=r["status"],
        )

    def days_to_due(self, today: date) -> int:
        return (self.due_date - today).days


@dataclass
class Offer:
    action: Action
    discount_pct: float
    pay_in_days: int          # days from today the buyer would pay
    message: str
    apr: float = 0.0
    clamped: bool = False     # True if the policy layer had to override the agent


@dataclass
class Outcome:
    invoice_id: str
    status: str               # agreed | pending_human_approval | no_deal | skipped
    reason: str
    discount_pct: Optional[float] = None
    pay_in_days: Optional[int] = None
    apr: Optional[float] = None
    saving_eur: Optional[float] = None
    rounds: int = 0
    transcript: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Audit log                                                                    #
# --------------------------------------------------------------------------- #

class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.session = uuid.uuid4().hex[:8]
        path.parent.mkdir(exist_ok=True)

    def write(self, event: str, **payload):
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "session": self.session,
               "event": event, **payload}
        with self.path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")


# --------------------------------------------------------------------------- #
# Policy layer — the "mandate". Agents propose; this decides what is allowed.  #
# --------------------------------------------------------------------------- #

class Mandate:
    """Bounds within which an agent may commit its principal. Enforced in code,
    not in the prompt: an LLM cannot talk itself past these."""

    def __init__(self, policy: dict, inv: Invoice, today: date):
        self.p = policy
        self.role = policy["role"]
        self.inv = inv
        self.days = inv.days_to_due(today)

    # -- limits expressed as discount % for this invoice's acceleration window --
    def floor_pct(self) -> float:
        if self.role == "buyer":   # buyer needs APR >= yield + hurdle
            return apr_to_discount(self.p["treasury_yield_apr"] + self.p["hurdle_spread_apr"], self.days)
        return self.p["min_discount_pct"]

    def ceiling_pct(self) -> float:
        if self.role == "buyer":
            return self.p["max_discount_pct"]
        # supplier never pays more than its cost of capital
        return apr_to_discount(self.p["cost_of_capital_apr"], self.days)

    def acceptable(self, discount_pct: float) -> bool:
        return self.floor_pct() - 1e-9 <= discount_pct <= self.ceiling_pct() + 1e-9

    def clamp(self, offer: Offer) -> Offer:
        lo, hi = self.floor_pct(), self.ceiling_pct()
        d = round(min(max(offer.discount_pct, lo), hi), 3)
        if offer.action == "accept" and not self.acceptable(offer.discount_pct):
            # agent tried to accept something outside mandate -> downgrade to counter
            offer.action, offer.discount_pct, offer.clamped = "offer", d, True
        elif offer.action == "offer" and abs(d - offer.discount_pct) > 1e-9:
            offer.discount_pct, offer.clamped = d, True
        offer.pay_in_days = max(0, min(offer.pay_in_days, self.days))
        offer.apr = round(discount_apr(offer.discount_pct, self.days - offer.pay_in_days), 2)
        if offer.clamped:
            offer.message += f" [policy layer clamped to {offer.discount_pct:.2f}% — outside {self.role} mandate]"
        return offer

    def describe(self) -> str:
        return (f"{self.role.upper()} mandate for {self.inv.invoice_id}: discount between "
                f"{self.floor_pct():.2f}% and {self.ceiling_pct():.2f}% "
                f"(window {self.days} days to due).")


# --------------------------------------------------------------------------- #
# Agents                                                                       #
# --------------------------------------------------------------------------- #

class Agent:
    def __init__(self, policy: dict, engine: str):
        self.p = policy
        self.role = policy["role"]
        self.engine = engine
        self._client = None
        if engine == "llm":
            import anthropic  # lazy import
            self._client = anthropic.Anthropic()

    # ---- heuristic: concede toward own limit, accept when inside mandate ----
    def _heuristic(self, m: Mandate, history: list[Offer], rnd: int) -> Offer:
        last = next((o for o in reversed(history) if o.action == "offer"), None)
        if last and m.acceptable(last.discount_pct):
            return Offer("accept", last.discount_pct, 0,
                         f"{self.p['party']}: {last.discount_pct:.2f}% is within mandate. Accepting.")
        if rnd >= self.p["max_rounds"]:
            return Offer("reject", 0, 0, f"{self.p['party']}: no agreement within {rnd} rounds.")
        # move a fraction of the way from opening toward the limit each round
        start = self.p["opening_discount_pct"]
        limit = m.floor_pct() if self.role == "buyer" else m.ceiling_pct()
        step = min(1.0, rnd / max(1, self.p["max_rounds"] - 1))
        proposal = start + (limit - start) * step
        return Offer("offer", round(proposal, 2), 0,
                     f"{self.p['party']}: proposing {proposal:.2f}% for immediate payment.")

    # ---- LLM: Claude reasons under the policy; output is structured JSON ----
    def _llm(self, m: Mandate, history: list[Offer], rnd: int) -> Offer:
        inv = m.inv
        system = f"""You are the {self.role} agent for {self.p['party']} on the Causa Prima-style
agent-to-agent network. You negotiate an early-payment discount on one invoice.
Your principal's policy (JSON): {json.dumps(self.p)}
Hard mandate for this invoice (computed, enforced in code): {m.describe()}
Reason like a treasurer: compare the implied APR of any discount to your policy rates.
Respond ONLY with JSON: {{"action":"offer|accept|reject","discount_pct":number,
"pay_in_days":int,"message":"one sentence to the counterparty"}}. No prose, no fences."""
        hist = "\n".join(f"- {o.message} (discount {o.discount_pct}%, APR {o.apr}%)" for o in history) or "(none)"
        user = (f"Invoice {inv.invoice_id}: EUR {inv.amount_eur:,.0f}, terms {inv.terms}, "
                f"{m.days} days to due, PO {inv.po_number or 'MISSING'}.\nRound {rnd} of {self.p['max_rounds']}.\n"
                f"Negotiation so far:\n{hist}\nYour move.")
        resp = self._client.messages.create(
            model=os.environ.get("A2A_MODEL", "claude-sonnet-5-5"),
            max_tokens=300, system=system, messages=[{"role": "user", "content": user}])
        # Safely find text across all content block types
        text_blocks = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        raw = text_blocks[0].strip().strip("`") if text_blocks else ""
        if raw.startswith("json"):
            raw = raw[4:]
        try:
            j = json.loads(raw)
            return Offer(j["action"], float(j.get("discount_pct", 0)), int(j.get("pay_in_days", 0)),
                         f"{self.p['party']}: {j.get('message', '')}")
        except Exception:
            # malformed output falls back to the deterministic strategy — never to a free-form commit
            return self._heuristic(m, history, rnd)

    def move(self, m: Mandate, history: list[Offer], rnd: int) -> Offer:
        raw = self._llm(m, history, rnd) if self.engine == "llm" else self._heuristic(m, history, rnd)
        return m.clamp(raw)


# --------------------------------------------------------------------------- #
# Orchestrator                                                                 #
# --------------------------------------------------------------------------- #

def negotiate(inv: Invoice, buyer_p: dict, supplier_p: dict, engine: str,
              today: date, log: AuditLog, auto_approve: bool = False,
              spent_this_week: float = 0.0) -> Outcome:
    log.write("start", invoice=inv.invoice_id, amount=inv.amount_eur, engine=engine)

    # ---- pre-checks the network does before agents even talk ----
    if inv.status != "open":
        log.write("skip", invoice=inv.invoice_id, reason=f"status={inv.status}")
        return Outcome(inv.invoice_id, "skipped", f"invoice status is '{inv.status}' — routed to dispute workflow")
    if buyer_p.get("requires_po") and not inv.po_number:
        log.write("skip", invoice=inv.invoice_id, reason="missing PO")
        return Outcome(inv.invoice_id, "skipped", "buyer policy requires a PO; none on invoice")
    if inv.days_to_due(today) < 5:
        return Outcome(inv.invoice_id, "skipped", "less than 5 days to due — no acceleration value")
    if spent_this_week + inv.amount_eur > buyer_p["weekly_early_pay_cap_eur"]:
        log.write("skip", invoice=inv.invoice_id, reason="weekly cap")
        return Outcome(inv.invoice_id, "skipped", "buyer weekly early-payment cap would be exceeded")

    bm, sm = Mandate(buyer_p, inv, today), Mandate(supplier_p, inv, today)
    log.write("mandate", invoice=inv.invoice_id, buyer=bm.describe(), supplier=sm.describe(),
              zopa=bm.floor_pct() <= sm.ceiling_pct())

    buyer, supplier = Agent(buyer_p, engine), Agent(supplier_p, engine)
    history: list[Offer] = []
    turn = [supplier, buyer]  # supplier opens (it wants the cash)
    mandates = {supplier: sm, buyer: bm}
    max_rounds = max(buyer_p["max_rounds"], supplier_p["max_rounds"])

    for rnd in range(1, max_rounds + 1):
        for agent in turn:
            o = agent.move(mandates[agent], history, rnd)
            history.append(o)
            log.write("message", invoice=inv.invoice_id, round=rnd, agent=agent.role, **asdict(o))
            if o.action == "reject":
                return Outcome(inv.invoice_id, "no_deal", o.message, rounds=rnd,
                               transcript=[asdict(h) for h in history])
            if o.action == "accept":
                d, days = o.discount_pct, mandates[agent].days - o.pay_in_days
                saving = round(inv.amount_eur * d / 100, 2)
                apr = round(discount_apr(d, days), 2)
                needs_human = (inv.amount_eur > buyer_p["human_approval_above_eur"]
                               or d > buyer_p["human_approval_above_discount_pct"])
                status = "pending_human_approval" if (needs_human and not auto_approve) else "agreed"
                log.write("outcome", invoice=inv.invoice_id, status=status, discount_pct=d,
                          apr=apr, saving_eur=saving, needs_human=needs_human)
                return Outcome(inv.invoice_id, status,
                               "above buyer approval threshold" if needs_human else "within both mandates",
                               d, o.pay_in_days, apr, saving, rnd, [asdict(h) for h in history])
    return Outcome(inv.invoice_id, "no_deal", "max rounds reached", rounds=max_rounds,
                   transcript=[asdict(h) for h in history])


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def load_invoices(path: Path) -> list[Invoice]:
    with path.open() as f:
        return [Invoice.from_row(r) for r in csv.DictReader(f)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=["heuristic", "llm"], default="heuristic")
    ap.add_argument("--invoice", help="negotiate a single invoice id")
    ap.add_argument("--today", default="2026-09-29")
    ap.add_argument("--auto-approve", action="store_true")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    today = date.fromisoformat(a.today)
    buyer_p = json.loads((ROOT / "policies/buyer.json").read_text())
    supplier_p = json.loads((ROOT / "policies/supplier.json").read_text())
    invoices = load_invoices(ROOT / "data/invoices.csv")
    if a.invoice:
        invoices = [i for i in invoices if i.invoice_id == a.invoice]
    log = AuditLog(ROOT / "logs/audit.jsonl")

    results, spent = [], 0.0
    for inv in invoices:
        out = negotiate(inv, buyer_p, supplier_p, a.engine, today, log, a.auto_approve, spent)
        if out.status == "agreed":
            spent += inv.amount_eur
        results.append(out)

    if a.json:
        print(json.dumps([asdict(r) for r in results], indent=2, default=str))
        return

    print(f"\nA2A negotiation — engine={a.engine} today={today} session={log.session}\n")
    print(f"{'invoice':10} {'amount':>10} {'status':24} {'disc%':>6} {'APR%':>7} {'saving €':>10} rnds  reason")
    tot_saving = tot_amt = 0.0
    for r in results:
        inv = next(i for i in invoices if i.invoice_id == r.invoice_id)
        d = f"{r.discount_pct:.2f}" if r.discount_pct is not None else "-"
        apr = f"{r.apr:.1f}" if r.apr is not None else "-"
        sv = f"{r.saving_eur:,.0f}" if r.saving_eur is not None else "-"
        print(f"{r.invoice_id:10} {inv.amount_eur:>10,.0f} {r.status:24} {d:>6} {apr:>7} {sv:>10} {r.rounds:>4}  {r.reason}")
        if r.status in ("agreed", "pending_human_approval"):
            tot_saving += r.saving_eur or 0; tot_amt += inv.amount_eur
    print(f"\nAccelerated EUR {tot_amt:,.0f} · buyer saving EUR {tot_saving:,.0f} · audit: logs/audit.jsonl")


if __name__ == "__main__":
    main()

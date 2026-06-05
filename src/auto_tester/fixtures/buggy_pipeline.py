"""
A tiny multi-step "orders" pipeline used to validate the tester.

Intent (the plain-English spec the oracle is given):
    Combine orders from two sources into one list. Each order has an ``id``,
    a ``customer``, a ``region``, and an ``amount`` (currency string like
    "$12.50"). The pipeline must:
      1. parse every amount into a number,
      2. de-duplicate by ``id`` (keep the first occurrence),
      3. merge both sources preserving each order's own customer and region,
      4. summarize: count ALL orders and sum their amounts (total revenue).
    No order may be silently dropped, and total revenue must equal the sum of
    every order's parsed amount.

The ``buggy=True`` path (default) contains four deliberate, realistic bugs that
each map to a different oracle type. ``buggy=False`` is the correct reference
used to confirm the tester produces no false positives.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..core.tracer import step

# Which bugs are active. Flipped off by the clean reference run.
_BUGGY = True


def _set_buggy(value: bool) -> None:
    global _BUGGY
    _BUGGY = value


# --------------------------------------------------------------------------- #
# Steps (each is traced)
# --------------------------------------------------------------------------- #
@step("orders.parse_amount")
def parse_amount(record: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the currency string in ``amount`` into a float ``amount_value``."""
    raw = str(record.get("amount", "")).replace("$", "").replace(",", "").strip()
    out = dict(record)
    if _BUGGY:
        # BUG B4: silently swallow unparseable amounts as 0.0 — revenue is
        # understated and the bad record is never flagged.
        try:
            out["amount_value"] = float(raw)
        except ValueError:
            out["amount_value"] = 0.0
    else:
        out["amount_value"] = float(raw)  # raises on bad input (surfaced)
    return out


@step("orders.dedup")
def dedup(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop later records sharing an ``id`` with an earlier one."""
    seen = set()
    result: List[Dict[str, Any]] = []
    if _BUGGY:
        # BUG B2: off-by-one — the last record is never examined, so a duplicate
        # sitting in the final position slips through.
        n = len(records) - 1
    else:
        n = len(records)
    for i in range(n):
        rec = records[i]
        if rec["id"] not in seen:
            seen.add(rec["id"])
            result.append(rec)
    if _BUGGY:
        # the unexamined tail record is appended unconditionally
        if records:
            result.append(records[-1])
    return result


@step("orders.merge")
def merge(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Project each record into the canonical output shape."""
    out = []
    for rec in records:
        if _BUGGY:
            # BUG B3: customer and region are swapped.
            out.append(
                {
                    "id": rec["id"],
                    "customer": rec.get("region"),
                    "region": rec.get("customer"),
                    "status": rec.get("status"),
                    "amount_value": rec["amount_value"],
                }
            )
        else:
            out.append(
                {
                    "id": rec["id"],
                    "customer": rec.get("customer"),
                    "region": rec.get("region"),
                    "status": rec.get("status"),
                    "amount_value": rec["amount_value"],
                }
            )
    return out


@step("orders.summarize")
def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Count orders and total their amounts."""
    if _BUGGY:
        # BUG B1: orders with status "hold" are silently dropped from the count
        # and revenue, even though intent says count ALL orders. The full list
        # is still returned, so order_count/total_revenue disagree with it.
        counted = [r for r in records if r.get("status") != "hold"]
    else:
        counted = records
    return {
        "order_count": len(counted),
        "total_revenue": round(sum(r["amount_value"] for r in counted), 2),
        "orders": records,  # full list still returned for traceability
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
@step("orders.run_pipeline")
def run_pipeline(source_a: List[Dict[str, Any]], source_b: List[Dict[str, Any]],
                 buggy: bool = True) -> Dict[str, Any]:
    """End-to-end: parse -> merge sources -> dedup -> project -> summarize."""
    _set_buggy(buggy)
    combined = [parse_amount(r) for r in (list(source_a) + list(source_b))]
    deduped = dedup(combined)
    merged = merge(deduped)
    return summarize(merged)

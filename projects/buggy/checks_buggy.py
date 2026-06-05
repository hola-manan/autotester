"""
Oracle suite for the orders pipeline (hand-written reference; this is the shape
``oracle_gen`` produces from intent.md).

Each invariant reconstructs the truth from the *input* (the case payload) and
compares it to the pipeline's output/trace — exactly how a human QA would check
"did what came out match what went in?".
"""

from auto_tester.core.checks import invariant, metamorphic


def _output(run):
    """Return the output dict, or None if the run crashed (handled elsewhere)."""
    return run.output if isinstance(run.output, dict) else None


def _input_index(run):
    """Map id -> first input order across both sources (dedup keeps first)."""
    idx = {}
    for src in ("source_a", "source_b"):
        for rec in run.case.payload.get(src, []):
            idx.setdefault(rec["id"], rec)
    return idx


def _parsed(amount_str):
    try:
        return float(str(amount_str).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


# B2 — dedup off-by-one leaves a duplicate id
@invariant(id="no_duplicate_ids", description="Output must contain no duplicate order ids (dedup by id).",
           severity="high", category="correctness")
def _(run):
    if _output(run) is None:
        return None
    ids = [o["id"] for o in run.output.get("orders", [])]
    dups = sorted({i for i in ids if ids.count(i) > 1})
    if dups:
        return {"observed": f"output has duplicate ids {dups}",
                "evidence": {"output_ids": ids, "duplicate_ids": dups}}
    return None


# B1 — order_count disagrees with the returned list (silent drop)
@invariant(id="count_matches_orders", description="order_count must equal the number of returned orders.",
           severity="critical", category="correctness")
def _(run):
    if _output(run) is None:
        return None
    orders = run.output.get("orders", [])
    count = run.output.get("order_count")
    if count != len(orders):
        return {"observed": f"order_count={count} but {len(orders)} orders were returned",
                "evidence": {"order_count": count, "returned_orders": len(orders)}}
    return None


# B1 — revenue does not reconcile with the returned orders
@invariant(id="revenue_reconciles", description="total_revenue must equal the sum of returned order amounts.",
           severity="critical", category="correctness")
def _(run):
    if _output(run) is None:
        return None
    orders = run.output.get("orders", [])
    expected = round(sum(o.get("amount_value", 0) for o in orders), 2)
    reported = run.output.get("total_revenue")
    if reported is not None and abs(reported - expected) > 0.001:
        return {"observed": f"total_revenue={reported} but returned orders sum to {expected}",
                "evidence": {"reported": reported, "sum_of_orders": expected}}
    return None


# B3 — customer/region swapped vs the input
@invariant(id="fields_preserved", description="Each output order must keep its input customer and region.",
           severity="high", category="correctness")
def _(run):
    if _output(run) is None:
        return None
    idx = _input_index(run)
    bad = []
    for o in run.output.get("orders", []):
        src = idx.get(o["id"])
        if not src:
            continue
        if o.get("customer") != src.get("customer") or o.get("region") != src.get("region"):
            bad.append({"id": o["id"],
                        "input": {"customer": src.get("customer"), "region": src.get("region")},
                        "output": {"customer": o.get("customer"), "region": o.get("region")}})
    if bad:
        return {"observed": f"{len(bad)} orders had customer/region altered from the input",
                "evidence": {"mismatches": bad[:5]}}
    return None


# B4 — an unparseable amount was silently turned into 0
@invariant(id="no_silent_zero_amount", description="A non-zero/unparseable input amount must not become 0 silently.",
           severity="high", category="fabrication")
def _(run):
    if _output(run) is None:
        return None
    idx = _input_index(run)
    swallowed = []
    for o in run.output.get("orders", []):
        if o.get("amount_value") == 0:
            src = idx.get(o["id"])
            if not src:
                continue
            true_val = _parsed(src.get("amount"))
            if true_val != 0:  # input was non-zero or unparseable, yet output is 0
                swallowed.append({"id": o["id"], "input_amount": src.get("amount")})
    if swallowed:
        return {"observed": f"{len(swallowed)} orders had a non-zero/bad amount silently coerced to 0",
                "evidence": {"swallowed": swallowed}}
    return None


# Metamorphic — appending an exact duplicate of source_a must not change totals
@metamorphic(id="dedup_idempotent",
             description="Re-feeding orders that are already present must not change order_count or revenue.",
             transform=lambda p: {**p, "source_b": list(p.get("source_b", [])) + list(p.get("source_a", []))},
             severity="high", category="correctness")
def _(base, variant):
    b, v = base.output, variant.output
    if b.get("order_count") != v.get("order_count") or abs(
        (b.get("total_revenue") or 0) - (v.get("total_revenue") or 0)
    ) > 0.001:
        return {"observed": (f"duplicating already-present orders changed the result: "
                             f"count {b.get('order_count')}->{v.get('order_count')}, "
                             f"revenue {b.get('total_revenue')}->{v.get('total_revenue')}"),
                "evidence": {"base": {"count": b.get("order_count"), "revenue": b.get("total_revenue")},
                             "variant": {"count": v.get("order_count"), "revenue": v.get("total_revenue")}}}
    return None

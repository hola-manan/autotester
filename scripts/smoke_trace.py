"""Smoke test: confirm the tracer captures each step of the buggy fixture."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auto_tester.core.tracer import trace_context
from auto_tester.fixtures import buggy_pipeline as bp

a = [
    {"id": 1, "customer": "Acme", "region": "north", "amount": "$10.00"},
    {"id": 2, "customer": "Beta", "region": "pending", "amount": "$5.50"},
    {"id": 3, "customer": "Cee", "region": "south", "amount": "oops"},
]
b = [
    {"id": 1, "customer": "Acme", "region": "north", "amount": "$10.00"},
    {"id": 3, "customer": "Cee", "region": "south", "amount": "$7.25"},
]

with trace_context() as t:
    out = bp.run_pipeline(a, b, buggy=True)

print("OUTPUT order_count =", out["order_count"], " total_revenue =", out["total_revenue"])
print("STEPS captured:", len(t.steps))
for s in t.steps:
    print(f"  [{s.order}] {s.name}  ({s.duration_ms:.2f}ms)  err={s.error is not None}")

# show what summarize saw vs returned (evidence the oracle would use)
summ = t.by_name("orders.summarize")[0]
print("\nsummarize() input record count:", len(summ.args.get("records", [])))
print("summarize() returned:", {k: out[k] for k in ("order_count", "total_revenue")})

"""
Hypothesis strategies for the orders fixture (hand-written reference; this is
the shape ``strategy_gen`` produces from intent.md + example payloads).

Payloads are STRUCTURALLY valid — parseable currency amounts, real field names —
so the fuzz stage explores the legal input space: duplicate ids across and
within sources, held orders, empty sources, unicode customers, zero amounts.
"""

from hypothesis import strategies as st

# A small id space makes duplicate ids (the dedup bug's trigger) likely.
_ids = st.integers(min_value=1, max_value=5)

_amount = st.integers(min_value=0, max_value=99999).map(
    lambda cents: f"${cents // 100}.{cents % 100:02d}")

_record = st.fixed_dictionaries({
    "id": _ids,
    "customer": st.text(min_size=1, max_size=12),
    "region": st.sampled_from(["north", "south", "east", "west"]),
    "status": st.sampled_from(["ok", "ok", "ok", "hold"]),
    "amount": _amount,
})


def payload_strategy():
    return st.fixed_dictionaries({
        "source_a": st.lists(_record, min_size=0, max_size=5),
        "source_b": st.lists(_record, min_size=0, max_size=5),
    })


SEEDS = [
    {  # duplicate id sitting in the final position (dedup edge)
        "source_a": [
            {"id": 1, "customer": "Acme", "region": "north", "status": "ok", "amount": "$10.00"},
        ],
        "source_b": [
            {"id": 1, "customer": "Acme", "region": "north", "status": "ok", "amount": "$10.00"},
        ],
    },
    {  # a held order (count/revenue disclosure edge)
        "source_a": [
            {"id": 2, "customer": "Beta", "region": "east", "status": "hold", "amount": "$5.50"},
        ],
        "source_b": [],
    },
    {  # both sources empty
        "source_a": [],
        "source_b": [],
    },
]

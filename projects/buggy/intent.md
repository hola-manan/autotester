# Intent: orders pipeline

Combine orders from two sources (`source_a`, `source_b`) into one result.

Each order has:
- `id` — unique order identifier
- `customer` — the customer's name
- `region` — the customer's region
- `status` — order status (e.g. `ok`, `hold`)
- `amount` — a currency string like `"$12.50"`

The pipeline must:

1. **Parse** every `amount` into a number. An amount that cannot be parsed is a
   data error and must be surfaced — it must NOT be silently treated as 0.
2. **De-duplicate** by `id`, keeping the first occurrence. The output must
   contain no duplicate `id`s.
3. **Merge** both sources, preserving each order's own `customer`, `region`,
   and `status` exactly (no field may be swapped or relabeled).
4. **Summarize**: report `order_count` = the number of ALL orders, and
   `total_revenue` = the sum of every order's parsed amount. No order may be
   silently dropped from the count or the revenue, regardless of its status.

Correctness expectations:
- `order_count` must equal the number of orders in the returned `orders` list.
- `total_revenue` must equal the sum of `amount_value` across the returned
  `orders`.
- Every order in the output must trace back to an input order with the same
  `id`, `customer`, and `region`.

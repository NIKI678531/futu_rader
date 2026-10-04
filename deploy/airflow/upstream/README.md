# MarketInsight producer contract

This folder is a hand-off contract, not a second collector. Apply
`futu_comments_collection_runs.mysql` as an
Alembic migration in the MarketInsight repository and add receipt writes to its
existing `comments_all` DAG.

For every run:

1. Insert a unique `run_id` with `status=running` before collection.
2. Count the collector's complete configured symbol set. Its fingerprint is
   `sha256:` plus SHA-256 of the sorted, de-duplicated symbols joined by `\n`.
3. On failure, update the same row to `failed` with a short redacted error. Do
   not set `complete_through` and do not publish the Dataset.
4. After all symbol writes commit, update the receipt to `succeeded`, set the
   fixed source `high_watermark_at`, `complete_through`, configured/attempted/
   succeeded counts and fingerprint in one transaction.
5. Only after that transaction commits, publish
   `market-insight://futu-community` with
   `outlet_events[dataset].extra={"source_run_id": run_id}`.

Partial and `comments_important` runs may have receipts, but must leave
`complete_through` null. Radar independently verifies every completion-proof
field and refuses to advance its anchor when any field is missing or mismatched.


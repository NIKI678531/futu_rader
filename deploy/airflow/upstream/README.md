# MarketInsight producer contract

This folder is an optional exact-completeness hand-off contract, not a second
collector. Radar can run in observed-partial mode without this table. Apply
`futu_comments_collection_runs.mysql` through an Alembic migration in the
MarketInsight repository only when exact bounded replay and verified
`sourceCompleteThrough` are required, then add receipt writes to the existing
`comments_all` DAG.

Full KOL/official article bodies additionally require
`futu_comments_full_article_indexes.mysql`. Without the
`(detail_updated_at, feed_id)` cursor, Radar now fails source preflight instead
of silently serving the abbreviated first feed snapshot.

The feed-details DAG must also deploy `futu_radar_kol_roster.json` and
`verify_futu_feed_details.py` from `collector_examples/`. The roster supplies
stable Futu user IDs instead of relying on mutable nicknames. The verifier makes
the task fail whenever any recent active-KOL post still lacks a successful
detail fetch, preventing a short list preview from being treated as the full
article and losing ETF mentions from later paragraphs.

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

The collector's configured list must come from Radar's desired catalog
manifest (`python -m jobs.hkex_catalog manifest`). The manifest includes active
products from the latest complete HKEX snapshot and returns the same count/
fingerprint format used by the receipt. Keep collecting the previous accepted
manifest until the next manifest is accepted.

Partial and `comments_important` runs may have receipts, but must leave
`complete_through` null. Radar independently verifies every completion-proof
field and refuses to advance its anchor when any field is missing or mismatched.


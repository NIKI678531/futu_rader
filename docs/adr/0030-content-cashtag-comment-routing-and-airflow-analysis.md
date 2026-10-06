# ADR-0030: Content-cashtag comment routing and scheduled AI analysis

Status: Accepted; the scheduled-analysis parts are superseded by
[ADR-0031](0031-daily-full-refresh-single-queue.md) (2026-10-06): one daily
`jobs.daily` entry covers every page range and every active product, and
calibration/human-gold reports are no longer run gates. Comment routing is
unchanged.

Schedule amendment (2026-10-05): the automatic check now runs every day at
08:00 Asia/Hong_Kong. Daily runs cover `d1,d2,d7`; Monday also covers
`d14,d30,mtd`. Existing outputs with unchanged input fingerprints are reused,
so only missing or stale work reaches the model. All release gates remain in
force.

## Decision

Comment-level AI eligibility is represented by `comment_product_routes`. A
route exists only when a strict FUTU cashtag for an active product appears in
the parent feed title/body or in that individual comment. The discussion
section (`feeds.code`) is provenance and remains the platform-metric grouping;
it does not grant AI eligibility. One comment may route to several products.

Date windows use the parent feed's `posted_at`, converted to Hong Kong calendar
boundaries as half-open intervals. Existing low-content, duplicate and
near-duplicate filters run after deterministic routing.

Route activation records `comment_route_version` and
`comment_route_pool_digest` in `meta_kv`. Comment task claims, published atomic
annotations, quality reports, and Layer-B fingerprints bind to that generation.
Rows that lose their route remain available for audit but cannot be claimed or
published. Activation marks all product synthesis outputs dirty and invalidates
the prior human-validation marker.

Automatic analysis is an Airflow `KubernetesPodOperator` workload scheduled at
08:00 Asia/Hong_Kong every day. It consumes the latest complete collection result,
uses a digest-pinned image and Secret-only runtime credentials, and starts
paused. Monday uses all analysis ranges; other days use `d1,d2,d7`.
Partial or budget-exhausted runs return non-zero and are resumed idempotently by
the next scheduled run.

Analysis is bounded to the strict-tag comment-volume Top 10 for each requested
range. Range priority is `d1`, then `d2`, then `d7`, followed by the longer
windows. Products outside that range's Top 10 are not queued for Layer A or
Layer B; existing historical outputs remain auditable but are not refreshed.

## Consequences

- Platform comment counts, heat and rankings retain the parent-feed filter.
- Comment AI and its downstream synthesis may include cross-section products,
  but only where content carries their exact cashtag.
- A route-rule or product-pool change requires a new backfill, calibration
  report and 400-row human-gold report before the DAG can be enabled.


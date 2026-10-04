# ADR-0030: Content-cashtag comment routing and scheduled AI analysis

Status: Accepted

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
20:30 Asia/Hong_Kong on weekdays. It consumes the earlier collection result,
uses a digest-pinned image and Secret-only runtime credentials, and starts
paused. Monday uses all analysis ranges; Tuesday through Friday use `d1,d2`.
Partial or budget-exhausted runs return non-zero and are resumed idempotently by
the next scheduled run.

## Consequences

- Platform comment counts, heat and rankings retain the parent-feed filter.
- Comment AI and its downstream synthesis may include cross-section products,
  but only where content carries their exact cashtag.
- A route-rule or product-pool change requires a new backfill, calibration
  report and 400-row human-gold report before the DAG can be enabled.


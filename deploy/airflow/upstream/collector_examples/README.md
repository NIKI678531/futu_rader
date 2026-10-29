# Futu collector replacement files

These files are complete replacement examples for the four DAGs supplied by
the operator.  Do not enable them until the source database migration and the
new Airflow Variable are ready.

## Copy locations

Copy the files into the Airflow Git repository:

| This folder | Airflow repository destination |
| --- | --- |
| `common/futu_collection_receipts.py` | `common/futu_collection_receipts.py` |
| `futu_all_comments_scrape.py` | The existing all-comments DAG file |
| `futu_important_comments_scrape.py` | The existing important-comments DAG file |
| `futu_feed_details_backfill.py` | The existing feed-details DAG file |
| `futu_radar_kol_roster.json` | Next to the feed-details DAG; copy from Radar's reviewed active roster |
| `verify_futu_feed_details.py` | Next to the feed-details DAG |
| `futu_api_check.py` | The existing API-check DAG file |

The old combined comments file defines both `all` and `important`. Remove or
rename it after adding the two split files; otherwise Airflow will report
duplicate DAG IDs.

## Required database changes

Apply `../futu_comments_collection_runs.mysql` and
`../futu_comments_full_article_indexes.mysql` to the MarketInsight database by
its normal migration process during an approved maintenance window. The second
migration contains these required indexes:

```sql
ALTER TABLE futu_comments_feeds
  ADD INDEX ix_futu_comments_feeds_detail_updated_feed
  (detail_updated_at, feed_id);

ALTER TABLE futu_comments_users
  ADD INDEX ix_futu_comments_users_scraped_user
  (scraped_at, user_id);
```

## Required Airflow Variable

Create `futu_comments_all_codes` under **Admin -> Variables**. Its value must be
the complete `codes` list printed by:

```text
python -m jobs.hkex_catalog manifest
```

JSON example:

```json
["2802", "2843", "3031", "3033", "3408", "3418", "3444", "3455", "3579"]
```

The example above is not the complete production list. Do not copy it as-is.

The scraper's internal `all` scope must contain the exact same complete list and
must update `futu_comments_stocks.last_scraped_at` after each successful symbol.
The receipt task checks this. A missing or stale symbol fails closed and leaves
`complete_through` empty.

## Important behavior

- Only a successful 19:30 HKT `futu_all_comments_scrape` run claims a complete
  day and publishes `market-insight://futu-community`.
- The 08:30/13:30 all runs write receipts without a complete day.
- Important and detail runs write audit receipts only.
- The detail run selects every active KOL by stable Futu UID, with nickname
  aliases retained only as a fallback. It then verifies that every recent KOL
  post has a non-null `detail_updated_at` and body. Any remaining preview-only
  row fails the run and prints its feed ID; incomplete text is never reported
  as a successful detail refresh.
- The detail run executes hourly from 09:15 through 20:15 on trading weekdays,
  with `max_active_runs=1`, so posts collected during the day do not remain on
  their abbreviated list preview until the next evening.
- Its 30-day scope and 1,000-row ceiling are fixed in the reviewed DAG rather
  than mutable Airflow Variables. If the ceiling is ever insufficient, the
  completeness verifier fails the run instead of silently accepting the first
  batch as complete.
- `futu_radar_kol_roster.json` must stay semantically identical to
  `backend/fixtures/kol_roster_updates.json`. Radar's repository tests enforce
  this before deployment, so an active-roster or nickname change cannot silently
  disappear from full-article collection again.
- `futu_api_check` remains an OpenD monitor and writes no community receipt.
- The screenshot also contains `futu_users_refresh`. Its source was not supplied,
  so it is not replaced by this bundle.

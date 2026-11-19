"""MarketInsight collection-run receipts used by the Futu collector DAGs.

Copy this file to the Airflow repository's ``common`` package.  The helper is
deliberately fail-closed: a closing ``comments_all`` run is successful only
when every configured ticker was refreshed after the receipt started.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

from airflow.exceptions import AirflowException, AirflowSkipException
from airflow.models import Variable
from sqlalchemy import create_engine, text

log = logging.getLogger(__name__)

ALL_CODES_VARIABLE = "futu_comments_all_codes"
DATABASE_URL_VARIABLE = "market_insight_database_url"


def _database_url() -> str:
    value = Variable.get(DATABASE_URL_VARIABLE).strip()
    if not value:
        raise AirflowException(f"Airflow Variable {DATABASE_URL_VARIABLE} is empty")
    return value


def _code(value: object) -> str:
    candidate = str(value or "").strip().upper()
    if candidate.startswith("HK."):
        candidate = candidate[3:]
    if candidate.endswith(".HK"):
        candidate = candidate[:-3]
    if len(candidate) == 5 and candidate.startswith("0"):
        candidate = candidate[1:]
    if re.fullmatch(r"[1-9]\d{3}", candidate) is None:
        raise AirflowException(f"Invalid Hong Kong product code: {value!r}")
    return candidate


def load_all_codes() -> tuple[str, ...]:
    """Read the exact Radar manifest codes from one Airflow Variable.

    The value may be a JSON list or a comma/space/newline separated string.
    """

    raw = Variable.get(ALL_CODES_VARIABLE).strip()
    if not raw:
        raise AirflowException(f"Airflow Variable {ALL_CODES_VARIABLE} is empty")
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = re.split(r"[\s,]+", raw)
    if isinstance(decoded, dict):
        decoded = decoded.get("codes")
    if not isinstance(decoded, list):
        raise AirflowException(
            f"Airflow Variable {ALL_CODES_VARIABLE} must be a JSON list or code list"
        )
    codes = tuple(sorted({_code(value) for value in decoded if str(value).strip()}))
    if not codes:
        raise AirflowException(f"Airflow Variable {ALL_CODES_VARIABLE} has no codes")
    return codes


def write_manifest_symbols(
    *,
    work_dir: str,
    symbol_scope: str,
    variable_name: str = ALL_CODES_VARIABLE,
) -> int:
    """Write the accepted Radar manifest as the collector's run-local input."""

    raw = Variable.get(variable_name).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AirflowException(f"Airflow Variable {variable_name} must be JSON") from exc
    manifest_symbols = payload.get("symbols") if isinstance(payload, dict) else None
    raw_codes = payload.get("codes") if isinstance(payload, dict) else payload
    if not isinstance(raw_codes, list) or not raw_codes:
        raise AirflowException(
            f"Airflow Variable {variable_name} must contain a non-empty codes list"
        )

    expected = {f"0{_code(value)}.HK" for value in raw_codes}
    source_path = Path(work_dir) / "symbols.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    by_ticker = {str(item["ticker"]).upper(): item for item in source}
    if manifest_symbols is not None:
        if not isinstance(manifest_symbols, list):
            raise AirflowException(
                f"Airflow Variable {variable_name} symbols must be a list"
            )
        by_ticker = {}
        for item in manifest_symbols:
            if not isinstance(item, dict):
                raise AirflowException(
                    f"Airflow Variable {variable_name} has an invalid symbol entry"
                )
            ticker = f"0{_code(item.get('ticker'))}.HK"
            if item.get("type") != "etf" or not isinstance(item.get("important"), bool):
                raise AirflowException(
                    f"Airflow Variable {variable_name} has invalid metadata for {ticker}"
                )
            by_ticker[ticker] = {
                "ticker": ticker,
                "type": "etf",
                "important": item["important"],
            }
        if set(by_ticker) != expected:
            raise AirflowException(
                f"Airflow Variable {variable_name} symbols must exactly match codes"
            )
    else:
        for ticker in expected - set(by_ticker):
            by_ticker[ticker] = {
                "ticker": ticker,
                "type": "etf",
                "important": False,
            }

    selected = [by_ticker[ticker] for ticker in sorted(expected)]
    if symbol_scope == "important":
        selected = [item for item in selected if item.get("important") is True]
    elif symbol_scope != "all":
        raise AirflowException(f"Unsupported symbol scope: {symbol_scope!r}")
    if not selected:
        raise AirflowException(f"Radar manifest has no symbols for {symbol_scope} scope")
    (Path(work_dir) / "collection_symbols.json").write_text(
        json.dumps(selected, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(selected)


def symbols_fingerprint(codes: tuple[str, ...]) -> str:
    payload = "\n".join(sorted(set(codes))).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _scheduled_for(value: str) -> datetime:
    """Parse the UTC-naive Airflow schedule timestamp used by Radar."""

    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError as exc:
        raise AirflowException(f"Invalid scheduled_for value: {value!r}") from exc


def start_receipt(
    *,
    collection_run_id: str,
    collection_kind: str,
    scheduled_for_utc: str,
    verify_all_codes: bool = False,
) -> dict:
    """Insert/reset a running receipt immediately before collection."""

    codes = load_all_codes() if verify_all_codes else ()
    configured_count = len(codes) if codes else None
    fingerprint = symbols_fingerprint(codes) if codes else None
    engine = create_engine(_database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            existing = conn.execute(
                text(
                    "SELECT status FROM futu_comments_collection_runs "
                    "WHERE run_id=:run_id FOR UPDATE"
                ),
                {"run_id": collection_run_id},
            ).scalar()
            if existing == "succeeded":
                raise AirflowSkipException(
                    f"Collection receipt {collection_run_id!r} already succeeded"
                )
            if existing is None:
                conn.execute(
                    text(
                        """
                        INSERT INTO futu_comments_collection_runs (
                            run_id, collection_kind, status, scheduled_for,
                            started_at, configured_symbol_count,
                            configured_symbols_fingerprint
                        ) VALUES (
                            :run_id, :kind, 'running', :scheduled_for,
                            CURRENT_TIMESTAMP(6), :configured_count, :fingerprint
                        )
                        """
                    ),
                    {
                        "run_id": collection_run_id,
                        "kind": collection_kind,
                        "scheduled_for": _scheduled_for(scheduled_for_utc),
                        "configured_count": configured_count,
                        "fingerprint": fingerprint,
                    },
                )
            else:
                conn.execute(
                    text(
                        """
                        UPDATE futu_comments_collection_runs
                        SET collection_kind=:kind,
                            status='running',
                            scheduled_for=:scheduled_for,
                            started_at=CURRENT_TIMESTAMP(6),
                            finished_at=NULL,
                            high_watermark_at=NULL,
                            complete_through=NULL,
                            configured_symbol_count=:configured_count,
                            configured_symbols_fingerprint=:fingerprint,
                            attempted_symbol_count=NULL,
                            succeeded_symbol_count=NULL,
                            error_summary=NULL
                        WHERE run_id=:run_id
                        """
                    ),
                    {
                        "run_id": collection_run_id,
                        "kind": collection_kind,
                        "scheduled_for": _scheduled_for(scheduled_for_utc),
                        "configured_count": configured_count,
                        "fingerprint": fingerprint,
                    },
                )
        return {
            "runId": collection_run_id,
            "configuredSymbolCount": configured_count,
            "configuredSymbolsFingerprint": fingerprint,
        }
    finally:
        engine.dispose()


def _source_high_watermark(conn):
    return conn.execute(
        text(
            """
            SELECT MAX(observed_at)
            FROM (
                SELECT MAX(scraped_at) AS observed_at FROM futu_comments_feeds
                UNION ALL
                SELECT MAX(detail_updated_at) AS observed_at FROM futu_comments_feeds
                UNION ALL
                SELECT MAX(scraped_at) AS observed_at FROM futu_comments_users
            ) source_watermarks
            """
        )
    ).scalar()


def _fresh_all_codes(conn, *, started_at: datetime, expected: set[str]) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT ticker, last_scraped_at, last_scrape_status
            FROM futu_comments_stocks
            WHERE last_scraped_at IS NOT NULL
            """
        )
    ).mappings()
    fresh = set()
    for row in rows:
        try:
            code = _code(row["ticker"])
        except AirflowException:
            continue
        status = str(row.get("last_scrape_status") or "").strip().lower()
        failed = "fail" in status or "error" in status
        if code in expected and row["last_scraped_at"] >= started_at and not failed:
            fresh.add(code)
    return fresh


def finish_receipt(
    *,
    collection_run_id: str,
    complete_through: str = "",
    verify_all_codes: bool = False,
) -> dict:
    """Commit one successful receipt after all collector writes have committed."""

    codes = load_all_codes() if verify_all_codes else ()
    expected = set(codes)
    requested_complete = date.fromisoformat(complete_through) if complete_through else None
    engine = create_engine(_database_url(), pool_pre_ping=True)
    error = None
    result = None
    try:
        with engine.begin() as conn:
            receipt = conn.execute(
                text(
                    """
                    SELECT collection_kind, status, started_at,
                           configured_symbol_count, configured_symbols_fingerprint
                    FROM futu_comments_collection_runs
                    WHERE run_id=:run_id FOR UPDATE
                    """
                ),
                {"run_id": collection_run_id},
            ).mappings().first()
            if receipt is None:
                raise AirflowException(f"Unknown collection receipt: {collection_run_id}")
            if receipt["status"] != "running":
                raise AirflowException(
                    f"Collection receipt {collection_run_id!r} is {receipt['status']!r}"
                )

            high_watermark = _source_high_watermark(conn)
            attempted = succeeded = None
            if verify_all_codes:
                expected_fingerprint = symbols_fingerprint(codes)
                if (
                    receipt["collection_kind"] != "comments_all"
                    or receipt["configured_symbol_count"] != len(codes)
                    or receipt["configured_symbols_fingerprint"] != expected_fingerprint
                ):
                    error = "comments_all configured symbol proof changed during collection"
                else:
                    fresh = _fresh_all_codes(
                        conn, started_at=receipt["started_at"], expected=expected
                    )
                    attempted = succeeded = len(fresh)
                    missing = sorted(expected - fresh)
                    if missing:
                        error = "symbols not refreshed in this run: " + ",".join(missing[:30])
                    elif high_watermark is None:
                        error = "source high watermark is empty"

            if error:
                conn.execute(
                    text(
                        """
                        UPDATE futu_comments_collection_runs
                        SET status='failed', finished_at=CURRENT_TIMESTAMP(6),
                            complete_through=NULL,
                            attempted_symbol_count=:attempted,
                            succeeded_symbol_count=:succeeded,
                            error_summary=:error
                        WHERE run_id=:run_id
                        """
                    ),
                    {
                        "run_id": collection_run_id,
                        "attempted": attempted,
                        "succeeded": succeeded,
                        "error": error[:1000],
                    },
                )
            else:
                conn.execute(
                    text(
                        """
                        UPDATE futu_comments_collection_runs
                        SET status='succeeded', finished_at=CURRENT_TIMESTAMP(6),
                            high_watermark_at=:high_watermark,
                            complete_through=:complete_through,
                            attempted_symbol_count=:attempted,
                            succeeded_symbol_count=:succeeded,
                            error_summary=NULL
                        WHERE run_id=:run_id
                        """
                    ),
                    {
                        "run_id": collection_run_id,
                        "high_watermark": high_watermark,
                        "complete_through": requested_complete,
                        "attempted": attempted,
                        "succeeded": succeeded,
                    },
                )
                result = {
                    "runId": collection_run_id,
                    "status": "succeeded",
                    "completeThrough": complete_through or None,
                    "highWatermarkAt": str(high_watermark) if high_watermark else None,
                }
        if error:
            raise AirflowException(error)
        return result
    finally:
        engine.dispose()


def fail_receipt(*, collection_run_id: str, reason: str) -> None:
    """Best-effort failure transition used by Airflow failure callbacks."""

    engine = create_engine(_database_url(), pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE futu_comments_collection_runs
                    SET status='failed', finished_at=CURRENT_TIMESTAMP(6),
                        complete_through=NULL, error_summary=:error
                    WHERE run_id=:run_id AND status='running'
                    """
                ),
                {"run_id": collection_run_id, "error": reason[:1000]},
            )
    except Exception:
        log.exception("Could not mark collection receipt %s failed", collection_run_id)
    finally:
        engine.dispose()

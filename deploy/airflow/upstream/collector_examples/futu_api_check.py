"""Monitor DAG for Futu OpenD market-data connectivity.

This monitor is intentionally separate from community collection receipts and
does not publish ``market-insight://futu-community``.
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from common.callbacks import on_failure_power_automate
from common.config import g_config
from common.constants import DEFAULT_ARGS, TAGS_MONITOR


def _check_futu_api():
    from futu import OpenQuoteContext, RET_OK

    host = g_config.FUTU_OPEND_HOST
    port = g_config.FUTU_OPEND_PORT
    quote_ctx = OpenQuoteContext(host=host, port=port)
    try:
        ret, data = quote_ctx.get_market_snapshot(
            ["HK.00700", "US.AAPL", "SH.600519", "SZ.000001"]
        )
        if ret != RET_OK:
            raise ValueError(f"Futu API query failed: {data}")
        codes = data["code"].values.tolist()
        print(f"Futu API is healthy ({host}:{port}), returned: {codes}")
    finally:
        quote_ctx.close()


with DAG(
    dag_id="futu_api_check",
    description="Futu OpenD API connectivity check",
    default_args={
        **DEFAULT_ARGS,
        "on_failure_callback": on_failure_power_automate,
    },
    start_date=datetime(2026, 3, 3),
    schedule="0 */1 * * *",
    catchup=False,
    tags=TAGS_MONITOR,
) as dag:
    check_futu = PythonOperator(
        task_id="check_futu_api",
        python_callable=_check_futu_api,
        execution_timeout=timedelta(seconds=30),
    )

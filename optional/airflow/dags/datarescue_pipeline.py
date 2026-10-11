"""Optional orchestration (compose profile `optional-orchestration`).

generate -> ingest -> wait for batch -> diagnose open incidents -> refresh dbt -> publish metrics

Every step talks to the DataRescue REST API (or the dbt CLI), so Airflow adds scheduling,
retries and task-level observability without duplicating platform logic. The built-in
scheduler in `app.workers.main scheduler` remains the default; this DAG is an alternative.
"""

import itertools
import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from airflow import DAG
from airflow.operators.python import PythonOperator

API = os.environ.get("DATARESCUE_API", "http://api:8000/api/v1")
USER = os.environ.get("DATARESCUE_USER", "engineer@example.test")
PASSWORD = os.environ.get("DATARESCUE_PASSWORD", "datarescue-demo")  # dev-only default
SOURCE = os.environ.get("DATARESCUE_SOURCE", "payments_core")


def _call(method: str, path: str, body: dict | None = None, token: str | None = None) -> Any:
    req = urllib.request.Request(  # noqa: S310 - fixed internal URL
        API + path,
        method=method,
        data=json.dumps(body).encode() if body else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read() or b"null")


def _token() -> str:
    return _call("POST", "/auth/login", {"email": USER, "password": PASSWORD})["access_token"]


def generate(**ctx: Any) -> str:
    seed = int(ctx["logical_date"].timestamp()) % 2**31  # same run -> same data on retry
    body = {"source": SOURCE, "scenario": "clean", "records": 1000, "seed": seed}
    return _call("POST", "/datasets/generate", body, _token())["id"]


def ingest(**ctx: Any) -> str:
    dataset_id = ctx["ti"].xcom_pull(task_ids="generate")
    # The default request key is derived from the dataset, so a task retry is idempotent.
    res = _call("POST", "/ingestions", {"source": SOURCE, "dataset_id": dataset_id}, _token())
    return res["batch_id"]


def wait_for_batch(**ctx: Any) -> dict:
    batch_id = ctx["ti"].xcom_pull(task_ids="ingest")
    token = _token()
    for _ in range(120):
        batch = _call("GET", f"/batches/{batch_id}", token=token)
        if batch["status"] in ("COMPLETE", "PARTIAL", "FAILED"):
            if batch["status"] == "FAILED":
                raise RuntimeError(f"batch {batch_id} failed: {batch['error_message']}")
            return {
                k: batch[k]
                for k in (
                    "status",
                    "accepted_count",
                    "rejected_count",
                    "skipped_count",
                    "incident_id",
                )
            }
        time.sleep(5)
    raise TimeoutError(f"batch {batch_id} did not finish")


def diagnose_open(**_: Any) -> int:
    token = _token()
    page = _call("GET", "/incidents?state=OPEN&limit=50", token=token)
    for incident in page["items"]:
        _call("POST", f"/incidents/{incident['id']}/diagnose", token=token)
    return len(page["items"])


def refresh_dbt(**_: Any) -> None:
    subprocess.run(
        # Fixed argv, dbt on PATH inside the Airflow image.
        ["dbt", "build", "--project-dir", "/opt/dbt", "--profiles-dir", "/opt/dbt/profiles"],  # noqa: S607
        check=True,
    )


def publish_metrics(**_: Any) -> dict:
    overview = _call("GET", "/metrics/overview", token=_token())
    summary = {
        "open_incidents": overview["incidents"]["open"],
        "quarantine_rate": overview["records"]["quarantine_rate"],
        "accepted": overview["records"]["accepted"],
    }
    print(json.dumps(summary))  # visible in the task log
    return summary


with DAG(
    dag_id="datarescue_pipeline",
    start_date=datetime(2026, 1, 1),
    schedule="@hourly",
    catchup=False,
    max_active_runs=1,
    default_args={
        "retries": 3,
        "retry_delay": timedelta(seconds=30),
        "retry_exponential_backoff": True,
    },
    tags=["datarescue"],
) as dag:
    steps = [
        PythonOperator(task_id="generate", python_callable=generate),
        PythonOperator(task_id="ingest", python_callable=ingest),
        PythonOperator(task_id="wait_for_batch", python_callable=wait_for_batch),
        PythonOperator(task_id="diagnose_open_incidents", python_callable=diagnose_open),
        PythonOperator(task_id="refresh_dbt", python_callable=refresh_dbt),
        PythonOperator(task_id="publish_metrics", python_callable=publish_metrics),
    ]
    for upstream, downstream in itertools.pairwise(steps):
        upstream >> downstream

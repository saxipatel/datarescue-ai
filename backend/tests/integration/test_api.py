"""HTTP-level tests: auth, RBAC, validation, error codes and the demo walkthrough."""

import io
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.reference import DEMO_PASSWORD
from tests.helpers import ingest

pytestmark = pytest.mark.integration


@pytest.fixture
def client(db):
    with TestClient(app) as c:
        yield c


def login(client: TestClient, role: str) -> dict[str, str]:
    r = client.post(
        "/api/v1/auth/login", json={"email": f"{role}@example.test", "password": DEMO_PASSWORD}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_health_and_ready(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    r = client.get("/api/v1/ready")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"


def test_auth_failures(client):
    r = client.post(
        "/api/v1/auth/login", json={"email": "engineer@example.test", "password": "wrong"}
    )
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    assert r.json()["correlation_id"]
    assert client.get("/api/v1/incidents").status_code == 401
    bad = {"Authorization": "Bearer not-a-jwt"}
    assert client.get("/api/v1/incidents", headers=bad).status_code == 401


def test_me_reports_permissions(client):
    body = client.get("/api/v1/auth/me", headers=login(client, "steward")).json()
    assert body["role"] == "steward" and "approve_repair" in body["permissions"]


def test_role_enforcement(client):
    analyst = login(client, "analyst")
    r = client.post(
        "/api/v1/simulations",
        headers=analyst,
        json={"scenario": "duplicate_event_ids", "records": 100},
    )
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    assert client.get("/api/v1/audit", headers=analyst).status_code == 403
    assert client.get("/api/v1/audit", headers=login(client, "admin")).status_code == 200


def test_validation_and_pagination(client):
    h = login(client, "engineer")
    r = client.get("/api/v1/batches?limit=500", headers=h)
    assert r.status_code == 422 and r.json()["code"] == "validation_failed"
    r = client.post("/api/v1/simulations", headers=h, json={"scenario": "nope"})
    assert r.status_code == 422
    assert client.get(f"/api/v1/incidents/{uuid.uuid4()}", headers=h).status_code == 404


def test_upload_rejections(client):
    h = login(client, "engineer")
    r = client.post(
        "/api/v1/ingestions/upload",
        headers=h,
        data={"source": "payments_core"},
        files={"file": ("data.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 422
    r = client.post(
        "/api/v1/ingestions/upload",
        headers=h,
        data={"source": "payments_core"},
        files={"file": ("data.csv", b"event_id,amount\n1,2\n", "text/csv")},
    )
    assert r.status_code == 422 and "missing_columns" in r.json()["details"]
    r = client.post(
        "/api/v1/ingestions/upload",
        headers=h,
        data={"source": "payments_core"},
        files={"file": ("data.csv", b"\xff\xfe\x00bad", "text/csv")},
    )
    assert r.status_code == 422


def test_csv_upload_ingests_and_dedupes_repeat_upload(client):
    h = login(client, "engineer")
    csv_body = (
        "event_id,transaction_id,customer_id,merchant_id,amount,currency,status,event_time,"
        "source_system,schema_version\n"
        f"{uuid.uuid4()},T1,C1,M1,10.50,USD,SETTLED,2026-01-05T10:00:00Z,payments_core,1\n"
        f"{uuid.uuid4()},T2,C2,M1,-3.00,USD,SETTLED,2026-01-05T10:01:00Z,payments_core,1\n"
        "broken line without enough columns\n"
    ).encode()
    files = {"file": ("batch.csv", io.BytesIO(csv_body), "text/csv")}
    r = client.post(
        "/api/v1/ingestions/upload", headers=h, data={"source": "payments_core"}, files=files
    )
    assert r.status_code == 202, r.text
    batch_id = r.json()["batch_id"]
    batch = client.get(f"/api/v1/batches/{batch_id}", headers=h).json()
    assert (batch["accepted_count"], batch["rejected_count"]) == (1, 2)
    assert batch["status"] == "PARTIAL"

    files = {"file": ("batch.csv", io.BytesIO(csv_body), "text/csv")}
    again = client.post(
        "/api/v1/ingestions/upload", headers=h, data={"source": "payments_core"}, files=files
    )
    assert again.status_code == 200 and again.json()["duplicate_request"] is True
    assert again.json()["batch_id"] == batch_id


def test_queue_down_returns_503(client, monkeypatch):
    h = login(client, "engineer")
    monkeypatch.setattr(get_settings(), "task_mode", "rq")
    monkeypatch.setattr(get_settings(), "redis_url", "redis://127.0.0.1:1/0")
    from app.workers import queue

    queue.redis_connection.cache_clear()
    try:
        r = client.post("/api/v1/simulations", headers=h, json={"scenario": "clean", "records": 50})
        assert r.status_code == 503 and r.json()["code"] == "queue_unavailable"
    finally:
        queue.redis_connection.cache_clear()


def test_demo_walkthrough_over_http(client, db):
    """A13 at the API level: the README demo, step by step."""
    for seq in (1, 2):
        ingest(db, seq)
    eng, stew = login(client, "engineer"), login(client, "steward")

    r = client.post(
        "/api/v1/simulations",
        headers=eng,
        json={"scenario": "duplicate_event_ids", "records": 1000, "corruption_pct": 10},
    )
    assert r.status_code == 202, r.text
    sim = client.get(f"/api/v1/simulations/{r.json()['simulation']['id']}", headers=eng).json()
    assert sim["evaluation"]["counts_match"] is True
    assert sim["evaluation"]["quarantine_false_positives"] == 0
    incident_id = sim["evaluation"]["incident_id"]

    detail = client.get(f"/api/v1/incidents/{incident_id}", headers=eng).json()
    assert detail["state"] == "ACTION_REQUIRED"
    kinds = {e["kind"] for e in detail["evidence"]}
    assert {"rule_failure", "batch_metadata", "duplicate_analysis", "impact"} <= kinds
    impact = client.get(f"/api/v1/incidents/{incident_id}/impact", headers=eng).json()
    assert "dashboard_revenue" in {n["node_key"] for n in impact["impacted"]}
    diagnosis = detail["diagnosis"]
    assert diagnosis["suspected_causes"][0]["cause_key"] == "non_idempotent_retry"
    assert diagnosis["suspected_causes"][0]["supporting_evidence_ids"]

    r = client.post(
        "/api/v1/repairs/proposals",
        headers=eng,
        json={
            "incident_id": incident_id,
            "action": "SKIP_IDENTICAL_DUPLICATE",
            "justification": "Identical redelivery of previous batch; confirm skip",
        },
    )
    assert r.status_code == 201, r.text
    proposal = r.json()

    # A08: not approved yet -> 403
    r = client.post(
        "/api/v1/recoveries",
        headers=stew,
        json={"proposal_id": proposal["id"], "idempotency_key": "demo-key-0001"},
    )
    assert r.status_code == 403 and r.json()["code"] == "approval_required"
    # Engineers cannot approve; approving before a dry run is a conflict.
    r = client.post(f"/api/v1/repairs/proposals/{proposal['id']}/approve", headers=eng, json={})
    assert r.status_code == 403
    r = client.post(f"/api/v1/repairs/proposals/{proposal['id']}/approve", headers=stew, json={})
    assert r.status_code == 409 and r.json()["code"] == "dry_run_required"

    r = client.post(
        "/api/v1/recoveries/dry-run", headers=stew, json={"proposal_id": proposal["id"]}
    )
    assert r.status_code == 200 and r.json()["counts"]["would_insert"] == 0
    r = client.post(
        f"/api/v1/repairs/proposals/{proposal['id']}/approve",
        headers=stew,
        json={"note": "dry run reviewed"},
    )
    assert r.status_code == 200 and r.json()["status"] == "APPROVED"

    before = client.get("/api/v1/metrics/overview", headers=eng).json()["records"]["accepted"]
    r = client.post(
        "/api/v1/recoveries",
        headers=stew,
        json={"proposal_id": proposal["id"], "idempotency_key": "demo-key-0001"},
    )
    assert r.status_code == 202 and r.json()["outcome"] == "created"
    job_id = r.json()["job"]["id"]
    job = client.get(f"/api/v1/recoveries/{job_id}", headers=stew).json()
    assert job["state"] == "SUCCEEDED" and job["verification"]["passed"] is True
    assert {a["action"] for a in job["audit"]} >= {"recovery.started", "recovery.succeeded"}
    assert client.get(f"/api/v1/incidents/{incident_id}", headers=eng).json()["state"] == (
        "RESOLVED"
    )

    r = client.post(
        "/api/v1/recoveries",
        headers=stew,
        json={"proposal_id": proposal["id"], "idempotency_key": "demo-key-0001"},
    )
    assert r.status_code == 200 and r.json()["outcome"] == "completed"
    overview = client.get("/api/v1/metrics/overview", headers=eng).json()
    after = overview["records"]["accepted"]
    # Averages come back as JSON numbers, not Decimal strings.
    assert isinstance(overview["mean_time_to_detect_seconds"], float)
    assert isinstance(overview["recoveries"]["mean_duration_seconds"], float)
    assert after == before  # A10: no duplicate accepted rows

    # Resolved is terminal.
    r = client.post(
        f"/api/v1/incidents/{incident_id}/acknowledge", headers=eng, json={"note": "late ack"}
    )
    assert r.status_code == 409 and r.json()["code"] == "invalid_transition"


def test_rule_editing(client):
    admin, eng = login(client, "admin"), login(client, "engineer")
    rules = {r["rule_key"]: r for r in client.get("/api/v1/quality/rules", headers=eng).json()}
    rid = rules["schema_contract"]["id"]
    r = client.patch(
        f"/api/v1/quality/rules/{rid}", headers=eng, json={"enabled": False, "reason": "try"}
    )
    assert r.status_code == 403
    r = client.patch(
        f"/api/v1/quality/rules/{rid}", headers=admin, json={"enabled": False, "reason": "try it"}
    )
    assert r.status_code == 409 and r.json()["code"] == "rule_locked"
    late = rules["late_event"]
    r = client.patch(
        f"/api/v1/quality/rules/{late['id']}",
        headers=admin,
        json={
            "configuration": {"max_failure_pct": 2},
            "severity": "medium",
            "expected_version": late["version"],
            "reason": "tighten late-event budget",
        },
    )
    assert r.status_code == 200 and r.json()["version"] == late["version"] + 1
    r = client.patch(
        f"/api/v1/quality/rules/{late['id']}",
        headers=admin,
        json={
            "configuration": {"max_failure_pct": 3},
            "expected_version": late["version"],
            "reason": "stale edit",
        },
    )
    assert r.status_code == 409
    r = client.patch(
        f"/api/v1/quality/rules/{late['id']}",
        headers=admin,
        json={"configuration": {"drop_table": True}, "reason": "bad key"},
    )
    assert r.status_code == 422
    audit = client.get("/api/v1/audit?action=rule.updated", headers=admin).json()
    assert (
        audit["total"] == 1
        and audit["items"][0]["before_hash"] != (audit["items"][0]["after_hash"])
    )


def test_quarantine_export_neutralizes_formulas(client, db):
    ingest(db, 1, "invalid_amount", 10, records=100)
    h = login(client, "steward")
    page = client.get("/api/v1/quarantine?limit=5", headers=h).json()
    assert page["total"] == 10 and len(page["items"]) == 5
    body = client.get("/api/v1/quarantine/export.csv", headers=h).text
    rows = body.strip().splitlines()
    assert len(rows) == 11
    detail = client.get(f"/api/v1/quarantine/{page['items'][0]['id']}", headers=h).json()
    assert "***" in detail["raw_payload"]["customer_id"]  # redacted in API responses


def test_stale_pipeline_simulation_opens_freshness_incident(client, db):
    ingest(db, 1, records=50)
    h = login(client, "engineer")
    r = client.post("/api/v1/simulations", headers=h, json={"scenario": "stale_pipeline"})
    assert r.status_code == 202
    result = r.json()["freshness_results"][0]
    assert result["status"] == "stale" and result["simulated_clock"] is True
    incidents = client.get("/api/v1/incidents?open_only=true", headers=h).json()["items"]
    assert any(i["category"] == "freshness" for i in incidents)

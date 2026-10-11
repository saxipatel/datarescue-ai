"""Scripted failure -> recovery walkthrough against a running stack (stdlib only).

    python scripts/demo_scenario.py [--api http://localhost:8000] [--chaos]

Steps: inject 10% duplicate event IDs into a 1,000-row batch as the engineer, wait for
the incident and its deterministic diagnosis, propose SKIP_IDENTICAL_DUPLICATE, dry run
and approve as the steward, execute, verify, then repeat with the same idempotency key.
With --chaos (requires CHAOS_ENABLED=true) a NORMALIZE_CURRENCY_CASE recovery is crashed
mid-run and retried with the same key.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

PASSWORD = "datarescue-demo"  # noqa: S105 - documented local demo credential


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/") + "/api/v1"
        self.token: str | None = None

    def call(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        expect: tuple[int, ...] = (200, 201, 202),
    ) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)  # noqa: S310
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed scheme
                status, payload = resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            status, payload = exc.code, json.loads(exc.read() or b"null")
        if status not in expect:
            raise SystemExit(f"{method} {path} -> {status}: {payload}")
        return status, payload

    def login(self, role: str) -> "Api":
        _, body = self.call(
            "POST", "/auth/login", {"email": f"{role}@example.test", "password": PASSWORD}
        )
        self.token = body["access_token"]
        return self


def wait_for(fn: Any, what: str, timeout: float = 120.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(1)
    raise SystemExit(f"timed out waiting for {what}")


def step(n: int, text: str) -> None:
    print(f"\n[{n}] {text}")


def run_recovery(
    eng: Api, stew: Api, incident_id: str, action: str, key: str, crash_after: int | None = None
) -> dict:
    _, proposal = eng.call(
        "POST",
        "/repairs/proposals",
        {
            "incident_id": incident_id,
            "action": action,
            "justification": f"Demo: {action} recommended by diagnosis",
        },
    )
    print(
        f"    proposal {proposal['id']} targets {proposal['target_count']} rows, "
        f"risk={proposal['risk_level']}"
    )
    _, dry = stew.call("POST", "/recoveries/dry-run", {"proposal_id": proposal["id"]})
    print(f"    dry run: {dry['counts']}")
    stew.call("POST", f"/repairs/proposals/{proposal['id']}/approve", {"note": "dry run reviewed"})
    print("    approved by steward")
    body: dict[str, Any] = {"proposal_id": proposal["id"], "idempotency_key": key}
    if crash_after:
        body["chaos_crash_after"] = crash_after
    _, started = stew.call("POST", "/recoveries", body)
    job_id = started["job"]["id"]

    def done() -> dict | None:
        _, job = stew.call("GET", f"/recoveries/{job_id}")
        return job if job["state"] in ("SUCCEEDED", "FAILED") else None

    def stalled() -> dict | None:
        # A crashed worker leaves the job RUNNING with a dead lock; the API resumes it.
        _, job = stew.call("GET", f"/recoveries/{job_id}")
        return (
            job if job["state"] in ("RUNNING", "FAILED", "SUCCEEDED") and job["attempts"] else None
        )

    if crash_after:
        job = wait_for(stalled, "crashed recovery")
        print(
            f"    attempt {job['attempts']} running with a crash injected after "
            f"{crash_after} records"
        )

        def resumed() -> dict | None:
            # in_progress while the crashed attempt still holds its lock; retry after that.
            _, r = stew.call(
                "POST", "/recoveries", {"proposal_id": proposal["id"], "idempotency_key": key}
            )
            return r if r["outcome"] in ("retry", "completed") else None

        retried = wait_for(resumed, "crashed attempt to release its lock", timeout=60)
        print(f"    retry with same key -> outcome={retried['outcome']}")
    job = wait_for(done, "recovery job")
    print(f"    job {job_id}: state={job['state']} attempts={job['attempts']}")
    for check in job["verification"]["checks"]:
        print(f"      {'PASS' if check['passed'] else 'FAIL'} {check['name']}: {check['detail']}")
    return job


def main() -> int:
    parser = argparse.ArgumentParser(description="DataRescue demo walkthrough")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--chaos", action="store_true")
    args = parser.parse_args()

    eng, stew = Api(args.api).login("engineer"), Api(args.api).login("steward")
    _, before = eng.call("GET", "/metrics/overview")
    step(1, f"Signed in. Accepted rows so far: {before['records']['accepted']}")

    step(2, "Inject 10% duplicate event IDs into a 1,000-row batch")
    _, sim = eng.call(
        "POST",
        "/simulations",
        {"scenario": "duplicate_event_ids", "records": 1000, "corruption_pct": 10},
    )
    sim_id = sim["simulation"]["id"]

    def evaluated() -> dict | None:
        _, s = eng.call("GET", f"/simulations/{sim_id}")
        return s if s["evaluation"] and s["evaluation"].get("incident_id") else None

    sim = wait_for(evaluated, "ingestion of the faulty batch")
    ev = sim["evaluation"]
    print(f"    actual {ev['actual']} (labels expected {ev['expected']})")
    print(
        f"    false positives={ev['quarantine_false_positives']} "
        f"detection latency={ev['detection_latency_seconds']}s"
    )
    incident_id = ev["incident_id"]

    step(3, "Incident, evidence and lineage")

    def diagnosed() -> dict | None:
        _, inc = eng.call("GET", f"/incidents/{incident_id}")
        return inc if inc["state"] == "ACTION_REQUIRED" else None

    incident = wait_for(diagnosed, "diagnosis")
    print(f"    {incident['title']} [{incident['severity']}] failed={incident['failed_rules']}")
    _, impact = eng.call("GET", f"/incidents/{incident_id}/impact")
    print("    downstream: " + ", ".join(n["node_key"] for n in impact["impacted"]))

    step(4, "Deterministic diagnosis")
    for cause in incident["diagnosis"]["suspected_causes"]:
        print(
            f"    [{cause['confidence_label']}] {cause['cause']} "
            f"(evidence {len(cause['supporting_evidence_ids'])})"
        )
    actions = [a["action"] for a in incident["diagnosis"]["recommended_actions"]]
    print(f"    recommended: {actions}")

    step(5, "Steward reviews dry run, approves, recovery executes")
    key = f"demo-{uuid.uuid4().hex[:12]}"
    job = run_recovery(eng, stew, incident_id, "SKIP_IDENTICAL_DUPLICATE", key)
    _, inc = eng.call("GET", f"/incidents/{incident_id}")
    print(f"    incident state: {inc['state']}")

    step(6, "Repeat the recovery with the same idempotency key")
    _, again = stew.call(
        "POST", "/recoveries", {"proposal_id": job["proposal_id"], "idempotency_key": key}
    )
    _, after = eng.call("GET", "/metrics/overview")
    added = after["records"]["accepted"] - before["records"]["accepted"]
    print(
        f"    outcome={again['outcome']}; accepted rows added by the whole demo: {added} "
        "(the 900 unique rows of the new batch)"
    )

    if args.chaos:
        step(7, "Chaos: crash a currency repair mid-run, retry with the same key")
        _, sim = eng.call(
            "POST",
            "/simulations",
            {"scenario": "malformed_currency", "records": 10000, "corruption_pct": 20},
        )
        sim_id = sim["simulation"]["id"]
        ev = wait_for(evaluated, "malformed currency batch")["evaluation"]
        incident_id = ev["incident_id"]
        wait_for(diagnosed, "diagnosis")
        run_recovery(
            eng,
            stew,
            incident_id,
            "NORMALIZE_CURRENCY_CASE",
            f"chaos-{uuid.uuid4().hex[:12]}",
            crash_after=500,
        )
    print("\ndemo complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())

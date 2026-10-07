import logging

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.ibm.gateway import Credentials, DeviceInfo, HardwareError, JobInfo, UsageInfo
from backend.main import app
from backend.router.api.ibm import IbmState, get_state

GOOD_KEY = "good-ibm-api-key-0123456789abcdefghijklmnopqrs"
OTHER_KEY = "other-users-ibm-api-key-0123456789abcdefghijk"
ORIGIN = "http://localhost:5173"
BELL = {
    "qubits": 2,
    "gates": [
        {"type": "H", "qubit": 0, "position": 0},
        {"type": "CNOT", "control": 0, "target": 1, "position": 1},
    ],
    "shots": 100,
    "backend_name": "ibm_test",
}


class FakeGateway:
    def __init__(self) -> None:
        self.statuses: dict[str, str] = {}
        self.submitted: list[tuple[str, int]] = []
        self.forgotten: list[str] = []
        self.cancelled: list[str] = []
        self.seen_keys: list[str] = []
        self.owners: dict[str, str] = {}

    def _check(self, credentials: Credentials) -> None:
        self.seen_keys.append(credentials.api_key.get_secret_value())
        if credentials.api_key.get_secret_value() not in (GOOD_KEY, OTHER_KEY):
            raise HardwareError("Could not connect to IBM Quantum with this API key and instance.", 401)

    def connect(self, credentials: Credentials) -> str:
        self._check(credentials)
        logging.getLogger("backend.test").info("connecting with %s", credentials.api_key.get_secret_value())
        return "crn:test-instance"

    def devices(self, credentials: Credentials, min_qubits: int) -> list[DeviceInfo]:
        self._check(credentials)
        return [DeviceInfo("ibm_test", 127, 3, True)]

    def usage(self, credentials: Credentials) -> UsageInfo:
        self._check(credentials)
        return UsageInfo(remaining_seconds=540.0, consumed_seconds=60.0, limit_reached=False)

    def submit(self, credentials, qasm, backend_name, shots, max_execution_time) -> JobInfo:
        self._check(credentials)
        job_id = f"job-{len(self.submitted) + 1}"
        self.submitted.append((job_id, shots))
        self.owners[job_id] = credentials.api_key.get_secret_value()
        self.statuses[job_id] = "queued"
        return JobInfo(job_id, backend_name, "queued")

    def job(self, credentials: Credentials, job_id: str) -> JobInfo:
        self._check(credentials)
        # Like IBM: only the instance that owns the job can see it
        if self.owners.get(job_id) != credentials.api_key.get_secret_value():
            raise HardwareError("Job not found", 404)
        status = self.statuses[job_id]
        if status == "done":
            return JobInfo(job_id, "ibm_test", "done", counts={"00": 48, "11": 52}, quantum_seconds=2.5)
        return JobInfo(job_id, "ibm_test", status)  # type: ignore[arg-type]

    def cancel(self, credentials: Credentials, job_id: str) -> None:
        self._check(credentials)
        self.cancelled.append(job_id)
        self.statuses[job_id] = "cancelled"

    def forget(self, cache_key: str) -> None:
        self.forgotten.append(cache_key)


def make_client(**overrides) -> tuple[TestClient, IbmState, FakeGateway]:
    settings = Settings(_env_file=None, allowed_origins=[ORIGIN], **overrides)
    gateway = FakeGateway()
    state = IbmState(settings, gateway=gateway)
    app.dependency_overrides[get_state] = lambda: state
    # https so the Secure cookie is sent back like a browser would
    return TestClient(app, base_url="https://testserver"), state, gateway


@pytest.fixture(autouse=True)
def _reset_overrides():
    yield
    app.dependency_overrides.clear()


def connect(client: TestClient, key: str = GOOD_KEY, origin: str | None = ORIGIN):
    headers = {"Origin": origin} if origin else {}
    return client.post("/api/ibm/session", json={"api_key": key}, headers=headers)


class TestSession:
    def test_connect_sets_a_hardened_session_cookie(self) -> None:
        client, _, _ = make_client()
        response = connect(client)

        assert response.status_code == 200
        assert response.json()["connected"] is True
        assert response.json()["instance"] == "crn:test-instance"
        cookie = response.headers["set-cookie"]
        assert cookie.startswith("__Host-qamposer_ibm=")
        for attribute in ["HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=7200"]:
            assert attribute.lower() in cookie.lower()
        assert response.headers["cache-control"] == "no-store"

    def test_never_returns_the_api_key(self) -> None:
        client, _, _ = make_client()
        response = connect(client)
        assert GOOD_KEY not in response.text
        assert GOOD_KEY not in response.headers["set-cookie"]
        assert GOOD_KEY not in client.get("/api/ibm/session").text

    def test_rejects_an_invalid_key_with_a_generic_message(self) -> None:
        client, _, _ = make_client()
        response = connect(client, key="wrong-key")
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["detail"] == "Could not connect to IBM Quantum with this API key and instance."
        assert "set-cookie" not in response.headers

    @pytest.mark.parametrize("origin", [None, "https://evil.example"])
    def test_rejects_requests_from_other_origins(self, origin: str | None) -> None:
        client, _, gateway = make_client()
        assert connect(client, origin=origin).status_code == 403
        assert gateway.seen_keys == []

    def test_reports_the_session_state(self) -> None:
        client, _, _ = make_client()
        assert client.get("/api/ibm/session").json() == {
            "connected": False,
            "instance": None,
            "expires_at": None,
        }
        connect(client)
        info = client.get("/api/ibm/session").json()
        assert info["connected"] is True
        assert info["expires_at"].endswith("+00:00")

    def test_disconnect_forgets_the_session(self) -> None:
        client, state, gateway = make_client()
        connect(client)
        session_id = client.cookies.get("__Host-qamposer_ibm")

        response = client.delete("/api/ibm/session", headers={"Origin": ORIGIN})

        assert response.status_code == 204
        assert "max-age=0" in response.headers["set-cookie"].lower()
        assert state.store.get(session_id) is None
        assert state.cipher.session_key(session_id) in gateway.forgotten
        assert client.get("/api/ibm/session").json()["connected"] is False

    def test_reconnecting_replaces_the_previous_session(self) -> None:
        client, state, _ = make_client()
        connect(client)
        first = client.cookies.get("__Host-qamposer_ibm")
        connect(client)
        assert state.store.get(first) is None

    def test_rate_limits_connection_attempts_per_ip(self) -> None:
        client, _, _ = make_client(ibm_session_rate_limit_per_minute=2)
        assert connect(client, key="wrong").status_code == 401
        assert connect(client, key="wrong").status_code == 401
        assert connect(client).status_code == 429

    def test_uses_the_forwarded_client_ip_behind_a_trusted_proxy(self) -> None:
        client, _, _ = make_client(ibm_session_rate_limit_per_minute=1, trusted_proxy_hops=1)

        def attempt(ip: str) -> int:
            headers = {"Origin": ORIGIN, "X-Forwarded-For": f"203.0.113.9, {ip}"}
            return client.post("/api/ibm/session", json={"api_key": "x"}, headers=headers).status_code

        assert attempt("198.51.100.1") == 401
        assert attempt("198.51.100.1") == 429
        assert attempt("198.51.100.2") == 401  # a different client is not affected

    def test_validation_errors_do_not_echo_the_key(self) -> None:
        client, _, _ = make_client()
        response = client.post("/api/ibm/session", json={"api_key": GOOD_KEY + "x" * 600}, headers={"Origin": ORIGIN})
        assert response.status_code == 422
        assert GOOD_KEY not in response.text

    def test_does_not_log_the_key(self, caplog: pytest.LogCaptureFixture) -> None:
        from backend.util.redact import RedactingFilter

        caplog.handler.addFilter(RedactingFilter())
        caplog.set_level(logging.DEBUG)
        client, _, _ = make_client()
        connect(client)
        client.post("/api/ibm/jobs", json=BELL, headers={"Origin": ORIGIN})
        assert GOOD_KEY not in caplog.text

    def test_can_be_disabled(self) -> None:
        client, _, _ = make_client(ibm_enabled=False)
        assert connect(client).status_code == 404


class TestDevicesAndUsage:
    def test_require_a_session(self) -> None:
        client, _, _ = make_client()
        assert client.get("/api/ibm/backends").status_code == 401
        assert client.get("/api/ibm/usage").status_code == 401

    def test_list_devices_and_usage(self) -> None:
        client, _, _ = make_client()
        connect(client)
        assert client.get("/api/ibm/backends?min_qubits=2").json() == [
            {"name": "ibm_test", "num_qubits": 127, "pending_jobs": 3, "operational": True}
        ]
        assert client.get("/api/ibm/usage").json()["remaining_seconds"] == 540.0


class TestJobs:
    def submit(self, client: TestClient, **changes):
        return client.post("/api/ibm/jobs", json={**BELL, **changes}, headers={"Origin": ORIGIN})

    def test_submit_poll_and_get_the_result(self) -> None:
        client, _, gateway = make_client()
        connect(client)

        submitted = self.submit(client)
        assert submitted.status_code == 201
        assert submitted.json() == {
            "id": "job-1",
            "backend": "ibm_test",
            "status": "queued",
            "result": None,
            "error": None,
        }
        assert client.get("/api/ibm/jobs/job-1").json()["status"] == "queued"

        gateway.statuses["job-1"] = "done"
        result = client.get("/api/ibm/jobs/job-1").json()["result"]
        assert result["counts"] == {"00": 48, "11": 52}
        assert result["execution_time"] == 2.5
        assert {p["state"] for p in result["qsphere"]} == {"00", "11"}

    def test_requires_an_allowed_origin(self) -> None:
        client, _, _ = make_client()
        connect(client)
        assert client.post("/api/ibm/jobs", json=BELL).status_code == 403
        assert client.post("/api/ibm/jobs", json=BELL, headers={"Origin": "https://evil.example"}).status_code == 403

    def test_other_users_cannot_read_or_cancel_a_job(self) -> None:
        owner, _, gateway = make_client()
        connect(owner)
        self.submit(owner)

        other = TestClient(app, base_url="https://testserver")
        connect(other, key=OTHER_KEY)
        assert other.get("/api/ibm/jobs/job-1").status_code == 404
        assert other.delete("/api/ibm/jobs/job-1", headers={"Origin": ORIGIN}).status_code == 404
        assert gateway.cancelled == []

    def test_a_reconnected_session_can_follow_its_own_job(self) -> None:
        # e.g. the server restarted, the session was lost, and the user reconnected
        client, _, gateway = make_client()
        connect(client)
        self.submit(client)
        client.delete("/api/ibm/session", headers={"Origin": ORIGIN})

        connect(client)  # same IBM key, new session
        gateway.statuses["job-1"] = "done"
        job = client.get("/api/ibm/jobs/job-1").json()
        assert job["status"] == "done"
        assert job["result"]["counts"] == {"00": 48, "11": 52}
        assert job["result"]["qsphere"] is None  # circuit unknown to the new session

    def test_rejects_malformed_job_ids(self) -> None:
        client, _, _ = make_client()
        connect(client)
        assert client.get("/api/ibm/jobs/bad%20id").status_code == 422

    def test_allows_one_active_job_at_a_time(self) -> None:
        client, _, gateway = make_client()
        connect(client)
        self.submit(client)
        assert self.submit(client).status_code == 409

        gateway.statuses["job-1"] = "done"
        assert self.submit(client).status_code == 201

    def test_rate_limits_submissions_per_session(self) -> None:
        client, _, gateway = make_client(ibm_job_rate_limit_per_hour=1)
        connect(client)
        self.submit(client)
        gateway.statuses["job-1"] = "done"
        assert self.submit(client).status_code == 429

    def test_caps_shots(self) -> None:
        client, _, gateway = make_client(ibm_max_shots=200)
        connect(client)
        assert self.submit(client, shots=201).status_code == 400
        assert gateway.submitted == []

    def test_rejects_invalid_circuits(self) -> None:
        client, _, gateway = make_client()
        connect(client)
        bad = {"type": "CNOT", "control": 0, "target": 0, "position": 0}
        assert self.submit(client, gates=[bad]).status_code == 400
        assert self.submit(client, backend_name="../etc").status_code == 422
        assert gateway.submitted == []

    def test_cancel(self) -> None:
        client, _, gateway = make_client()
        connect(client)
        self.submit(client)

        assert client.delete("/api/ibm/jobs/job-1", headers={"Origin": ORIGIN}).status_code == 204
        assert gateway.cancelled == ["job-1"]
        assert self.submit(client).status_code == 201  # no longer active

    def test_require_a_session(self) -> None:
        client, _, _ = make_client()
        assert self.submit(client).status_code == 401
        assert client.get("/api/ibm/jobs/job-1").status_code == 401

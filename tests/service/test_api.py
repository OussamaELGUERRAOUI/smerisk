import gzip
import json
import re
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

import smerisk
from smerisk_service.errors import error_body
from smerisk_service.jobs import JobStatus, JobStore
from smerisk_service.main import create_app
from smerisk_service.settings import Settings
from tests.usecases.test_examples import EXAMPLES, assert_close

A = {"Authorization": "Bearer token-a"}
B = {"Authorization": "Bearer token-b"}
PERT = {"tasks": [{"id": "t1", "optimistic": 2, "most_likely": 4, "pessimistic": 9}]}


@pytest.fixture
def app() -> Any:
    return create_app(Settings(api_tokens="token-a,token-b"))  # type: ignore[arg-type]


@pytest.fixture
def client(app: Any) -> TestClient:
    return TestClient(app)


def wait_job(client: TestClient, response: Any, headers: dict[str, str] = A) -> dict[str, Any]:
    assert response.status_code == 202, response.text
    client.app.state.jobs.wait()  # type: ignore[attr-defined]
    job = client.get(response.json()["status_url"], headers=headers)
    assert job.status_code == 200
    return dict(job.json())


# --- service --------------------------------------------------------------------------------------------------


def test_health_is_open(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "library_version": smerisk.__version__}


def test_tokens_are_required(client: TestClient) -> None:
    assert client.post("/v1/pert/estimate", json=PERT).status_code == 401
    unknown = client.post("/v1/pert/estimate", json=PERT, headers={"Authorization": "Bearer nope"})
    assert unknown.status_code == 401 and unknown.json() == {"error": "unknown token"}
    anonymous = TestClient(create_app(Settings(allow_anonymous=True)))
    assert anonymous.post("/v1/pert/estimate", json=PERT).status_code == 200


def test_openapi_lists_every_route(app: Any) -> None:
    paths = app.openapi()["paths"]
    assert len([p for p in paths if p.startswith("/v1/usecases/")]) == 11
    assert (
        len(
            [
                p
                for p in paths
                if p.startswith("/v1/") and "usecases" not in p and "jobs" not in p and "datasets" not in p
            ]
        )
        == 15
    )
    assert {"/v1/jobs/{job_id}", "/v1/datasets", "/v1/datasets/fec", "/v1/datasets/{dataset_id}"} <= set(paths)
    schema = paths["/v1/usecases/cash-at-risk"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert schema["$ref"].endswith("CashAtRiskRequest")


# --- formulas -------------------------------------------------------------------------------------------------


def test_formula_answers_like_the_library(client: TestClient) -> None:
    response = client.post("/v1/pert/estimate", json=PERT, headers=A)
    assert response.status_code == 200
    expected = smerisk.pert_estimate(PERT).model_dump(mode="json")
    body = response.json()
    assert body["result"] == expected["result"] and body["assumptions"] == expected["assumptions"]
    assert body["meta"]["library_version"] == smerisk.__version__


def test_errors_follow_the_specification(client: TestClient) -> None:
    invalid = client.post("/v1/pert/estimate", json={"tasks": [{"id": "t", "optimistic": 5}]}, headers=A)
    assert invalid.status_code == 422
    assert {"path": ["tasks", 0, "most_likely"], "message": "Field required"} in invalid.json()["errors"]
    impossible = client.post(
        "/v1/operations/erlang-c", json={"arrival_rate": 100, "service_time_minutes": 3, "agents": 5}, headers=A
    )
    assert impossible.status_code == 409
    assert impossible.json()["code"] == "UNSTABLE_QUEUE" and impossible.json()["details"]["minimum_agents"] == 6
    too_large = client.post(
        "/v1/operations/erlang-c", json={"arrival_rate": 1_000_000, "service_time_minutes": 60}, headers=A
    )
    assert too_large.status_code == 413 and too_large.json()["error"] == "INPUT_TOO_LARGE"


def test_body_size_limit() -> None:
    small = TestClient(create_app(Settings(api_tokens="token-a", max_body_bytes=200)))  # type: ignore[arg-type]
    big = {"tasks": [{"id": f"t{i}", "optimistic": 1, "most_likely": 2, "pessimistic": 3} for i in range(20)]}
    response = small.post("/v1/pert/estimate", json=big, headers=A)
    assert response.status_code == 413 and response.json()["field"] == "body"


def log_events(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]


def test_internal_errors_hide_the_message(app: Any, capsys: pytest.CaptureFixture[str]) -> None:
    def boom() -> None:
        raise RuntimeError("customer 42 owes 1,234.56")

    app.add_api_route("/boom", boom)
    response = TestClient(app, raise_server_exceptions=False).get("/boom", headers={"X-Request-ID": "req-500"})
    assert response.status_code == 500 and response.json() == {"error": "INTERNAL_ERROR", "trace_id": "req-500"}
    out = capsys.readouterr().out
    assert "customer 42" not in out
    (error,) = [e for e in map(json.loads, out.splitlines()) if e["event"] == "internal_error"]
    assert error["exception"] == "RuntimeError" and error["trace_id"] == "req-500" and error["where"]
    status, body = error_body(ValueError("outside a request"))
    assert status == 500 and len(body["trace_id"]) == 32


def test_small_simulation_answers_directly_and_large_one_as_a_job(client: TestClient) -> None:
    aggregate = {
        "periods": ["2027-01", "2027-02"],
        "components": [{"id": "sales", "sign": 1, "by_period": {"2027-01": {"type": "normal", "mean": 100, "sd": 10}}}],
        "seed": 1,
    }
    assert client.post("/v1/simulation/aggregate", json=aggregate, headers=A).status_code == 200
    job = wait_job(
        client, client.post("/v1/simulation/aggregate", json={**aggregate, "n_simulations": 30_000}, headers=A)
    )
    assert job["status"] == "SUCCEEDED" and job["progress"] == 1
    assert job["result"]["assumptions"]["n_simulations"] == 30_000


def test_jobs_return_the_library_result_and_stay_private(client: TestClient) -> None:
    project = {
        "contract_price": 50_000,
        "actual_cost": 10_000,
        "remaining_tasks": [{"id": "t1", "optimistic": 10, "most_likely": 15, "pessimistic": 30}],
        "daily_cost": {"type": "fixed", "value": 500},
        "seed": 9,
    }
    response = client.post("/v1/projects/fixed-price-completion", json=project, headers=A)
    assert response.headers["location"] == response.json()["status_url"]
    job = wait_job(client, response)
    assert job["result"]["result"] == smerisk.fixed_price_completion(project).model_dump(mode="json")["result"]
    assert client.get(response.json()["status_url"], headers=B).status_code == 404
    assert client.get("/v1/jobs/unknown", headers=A).status_code == 404


def test_failed_job_reports_the_error(client: TestClient) -> None:
    tasks = [{"id": f"t{i}", "optimistic": 1, "most_likely": 2, "pessimistic": 3} for i in range(201)]
    project = {
        "contract_price": 1,
        "actual_cost": 0,
        "remaining_tasks": tasks,
        "daily_cost": {"type": "fixed", "value": 1},
    }
    job = wait_job(client, client.post("/v1/projects/fixed-price-completion", json=project, headers=A))
    assert job["status"] == "FAILED" and job["error"]["status_code"] == 413 and job["result"] is None


class Done(BaseModel):
    value: int


def test_job_quota_and_isolation() -> None:
    store = JobStore(workers=1, ttl_seconds=60, max_per_caller=1)
    release = threading.Event()

    def slow() -> Done:
        release.wait(5)
        return Done(value=1)

    first = store.submit("a", slow)
    with pytest.raises(HTTPException) as exc:
        store.submit("a", slow)
    assert exc.value.status_code == 429
    other = store.submit("b", slow)  # another caller is not blocked
    release.set()
    store.wait()
    assert store.get("a", first.id).status == JobStatus.SUCCEEDED  # type: ignore[union-attr]
    assert store.get("a", other.id) is None and store.get("b", other.id).result == {"value": 1}  # type: ignore[union-attr]
    store.shutdown()


# --- use cases ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("folder", EXAMPLES, ids=lambda p: p.name)
def test_examples_through_the_api(client: TestClient, folder: Path) -> None:
    inp = json.loads((folder / "input.json").read_text("utf-8"))
    expected = json.loads((folder / "expected.json").read_text("utf-8"))
    response = client.post(f"/v1/usecases/{folder.name.replace('_', '-')}", json=inp, headers=A)
    body = wait_job(client, response)["result"] if response.status_code == 202 else response.json()
    assert response.status_code in (200, 202), response.text
    body["meta"].pop("duration_ms")
    body["meta"].pop("library_version")
    assert_close(body, expected)


def test_usecase_validation_error(client: TestClient) -> None:
    response = client.post("/v1/usecases/counter-staffing", json={"sales": []}, headers=A)
    assert response.status_code == 422
    assert ["settings"] in [e["path"] for e in response.json()["errors"]]


# --- datasets -------------------------------------------------------------------------------------------------


def test_dataset_replaces_the_raw_data(client: TestClient) -> None:
    folder = next(f for f in EXAMPLES if f.name == "receivables_risk")
    inp = json.loads((folder / "input.json").read_text("utf-8"))
    created = client.post("/v1/datasets", json={"invoices": inp["invoices"]}, headers=A)
    assert created.status_code == 201 and created.json()["contracts"] == {"invoices": len(inp["invoices"])}
    assert created.json()["status"] == "COMPLETE"  # one request: the whole dataset
    dataset_id = created.json()["dataset_id"]
    direct = client.post("/v1/usecases/receivables-risk", json=inp, headers=A).json()
    stored = client.post(
        "/v1/usecases/receivables-risk", json={"dataset_id": dataset_id, "settings": inp["settings"]}, headers=A
    )
    assert stored.status_code == 200 and stored.json()["result"] == direct["result"]
    other = client.post("/v1/usecases/receivables-risk", json={"dataset_id": dataset_id}, headers=B)
    assert other.status_code == 404
    assert client.delete(f"/v1/datasets/{dataset_id}", headers=B).status_code == 404
    assert client.delete(f"/v1/datasets/{dataset_id}", headers=A).status_code == 204
    assert client.post("/v1/usecases/receivables-risk", json={"dataset_id": dataset_id}, headers=A).status_code == 404


def test_dataset_rejects_unknown_contracts_and_empty_pages(client: TestClient) -> None:
    assert client.post("/v1/datasets", json={"employees": []}, headers=A).status_code == 422
    opened = client.post("/v1/datasets", json={}, headers=A).json()
    assert opened["status"] == "OPEN" and opened["total_pages"] is None
    empty = client.put(f"/v1/datasets/{opened['dataset_id']}/pages/1", json={"invoices": []}, headers=A)
    assert empty.status_code == 422


def test_fec_upload(client: TestClient) -> None:
    header = "JournalCode\tEcritureNum\tEcritureDate\tCompteNum\tCompAuxLib\tDebit\tCredit"
    lines = [f"AC\t{i}\t20260315\t606100\tSupplier {i}\t{100 + 37 * i},50\t0,00" for i in range(150)]
    fec = "\n".join([header, *lines, "AC\t999\t20261399\t606100\tx\t1,00\t0,00"]).encode("cp1252")
    created = client.post(
        "/v1/datasets/fec",
        files={"fec": ("123456789FEC20261231.txt", fec, "text/plain")},
        data={
            "data": json.dumps(
                {"sales": [{"site_ref": "s", "date": "2026-10-01", "quantity": 1, "revenue_excl_tax": 1}]}
            )
        },
        headers=A,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["contracts"] == {"sales": 1, "ledger_entries": 150} and body["ignored_fec_lines"]["count"] == 1
    assert body["status"] == "COMPLETE" and body["total_pages"] == 1
    audit = client.post(
        "/v1/usecases/ledger-audit",
        json={"dataset_id": body["dataset_id"], "settings": {"as_of": "2026-10-20"}},
        headers=A,
    )
    assert audit.status_code == 200 and audit.json()["result"]["n_used"] == 150


def test_openapi_module_writes_the_contract(tmp_path: Path) -> None:
    from smerisk_service import openapi

    target = tmp_path / "openapi.json"
    openapi.main(["openapi", str(target)])
    contract = json.loads(target.read_text("utf-8"))
    assert contract["info"]["version"] == smerisk.__version__ and "/v1/usecases/payroll-at-risk" in contract["paths"]


DOCS = Path(__file__).resolve().parents[2] / "docs"
HTTP_EXAMPLES = sorted(p for p in [*DOCS.glob("formulas/*.md"), *DOCS.glob("usecases/*.md")])


@pytest.mark.parametrize("page", HTTP_EXAMPLES, ids=lambda p: p.stem)
def test_documented_http_example_is_accepted(client: TestClient, page: Path) -> None:
    text = page.read_text("utf-8").split("## HTTP", 1)[1].split("\n## ", 1)[0]
    path = re.search(r"`POST (/v1/\S+)`", text)
    body = re.search(r"```json\n(.*?)```", text, re.S)
    assert path and body, f"{page.name}: no route or JSON body in the HTTP section"
    response = client.post(path.group(1), json=json.loads(body.group(1)), headers=A)
    if response.status_code == 202:
        job = wait_job(client, response)
        assert job["status"] == "SUCCEEDED", job["error"]
    else:
        assert response.status_code == 200, response.text


def test_streamed_body_above_the_limit_is_rejected() -> None:
    small = TestClient(create_app(Settings(api_tokens="token-a", max_body_bytes=200)))  # type: ignore[arg-type]

    def chunks() -> Any:
        yield b'{"tasks": ['
        for i in range(20):
            yield f'{{"id": "t{i}", "optimistic": 1, "most_likely": 2, "pessimistic": 3}},'.encode()
        yield b'{"id": "last", "optimistic": 1, "most_likely": 2, "pessimistic": 3}]}'

    response = small.post("/v1/pert/estimate", content=chunks(), headers={**A, "Content-Type": "application/json"})
    assert response.status_code == 413 and response.json()["limit"] == 200


def test_lifespan_and_openapi_on_standard_output(capsys: pytest.CaptureFixture[str]) -> None:
    from smerisk_service import openapi

    with TestClient(create_app(Settings(allow_anonymous=True))) as client:
        assert client.post("/v1/pert/estimate", json=PERT).status_code == 200
    capsys.readouterr()  # drop the request logs
    openapi.main(["openapi"])
    assert json.loads(capsys.readouterr().out)["info"]["title"] == "smerisk-service"


def test_fec_upload_with_invalid_other_contracts(client: TestClient) -> None:
    fec = b"JournalCode\tEcritureNum\tEcritureDate\tCompteNum\tDebit\tCredit\nAC\t1\t20260315\t606100\t1,00\t0,00\n"
    response = client.post("/v1/datasets/fec", files={"fec": ("f.txt", fec)}, data={"data": "{not json"}, headers=A)
    assert response.status_code == 422


def test_streamed_body_within_the_limit_is_passed_on() -> None:
    client = TestClient(create_app(Settings(api_tokens="token-a")))  # type: ignore[arg-type]

    def chunks() -> Any:
        yield b'{"tasks": [{"id": "t", "optimistic": 1, '
        yield b'"most_likely": 2, "pessimistic": 4}]}'

    response = client.post("/v1/pert/estimate", content=chunks(), headers={**A, "Content-Type": "application/json"})
    assert response.status_code == 200


# --- pages, compression, logs ---------------------------------------------------------------------------------


def receivables_input() -> dict[str, Any]:
    folder = next(f for f in EXAMPLES if f.name == "receivables_risk")
    return dict(json.loads((folder / "input.json").read_text("utf-8")))


def test_dataset_sent_in_numbered_pages(client: TestClient) -> None:
    inp = receivables_input()
    invoices = inp["invoices"]
    created = client.post("/v1/datasets", json={"total_pages": 3, "invoices": invoices[:30]}, headers=A).json()
    dataset_id = created["dataset_id"]
    assert (created["status"], created["received_pages"], created["missing_pages"]) == ("OPEN", [1], [2, 3])
    page3 = client.put(f"/v1/datasets/{dataset_id}/pages/3", json={"invoices": invoices[60:]}, headers=A)
    assert page3.json()["missing_pages"] == [2]  # pages may arrive in any order
    use = {"dataset_id": dataset_id, "settings": inp["settings"]}
    incomplete = client.post("/v1/usecases/receivables-risk", json=use, headers=A)
    assert incomplete.status_code == 409 and incomplete.json()["code"] == "DATASET_INCOMPLETE"
    assert incomplete.json()["details"] == {"total_pages": 3, "missing_pages": [2]}
    for _ in range(2):  # a retry replaces the page: no duplicate records
        page2 = client.put(f"/v1/datasets/{dataset_id}/pages/2", json={"invoices": invoices[30:60]}, headers=A)
    assert page2.json()["status"] == "COMPLETE" and page2.json()["contracts"] == {"invoices": len(invoices)}
    paged = client.post("/v1/usecases/receivables-risk", json=use, headers=A)
    direct = client.post("/v1/usecases/receivables-risk", json=inp, headers=A)
    assert paged.status_code == 200 and paged.json()["result"] == direct.json()["result"]
    status = client.get(f"/v1/datasets/{dataset_id}", headers=A).json()
    assert status["received_pages"] == [1, 2, 3] and "invoice_ref" not in json.dumps(status)  # never the records
    assert client.get(f"/v1/datasets/{dataset_id}", headers=B).status_code == 404
    assert (
        client.put(f"/v1/datasets/{dataset_id}/pages/4", json={"invoices": invoices[:1]}, headers=A).status_code == 422
    )
    assert (
        client.put(f"/v1/datasets/{dataset_id}/pages/1", json={"invoices": invoices[:1]}, headers=B).status_code == 404
    )


def test_dataset_with_an_unknown_number_of_pages(client: TestClient) -> None:
    inp = receivables_input()
    invoices = inp["invoices"]
    dataset_id = client.post("/v1/datasets", json={}, headers=A).json()["dataset_id"]
    for number, start in [(1, 0), (2, 40)]:
        client.put(
            f"/v1/datasets/{dataset_id}/pages/{number}", json={"invoices": invoices[start : start + 40]}, headers=A
        )
    use = {"dataset_id": dataset_id, "settings": inp["settings"]}
    unknown = client.post("/v1/usecases/receivables-risk", json=use, headers=A)
    assert unknown.status_code == 409 and "complete" in unknown.json()["message"]
    missing = client.post(f"/v1/datasets/{dataset_id}/complete", json={"total_pages": 3}, headers=A)
    assert missing.status_code == 409 and missing.json()["details"]["missing_pages"] == [3]
    wrong = client.post(f"/v1/datasets/{dataset_id}/complete", json={"total_pages": 2}, headers=A)
    assert wrong.status_code == 409 and wrong.json()["code"] == "TOTAL_PAGES_MISMATCH"  # 3 was given just before
    other = client.post("/v1/datasets", json={}, headers=A).json()["dataset_id"]
    client.put(f"/v1/datasets/{other}/pages/1", json={"invoices": invoices[:40]}, headers=A)
    client.put(f"/v1/datasets/{other}/pages/2", json={"invoices": invoices[40:]}, headers=A)
    too_few = client.post(f"/v1/datasets/{other}/complete", json={"total_pages": 1}, headers=A)
    assert too_few.status_code == 422
    done = client.post(f"/v1/datasets/{other}/complete", json={"total_pages": 2}, headers=A)
    assert done.status_code == 200 and done.json()["status"] == "COMPLETE"
    assert client.post("/v1/usecases/receivables-risk", json={**use, "dataset_id": other}, headers=A).status_code == 200


def test_dataset_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app(Settings(api_tokens="token-a", max_dataset_bytes=2_000)))  # type: ignore[arg-type]
    rows = [
        {"invoice_ref": f"F{i}", "customer_ref": "C", "issue_date": "2026-01-01", "due_date": "2026-02-01", "amount": 1}
        for i in range(10)
    ]
    dataset_id = client.post("/v1/datasets", json={"total_pages": 2, "invoices": rows}, headers=A).json()["dataset_id"]
    too_big = client.put(f"/v1/datasets/{dataset_id}/pages/2", json={"invoices": rows * 3}, headers=A)
    assert too_big.status_code == 413 and too_big.json()["field"] == "dataset"
    monkeypatch.setattr("smerisk_service.datasets.MAX_RECORDS", 12)
    too_many = client.put(f"/v1/datasets/{dataset_id}/pages/2", json={"invoices": rows[:3]}, headers=A)
    assert too_many.status_code == 413 and too_many.json()["field"] == "invoices"
    replaced = client.put(f"/v1/datasets/{dataset_id}/pages/1", json={"invoices": rows[:2]}, headers=A)
    assert replaced.status_code == 200 and replaced.json()["contracts"] == {"invoices": 2}  # counts the new page only


def test_fec_sent_in_parts(client: TestClient) -> None:
    header = "JournalCode\tEcritureNum\tEcritureDate\tCompteNum\tDebit\tCredit"

    def part(start: int) -> bytes:
        lines = [f"AC\t{i}\t20260315\t606100\t{100 + 37 * i},50\t0,00" for i in range(start, start + 75)]
        return "\n".join([header, *lines]).encode()

    dataset_id = client.post("/v1/datasets", json={"total_pages": 2}, headers=A).json()["dataset_id"]
    for number, start in [(2, 75), (1, 0)]:
        response = client.put(
            f"/v1/datasets/{dataset_id}/pages/{number}/fec",
            files={"fec": (f"part{number}.txt", part(start))},
            headers=A,
        )
    assert response.status_code == 200 and response.json()["contracts"] == {"ledger_entries": 150}
    audit = client.post(
        "/v1/usecases/ledger-audit", json={"dataset_id": dataset_id, "settings": {"as_of": "2026-10-20"}}, headers=A
    )
    assert audit.json()["result"]["n_used"] == 150
    empty = client.post("/v1/datasets/fec", files={"fec": ("empty.txt", header.encode())}, headers=A)
    assert empty.status_code == 422


def test_gzip_request(client: TestClient) -> None:
    body = gzip.compress(json.dumps(PERT).encode())
    headers = {**A, "Content-Type": "application/json", "Content-Encoding": "gzip"}
    response = client.post("/v1/pert/estimate", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["result"] == client.post("/v1/pert/estimate", json=PERT, headers=A).json()["result"]
    assert client.post("/v1/pert/estimate", content=b"not gzip", headers=headers).status_code == 400
    brotli = client.post("/v1/pert/estimate", content=b"x", headers={**headers, "Content-Encoding": "br"})
    assert brotli.status_code == 415


def test_gzip_bomb_is_stopped() -> None:
    client = TestClient(create_app(Settings(api_tokens="token-a", max_decompressed_bytes=10_000)))  # type: ignore[arg-type]
    bomb = gzip.compress(b"0" * 5_000_000)  # 5 MB of zeros in a few kilobytes
    response = client.post(
        "/v1/pert/estimate", content=bomb, headers={**A, "Content-Type": "application/json", "Content-Encoding": "gzip"}
    )
    assert len(bomb) < 10_000 and response.status_code == 413
    assert response.json()["field"] == "body (decompressed)"


def test_request_and_job_logs(client: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
    secret = {"tasks": [{"id": "secret-task-name", "optimistic": 2, "most_likely": 4, "pessimistic": 9}]}
    response = client.post("/v1/pert/estimate", json=secret, headers={**A, "X-Request-ID": "abc-123"})
    assert response.headers["x-request-id"] == "abc-123"
    generated = client.post("/v1/pert/estimate", json=secret, headers=A).headers["x-request-id"]
    project = {
        "contract_price": 50_000,
        "actual_cost": 0,
        "daily_cost": {"type": "fixed", "value": 500},
        "remaining_tasks": [{"id": "t1", "optimistic": 10, "most_likely": 15, "pessimistic": 30}],
    }
    wait_job(
        client,
        client.post("/v1/projects/fixed-price-completion", json=project, headers={**A, "X-Request-ID": "job-req"}),
    )
    events = log_events(capsys)
    assert "secret-task-name" not in json.dumps(events)
    request = next(e for e in events if e["event"] == "request" and e.get("request_id") == "abc-123")
    assert request["route"] == "/v1/pert/estimate" and request["status"] == 200 and request["method"] == "POST"
    assert len(request["caller"]) == 24 and "token-a" not in json.dumps(events)
    assert any(e.get("request_id") == generated for e in events) and len(generated) == 32
    job_events = [e for e in events if e["event"].startswith("job.")]
    assert [e["event"] for e in job_events] == ["job.submitted", "job.started", "job.finished"]
    assert all(e["request_id"] == "job-req" for e in job_events)
    assert job_events[-1]["status"] == "SUCCEEDED" and job_events[-1]["label"] == "/v1/projects/fixed-price-completion"
    route_with_id = next(e for e in events if e["event"] == "request" and e["route"].startswith("/v1/jobs/"))
    assert route_with_id["route"] == "/v1/jobs/{job_id}"


def test_text_logs_and_startup_warning(capsys: pytest.CaptureFixture[str]) -> None:
    with TestClient(create_app(Settings(log_format="text"))) as client:
        assert client.post("/v1/pert/estimate", json=PERT).status_code == 401
    out = capsys.readouterr().out
    assert "service.no_token" in out
    line = next(line for line in out.splitlines() if " request " in line)
    assert "method=POST" in line and "route=/v1/pert/estimate" in line and "status=401" in line


def test_one_version_for_library_and_service(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from smerisk_service import main

    assert main.installed_version() == smerisk.__version__  # both read src/smerisk/_version.py
    monkeypatch.setattr(main, "installed_version", lambda: "0.0.1")
    with TestClient(create_app(Settings(allow_anonymous=True))):
        pass
    (warning,) = [e for e in log_events(capsys) if e["event"] == "service.version_mismatch"]
    assert (warning["service"], warning["library"]) == ("0.0.1", smerisk.__version__)


def test_version_outside_an_installation(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.metadata

    from smerisk_service import main

    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    assert main.installed_version() is None

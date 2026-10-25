# HTTP service

The `smerisk-service` package (FastAPI) serves the formulas and the use cases under `/v1`. It calls the public
functions of `smerisk` and contains no calculation.

## Running it

Two ways to use smerisk: call the library from Python (`pip install smerisk`), or run this service from a clone of
the repository and call it over HTTP from any language. The service itself is not published on PyPI.

```bash
git clone https://github.com/OussamaELGUERRAOUI/smerisk.git
cd smerisk

# With pip, in a virtual environment (Windows: .venv\Scripts\python instead of .venv/bin/python)
python -m venv .venv
.venv/bin/python -m pip install ./ ./service        # the library and the service, with their dependencies
export SMERISK_API_TOKENS=token-of-app-1,token-of-app-2
.venv/bin/python -m uvicorn smerisk_service.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log

# Or with uv (also installs the development tools)
uv sync
SMERISK_API_TOKENS=token-of-app-1 uv run uvicorn smerisk_service.main:app --port 8000 --no-access-log

# Or with Docker
docker build -f service/Dockerfile -t smerisk-service .
docker run -p 8000:8000 -e SMERISK_API_TOKENS=token-of-app-1 smerisk-service
```

On Windows PowerShell, set the tokens with `$env:SMERISK_API_TOKENS = "token-of-app-1"` before `uvicorn`. The
[README](../README.md#running-the-http-service) gives each step for Linux, macOS, and Windows, and a first call to
check the service. To update it, `git pull` then run the install command again.

For a hosted service:

- **One process** (`--workers 1`, the default): jobs and datasets live in its memory, and a second process would not
  see them. Parallel calculations come from `SMERISK_JOB_WORKERS`.
- **HTTPS**: put the service behind a reverse proxy (nginx, Caddy, Traefik, a cloud load balancer) that terminates
  TLS; tokens must not travel in clear.
- **Tokens**: give each calling application its own token, so that its jobs and datasets stay separate and its calls
  can be followed in the logs.

The interactive documentation is at `/docs` and the OpenAPI contract at `/openapi.json`; the CI also writes it as
the `openapi` artifact (`python -m smerisk_service.openapi openapi.json`).

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `SMERISK_API_TOKENS` | none | accepted bearer tokens, comma-separated: one per calling application |
| `SMERISK_ALLOW_ANONYMOUS` | `false` | accept calls without a token (local development only) |
| `SMERISK_MAX_BODY_BYTES` | 52428800 (50 MB) | larger requests, as sent (compressed or not), are rejected with 413 |
| `SMERISK_MAX_DECOMPRESSED_BYTES` | 209715200 (200 MB) | a gzip body larger once decompressed is rejected with 413 |
| `SMERISK_MAX_DATASET_BYTES` | 524288000 (500 MB) | total size of a dataset, all pages together |
| `SMERISK_RESULT_TTL_SECONDS` | 86400 | retention of job results and datasets |
| `SMERISK_JOB_WORKERS` | 2 | calculations running at the same time |
| `SMERISK_MAX_JOBS_PER_CALLER` | 10 | jobs pending or running per token; above, 429 |
| `SMERISK_MAX_DATASETS_PER_CALLER` | 20 | datasets kept per token; above, 429 |
| `SMERISK_DATASET_KEY` | generated | Fernet key that encrypts datasets in memory |
| `SMERISK_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `SMERISK_LOG_FORMAT` | `json` | `json` (one object per line, for a log collector) or `text` (development) |

Without a token and without `SMERISK_ALLOW_ANONYMOUS`, every `/v1` call answers 401. `/health` is always open.

## Routes

| Routes | Level | Answer |
| --- | --- | --- |
| `POST /v1/usecases/{name}` (11 use cases, see [the index](README.md#use-cases)) | business: raw data | 200, or 202 for `payroll-at-risk`, `cash-at-risk`, `fixed-price-risk`, `utilization-forecast` |
| `POST /v1/simulation/aggregate`, `/v1/pert/estimate`, … (15 formulas, see [the index](README.md#formula-reference)) | formulas: parameters | 200; 202 for `fixed-price-completion`, `utilization/forecast`, and `simulation/aggregate` above 20,000 draws |
| `GET /v1/jobs/{job_id}` | jobs | status and result |
| `POST /v1/datasets`, `PUT /v1/datasets/{id}/pages/{n}` (and `/fec`), `POST /v1/datasets/{id}/complete`, `GET /v1/datasets/{id}`, `POST /v1/datasets/fec`, `DELETE /v1/datasets/{id}` | datasets | 201, 200, 204 |
| `GET /health` | service | `{"status": "ok", "library_version": …}` |

Request bodies are the inputs of the Python functions, field for field; responses are the same envelope (`result`,
`assumptions`, `warnings`, `meta`, and for use cases `derived_parameters` and `data_quality`).

```bash
curl -s -X POST http://localhost:8000/v1/pert/estimate \
  -H "Authorization: Bearer token-of-app-1" -H "Content-Type: application/json" \
  -d '{"tasks": [{"id": "t", "optimistic": 1, "most_likely": 2, "pessimistic": 4}]}'
```

## Jobs (§2.3)

A route that simulates answers `202 Accepted` with `{"job_id": …, "status_url": "/v1/jobs/{job_id}"}` and the same
URL in the `Location` header. `GET` on it returns `status` (`PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`),
`progress` (0 or 1), and once finished either `result` (the full envelope) or `error` (`status_code` 422, 409, 413,
or 500, and the body the synchronous call would have returned). A job is visible only with the token that created
it. Results are kept 24 hours, in memory: a restart loses them, and the application stores what it wants to keep.

## Datasets (§12.5)

To avoid sending the same history at each call, store it once:

```bash
curl -s -X POST http://localhost:8000/v1/datasets -H "Authorization: Bearer token-of-app-1" \
  -H "Content-Type: application/json" -d '{"invoices": [ … ], "bank_balances": [ … ]}'
# {"dataset_id": "1984bf79…", "expires_at": "…", "contracts": {"invoices": 812, "bank_balances": 1}}
```

then call a use case with `{"dataset_id": "1984bf79…", "settings": { … }}`. Data sent in the body wins over the
dataset, contract by contract. A FEC file is sent as is, in a multipart request, to `POST /v1/datasets/fec` (field
`fec`, and optionally `data` with the other contracts as JSON): its labels are dropped and the lines that cannot be
read are counted in `ignored_fec_lines`.

A dataset is encrypted in memory, visible only with the token that created it, and deleted 24 hours after its last
page or with `DELETE /v1/datasets/{id}`.

## Large data

A large history is sent in **numbered pages**, then the calculation is called once on the whole dataset. The
number of pages is announced when the dataset is created:

```text
POST /v1/datasets                  {"total_pages": 3, "invoices": [records 1 to 20,000]}   → page 1, status OPEN
PUT  /v1/datasets/{id}/pages/3     {"invoices": [records 40,001 to 52,480]}                → missing_pages [2]
PUT  /v1/datasets/{id}/pages/2     {"invoices": [records 20,001 to 40,000], "bank_balances": [ … ]}
                                                                                           → status COMPLETE
POST /v1/usecases/cash-at-risk     {"dataset_id": "…", "settings": { … }}
```

- **Retries are safe**: sending a page again replaces it, so a page whose answer was lost is simply sent again.
- **Any order**: pages can be sent in parallel; records are put back together in page order, contract by contract.
- **No calculation on part of the data**: while a page is missing, a use case answers 409 `DATASET_INCOMPLETE` with
  the missing pages (`details.missing_pages`).
- **Number of pages not known in advance**: create the dataset with `{}`, send the pages, then
  `POST /v1/datasets/{id}/complete` with `{"total_pages": n}`: 409 with the missing pages if some did not arrive, and
  409 `TOTAL_PAGES_MISMATCH` if a different number was given before.
- **State**: `GET /v1/datasets/{id}` returns `status` (`OPEN` or `COMPLETE`), `total_pages`, `received_pages`,
  `missing_pages`, and the records by contract. It never returns the records themselves: what is retrieved is the
  result of the calculation.
- A FEC file can be one page (`PUT /v1/datasets/{id}/pages/{n}/fec`), or split into several pages, each with the
  header line.
- Pages of 10,000 to 50,000 records are a good size; at most 10,000 pages. Limits: 1,000,000 records per contract
  and `SMERISK_MAX_DATASET_BYTES` per dataset (413 above).
- A dataset sent in one request (records without `total_pages`) is complete at once.

A calculation always reads the whole dataset: the parameters (distributions, correlations, transitions) are
estimated on the full history. Results are not paginated.

Any request can also be **compressed** with `Content-Encoding: gzip`: JSON shrinks 10 to 20 times. The limit
`SMERISK_MAX_BODY_BYTES` applies to the compressed body, and `SMERISK_MAX_DECOMPRESSED_BYTES` to the decompressed
one; decompression stops at that limit, so a compressed bomb is refused without filling the memory. Other encodings
answer 415.

```bash
gzip -c invoices.json | curl -s -X POST http://localhost:8000/v1/datasets \
  -H "Authorization: Bearer token-of-app-1" -H "Content-Type: application/json" -H "Content-Encoding: gzip" \
  --data-binary @-
```

## Errors (§2.2)

| Status | Body |
| --- | --- |
| 401 | `{"error": "missing bearer token"}` or `{"error": "unknown token"}` |
| 404 | `{"error": "unknown or expired job"}` (or dataset); also for a job or dataset of another token |
| 409 | `{"code": "UNSTABLE_QUEUE", "message": …, "details": {…}}` |
| 413 | `{"error": "INPUT_TOO_LARGE", "field": …, "size": …, "limit": …}` |
| 422 | `{"errors": [{"path": ["tasks", 0, "most_likely"], "message": "Field required"}]}` |
| 429 | `{"error": "10 jobs pending or running; wait for one to finish"}` |
| 415 | `{"error": "unsupported Content-Encoding: br; use gzip"}` |
| 500 | `{"error": "INTERNAL_ERROR", "trace_id": …}`: the trace id is the request id, found in the logs |

## Logs

The service writes one line per event on standard output, in JSON by default:

```json
{"time": "2026-10-24T16:24:21.512+00:00", "level": "INFO", "event": "request", "request_id": "abc-123",
 "method": "POST", "route": "/v1/usecases/cash-at-risk", "status": 202, "duration_ms": 41.3,
 "request_bytes": 182344, "response_bytes": 98, "caller": "5f0c1b9e2d7a4c3b8e6f1a2d"}
```

| Event | When | Fields |
| --- | --- | --- |
| `request` | every request | `request_id`, `method`, `route` (the template, e.g. `/v1/jobs/{job_id}`), `status`, `duration_ms`, `request_bytes`, `response_bytes`, `caller` |
| `job.submitted`, `job.started`, `job.finished` | jobs | `job_id`, `label` (route), `queued`, `waited_ms`, `status`, `http_status`, `duration_ms` |
| `job.refused`, `job.expired` | quota reached, results removed | `active`, `count` |
| `dataset.created`, `dataset.page`, `dataset.completed`, `dataset.deleted`, `dataset.expired` | datasets | `dataset_id`, `page`, `replaced`, `page_records`, `received`, `total_pages`, `state`, `records` by contract |
| `internal_error` | error 500 | `trace_id`, `exception` (type only), `where` (last frames) |
| `service.started`, `service.no_token`, `service.stopped` | start and stop | version, number of tokens, workers |

Every response has an `X-Request-ID` header. A caller can send its own (`X-Request-ID`, up to 64 letters, digits,
`.`, `_`, `-`) to follow a call across its systems; jobs keep the request id of the call that created them.

The logs never contain request bodies, records, amounts, tokens, or exception messages (they could quote data):
the caller is a hash of its token, and an internal error is logged with its type and the place it happened.

## Limitations

Jobs and datasets live in the memory of one process: a restart loses them, and several processes or instances behind a
load balancer would not share them. Run one process per service, or one instance per group of callers, until a shared
store (for example Redis) is added. The service does not log request bodies.

# smerisk

Probability and statistical calculations for small and medium-sized businesses: cash-flow and payroll risk,
fixed-price projects, utilization, rate estimation with sparse data, survival, receivables, accounting anomalies,
perishable goods, service staffing, and forecast reliability.

Two ways to use it:

- **Python library** (`pip install smerisk`): pure functions with no database or network access.
- **HTTP service** (FastAPI): clone this repository and run the service to call the same functions under `/v1/...`
  from any language; see [Running the HTTP service](#running-the-http-service).

Documentation of each formula and use case is in [`docs/`](https://github.com/OussamaELGUERRAOUI/smerisk/blob/main/docs/README.md).

> **Status:** under development (`0.1.0.dev0`). The fifteen formula-level functions (§3 to §8), raw-data contracts
> (§10), parameter preparation (§11), the eleven business use cases (§12), and the HTTP service are implemented.
> See the [changelog](https://github.com/OussamaELGUERRAOUI/smerisk/blob/main/CHANGELOG.md).

## Installation

```bash
pip install smerisk
```

## Example

```python
from smerisk import risk_measures

result = risk_measures(
    samples=[float(value) for value in range(1, 101)],
    confidence=0.95,
)

print(round(result.result.quantile, 2))  # 5.95
print(round(result.result.var, 2))  # 44.55
print(result.result.expected_shortfall)  # 3.0
print(result.assumptions)
```

## Running the HTTP service

### Prerequisites

- **Python 3.11 or newer** (`python --version`) and **Git**.
- Nothing else to install by hand: `pip install ./ ./service` installs the library and the service with all their
  dependencies (FastAPI, Uvicorn, Pydantic, NumPy, SciPy, cryptography, python-multipart).

### 1. Get the code and install it

Linux and macOS:

```bash
git clone https://github.com/OussamaELGUERRAOUI/smerisk.git
cd smerisk
python3 -m venv .venv
.venv/bin/python -m pip install ./ ./service
```

Windows (PowerShell):

```powershell
git clone https://github.com/OussamaELGUERRAOUI/smerisk.git
cd smerisk
python -m venv .venv
.venv\Scripts\python -m pip install ./ ./service
```

The commands call the Python of `.venv` directly, so there is no environment to activate.

### 2. Choose a token and start the service

Each application that calls the service uses a token: a secret string that you choose. To create one:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Linux and macOS:

```bash
export SMERISK_API_TOKENS="paste-the-token-here"
.venv/bin/python -m uvicorn smerisk_service.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Windows (PowerShell):

```powershell
$env:SMERISK_API_TOKENS = "paste-the-token-here"
.venv\Scripts\python -m uvicorn smerisk_service.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Several applications: separate their tokens with commas. `127.0.0.1` keeps the service on your machine; on a server,
use `--host 0.0.0.0` behind a reverse proxy that serves HTTPS. Keep `--workers 1`. Stop the service with `Ctrl+C`.

### 3. Check that it works

- `http://127.0.0.1:8000/health` answers `{"status": "ok", ...}`.
- `http://127.0.0.1:8000/docs` shows every route; the **Authorize** button takes the token.
- A first calculation:

```bash
curl -X POST http://127.0.0.1:8000/v1/pert/estimate \
  -H "Authorization: Bearer paste-the-token-here" -H "Content-Type: application/json" \
  -d '{"tasks": [{"id": "design", "optimistic": 2, "most_likely": 4, "pessimistic": 9}]}'
```

```powershell
$body = '{"tasks": [{"id": "design", "optimistic": 2, "most_likely": 4, "pessimistic": 9}]}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/v1/pert/estimate `
  -Headers @{ Authorization = "Bearer paste-the-token-here" } -ContentType "application/json" -Body $body
```

The answer contains `"expected": 4.5` in `result.total`.

To update: `git pull`, then run the install command of step 1 again. With Docker instead of Python:
`docker build -f service/Dockerfile -t smerisk-service .` then
`docker run -p 8000:8000 -e SMERISK_API_TOKENS=paste-the-token-here smerisk-service`.

The full guide (configuration, use cases, jobs, large datasets, errors, logs) is
[docs/service.md](https://github.com/OussamaELGUERRAOUI/smerisk/blob/main/docs/service.md).

## License

Apache 2.0; see [LICENSE](https://github.com/OussamaELGUERRAOUI/smerisk/blob/main/LICENSE).

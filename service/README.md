# smerisk-service

HTTP service (FastAPI) that serves the `smerisk` library under `/v1`: the formulas, and the business use cases that
take raw data (payslips, invoices, FEC files, timesheets, sales). It contains no calculation of its own and stores
nothing durably. It is run from a clone of the repository; only the library is published on PyPI.

```bash
# From the root of the repository
pip install ./ ./service
SMERISK_API_TOKENS=token-of-app-1 uvicorn smerisk_service.main:app --host 0.0.0.0 --port 8000 --workers 1
```

The guide (configuration, routes, datasets, errors, logs) is [`docs/service.md`](../docs/service.md).

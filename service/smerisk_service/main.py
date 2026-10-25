"""Create the application and mount its routes."""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from cryptography.fernet import Fernet
from fastapi import FastAPI

import smerisk
from smerisk_service import errors, logs
from smerisk_service.datasets import DatasetStore
from smerisk_service.jobs import JobStore
from smerisk_service.limits import BodySizeLimit, GzipRequest
from smerisk_service.logs import RequestLog
from smerisk_service.routes import datasets, formulas, jobs, usecases
from smerisk_service.settings import Settings, get_settings

DESCRIPTION = """Probabilistic calculations for small and medium-sized businesses.

- **Use cases** (`/v1/usecases/…`): send raw data (payslips, invoices, FEC lines, timesheets, sales); the service
  estimates the parameters and returns the result with `derived_parameters` and `data_quality`.
- **Formulas** (`/v1/simulation`, `/v1/pert`, …): send the parameters yourself.

Calls take a bearer token. Simulations answer `202` with a job to poll at `GET /v1/jobs/{job_id}`. Results and
datasets are kept 24 hours; the service has no durable storage and never receives personal data.
"""


def installed_version() -> str | None:
    """Version of the installed service; ``None`` when it runs from the sources without being installed."""
    try:
        return importlib.metadata.version("smerisk-service")
    except importlib.metadata.PackageNotFoundError:
        return None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logs.configure(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logs.event(
            "service.started",
            version=smerisk.__version__,
            tokens=len(settings.api_tokens),
            anonymous=settings.allow_anonymous,
            job_workers=settings.job_workers,
        )
        if (service := installed_version()) not in (None, smerisk.__version__):
            logs.event(
                "service.version_mismatch",
                logging.WARNING,
                service=service,
                library=smerisk.__version__,
                hint="reinstall both from the same clone: pip install ./ ./service",
            )
        if not settings.api_tokens and not settings.allow_anonymous:
            logs.event("service.no_token", logging.WARNING, hint="set SMERISK_API_TOKENS: every /v1 call answers 401")
        yield
        app.state.jobs.shutdown()
        logs.event("service.stopped")

    app = FastAPI(title="smerisk-service", version=smerisk.__version__, description=DESCRIPTION, lifespan=lifespan)
    app.state.settings = settings
    app.state.jobs = JobStore(
        workers=settings.job_workers,
        ttl_seconds=settings.result_ttl_seconds,
        max_per_caller=settings.max_jobs_per_caller,
    )
    key = settings.dataset_key.get_secret_value().encode() if settings.dataset_key else Fernet.generate_key()
    app.state.datasets = DatasetStore(
        ttl_seconds=settings.result_ttl_seconds,
        max_per_caller=settings.max_datasets_per_caller,
        max_bytes=settings.max_dataset_bytes,
        key=key,
    )
    errors.install(app)
    # The last middleware added runs first: request log, then size limit on the body as sent, then decompression.
    app.add_middleware(GzipRequest, limit=settings.max_decompressed_bytes)
    app.add_middleware(BodySizeLimit, limit=settings.max_body_bytes)
    app.add_middleware(RequestLog)
    for module in (usecases, formulas, jobs, datasets):
        app.include_router(module.router)

    @app.get("/health", tags=["service"])
    def health() -> dict[str, str]:
        return {"status": "ok", "library_version": smerisk.__version__}

    return app


app = create_app()

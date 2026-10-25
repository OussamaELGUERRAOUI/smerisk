# Build from the repository root: docker build -f service/Dockerfile -t smerisk-service .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY service ./service
RUN pip install --no-cache-dir ./ ./service

RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8000
# The service writes its own request log (logs.py): the uvicorn access log would repeat it.
CMD ["uvicorn", "smerisk_service.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

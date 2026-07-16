FROM python:3.12-slim AS build

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir . gunicorn

FROM python:3.12-slim

RUN useradd --create-home carma
COPY --from=build /opt/venv /opt/venv
COPY migrations /app/migrations
ENV PATH="/opt/venv/bin:$PATH" \
    CARMA_MIGRATIONS_DIR=/app/migrations
USER carma
EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "carma.entrypoints.api:create_app()"]

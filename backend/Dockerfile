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
# gthread: SSE clients hold a thread each for their whole connection, so the
# sync worker would serialize everything behind one stream. 2x8 threads keeps
# a handful of streams plus normal requests responsive (see the stream
# endpoint's docstring for the honest capacity assessment). The gthread main
# loop keeps heartbeating while threads stream, so the default timeout does
# not kill long-lived SSE responses.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "8", \
     "carma.entrypoints.api:create_app()"]

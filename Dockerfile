# The server image (wiki: Stack > Deployment on AWS). No models: they run in the Nitro Enclave.
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra server --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY settings ./settings
RUN uv sync --frozen --extra server --no-dev

RUN useradd --uid 10001 --no-create-home app && mkdir -p /app/data && chown app /app/data
USER app
EXPOSE 8000
ENTRYPOINT ["backend"]
CMD ["server", "--host", "0.0.0.0", "--port", "8000"]

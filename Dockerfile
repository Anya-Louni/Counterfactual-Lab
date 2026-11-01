FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend backend
# Runtime data (networks, facilities, population cells, cached baseline) built by scripts/.
ARG DATA_URL=https://github.com/Anya-Louni/counterfactual-lab/releases/download/data-2026-10-31/runtime-data.tar.gz
ADD ${DATA_URL} /tmp/data.tar.gz
RUN mkdir -p data && tar -xzf /tmp/data.tar.gz -C data && rm /tmp/data.tar.gz
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-sync uvicorn backend.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]

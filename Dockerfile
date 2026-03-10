FROM python:3.11-slim

WORKDIR /app

# Install uv (pinned version for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

# Keep uv cache under /app so non-root user can access it
ENV UV_CACHE_DIR=/app/.cache/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies (no editable install, no dev extras)
RUN uv sync --frozen --no-dev --extra metrics

# Copy source
COPY src/ ./src/
COPY static/ ./static/

# Install the package (non-editable for production)
RUN uv pip install --no-deps .

# Run as non-root
RUN groupadd --gid 1001 veronica && \
    useradd --uid 1001 --gid 1001 --no-create-home veronica && \
    chown -R veronica:veronica /app
USER veronica

EXPOSE 8000

CMD ["uv", "run", "veronica", "serve", "--host", "0.0.0.0", "--port", "8000"]

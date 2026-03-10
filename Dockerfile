FROM python:3.11-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies (no editable install, no dev extras)
RUN uv sync --frozen --no-dev --extra metrics

# Copy source
COPY src/ ./src/
COPY static/ ./static/

# Install the package
RUN uv pip install --no-deps -e .

EXPOSE 8000

CMD ["uv", "run", "veronica", "serve", "--host", "0.0.0.0", "--port", "8000"]

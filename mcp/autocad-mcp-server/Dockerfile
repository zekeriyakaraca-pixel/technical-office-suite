FROM python:3.11-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Install deps
RUN uv sync --frozen --no-dev

# Copy source
COPY src/ ./src/

ENV PYTHONPATH=/app/src
ENV AUTOCAD_MCP_BACKEND=ezdxf
ENV AUTOCAD_MCP_IPC_DIR=/tmp/autocad-ipc

RUN useradd -m mcpuser && chown -R mcpuser /app
USER mcpuser

CMD ["uv", "run", "python", "-m", "autocad_mcp"]

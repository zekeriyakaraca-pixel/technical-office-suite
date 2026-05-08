FROM python:3.11-slim

WORKDIR /app

# System deps for PyMuPDF and ezdxf
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock* ./

# Install dependencies (no editable install — production mode)
RUN uv sync --frozen --no-dev

# Copy application source
COPY technical_office_runtime/ ./technical_office_runtime/

# Expose port
EXPOSE 7770

# Run as non-root user
RUN useradd -m toffice && chown -R toffice /app
USER toffice

CMD ["uv", "run", "uvicorn", "technical_office_runtime.app:app", \
     "--host", "0.0.0.0", "--port", "7770", \
     "--workers", "1", "--log-level", "info"]

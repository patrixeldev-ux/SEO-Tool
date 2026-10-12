# Dev image for the API, worker and beat (same image, different command).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

WORKDIR /app

# Dependencies first (cached layer). The virtualenv lives OUTSIDE /app so a bind mount of the source
# tree (Windows!) never hides or clobbers it.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project

# Chromium for the "is the content there without JavaScript?" audit check. Installed outside /app and
# /opt/venv so a bind mount of the source never hides it.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN uv sync --frozen

EXPOSE 8000
CMD ["uvicorn", "searchos.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.13-slim
WORKDIR /app
RUN useradd -m app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY . .
USER app
# Migrations puis API (en production : migrations dans une étape de déploiement séparée)
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]

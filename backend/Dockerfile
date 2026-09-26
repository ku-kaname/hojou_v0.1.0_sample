# 社内備品貸出管理システム バックエンド
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY migrations ./migrations

# 一般ユーザー権限で実行する（コンテナ内でも最小権限）
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

# 日次処理（APScheduler）が二重に動かないよう、ワーカーは1つとする
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]

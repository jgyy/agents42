FROM python:3.12-slim

WORKDIR /app

COPY app/pyproject.toml ./
COPY app/src ./src

RUN pip install --no-cache-dir -e .

COPY migrations ./migrations

EXPOSE 8000

CMD ["uvicorn", "agents42.api:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt torch --index-url https://download.pytorch.org/whl/cpu

COPY shared/ ./shared/
COPY services/pinn-core/app/ ./app/

ENV PYTHONPATH=/app
ENV SERVICE_PORT=8002

EXPOSE 8002
HEALTHCHECK CMD curl -f http://localhost:8002/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]

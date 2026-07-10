FROM python:3.10-slim

WORKDIR /app

COPY requirements-base.txt /app/requirements-base.txt
COPY shared /app/shared
COPY api_gateway/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY api_gateway /app/api_gateway

ENV PYTHONPATH=/app
EXPOSE 8000

CMD ["uvicorn", "api_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]

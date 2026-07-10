FROM python:3.10-slim

WORKDIR /app

COPY requirements-base.txt /app/requirements-base.txt
COPY shared /app/shared
COPY data_ingestion/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY data_ingestion /app/data_ingestion

ENV PYTHONPATH=/app

CMD ["python", "-m", "data_ingestion.main"]

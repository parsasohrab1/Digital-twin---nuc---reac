FROM python:3.10-slim

WORKDIR /app

COPY requirements-base.txt /app/requirements-base.txt
COPY shared /app/shared
COPY alerts/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY alerts /app/alerts

ENV PYTHONPATH=/app

CMD ["python", "-m", "alerts.main"]

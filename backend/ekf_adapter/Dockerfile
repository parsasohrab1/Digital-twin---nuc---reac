FROM python:3.10-slim

WORKDIR /app

COPY requirements-base.txt /app/requirements-base.txt
COPY shared /app/shared
COPY ekf_adapter/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY ekf_adapter /app/ekf_adapter

ENV PYTHONPATH=/app

CMD ["python", "-m", "ekf_adapter.main"]

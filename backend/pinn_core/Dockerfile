FROM python:3.10-slim

WORKDIR /app

COPY requirements-base.txt /app/requirements-base.txt
COPY shared /app/shared
COPY pinn_core/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY pinn_core /app/pinn_core

ENV PYTHONPATH=/app

CMD ["python", "-m", "pinn_core.main"]

FROM python:3.10-slim

WORKDIR /app

COPY requirements-base.txt /app/requirements-base.txt
COPY shared /app/shared
COPY dss/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY dss /app/dss

ENV PYTHONPATH=/app

CMD ["python", "-m", "dss.main"]

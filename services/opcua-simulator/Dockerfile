FROM python:3.10-slim

WORKDIR /app

RUN pip install --no-cache-dir asyncua==1.0.6 numpy==1.26.4

COPY server.py /app/server.py

EXPOSE 4840

CMD ["python", "server.py"]

.PHONY: setup up down logs build generate-data train-pinn clean status

setup:
	@if not exist .env copy .env.example .env
	@echo Setup complete. Edit .env before production deployment.

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

build:
	docker compose build

generate-data:
	pip install -r requirements-dev.txt
	python data.py

train-pinn:
	pip install -r training/requirements.txt
	python scripts/generate_parquet.py
	python training/train_pinn.py --cpu
	python scripts/validate_phase1.py

validate-phase4:
	python scripts/validate_phase4.py

clean:
	docker compose down -v

status:
	docker compose ps
	@echo.
	@echo Services:
	@echo   Dashboard:  http://localhost:5173
	@echo   API:        http://localhost:8000/docs
	@echo   Nginx:      http://localhost
	@echo   InfluxDB:   http://localhost:8086
	@echo   RabbitMQ:   http://localhost:15672

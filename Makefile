.PHONY: help install dev test lint format run docker-up docker-down clean

help:
	@echo "ClaimBridge Development Commands"
	@echo "=================================="
	@echo "make install      - Install dependencies"
	@echo "make dev          - Install development dependencies"
	@echo "make test         - Run tests with coverage"
	@echo "make lint         - Run linters (flake8, mypy)"
	@echo "make format       - Format code with black and isort"
	@echo "make run          - Run the FastAPI server"
	@echo "make docker-up    - Start Docker containers"
	@echo "make docker-down  - Stop Docker containers"
	@echo "make clean        - Clean cache and build files"

install:
	pip install -r requirements.txt

dev:
	pip install -r requirements.txt
	pip install pytest pytest-asyncio pytest-cov black flake8 mypy isort

test:
	pytest tests/ -v --cov=src/claimbridge --cov-report=html

lint:
	flake8 src/ tests/
	mypy src/

format:
	black src/ tests/
	isort src/ tests/

run:
	uvicorn claimbridge.main:app --host 0.0.0.0 --port 8000 --reload

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

docker-logs:
	docker-compose logs -f claimbridge

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache .coverage htmlcov dist build *.egg-info

.PHONY: all up down help proof test demo seed clean doctor

all: up

up:
	docker compose up -d
	@python scripts/doctor.py || true

doctor:
	python scripts/doctor.py

down:
	docker compose down -v

help:
	@echo "Quorum Makefile Commands:"
	@echo "  make up     - Starts all docker services and checks them"
	@echo "  make doctor - Checks FalkorDB, backend and MCP and prints the fix for anything missing"
	@echo "  make down   - Tears down docker services"
	@echo "  make proof  - Runs all tests, benchmarks, and generates docs/PROOF.md"
	@echo "  make test   - Runs pytest backend test suite"
	@echo "  make demo   - Starts the deterministic replay mode backend"
	@echo "  make seed   - Generates the synthetic corpus and precomputed embeddings"

proof:
	@echo "Running tests and benchmarks..."
	python -m pytest backend/tests
	python bench/run.py

test:
	python -m pytest backend/tests

demo:
	@echo "Starting Quorum backend in deterministic replay mode..."
	QUORUM_ENV=development python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000

seed:
	@echo "Seeding database..."
	python scripts/seed.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

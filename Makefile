.PHONY: help install dev admin-deps test lint format typecheck docker-build docker-run docker-stop docker-logs clean

help:
	@echo "HH Applicant Tool - Development Commands"
	@echo ""
	@echo "Setup & Installation:"
	@echo "  make install          - Install dependencies with poetry"
	@echo "  make dev              - Install dev dependencies"
	@echo "  make admin-deps       - Install admin runtime/test dependencies"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint             - Run ruff, isort, pylint checks"
	@echo "  make format           - Auto-format code (ruff, isort)"
	@echo "  make typecheck        - Run type checking (pyright)"
	@echo "  make test             - Run pytest"
	@echo "  make ci               - Run all checks (lint + typecheck + test)"
	@echo ""
	@echo "Scheduling (Auto-run):"
	@echo "  make schedule         - Install the canonical cron schedule"
	@echo "  make unschedule       - Remove the canonical cron schedule"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build     - Build Docker image"
	@echo "  make docker-run       - Start containers with docker-compose"
	@echo "  make docker-stop      - Stop containers"
	@echo "  make docker-logs      - Show container logs"
	@echo "  make docker-shell     - Open bash in container"
	@echo ""
	@echo "Utils:"
	@echo "  make clean            - Remove cache and build artifacts"
	@echo "  make setup-config     - Copy example configs"

install:
	poetry install
	$(MAKE) admin-deps

dev:
	poetry install --with dev
	$(MAKE) admin-deps
	poetry run pre-commit install

admin-deps:
	poetry run python -m pip install --no-cache-dir -r admin/requirements.txt

test:
	poetry run pytest tests/ -v

test-cov:
	poetry run pytest tests/ -v --cov=src/hh_applicant_tool --cov-report=term --cov-report=html

lint:
	@echo "🔍 Running linters..."
	poetry run ruff check src/ tests/
	poetry run isort --check-only src/ tests/
	poetry run pylint src/ tests/
	@echo "✅ All linters passed!"

format:
	@echo "📝 Formatting code..."
	poetry run ruff check --fix src/ tests/
	poetry run isort src/ tests/
	poetry run ruff format src/ tests/
	@echo "✅ Code formatted!"

lint-fix:
	@echo "🔧 Auto-fixing lint issues..."
	poetry run isort src/ tests/
	poetry run ruff check --fix src/ tests/
	@echo "✅ Auto-fixes applied!"

typecheck:
	poetry run basedpyright src/ tests/

ci: lint typecheck test
	@echo "✅ All checks passed!"

docker-build:
	docker compose build

docker-run:
	docker compose up -d
	@echo "🚀 Container started. Logs:"
	docker compose logs -f

docker-stop:
	docker compose down

docker-logs:
	docker compose logs -f hh_applicant_tool

docker-shell:
	docker compose exec hh_applicant_tool bash

docker-test:
	docker compose run --rm hh_applicant_tool poetry run pytest tests/ -v

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.pyd" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytype" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ .coverage coverage.xml

setup-config:
	@if [ ! -f .env ]; then cp .env.example .env && echo "✅ Created .env"; else echo "⚠️  .env already exists"; fi
	@if [ ! -f config/config.yaml ]; then cp config/config.example.yaml config/config.yaml && echo "✅ Created config/config.yaml"; else echo "⚠️  config/config.yaml already exists"; fi
	@echo "📝 Edit these files with your values:"
	@echo "  - .env"
	@echo "  - config/config.yaml"

# === Scheduling / Auto-run ===

schedule:
	@echo "🕐 Setting up cron for daily auto-run at 09:00..."
	@bash scripts/setup-cron.sh

unschedule:
	@echo "🗑️  Removing work-optimization cron jobs..."
	@tmp=$(mktemp); \
	crontab -l 2>/dev/null | awk ' \
		$0 == "# work-optimization autonomous HH jobs" {in_main=1; next} \
		$0 == "# work-optimization ops snapshots" {in_main=0; ops_left=2; next} \
		in_main {next} \
		ops_left > 0 {ops_left--; next} \
		{print} \
	' > "$tmp" || true; \
	crontab "$$tmp"; \
	rm -f "$$tmp"
	@echo "✅ Cron jobs removed"

.PHONY: help install dev admin-deps test lint format typecheck docker-build docker-run docker-stop docker-logs clean setup-config docker-test docker-shell ci test-cov schedule unschedule

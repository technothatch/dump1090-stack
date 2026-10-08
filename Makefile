# dump1090-stack - common operations. Run `make help` for a summary.
#
# Recipes are plain POSIX shell. Requirements: Docker Compose v2 and
# Python 3.11+ (`pip install -e ".[dev]"` provides the test tooling).

PYTHON ?= python
ENV_FILE ?= .env.example

.PHONY: help up up-recorder down logs ps config validate test lint format check clean

help:  ## Show this help
	@echo "dump1090-stack targets:"
	@echo "  up            build and start the stack"
	@echo "  up-recorder   start the stack plus the recorder profile"
	@echo "  down          stop the stack and remove the containers"
	@echo "  logs          follow the container logs"
	@echo "  ps            show container status"
	@echo "  config        validate compose.yaml and the repository files"
	@echo "  validate      run the Python validator only"
	@echo "  test          run the test suite with coverage"
	@echo "  lint          ruff check + mypy"
	@echo "  format        apply ruff format"
	@echo "  check         everything CI runs"
	@echo "  clean         remove caches and build artifacts"

up:  ## Build and start the stack
	docker compose --env-file $(ENV_FILE) up -d --build

up-recorder:  ## Start the stack plus the recorder profile
	docker compose --env-file $(ENV_FILE) --profile recorder up -d --build

down:  ## Stop the stack
	docker compose --env-file $(ENV_FILE) down

logs:  ## Follow the container logs
	docker compose --env-file $(ENV_FILE) logs -f --tail=100

ps:  ## Show container status
	docker compose --env-file $(ENV_FILE) ps

config: validate  ## Validate compose.yaml and the repository files
	docker compose --env-file $(ENV_FILE) config -q

validate:  ## Run the Python validator only
	$(PYTHON) -c "import sys; sys.path.insert(0, 'src'); from dump1090_stack.validate import main; raise SystemExit(main())"

test:  ## Run the test suite with coverage
	$(PYTHON) -m pytest -q --cov --cov-report=term-missing --cov-fail-under=80

lint:  ## ruff check + mypy
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy src

format:  ## Apply ruff format
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

check:  ## Everything CI runs
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy src
	$(PYTHON) -m pytest -q --cov --cov-report=term-missing --cov-fail-under=80

clean:  ## Remove caches and build artifacts
	rm -rf build dist .coverage .pytest_cache .ruff_cache .mypy_cache coverage.xml htmlcov
	find . -name "*.egg-info" -prune -exec rm -rf {} +
	find . -name "__pycache__" -prune -exec rm -rf {} +

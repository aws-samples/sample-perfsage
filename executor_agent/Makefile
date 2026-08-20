.PHONY: install install-dev lint format test test-cov build deploy clean local-run

# Install production dependencies
install:
	pip install -e .

# Install development dependencies
install-dev:
	pip install -e ".[dev]"

# Run linter
lint:
	ruff check src/ tests/
	mypy src/

# Auto-format code
format:
	ruff format src/ tests/
	ruff check --fix src/ tests/

# Run tests
test:
	pytest tests/ -v

# Run tests with coverage
test-cov:
	pytest tests/ -v --cov=perfsage_executor --cov-report=html --cov-report=term-missing

# Build Docker images (from repo root)
build:
	docker build -t perfsage-k6-runner -f ../docker/Dockerfile.k6 ..
	docker build -t perfsage-executor -f ../docker/Dockerfile.agent ..

# Deploy AWS infrastructure via CDK (from repo root)
deploy:
	cd ../infra && pip install -r requirements.txt && cdk deploy --all --require-approval never

# Destroy AWS infrastructure
destroy:
	cd ../infra && cdk destroy --all --force

# Run locally with Docker (from repo root)
local-run:
	docker compose -f ../docker/docker-compose.yml up -d

# Stop local services
local-stop:
	docker compose -f ../docker/docker-compose.yml down

# Clean build artifacts
clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

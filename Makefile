# ─────────────────────────────────────────────────────────────────────────────
# Graph-Based Fraud & Mule Account Detection — Makefile
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: help env data train-baselines train-gnns explain benchmark \
        serve docker-build docker-up test lint clean

PYTHON     := python
SRC        := src
CONDA_ENV  := fraud-detection
DATA_DIR   := data/raw

help:            ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ── Environment ───────────────────────────────────────────────────────────────
env:             ## Create conda environment (Windows, no RAPIDS)
	conda env create -f environment.yml
	@echo "Run: conda activate $(CONDA_ENV)"

env-rapids:      ## Create RAPIDS conda environment (WSL2/Linux only)
	conda env create -f environment-rapids.yml
	@echo "Run: conda activate fraud-detection-rapids"

# ── Data ──────────────────────────────────────────────────────────────────────
data:            ## Download PaySim dataset from Kaggle
	$(PYTHON) scripts/download_data.py

# ── Training ──────────────────────────────────────────────────────────────────
train-baselines: ## Train LogReg + XGBoost + LightGBM baselines
	$(PYTHON) -m src.training.run_baselines \
	  --data-path $(DATA_DIR)/PS_20174392719_1491204439457_log.csv \
	  --output-dir outputs/baselines \
	  --experiment-name fraud-baselines

train-gcn:       ## Train GCN model
	$(PYTHON) -m src.training.run_gnn --model gcn \
	  --experiment-name fraud-gnn

train-sage:      ## Train GraphSAGE model
	$(PYTHON) -m src.training.run_gnn --model graphsage \
	  --experiment-name fraud-gnn

train-gat:       ## Train GAT model
	$(PYTHON) -m src.training.run_gnn --model gat \
	  --experiment-name fraud-gnn

train-all:       ## Train all models sequentially
	$(MAKE) train-baselines
	$(MAKE) train-gcn
	$(MAKE) train-sage
	$(MAKE) train-gat

# ── Explainability ────────────────────────────────────────────────────────────
explain:         ## Run GNNExplainer + SHAP on best models
	$(PYTHON) -m src.explainability.run_explain \
	  --output-dir outputs/explanations

# ── GPU Benchmark ─────────────────────────────────────────────────────────────
benchmark:       ## Run NetworkX vs cuGraph speedup benchmark (WSL2/Linux)
	$(PYTHON) -m src.graph.benchmark \
	  --output outputs/benchmark_results.json

# ── API ───────────────────────────────────────────────────────────────────────
serve:           ## Start FastAPI server (dev mode)
	uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload

mlflow-ui:       ## Start MLflow tracking UI
	mlflow ui --backend-store-uri mlruns/ --port 5000

# ── Docker ────────────────────────────────────────────────────────────────────
docker-build:    ## Build Docker image
	docker build -f docker/Dockerfile -t fraud-detection-api:latest .

docker-up:       ## Start API + MLflow in Docker
	docker-compose -f docker/docker-compose.yml up -d

docker-down:     ## Stop Docker containers
	docker-compose -f docker/docker-compose.yml down

# ── Tests ─────────────────────────────────────────────────────────────────────
test:            ## Run all unit tests
	pytest tests/ -v --tb=short

test-api:        ## Run API tests only
	pytest tests/test_api.py -v

# ── Code Quality ──────────────────────────────────────────────────────────────
lint:            ## Run flake8 + black check
	flake8 $(SRC) tests --max-line-length=120
	black $(SRC) tests --check

format:          ## Auto-format with black
	black $(SRC) tests

# ── Clean ─────────────────────────────────────────────────────────────────────
clean:           ## Remove generated outputs (keep data + mlruns)
	rm -rf outputs/ __pycache__ .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

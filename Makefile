.PHONY: help test-batch test clean

help:
	@echo "Available commands:"
	@echo "  test-batch   - Runs a standard end-to-end batch test."
	@echo "  test         - Runs all automated tests with pytest."
	@echo "  clean        - Removes test results."

test-batch:
	@echo "Running end-to-end batch test..."
	python -m src.cli recommend-batch --spec-dir data/test_specs --out-dir results/test_run --goals configs/goals/compostable.json --workers 1

test:
	@echo "Running all automated tests..."
	pytest tests/

clean:
	@rm -rf results/test_run
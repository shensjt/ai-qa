UV ?= uv

.PHONY: setup format lint typecheck test check doctor test-contract test-e2e eval-rag clean

setup:
	$(UV) sync --locked

format:
	$(UV) run ruff format .

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

typecheck:
	$(UV) run mypy

test:
	$(UV) run pytest

# 无测试可收集时 pytest 返回 5；该情况下给出明确提示而不是伪装通过。
test-contract:
	@$(UV) run pytest -m contract -q; status=$$?; \
	if [ $$status -eq 5 ]; then echo "尚无 contract 测试（Phase 3 引入）"; else exit $$status; fi

test-e2e:
	@$(UV) run pytest -m e2e -q; status=$$?; \
	if [ $$status -eq 5 ]; then echo "尚无 e2e 测试（Phase 3 引入）"; else exit $$status; fi

eval-rag:
	@$(UV) run pytest -m eval -q; status=$$?; \
	if [ $$status -eq 5 ]; then echo "尚无 eval 测试（Phase 4 引入）"; else exit $$status; fi

check: lint typecheck test

doctor:
	$(UV) run ai-qa doctor --config config/settings.example.yaml

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

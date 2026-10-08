test: ./.venv-pytest
	./.venv-pytest/bin/pytest -cov -q tests

./.venv-pytest:
	python3 -m venv .venv-pytest
	./.venv-pytest/bin/pip install pytest pytest-cov

clean:
	rm -rf .venv-pytest

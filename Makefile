PYTEST = ./.venv-pytest/bin/pytest
COVERAGE = ./.venv-pytest/bin/coverage

# Run the test suite.
test: ./.venv-pytest
	$(PYTEST) -q tests

# Run the suite and report the coverage of the git-oups script itself.
# The functional tests run git-oups in a subprocess, so subprocess coverage is
# enabled with COVERAGE_PROCESS_START plus the sitecustomize hook below. The
# data file must be absolute, otherwise the subprocesses write it inside their
# temporary repositories and it is lost when they are cleaned up.
coverage: ./.venv-pytest
	PYTHONPATH=$(CURDIR)/.coverage-hook \
	COVERAGE_PROCESS_START=$(CURDIR)/.coveragerc \
	COVERAGE_FILE=$(CURDIR)/.coverage \
	$(PYTEST) -q tests
	$(COVERAGE) combine
	$(COVERAGE) report -m

./.venv-pytest:
	python3 -m venv .venv-pytest
	./.venv-pytest/bin/pip install pytest coverage

clean:
	rm -rf .venv-pytest
	rm -f .coverage .coverage.*

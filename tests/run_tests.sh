#!/bin/bash
# Run the Praetorium test suite.
#
# In the app container (integration DB available):
#   docker exec praetorium-app bash /app/tests/run_tests.sh
#
# Fast local mode (stop on first failure):
#   FAST=1 bash tests/run_tests.sh
#
# CI mode (default): run the whole suite so you get the full failure set.
set -e
cd "$(dirname "$0")/.."

PYTEST_ARGS="tests/ -v --tb=short"
if [ "${FAST:-0}" = "1" ]; then
    PYTEST_ARGS="$PYTEST_ARGS -x"
fi

python -m pytest $PYTEST_ARGS 2>&1

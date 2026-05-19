#!/bin/bash
# Run the Praetorium test suite inside the app container.
#
# Usage (from host):
#   docker exec praetorium-app bash /app/tests/run_tests.sh
#
# For integration tests (needs running DB):
#   docker exec -e INTEGRATION_TESTS=1 praetorium-app bash /app/tests/run_tests.sh
set -e
cd /app
python -m pytest tests/ -v --tb=short -x 2>&1

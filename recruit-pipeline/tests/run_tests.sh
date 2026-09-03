#!/bin/bash
# Run the recruit-pipeline daemon test suite.
#
# Locally (plain pytest, no portal app, no DB, no network):
#   bash recruit-pipeline/tests/run_tests.sh
#
# Fast mode (stop on first failure):
#   FAST=1 bash recruit-pipeline/tests/run_tests.sh
#
# These tests deliberately do NOT live in the portal's tests/ directory:
# that suite's conftest.py imports the Starlette app, pytest_asyncio and a
# database, none of which the daemon needs. The daemon suite must stay
# runnable with nothing but pytest + requests.
#
# The daemon runs on the NC droplet, not in praetorium-app, and pytest is not
# installed in the prod image -- so do NOT try to run this with
# `docker exec praetorium-app`.
set -e
cd "$(dirname "$0")/.."

PYTEST_ARGS="tests/ -v --tb=short"
if [ "${FAST:-0}" = "1" ]; then
    PYTEST_ARGS="$PYTEST_ARGS -x"
fi

python3 -m pytest $PYTEST_ARGS 2>&1

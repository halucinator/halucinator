#!/bin/bash

HALUCINATOR_ROOT=$(cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd)

export PYTHONPATH=$HALUCINATOR_ROOT/src:$HALUCINATOR_ROOT/test/pytest/helpers
export HALUCINATOR_CONTINUE_AFTER_BUG=1

exec pytest --cov=src --cov-branch --junitxml=tests/results/test_report.xml "$@" test/pytest/

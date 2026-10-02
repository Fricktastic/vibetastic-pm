#!/usr/bin/env bash
# Builder-visible verifier: compiles and produces JSON. Correctness is judged by check.py.
set -e
python3 -m py_compile scripts/tier-table.py
python3 scripts/tier-table.py | python3 -m json.tool >/dev/null

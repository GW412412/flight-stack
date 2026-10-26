#!/usr/bin/env bash
set -euo pipefail

python3 --version
python3 -m pytest
python3 -m ruff check src tests

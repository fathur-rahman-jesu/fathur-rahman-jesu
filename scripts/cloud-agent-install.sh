#!/usr/bin/env bash
# Idempotent Cloud Agent install: MySQL client/server, sysbench, Python venv.
set -euo pipefail

cd "$(dirname "$0")/.."

export DEBIAN_FRONTEND=noninteractive

sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
  mysql-server \
  mysql-client \
  sysbench \
  python3-venv \
  python3-pip

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

.venv/bin/pip install --upgrade pip
if [[ -f requirements.txt ]]; then
  .venv/bin/pip install -r requirements.txt
else
  .venv/bin/pip install 'PyMySQL>=1.1.0'
fi

echo "cloud-agent-install: done"

#!/usr/bin/env bash
# Per-boot Cloud Agent start: bring up MySQL and ensure loadtest credentials.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

# Ensure runtime dirs exist (needed outside full systemd).
sudo mkdir -p /var/run/mysqld
sudo chown mysql:mysql /var/run/mysqld 2>/dev/null || true

if ! pgrep -x mysqld >/dev/null 2>&1; then
  if command -v service >/dev/null 2>&1; then
    sudo service mysql start
  else
    sudo mysqld_safe --user=mysql &
  fi
fi

# Wait until MySQL accepts connections (up to ~30s).
for _ in $(seq 1 30); do
  if sudo mysqladmin ping --silent 2>/dev/null; then
    break
  fi
  sleep 1
done

if ! sudo mysqladmin ping --silent 2>/dev/null; then
  echo "cloud-agent-start: MySQL failed to become ready" >&2
  exit 1
fi

# Local demo credentials used by the toolkit (see README).
# Password is for local VM / Cloud Agent use only — not a production secret.
sudo mysql <<'SQL'
CREATE DATABASE IF NOT EXISTS loadtest CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS sbtest CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'loadtest'@'localhost' IDENTIFIED WITH mysql_native_password BY 'loadtest';
CREATE USER IF NOT EXISTS 'loadtest'@'127.0.0.1' IDENTIFIED WITH mysql_native_password BY 'loadtest';
CREATE USER IF NOT EXISTS 'loadtest'@'%' IDENTIFIED WITH mysql_native_password BY 'loadtest';
GRANT ALL PRIVILEGES ON *.* TO 'loadtest'@'localhost' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON *.* TO 'loadtest'@'127.0.0.1' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON *.* TO 'loadtest'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
SQL

echo "cloud-agent-start: MySQL ready (user=loadtest password=loadtest)"

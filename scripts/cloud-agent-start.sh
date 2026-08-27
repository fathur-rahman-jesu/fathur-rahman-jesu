#!/usr/bin/env bash
# Per-boot Cloud Agent start: bring up MySQL and ensure loadtest credentials.
#
# Snapshotted /var/lib/mysql often fails InnoDB init on overlay filesystems
# (OS error 22). If MySQL does not become ready, reinitialize a fresh datadir.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

MYSQL_DATADIR="${MYSQL_DATADIR:-/var/lib/mysql}"

ensure_runtime_dirs() {
  sudo mkdir -p /var/run/mysqld
  sudo chown mysql:mysql /var/run/mysqld 2>/dev/null || true
}

stop_mysql() {
  if command -v service >/dev/null 2>&1; then
    sudo service mysql stop 2>/dev/null || true
  fi
  if pgrep -x mysqld >/dev/null 2>&1; then
    sudo kill "$(pgrep -x mysqld | head -1)" 2>/dev/null || true
    sleep 1
  fi
  # Clear stale PID/socket so a restart is clean.
  sudo rm -f /var/run/mysqld/mysqld.pid /var/run/mysqld/mysqld.sock 2>/dev/null || true
}

start_mysql() {
  ensure_runtime_dirs
  if pgrep -x mysqld >/dev/null 2>&1; then
    return 0
  fi
  if command -v service >/dev/null 2>&1; then
    # Return non-zero when the service script fails (e.g. broken snapshotted datadir).
    sudo service mysql start
  else
    sudo mysqld_safe --user=mysql &
    sleep 2
  fi
}

wait_ready() {
  local seconds="${1:-30}"
  local i
  for i in $(seq 1 "$seconds"); do
    if sudo mysqladmin ping --silent 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

reinitialize_datadir() {
  echo "cloud-agent-start: reinitializing MySQL datadir at ${MYSQL_DATADIR}"
  stop_mysql
  sudo rm -rf "${MYSQL_DATADIR}"
  sudo mkdir -p "${MYSQL_DATADIR}"
  sudo chown mysql:mysql "${MYSQL_DATADIR}"
  sudo mysqld --initialize-insecure --user=mysql --datadir="${MYSQL_DATADIR}"
}

ensure_runtime_dirs

if ! start_mysql || ! wait_ready 15; then
  echo "cloud-agent-start: MySQL did not become ready; attempting datadir reinit"
  reinitialize_datadir
  if ! start_mysql || ! wait_ready 30; then
    echo "cloud-agent-start: MySQL failed to become ready after reinit" >&2
    sudo tail -n 50 /var/log/mysql/error.log 2>/dev/null || true
    exit 1
  fi
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

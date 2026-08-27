#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# Sysbench MySQL load test wrapper
# ----------------------------------------------------------------------------
# Wrapper untuk menjalankan benchmark MySQL standar industri menggunakan
# sysbench (https://github.com/akopytov/sysbench).
#
# Install sysbench:
#   Ubuntu/Debian : sudo apt-get install -y sysbench
#   RHEL/CentOS   : sudo yum install -y sysbench
#   macOS         : brew install sysbench
#
# Konfigurasi via environment variables atau argument:
#   MYSQL_HOST     (default: 127.0.0.1)
#   MYSQL_PORT     (default: 3306)
#   MYSQL_USER     (default: root)
#   MYSQL_PASSWORD (default: kosong)
#   MYSQL_DB       (default: sbtest)
#   TABLES         (default: 4)        - jumlah tabel
#   TABLE_SIZE     (default: 100000)   - jumlah baris per tabel
#   THREADS        (default: 16)       - jumlah concurrent thread
#   DURATION       (default: 60)       - durasi test (detik)
#   REPORT_INTERVAL (default: 5)       - interval laporan (detik)
#
# Usage:
#   ./sysbench_mysql.sh prepare
#   ./sysbench_mysql.sh run            # oltp_read_write (default)
#   ./sysbench_mysql.sh run oltp_read_only
#   ./sysbench_mysql.sh run oltp_write_only
#   ./sysbench_mysql.sh run oltp_point_select
#   ./sysbench_mysql.sh run oltp_update_index
#   ./sysbench_mysql.sh cleanup
# ----------------------------------------------------------------------------
set -euo pipefail

MYSQL_HOST=${MYSQL_HOST:-127.0.0.1}
MYSQL_PORT=${MYSQL_PORT:-3306}
MYSQL_USER=${MYSQL_USER:-root}
MYSQL_PASSWORD=${MYSQL_PASSWORD:-}
MYSQL_DB=${MYSQL_DB:-sbtest}

TABLES=${TABLES:-4}
TABLE_SIZE=${TABLE_SIZE:-100000}
THREADS=${THREADS:-16}
DURATION=${DURATION:-60}
REPORT_INTERVAL=${REPORT_INTERVAL:-5}

ACTION=${1:-help}
TEST=${2:-oltp_read_write}

if ! command -v sysbench >/dev/null 2>&1; then
    echo "ERROR: sysbench tidak ditemukan. Install terlebih dahulu." >&2
    echo "  Debian/Ubuntu: sudo apt-get install -y sysbench" >&2
    exit 1
fi

COMMON_ARGS=(
    --db-driver=mysql
    --mysql-host="$MYSQL_HOST"
    --mysql-port="$MYSQL_PORT"
    --mysql-user="$MYSQL_USER"
    --mysql-password="$MYSQL_PASSWORD"
    --mysql-db="$MYSQL_DB"
    --tables="$TABLES"
    --table-size="$TABLE_SIZE"
    --threads="$THREADS"
)

ensure_db() {
    if command -v mysql >/dev/null 2>&1; then
        MYSQL_PWD="$MYSQL_PASSWORD" mysql \
            -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" \
            -e "CREATE DATABASE IF NOT EXISTS \`$MYSQL_DB\`;" 2>/dev/null || true
    fi
}

case "$ACTION" in
    prepare)
        ensure_db
        echo ">>> Prepare: $TABLES tabel x $TABLE_SIZE baris"
        sysbench oltp_common "${COMMON_ARGS[@]}" prepare
        ;;
    run)
        echo ">>> Run: $TEST, threads=$THREADS, duration=${DURATION}s"
        sysbench "$TEST" "${COMMON_ARGS[@]}" \
            --time="$DURATION" \
            --report-interval="$REPORT_INTERVAL" \
            run
        ;;
    cleanup)
        echo ">>> Cleanup tabel sbtest"
        sysbench oltp_common "${COMMON_ARGS[@]}" cleanup
        ;;
    help|*)
        sed -n '2,30p' "$0"
        ;;
esac

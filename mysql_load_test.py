#!/usr/bin/env python3
"""
MySQL Performance / Load Test Script
====================================

Script untuk melakukan performance test dan load test pada database MySQL.
Mendukung beberapa mode test:
  - precheck : validasi koneksi, privilege, konfigurasi server, dan kapasitas
  - prepare  : membuat schema + dataset awal
  - read     : workload SELECT (point query)
  - write    : workload INSERT
  - update   : workload UPDATE
  - mixed    : workload campuran OLTP-like (60% point select, 10% range select,
               20% update, 10% insert)
  - cleanup  : hapus tabel test

Statistik yang dihasilkan:
  - total queries, QPS (queries per second)
  - latency: min, avg, p50, p95, p99, max
  - jumlah error
  - durasi total

Usage:
  python3 mysql_load_test.py precheck --threads 16
  python3 mysql_load_test.py prepare --rows 100000
  python3 mysql_load_test.py mixed --threads 16 --duration 60
  python3 mysql_load_test.py read   --threads 32 --duration 30
  python3 mysql_load_test.py cleanup

Koneksi DB dapat diatur lewat argument atau environment variables:
  MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import string
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List

try:
    import pymysql
    from pymysql.cursors import Cursor
except ImportError:
    sys.stderr.write(
        "ERROR: modul 'pymysql' belum terinstall.\n"
        "Install dengan: pip install pymysql\n"
    )
    sys.exit(1)


TABLE_NAME = "loadtest_users"


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------
@dataclass
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "DBConfig":
        return cls(
            host=args.host or os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(args.port or os.getenv("MYSQL_PORT", "3306")),
            user=args.user or os.getenv("MYSQL_USER", "root"),
            password=args.password or os.getenv("MYSQL_PASSWORD", ""),
            database=args.database or os.getenv("MYSQL_DB", "loadtest"),
        )


def connect(cfg: DBConfig, autocommit: bool = True) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        database=cfg.database,
        autocommit=autocommit,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )


def ensure_database(cfg: DBConfig) -> None:
    """Buat database jika belum ada."""
    conn = pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        autocommit=True,
        charset="utf8mb4",
        connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{cfg.database}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pre-flight validation
# ---------------------------------------------------------------------------
REQUIRED_PRIVS = {"SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "INDEX"}


def _fmt_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.2f} {u}"
        f /= 1024
    return f"{f:.2f} TB"


def _ok(label: str, msg: str = "") -> None:
    print(f"  [ OK ]  {label}" + (f"  -> {msg}" if msg else ""))


def _warn(label: str, msg: str) -> None:
    print(f"  [WARN]  {label}  -> {msg}")


def _fail(label: str, msg: str) -> None:
    print(f"  [FAIL]  {label}  -> {msg}")


def precheck(cfg: DBConfig, planned_threads: int) -> int:
    """
    Validasi pre-flight sebelum load test.
    Return 0 jika semua OK / hanya warning, 1 jika ada FAIL.
    """
    print("=" * 60)
    print("  MySQL Pre-flight Check")
    print("=" * 60)
    print(f"  target  : {cfg.user}@{cfg.host}:{cfg.port}/{cfg.database}")
    print(f"  threads : {planned_threads} (rencana)")
    print("-" * 60)

    fails = 0
    warns = 0

    # 1. Koneksi + RTT
    try:
        t0 = time.perf_counter()
        conn = pymysql.connect(
            host=cfg.host, port=cfg.port, user=cfg.user,
            password=cfg.password, autocommit=True,
            connect_timeout=10, charset="utf8mb4",
        )
        rtt_ms = (time.perf_counter() - t0) * 1000
        _ok("Connect ke server", f"RTT handshake {rtt_ms:.1f} ms")
        if rtt_ms > 50:
            _warn("Latency client<->server tinggi",
                  f"{rtt_ms:.1f} ms — hasil benchmark bisa didominasi network")
            warns += 1
    except Exception as e:
        _fail("Connect ke server", str(e))
        print("=" * 60)
        return 1

    try:
        with conn.cursor() as cur:
            # 2. Versi + engine
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
            _ok("MySQL version", version)

            cur.execute("SHOW VARIABLES LIKE 'default_storage_engine'")
            engine = cur.fetchone()[1]
            if engine.lower() == "innodb":
                _ok("default_storage_engine", engine)
            else:
                _warn("default_storage_engine", f"{engine} (umumnya pakai InnoDB)")
                warns += 1

            # 3. Konfigurasi yang relevan
            interesting = [
                "max_connections",
                "innodb_buffer_pool_size",
                "innodb_log_file_size",
                "innodb_redo_log_capacity",
                "innodb_flush_log_at_trx_commit",
                "innodb_flush_method",
                "innodb_io_capacity",
                "innodb_io_capacity_max",
                "sync_binlog",
                "log_bin",
                "slow_query_log",
                "general_log",
                "thread_cache_size",
                "table_open_cache",
            ]
            vars_map = {}
            for v in interesting:
                cur.execute(f"SHOW VARIABLES LIKE '{v}'")
                row = cur.fetchone()
                if row:
                    vars_map[v] = row[1]

            print("-" * 60)
            print("  Server variables:")
            for k, v in vars_map.items():
                pretty = v
                if k in ("innodb_buffer_pool_size", "innodb_log_file_size",
                         "innodb_redo_log_capacity") and v.isdigit():
                    pretty = f"{v}  ({_fmt_bytes(int(v))})"
                print(f"    {k:<35s} = {pretty}")
            print("-" * 60)

            # 4. max_connections vs planned threads
            max_conn = int(vars_map.get("max_connections", "0") or 0)
            cur.execute("SHOW STATUS LIKE 'Threads_connected'")
            used = int(cur.fetchone()[1])
            free = max_conn - used
            label = f"max_connections={max_conn}, used={used}, free={free}"
            need = planned_threads + 2  # +2 buffer (connect helper, etc.)
            if max_conn == 0:
                _warn("max_connections", "tidak terbaca")
                warns += 1
            elif free < need:
                _fail("Kapasitas koneksi", f"{label} — butuh ~{need}")
                fails += 1
            else:
                _ok("Kapasitas koneksi", label)

            # 5. Warning konfigurasi yang sering bikin write lambat
            if vars_map.get("innodb_flush_log_at_trx_commit") == "1":
                _warn("innodb_flush_log_at_trx_commit=1",
                      "mode ACID penuh — write akan terlihat lebih lambat (normal)")
                warns += 1
            if vars_map.get("sync_binlog") == "1" and vars_map.get("log_bin", "OFF").upper() != "OFF":
                _warn("sync_binlog=1 + binlog ON",
                      "fsync binlog setiap commit — write throughput lebih rendah")
                warns += 1
            if vars_map.get("general_log", "OFF").upper() == "ON":
                _warn("general_log=ON",
                      "mencatat semua query — matikan saat benchmark")
                warns += 1
            if vars_map.get("slow_query_log", "OFF").upper() == "ON":
                _warn("slow_query_log=ON",
                      "boleh saja, tapi sedikit menambah overhead")
                warns += 1

            # 6. Privilege
            cur.execute("SHOW GRANTS FOR CURRENT_USER()")
            grants = " ".join(row[0].upper() for row in cur.fetchall())
            if "ALL PRIVILEGES" in grants:
                _ok("Privilege user", "ALL PRIVILEGES")
            else:
                missing = [p for p in REQUIRED_PRIVS if p not in grants]
                if missing:
                    _fail("Privilege user", f"kurang: {', '.join(sorted(missing))}")
                    fails += 1
                else:
                    _ok("Privilege user",
                        f"punya {', '.join(sorted(REQUIRED_PRIVS))}")

            # 7. Database target
            try:
                cur.execute(
                    "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                    "WHERE SCHEMA_NAME = %s", (cfg.database,))
                if cur.fetchone():
                    _ok("Database target ada", cfg.database)
                else:
                    _warn("Database target belum ada",
                          f"`{cfg.database}` akan dibuat saat prepare")
                    warns += 1
            except Exception as e:
                _warn("Cek database target", str(e))
                warns += 1

            # 8. Tabel test
            try:
                cur.execute(
                    "SELECT TABLE_ROWS FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                    (cfg.database, TABLE_NAME))
                row = cur.fetchone()
                if row is None:
                    _ok("Tabel test", f"`{TABLE_NAME}` belum ada (akan dibuat oleh prepare)")
                else:
                    _warn(f"Tabel `{TABLE_NAME}` sudah ada",
                          f"~{row[0]} baris — akan di-DROP oleh `prepare`")
                    warns += 1
            except Exception as e:
                _warn("Cek tabel test", str(e))
                warns += 1

            # 9. Round-trip query latency (SELECT 1)
            samples = []
            for _ in range(20):
                t0 = time.perf_counter()
                cur.execute("SELECT 1")
                cur.fetchall()
                samples.append((time.perf_counter() - t0) * 1000)
            samples.sort()
            _ok("Round-trip SELECT 1",
                f"min={samples[0]:.2f}ms  "
                f"p50={percentile(samples, 0.50):.2f}ms  "
                f"p95={percentile(samples, 0.95):.2f}ms  "
                f"max={samples[-1]:.2f}ms")

    finally:
        conn.close()

    print("-" * 60)
    if fails:
        print(f"  RESULT: {fails} FAIL, {warns} WARN — perbaiki dulu sebelum load test.")
    elif warns:
        print(f"  RESULT: OK dengan {warns} warning(s) — boleh lanjut, perhatikan catatan di atas.")
    else:
        print("  RESULT: semua check OK. Siap load test.")
    print("=" * 60)
    return 1 if fails else 0


# ---------------------------------------------------------------------------
# Schema + data preparation
# ---------------------------------------------------------------------------
def random_string(n: int) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def prepare(cfg: DBConfig, rows: int, batch_size: int = 1000) -> None:
    ensure_database(cfg)
    conn = connect(cfg, autocommit=False)
    try:
        with conn.cursor() as cur:
            print(f"[prepare] Membuat tabel `{TABLE_NAME}` ...")
            cur.execute(f"DROP TABLE IF EXISTS `{TABLE_NAME}`")
            cur.execute(
                f"""
                CREATE TABLE `{TABLE_NAME}` (
                    id            BIGINT       NOT NULL AUTO_INCREMENT,
                    username      VARCHAR(64)  NOT NULL,
                    email         VARCHAR(128) NOT NULL,
                    score         INT          NOT NULL DEFAULT 0,
                    payload       VARCHAR(255) NOT NULL,
                    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                                ON UPDATE CURRENT_TIMESTAMP,
                    PRIMARY KEY (id),
                    KEY idx_username (username),
                    KEY idx_score    (score)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            conn.commit()

            print(f"[prepare] Insert {rows:,} baris (batch {batch_size}) ...")
            sql = (
                f"INSERT INTO `{TABLE_NAME}` "
                f"(username, email, score, payload) VALUES (%s, %s, %s, %s)"
            )
            inserted = 0
            t0 = time.perf_counter()
            while inserted < rows:
                this_batch = min(batch_size, rows - inserted)
                data = [
                    (
                        f"user_{inserted + i}_{random_string(6)}",
                        f"user_{inserted + i}@example.com",
                        random.randint(0, 10_000),
                        random_string(120),
                    )
                    for i in range(this_batch)
                ]
                cur.executemany(sql, data)
                conn.commit()
                inserted += this_batch
                if inserted % (batch_size * 10) == 0 or inserted == rows:
                    elapsed = time.perf_counter() - t0
                    rate = inserted / elapsed if elapsed > 0 else 0
                    print(
                        f"  {inserted:,}/{rows:,} baris "
                        f"({rate:,.0f} rows/sec)"
                    )
        print("[prepare] Selesai.")
    finally:
        conn.close()


def cleanup(cfg: DBConfig) -> None:
    conn = connect(cfg)
    try:
        with conn.cursor() as cur:
            print(f"[cleanup] DROP TABLE IF EXISTS `{TABLE_NAME}`")
            cur.execute(f"DROP TABLE IF EXISTS `{TABLE_NAME}`")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Workload operations
# ---------------------------------------------------------------------------
def op_select(cur: Cursor, max_id: int) -> None:
    rid = random.randint(1, max_id)
    cur.execute(
        f"SELECT id, username, email, score FROM `{TABLE_NAME}` WHERE id = %s",
        (rid,),
    )
    cur.fetchall()


def op_range_select(cur: Cursor, max_id: int) -> None:
    start = random.randint(1, max(1, max_id - 100))
    cur.execute(
        f"SELECT id, score FROM `{TABLE_NAME}` "
        f"WHERE id BETWEEN %s AND %s",
        (start, start + 100),
    )
    cur.fetchall()


def op_update(cur: Cursor, max_id: int) -> None:
    rid = random.randint(1, max_id)
    cur.execute(
        f"UPDATE `{TABLE_NAME}` SET score = score + 1 WHERE id = %s",
        (rid,),
    )


def op_insert(cur: Cursor, _max_id: int) -> None:
    cur.execute(
        f"INSERT INTO `{TABLE_NAME}` (username, email, score, payload) "
        f"VALUES (%s, %s, %s, %s)",
        (
            f"u_{random_string(10)}",
            f"{random_string(8)}@example.com",
            random.randint(0, 10_000),
            random_string(120),
        ),
    )


# ---------------------------------------------------------------------------
# Stats collection
# ---------------------------------------------------------------------------
@dataclass
class ThreadStats:
    latencies_ms: List[float] = field(default_factory=list)
    errors: int = 0
    queries: int = 0


def percentile(sorted_data: List[float], pct: float) -> float:
    if not sorted_data:
        return 0.0
    k = (len(sorted_data) - 1) * pct
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    if f == c:
        return sorted_data[f]
    return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------
def worker_loop(
    cfg: DBConfig,
    pick_op: Callable[[], Callable[[Cursor, int], None]],
    max_id: int,
    stop_at: float,
    stats: ThreadStats,
) -> None:
    try:
        conn = connect(cfg)
    except Exception as e:
        stats.errors += 1
        sys.stderr.write(f"[worker] connect error: {e}\n")
        return

    try:
        with conn.cursor() as cur:
            while time.perf_counter() < stop_at:
                op = pick_op()
                t0 = time.perf_counter()
                try:
                    op(cur, max_id)
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    stats.latencies_ms.append(elapsed_ms)
                    stats.queries += 1
                except Exception:
                    stats.errors += 1
    finally:
        try:
            conn.close()
        except Exception:
            pass


def build_op_picker(workload: str):
    """Return a callable yang menghasilkan satu operasi per panggilan."""
    if workload == "read":
        ops = [op_select]
        weights = [1.0]
    elif workload == "write":
        ops = [op_insert]
        weights = [1.0]
    elif workload == "update":
        ops = [op_update]
        weights = [1.0]
    elif workload == "mixed":
        # OLTP-like default: 60% point select, 10% range select, 20% update, 10% insert
        ops = [op_select, op_range_select, op_update, op_insert]
        weights = [0.60, 0.10, 0.20, 0.10]
    else:
        raise ValueError(f"workload tidak dikenal: {workload}")

    def pick() -> Callable[[Cursor, int], None]:
        return random.choices(ops, weights=weights, k=1)[0]

    return pick


def get_max_id(cfg: DBConfig) -> int:
    conn = connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM `{TABLE_NAME}`")
            row = cur.fetchone()
            return int(row[0]) if row else 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------
def run_test(
    cfg: DBConfig,
    workload: str,
    threads: int,
    duration: int,
    warmup: int,
) -> None:
    max_id = get_max_id(cfg)
    if max_id == 0:
        sys.stderr.write(
            f"ERROR: tabel `{TABLE_NAME}` kosong atau belum dibuat.\n"
            f"Jalankan dulu: python3 {sys.argv[0]} prepare --rows 100000\n"
        )
        sys.exit(2)

    pick_op = build_op_picker(workload)

    print("=" * 60)
    print(f"  MySQL Load Test")
    print("=" * 60)
    print(f"  target    : {cfg.user}@{cfg.host}:{cfg.port}/{cfg.database}")
    print(f"  workload  : {workload}")
    print(f"  threads   : {threads}")
    print(f"  duration  : {duration}s (warmup {warmup}s)")
    print(f"  table rows: ~{max_id:,}")
    print("=" * 60)

    if warmup > 0:
        print(f"[warmup] running {warmup}s ...")
        warm_stop = time.perf_counter() + warmup
        warm_stats = [ThreadStats() for _ in range(threads)]
        warm_threads = [
            threading.Thread(
                target=worker_loop,
                args=(cfg, pick_op, max_id, warm_stop, warm_stats[i]),
                daemon=True,
            )
            for i in range(threads)
        ]
        for t in warm_threads:
            t.start()
        for t in warm_threads:
            t.join()
        print("[warmup] done.")

    print(f"[run] starting {threads} threads for {duration}s ...")
    stats = [ThreadStats() for _ in range(threads)]
    stop_at = time.perf_counter() + duration
    t_start = time.perf_counter()
    workers = [
        threading.Thread(
            target=worker_loop,
            args=(cfg, pick_op, max_id, stop_at, stats[i]),
            daemon=True,
        )
        for i in range(threads)
    ]
    for t in workers:
        t.start()

    # Live progress
    last_print = t_start
    last_queries = 0
    while time.perf_counter() < stop_at:
        time.sleep(1.0)
        now = time.perf_counter()
        total_q = sum(s.queries for s in stats)
        interval = now - last_print
        qps_now = (total_q - last_queries) / interval if interval > 0 else 0
        elapsed = now - t_start
        print(
            f"  t={elapsed:5.1f}s  queries={total_q:>10,}  "
            f"qps={qps_now:>9,.0f}",
            flush=True,
        )
        last_print = now
        last_queries = total_q

    for t in workers:
        t.join()
    total_elapsed = time.perf_counter() - t_start

    # Aggregate
    all_latencies: List[float] = []
    total_queries = 0
    total_errors = 0
    for s in stats:
        all_latencies.extend(s.latencies_ms)
        total_queries += s.queries
        total_errors += s.errors

    print("=" * 60)
    print("  RESULT")
    print("=" * 60)
    print(f"  duration       : {total_elapsed:.2f} s")
    print(f"  total queries  : {total_queries:,}")
    print(f"  total errors   : {total_errors:,}")
    qps = total_queries / total_elapsed if total_elapsed > 0 else 0
    print(f"  throughput     : {qps:,.2f} queries/sec")
    if all_latencies:
        all_latencies.sort()
        avg = statistics.fmean(all_latencies)
        print(f"  latency (ms)   : min={all_latencies[0]:.2f}  "
              f"avg={avg:.2f}  "
              f"p50={percentile(all_latencies, 0.50):.2f}  "
              f"p95={percentile(all_latencies, 0.95):.2f}  "
              f"p99={percentile(all_latencies, 0.99):.2f}  "
              f"max={all_latencies[-1]:.2f}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def add_conn_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host",     default=None, help="MySQL host (env MYSQL_HOST)")
    p.add_argument("--port",     default=None, type=int, help="MySQL port (env MYSQL_PORT)")
    p.add_argument("--user",     default=None, help="MySQL user (env MYSQL_USER)")
    p.add_argument("--password", default=None, help="MySQL password (env MYSQL_PASSWORD)")
    p.add_argument("--database", default=None, help="MySQL database (env MYSQL_DB)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MySQL performance / load test tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("precheck", help="Validasi pre-flight (koneksi, privilege, konfigurasi)")
    add_conn_args(p_check)
    p_check.add_argument("--threads", type=int, default=8,
                         help="Jumlah thread yang akan dipakai (untuk cek max_connections)")

    p_prep = sub.add_parser("prepare", help="Buat schema + seed data")
    add_conn_args(p_prep)
    p_prep.add_argument("--rows", type=int, default=100_000, help="Jumlah baris awal (default 100000)")
    p_prep.add_argument("--batch-size", type=int, default=1000)

    p_clean = sub.add_parser("cleanup", help="Hapus tabel test")
    add_conn_args(p_clean)

    for name, helptxt in [
        ("read",   "Workload SELECT (point query)"),
        ("write",  "Workload INSERT"),
        ("update", "Workload UPDATE"),
        ("mixed",  "Workload campuran OLTP-like"),
    ]:
        sp = sub.add_parser(name, help=helptxt)
        add_conn_args(sp)
        sp.add_argument("--threads",  type=int, default=8,  help="Jumlah concurrent threads")
        sp.add_argument("--duration", type=int, default=30, help="Durasi test dalam detik")
        sp.add_argument("--warmup",   type=int, default=3,  help="Durasi warmup dalam detik")

    args = parser.parse_args()
    cfg = DBConfig.from_args(args)

    try:
        if args.command == "precheck":
            sys.exit(precheck(cfg, planned_threads=args.threads))
        elif args.command == "prepare":
            prepare(cfg, rows=args.rows, batch_size=args.batch_size)
        elif args.command == "cleanup":
            cleanup(cfg)
        elif args.command in ("read", "write", "update", "mixed"):
            run_test(
                cfg=cfg,
                workload=args.command,
                threads=args.threads,
                duration=args.duration,
                warmup=args.warmup,
            )
        else:
            parser.print_help()
            sys.exit(1)
    except KeyboardInterrupt:
        sys.stderr.write("\n[interrupt] dihentikan oleh user.\n")
        sys.exit(130)
    except pymysql.err.OperationalError as e:
        sys.stderr.write(f"\nERROR koneksi MySQL: {e}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()

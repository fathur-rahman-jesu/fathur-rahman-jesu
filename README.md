# MySQL Performance / Load Test Toolkit

Toolkit sederhana untuk melakukan **performance test** dan **load test** pada
database MySQL. Repo ini berisi dua tool yang saling melengkapi:

| File                  | Bahasa | Kegunaan                                                                 |
| --------------------- | ------ | ------------------------------------------------------------------------ |
| `mysql_load_test.py`  | Python | Tool custom, mudah dimodifikasi, mengukur QPS + latency (p50/p95/p99).   |
| `sysbench_mysql.sh`   | Bash   | Wrapper untuk `sysbench` — benchmark standar industri (OLTP).            |

---

## 1. Persiapan

### Instal dependency Python

```bash
pip install -r requirements.txt
```

### (Opsional) Instal sysbench

```bash
# Ubuntu / Debian
sudo apt-get update && sudo apt-get install -y sysbench

# RHEL / CentOS / Fedora
sudo yum install -y sysbench

# macOS
brew install sysbench
```

### Konfigurasi koneksi

Bisa via argument CLI **atau** environment variables:

```bash
export MYSQL_HOST=127.0.0.1
export MYSQL_PORT=3306
export MYSQL_USER=root
export MYSQL_PASSWORD=secret
export MYSQL_DB=loadtest
```

---

## 2. Menjalankan `mysql_load_test.py`

### Langkah-langkah

```bash
# 1. Validasi koneksi, privilege, dan konfigurasi server
python3 mysql_load_test.py precheck --threads 16

# 2. Siapkan schema + seed data (100 ribu baris)
python3 mysql_load_test.py prepare --rows 100000

# 3. Jalankan workload yang diinginkan
python3 mysql_load_test.py read   --threads 32 --duration 60   # 100% SELECT
python3 mysql_load_test.py write  --threads 16 --duration 60   # 100% INSERT
python3 mysql_load_test.py update --threads 16 --duration 60   # 100% UPDATE
python3 mysql_load_test.py mixed  --threads 16 --duration 60   # OLTP campuran

# 4. Bersihkan
python3 mysql_load_test.py cleanup
```

### Apa yang dicek oleh `precheck`

- Koneksi ke server + RTT handshake.
- Versi MySQL dan `default_storage_engine`.
- Variabel server penting: `max_connections`, `innodb_buffer_pool_size`,
  `innodb_flush_log_at_trx_commit`, `sync_binlog`, `log_bin`, `slow_query_log`,
  `general_log`, dll.
- Privilege user (`SELECT`, `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`, `INDEX`).
- Kapasitas koneksi: `max_connections − used` vs `--threads`.
- Apakah database & tabel test sudah ada (warning kalau akan ter-overwrite).
- Round-trip latency `SELECT 1` (min / p50 / p95 / max).

Exit code `0` jika semua OK / warning, `1` jika ada FAIL.

### Contoh output

```
============================================================
  MySQL Load Test
============================================================
  target    : root@127.0.0.1:3306/loadtest
  workload  : mixed
  threads   : 16
  duration  : 60s (warmup 3s)
  table rows: ~100,000
============================================================
[warmup] running 3s ...
[warmup] done.
[run] starting 16 threads for 60s ...
  t=  1.0s  queries=     8,231  qps=    8,231
  t=  2.0s  queries=    16,902  qps=    8,671
  ...
============================================================
  RESULT
============================================================
  duration       : 60.01 s
  total queries  : 512,438
  total errors   : 0
  throughput     : 8,539.55 queries/sec
  latency (ms)   : min=0.21  avg=1.87  p50=1.52  p95=4.13  p99=8.40  max=82.10
============================================================
```

### Workload yang tersedia

| Workload | Komposisi                                                              |
| -------- | ---------------------------------------------------------------------- |
| `read`   | 100% point SELECT `WHERE id = ?`                                       |
| `write`  | 100% INSERT                                                            |
| `update` | 100% UPDATE `score = score + 1 WHERE id = ?`                           |
| `mixed`  | 60% point SELECT, 10% range SELECT, 20% UPDATE, 10% INSERT (OLTP-like) |

Edit fungsi `build_op_picker()` di `mysql_load_test.py` untuk mengubah rasio
atau menambah query custom.

---

## 3. Menjalankan `sysbench_mysql.sh`

Sysbench adalah benchmark standar industri yang sering dipakai untuk
membandingkan performa MySQL antar konfigurasi/hardware.

```bash
chmod +x sysbench_mysql.sh

# 1. Prepare data (default: 4 tabel x 100k baris)
./sysbench_mysql.sh prepare

# 2. Jalankan benchmark
./sysbench_mysql.sh run                     # default: oltp_read_write
./sysbench_mysql.sh run oltp_read_only
./sysbench_mysql.sh run oltp_write_only
./sysbench_mysql.sh run oltp_point_select
./sysbench_mysql.sh run oltp_update_index

# 3. Cleanup
./sysbench_mysql.sh cleanup
```

Override parameter via environment variable:

```bash
THREADS=64 DURATION=120 TABLE_SIZE=1000000 ./sysbench_mysql.sh run oltp_read_write
```

---

## 4. Tips untuk hasil yang valid

1. **Jalankan dari host terpisah** dari MySQL server agar tidak rebutan CPU.
2. **Warm-up dulu** (sudah otomatis 3 detik di script Python) untuk mengisi
   buffer pool InnoDB.
3. **Pakai dataset > buffer pool** kalau ingin mengukur kinerja disk; pakai
   dataset < buffer pool untuk mengukur kinerja in-memory.
4. **Naikkan thread bertahap** (4 → 8 → 16 → 32 → 64) untuk menemukan
   titik saturasi.
5. **Pantau server-side** (`SHOW GLOBAL STATUS`, `iostat`, `top`,
   `SHOW ENGINE INNODB STATUS`) saat test berjalan.
6. **Ulangi minimal 3x** dan ambil median untuk mengurangi noise.
7. Pastikan **`max_connections`** MySQL ≥ jumlah thread yang dipakai.

---

## 5. Troubleshooting

| Masalah                                | Solusi                                                                |
| -------------------------------------- | --------------------------------------------------------------------- |
| `Too many connections`                 | Naikkan `max_connections` di MySQL atau turunkan `--threads`.         |
| `Access denied for user`               | Cek `MYSQL_USER` / `MYSQL_PASSWORD` atau pakai argument `--user`.     |
| `Lock wait timeout exceeded`           | Wajar saat workload write tinggi; naikkan `innodb_lock_wait_timeout`. |
| QPS jauh lebih rendah dari ekspektasi  | Cek `iostat`, `htop`, network latency antara client ↔ server.         |

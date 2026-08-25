# Incident Writeups — Database Performance Incident Lab

Two independent incidents, each run through a full break → detect → diagnose → fix → verify cycle on a PostgreSQL-backed Flask application.

---

## Incident A: Missing Index Causing Slow Query (STAR)

**Situation:** A Flask app backed by PostgreSQL exposed an endpoint, `GET /orders/by-email/<email>`, that looked up a customer's orders by filtering `orders.customer_email` against a table of 500,000 rows. The column had no index — a realistic scenario where a query ships without its indexing needs being considered.

**Task:** Confirm the query was genuinely slow (not just theoretically suboptimal), identify the root cause with the correct diagnostic tool, fix it, and verify the fix with measured before/after numbers.

**Action:**
1. Established a baseline by running `EXPLAIN ANALYZE` on the underlying query directly in `psql`, and separately timing the API call end-to-end with `curl -w`.
2. First baseline attempt was misleading: PostgreSQL automatically parallelized the sequential scan (2 workers via `Gather`), returning in ~17ms — fast enough to undersell the real problem. Disabled automatic parallelism at the database level (`ALTER DATABASE db_perf_lab SET max_parallel_workers_per_gather = 0`) to get an honest single-threaded baseline: `Seq Scan on orders`, 499,951 rows removed by filter, **30.636 ms execution time**, **53.6 ms API round-trip**.
3. Applied the fix: `CREATE INDEX idx_orders_customer_email ON orders (customer_email);`
4. Re-ran the identical `EXPLAIN ANALYZE` and `curl` timing to verify.

**Result:** Query plan switched to `Bitmap Index Scan` using the new index. Execution time dropped to **2.248 ms** (13.6x faster), API round-trip dropped to **5.5 ms** (9.7x faster). Confirmed no other queries or endpoints were affected by the change (the index is additive, not a schema-breaking change).

**Lesson for future work:** Always verify a "slow query" baseline isn't being masked by automatic parallelism or caching effects before concluding a fix isn't needed — the honest single-threaded number is what a genuinely loaded production system under contention for CPU workers would actually see.

---

## Incident B: Connection Pool Exhaustion from a Code Leak (STAR)

**Situation:** A new "reporting" endpoint, `GET /reports/leaky-summary`, was added to the same Flask app. It called `engine.connect()` directly to run a quick count query, rather than using the connection as a context manager. On a single manual test, it worked perfectly — the bug was invisible until enough traffic hit it.

**Task:** Reproduce a realistic production failure mode (a slow connection leak that only manifests under sustained use), diagnose it with the correct database-level tool rather than just guessing from application symptoms, fix the actual root cause, and verify the fix holds under load past the original failure point.

**Action:**
1. Confirmed baseline: normal `pg_stat_activity` connection count (1-2 sessions), pool configured as `pool_size=5, max_overflow=2, pool_timeout=10`.
2. Triggered the incident with a sequential load loop — no concurrency needed, since each unreturned connection permanently reduces the pool regardless of whether requests overlap in time. The 8th request in a 9-request loop hung for the full 10-second `pool_timeout` and failed with a 500 error; Flask's own traceback pinpointed it exactly: `sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 2 reached, connection timed out, timeout 10.00`.
3. Diagnosed at the database level with `pg_stat_activity` (not just trusting the application error) — found exactly 7 connections from `app_user`, all in `idle in transaction` state, with `state_change` timestamps matching the load test precisely. `idle in transaction` (rather than plain `idle`) confirmed these were genuinely abandoned mid-transaction, not just pooled-and-waiting.
4. Identified root cause in the code: `engine.connect()` was called without a `with` block or explicit `.close()`, so nothing guaranteed the connection's return to the pool.
5. Fixed by wrapping the connection acquisition in a `with engine.connect() as conn:` block, guaranteeing release even on an exception path.
6. Verified by re-running the load loop at 15 requests — nearly double the original 9-request failure point — with all 15 succeeding at `200` in ~25-30ms each, and a follow-up `pg_stat_activity` check showing a healthy 1-2 connections with none stuck in `idle in transaction`.

**Result:** Root cause fixed at the code level (not papered over by just raising the pool size, which would have delayed the same failure rather than preventing it). Verified recovery under load exceeding the original failure threshold.

**Lesson for future work:** When a fix is available at two levels — application code and infrastructure config — prefer fixing the actual root cause (the leak) over the config-level workaround (a bigger pool), which only buys time before the same bug causes the same outage at a higher connection count. `idle in transaction` in `pg_stat_activity` is a stronger signal of an application bug than plain `idle`, and worth specifically alerting on in a real monitoring setup.

---

## Other troubleshooting notes from the build (condensed)

- Ubuntu image used for the VM didn't have `python3-venv` installed by default — `python3 -m venv` failed with an `ensurepip` error until installing the package explicitly.
- A literal placeholder value left in a config file (`.env`'s database password, and separately a test email in a `curl` command) produced real, confusing failures — a reminder to always verify placeholder substitution actually happened rather than assuming a copy-paste worked.
- The initial seed script used SQLAlchemy's `text()` combined with `executemany()`, which under psycopg2 is effectively row-by-row — painfully slow for 500,000 rows. Switched to raw `psycopg2.extras.execute_values` for true multi-row bulk inserts, cutting the seed time from an apparent hang to under a minute.
- The VM (Ubuntu with a desktop environment) fully froze, including SSH, after sitting idle — root cause was systemd/GNOME automatic suspend actually suspending the guest OS, not a resource-exhaustion freeze. Fixed by masking the relevant systemd targets so the guest cannot suspend regardless of trigger: `sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target`.
- Appending a new Flask route to `app.py` with `cat >> app.py` placed the code after the blocking `if __name__ == "__main__": app.run(...)` line at the end of the file — since `app.run()` never returns, the appended route was never actually registered, despite being present in the file. Routes need to be defined before the entry-point block.
- A stale Flask process from an earlier terminal session was still holding port 5000 in the background, silently serving old code and masking all of the above fixes until it was identified (`lsof -i :5000`) and killed (`fuser -k 5000/tcp`).

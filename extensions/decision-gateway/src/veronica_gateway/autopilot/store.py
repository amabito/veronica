"""Durable local ledger, recoverable work journal, and acknowledged audit events."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from ..contracts import canonical
from .contracts import Document, ReviewRequired, Stopped
from .filesystem import no_links


class Store:
    def __init__(self, path: Path):
        no_links(path)
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(path) + suffix)
            no_links(sidecar)
            if sidecar.exists() and sidecar.stat().st_nlink != 1:
                raise Stopped("linked_database_sidecar_rejected")
        if path.exists() and path.stat().st_nlink != 1:
            raise Stopped("linked_database_rejected")
        self.db = sqlite3.connect(path, timeout=0, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        try:
            app_id = self.db.execute("PRAGMA application_id").fetchone()[0]
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                     "AND name NOT LIKE 'sqlite_%'").fetchall()
            if app_id not in (0, 0x56455241) or (app_id == 0 and tables):
                raise Stopped("foreign_database_rejected")
            if app_id == 0x56455241:
                version = self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                if version is None or version[0] != "1":
                    raise Stopped("unknown_database_schema")
            self.db.execute("PRAGMA journal_mode=DELETE")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript("""
                BEGIN IMMEDIATE;
                PRAGMA application_id=1447383617;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(
                    job_id TEXT PRIMARY KEY, source_sha TEXT NOT NULL,
                    suffix TEXT NOT NULL, plan_json TEXT NOT NULL,
                    business_key TEXT NOT NULL, archive_rel TEXT NOT NULL,
                    state TEXT NOT NULL, reason TEXT,
                    enrolled_policy TEXT NOT NULL, completion_json TEXT);
                CREATE TABLE IF NOT EXISTS ledger(
                    business_key TEXT PRIMARY KEY, record_json TEXT NOT NULL,
                    first_source_sha TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL, run_id TEXT NOT NULL, job_id TEXT,
                    policy_sha TEXT NOT NULL, operation TEXT NOT NULL,
                    status TEXT NOT NULL, reason TEXT);
                CREATE TABLE IF NOT EXISTS intake_reviews(
                    job_id TEXT PRIMARY KEY, reason TEXT NOT NULL, policy_sha TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(
                    run_id TEXT PRIMARY KEY, report_json TEXT NOT NULL);
                INSERT OR IGNORE INTO meta VALUES('schema','1');
                COMMIT;
            """)
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def audit(self, run_id, job_id, policy_sha, operation, status, reason=None):
        last = self.db.execute("SELECT MAX(sequence) FROM events").fetchone()[0]
        if last is not None and last >= 100_000:
            raise Stopped("audit_capacity_reached")
        # SQLite commits acknowledge storage; unlike a best-effort emitter, failures propagate.
        self.db.execute("INSERT INTO events(at,run_id,job_id,policy_sha,operation,status,reason) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (datetime.now(timezone.utc).isoformat(), run_id, job_id,
                         policy_sha, operation, status, reason))

    def job(self, job_id):
        return self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()

    def enroll(self, job_id, source_sha, suffix, document, archive_rel, policy_sha):
        with self.transaction():
            row = self.job(job_id)
            if row is not None:
                if (row['source_sha'] != source_sha or row['suffix'] != suffix
                        or row['plan_json'] != document.json
                        or row['archive_rel'] != archive_rel):
                    raise ReviewRequired("journal_plan_conflict")
                return
            self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,NULL,?,NULL)",
                            (job_id, source_sha, suffix, document.json,
                             document.business_key, archive_rel, "READY", policy_sha))

    def register(self, job_id, document):
        with self.transaction():
            row = self.db.execute("SELECT record_json FROM ledger WHERE business_key=?",
                                  (document.business_key,)).fetchone()
            if row is not None and row[0] != document.json:
                raise ReviewRequired("business_key_conflict")
            if row is None:
                job = self.job(job_id)
                self.db.execute("INSERT INTO ledger VALUES(?,?,?,?)",
                                (document.business_key, document.json, job['source_sha'],
                                 datetime.now(timezone.utc).isoformat()))
            self.db.execute("UPDATE jobs SET state='REGISTERED',reason=NULL WHERE job_id=?",
                            (job_id,))

    def verify_ledger(self, document):
        row = self.db.execute("SELECT record_json FROM ledger WHERE business_key=?",
                              (document.business_key,)).fetchone()
        if row is None or row[0] != document.json:
            raise ReviewRequired("ledger_verification_failed")

    def set_state(self, job_id, state, reason=None):
        self.db.execute("UPDATE jobs SET state=?,reason=? WHERE job_id=?", (state, reason, job_id))

    def prepare_completion(self, job_id, body):
        with self.transaction():
            row = self.job(job_id)
            if row['completion_json'] is None:
                self.db.execute("UPDATE jobs SET completion_json=?,state='VERIFIED' WHERE job_id=?",
                                (canonical(body), job_id))
            return self.job(job_id)['completion_json']

    def cursor(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
        return "" if row is None else row[0]

    def set_cursor(self, name):
        self.db.execute("INSERT INTO meta(key,value) VALUES('cursor',?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (name,))

    def intake_review(self, job_id, policy_sha):
        row = self.db.execute("SELECT reason FROM intake_reviews WHERE job_id=? AND policy_sha=?",
                              (job_id, policy_sha)).fetchone()
        return None if row is None else row[0]

    def save_intake_review(self, job_id, reason, policy_sha):
        self.db.execute("INSERT INTO intake_reviews VALUES(?,?,?) ON CONFLICT(job_id) "
                        "DO UPDATE SET reason=excluded.reason,policy_sha=excluded.policy_sha",
                        (job_id, reason, policy_sha))

    def save_run(self, run_id, body):
        if self.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] >= 10_000:
            raise Stopped("report_capacity_reached")
        self.db.execute("INSERT INTO runs VALUES(?,?)", (run_id, canonical(body)))

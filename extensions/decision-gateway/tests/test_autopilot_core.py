"""REAL core integration. No mock enforcement accepted as evidence in this module."""
import json
from pathlib import Path
import os
import sqlite3
from contextlib import closing
import subprocess
import sys
from unittest.mock import patch

import pytest

pytest.importorskip('veronica_core', reason='real veronica-core optional dependency unavailable')
from veronica_core.containment import ChainMetadata, ExecutionConfig, ExecutionContext
from veronica_gateway.autopilot import Autopilot, CoreToolBoundary, Document, Stopped, initialize
from veronica_gateway.autopilot.store import Store


def setup(tmp_path):
    root = tmp_path / 'workspace'
    policy = initialize(root, ('Example Supplier',))
    document = Document('R-1', 'invoice', 'Example Supplier', '2026-09-16', 'Real workflow')
    (root / 'inbox' / 'sample.json').write_text(document.json)
    return root, policy


def test_real_kernel_autonomous_end_to_end_and_replay(tmp_path):
    root, policy = setup(tmp_path)
    first = Autopilot(policy).run_once()
    second = Autopilot(policy).run_once()
    assert first['counts']['completed'] == 1
    assert second['counts']['already_completed'] == 1
    assert (root / 'inbox' / 'sample.json').exists()
    with closing(sqlite3.connect(root / '.veronica' / 'state.sqlite3')) as db, db:
        assert db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0] == 1
        assert db.execute('SELECT state FROM jobs').fetchone()[0] == 'COMPLETED'
        assert db.execute("SELECT COUNT(*) FROM events WHERE status='acknowledged'").fetchone()[0] > 0


def context(max_steps=20):
    return ExecutionContext(config=ExecutionConfig(max_cost_usd=0.01, max_steps=max_steps,
        max_retries_total=10, timeout_ms=0), metadata=ChainMetadata(request_id='request', chain_id='test'))


def test_real_tool_boundary_returns_callback_value(tmp_path):
    target = tmp_path / 'result.txt'
    with context() as core:
        def write():
            target.write_text('actual side effect')
            return 'verified'
        assert CoreToolBoundary(core).call('archive_copy', write) == 'verified'
        assert target.read_text() == 'actual side effect'
        assert core.get_snapshot().step_count == 1


def test_real_halt_blocks_operation(tmp_path):
    target = tmp_path / 'must-not-exist'
    with context() as core:
        core.abort('test_stop')
        with pytest.raises(Stopped):
            CoreToolBoundary(core).call('archive_copy', lambda: target.touch())
    assert not target.exists()


def test_real_shared_step_limit_no_unprotected_fallback(tmp_path):
    root, policy = setup(tmp_path)
    with context(max_steps=1) as core:
        with pytest.raises(Stopped):
            Autopilot(policy, boundary_factory=lambda _: CoreToolBoundary(core)).run_once()
    with closing(sqlite3.connect(root / '.veronica' / 'state.sqlite3')) as db, db:
        assert db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0] == 0


def test_real_failure_preserves_commit_and_reconciles_next_run(tmp_path):
    root, policy = setup(tmp_path)
    original = Store.register
    def fail(store, *args):
        original(store, *args)
        raise OSError('lost acknowledgement')
    with patch.object(Store, 'register', fail):
        report = Autopilot(policy).run_once()
    assert report['counts']['retryable'] == 1
    assert Autopilot(policy).run_once()['counts']['completed'] == 1
    with closing(sqlite3.connect(root / '.veronica' / 'state.sqlite3')) as db, db:
        assert db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0] == 1


def test_real_bad_document_does_not_stop_good_document(tmp_path):
    root, policy = setup(tmp_path)
    (root / 'inbox' / '0-bad.txt').write_text('unstructured, cannot import')
    report = Autopilot(policy).run_once()
    assert report['counts']['review_required'] == report['counts']['completed'] == 1


def test_real_cli_demo_runs_to_completion(tmp_path):
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).parents[1] / 'src')
    proc = subprocess.run([sys.executable, '-m', 'veronica_gateway.autopilot', 'demo',
                           '--workspace', str(tmp_path / 'demo')],
                          text=True, capture_output=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    result = json.loads(proc.stdout)
    assert result['first']['counts']['completed'] == 1
    assert result['second']['counts']['already_completed'] == 2

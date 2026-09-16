"""Portable file identity and change-detection regression tests."""
import os
import pytest
from veronica_gateway.autopilot import ReviewRequired, Stopped
from veronica_gateway.autopilot.filesystem import read_regular


def test_read_compares_consistent_handle_metadata_not_path_stat(tmp_path, monkeypatch):
    # Emulate differing path-stat/fstat representations. No identity/time field
    # is ignored: every handle observation still has to match exactly.
    from types import SimpleNamespace
    path = tmp_path / 'document.json'
    path.write_bytes(b'unchanged')
    actual = os.fstat
    def handle_stat(fd):
        s = actual(fd)
        values = {k: getattr(s, k) for k in
                  ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size',
                   'st_mtime_ns', 'st_ctime_ns')}
        values['st_dev'] += 100
        values['st_ctime_ns'] += 100
        return SimpleNamespace(**values)
    monkeypatch.setattr(os, 'fstat', handle_stat)
    assert read_regular(path, 100) == b'unchanged'


@pytest.mark.parametrize('observation,field', [(2, 'st_mtime_ns'), (2, 'st_ctime_ns'),
    (2, 'st_size'), (3, 'st_ino'), (3, 'st_dev'), (3, 'st_nlink')])
def test_read_rejects_modified_or_replaced_handle_snapshot(tmp_path, monkeypatch, observation, field):
    from types import SimpleNamespace
    path = tmp_path / 'document.json'
    path.write_bytes(b'unchanged')
    actual = os.fstat
    calls = []
    def changed(fd):
        calls.append(fd)
        s = actual(fd)
        values = {k: getattr(s, k) for k in
                  ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size',
                   'st_mtime_ns', 'st_ctime_ns')}
        if len(calls) == observation:
            values[field] += 1
        return SimpleNamespace(**values)
    monkeypatch.setattr(os, 'fstat', changed)
    with pytest.raises(ReviewRequired, match='source_changed_during_read'):
        read_regular(path, 100)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX FIFO fixture')
def test_fifo_input_rejected_without_blocking(tmp_path):
    path = tmp_path / 'input.json'
    os.mkfifo(path)
    with pytest.raises(Stopped, match='not_an_exclusive_regular_file'):
        read_regular(path, 100)

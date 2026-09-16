"""Local, service-owned workspace; no network shares or hostile same-user sandbox claim."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import os
import stat


from .contracts import ReviewRequired, Stopped


def no_links(path: Path):
    """Reject existing symlinks and Windows reparse points, including parents."""
    for part in (path, *path.parents):
        try:
            s = part.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(s.st_mode)
                or getattr(s, "st_file_attributes", 0) & 0x400):
            raise Stopped("linked_path_rejected")


def read_regular(path: Path, limit: int) -> bytes:
    no_links(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise Stopped("not_an_exclusive_regular_file")
        if before.st_size > limit:
            raise ReviewRequired("document_too_large")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    current = path.stat(follow_symlinks=False)
    fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if fields(before) != fields(after) or fields(after) != fields(current):
        raise ReviewRequired("source_changed_during_read")
    if len(data) > limit:
        raise ReviewRequired("document_too_large")
    return data


def fsync_dir(path: Path):
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_once(path: Path, data: bytes):
    """Atomic no-clobber publication. Equal existing bytes are a verified replay."""
    no_links(path)
    tmp = path.parent / (".veronica-pending-" + path.name)
    no_links(tmp)
    if tmp.exists():
        if path.exists() and os.path.samefile(tmp, path):
            # Previous publication succeeded but its temporary hardlink survived a crash.
            tmp.unlink()
            fsync_dir(path.parent)
        else:
            # Reserved service-owned temp name; no input/customer file uses this name.
            if not tmp.is_file() or tmp.stat().st_nlink != 1:
                raise Stopped("invalid_pending_artifact")
            tmp.unlink()
    if path.exists():
        if read_regular(path, len(data) + 1) != data:
            raise ReviewRequired("artifact_conflict")
        return
    try:
        with tmp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # A hard-link publish provides atomic create-if-absent; never overwrite.
            os.link(tmp, path, follow_symlinks=False)
        except FileExistsError:
            if read_regular(path, len(data) + 1) != data:
                raise ReviewRequired("artifact_conflict")
        finally:
            tmp.unlink(missing_ok=True)
        fsync_dir(path.parent)
        if read_regular(path, len(data) + 1) != data:
            raise ReviewRequired("artifact_verification_failed")
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def workspace_lock(path: Path):
    """OS-released single-runner lock; local filesystem only, crash-safe unlock."""
    no_links(path)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "r+b", buffering=0) as stream:
        if os.fstat(fd).st_nlink != 1:
            raise Stopped("linked_lock_rejected")
        locked = False
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    stream.write(b"0")
                stream.seek(0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise Stopped("workspace_busy") from None
        try:
            yield
        finally:
            if locked:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)


def content_hash(data: bytes) -> str:
    return sha256(data).hexdigest()

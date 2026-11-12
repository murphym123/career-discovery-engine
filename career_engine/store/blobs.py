"""Content-addressed capture storage for the local Prospect data directory."""
import fcntl
import gzip
import hashlib
import os
import re
import tempfile
from calendar import monthrange
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def outside_git(path):
    resolved = Path(path).expanduser().resolve()
    if any((parent / ".git").exists() or parent.name == ".git"
           for parent in (resolved, *resolved.parents)):
        raise ValueError("Prospect data must be outside a Git checkout")
    return resolved


def managed_path(data_dir, relative):
    path = outside_git(data_dir / relative)
    if not path.is_relative_to(data_dir):
        raise ValueError("Prospect data path escapes its data directory")
    return path


def digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Expected a lowercase SHA-256 digest")
    return value


class BlobStore:
    """Store public response bodies as immutable, compressed files."""

    def __init__(self, data_dir):
        self.data_dir = outside_git(data_dir)
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root = managed_path(self.data_dir, "blobs")

    def _path(self, sha):
        sha = digest(sha)
        return managed_path(self.data_dir, self.root / "public" / sha[:2] / f"{sha}.gz")

    @contextmanager
    def locked(self, *, write=False):
        lock_path = managed_path(self.data_dir, ".blobs.lock")
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            yield

    def put(self, body, retention_class):
        if retention_class == "private_email":
            raise NotImplementedError("Private-email capture requires sanitization in M4")
        if retention_class != "public":
            raise ValueError("This retention class does not permit capture in v1")
        if not isinstance(body, bytes):
            raise TypeError("A raw capture must be bytes")
        sha = hashlib.sha256(body).hexdigest()
        path = self._path(sha)
        with self.locked(write=True):
            if path.exists():
                # A later retrieval of the same bytes renews their retention age.
                path.touch(exist_ok=True)
                return sha
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staged:
                temporary = Path(staged.name)
                try:
                    with gzip.GzipFile(filename="", fileobj=staged, mode="wb", mtime=0) as compressed:
                        compressed.write(body)
                    staged.flush()
                    os.fsync(staged.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
        return sha

    def exists(self, sha):
        return self._path(sha).is_file()

    def get(self, sha):
        with self.locked():
            body = gzip.decompress(self._path(sha).read_bytes())
        if hashlib.sha256(body).hexdigest() != sha:
            raise ValueError("Capture does not match its SHA-256 digest")
        return body

    def purge(self, retention_class, older_than=None):
        """Remove public bodies older than a cutoff (18 calendar months by default)."""
        if retention_class != "public":
            raise ValueError("Only the public retention class stores captures in v1")
        if older_than is None:
            now = datetime.now(timezone.utc)
            year, month = divmod(now.year * 12 + now.month - 1 - 18, 12)
            older_than = now.replace(year=year, month=month + 1,
                                     day=min(now.day, monthrange(year, month + 1)[1]))
        if older_than.utcoffset() is None:
            raise ValueError("Retention cutoff must specify a timezone")
        cutoff = older_than.timestamp()
        purged = []
        with self.locked(write=True):
            root = managed_path(self.data_dir, self.root / "public")
            for path in sorted(root.glob("[0-9a-f][0-9a-f]/*.gz")):
                if re.fullmatch(r"[0-9a-f]{64}", path.stem) and self._path(path.stem) == path and path.stat().st_mtime < cutoff:
                    path.unlink()
                    purged.append(path.stem)
        return purged

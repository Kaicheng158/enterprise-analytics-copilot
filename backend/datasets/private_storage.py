"""Opaque-reference local blob adapter for synthetic PV2-02a verification."""
import hashlib
import os
from pathlib import Path
import re
import tempfile
import uuid

from .persistence_models import PersistenceFailure


REFERENCE = re.compile(r"private:v1:([0-9a-f]{32}):(raw|dataset)\Z")


class PrivateLocalBlobStore:
    def __init__(self, root: Path):
        root = Path(root)
        if not root.is_absolute():
            raise PersistenceFailure("invalid_storage_root")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise PersistenceFailure("invalid_storage_root")
        os.chmod(root, 0o700)
        self._root = root.resolve(strict=True)
        self._objects = self._root / "objects"
        self._objects.mkdir(mode=0o700, exist_ok=True)
        if self._objects.is_symlink() or self._objects.resolve(strict=True).parent != self._root:
            raise PersistenceFailure("invalid_storage_root")
        os.chmod(self._objects, 0o700)

    @staticmethod
    def new_reference(kind: str) -> str:
        if kind not in {"raw", "dataset"}:
            raise PersistenceFailure("invalid_storage_kind")
        return f"private:v1:{uuid.uuid4().hex}:{kind}"

    def _path(self, reference: str) -> Path:
        match = REFERENCE.fullmatch(reference)
        if not match:
            raise PersistenceFailure("invalid_storage_reference")
        directory = self._objects / match.group(1)[:2]
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink() or directory.resolve(strict=True).parent != self._objects:
            raise PersistenceFailure("invalid_storage_reference")
        os.chmod(directory, 0o700)
        path = directory / f"{match.group(1)}.{match.group(2)}"
        if path.parent.resolve(strict=True) != directory.resolve(strict=True):
            raise PersistenceFailure("invalid_storage_reference")
        return path

    def put(self, reference: str, content: bytes) -> tuple[int, str]:
        if type(content) is not bytes:
            raise PersistenceFailure("invalid_blob")
        target = self._path(reference)
        if target.exists():
            raise PersistenceFailure("storage_reference_exists")
        descriptor, temporary = tempfile.mkstemp(prefix=".staging-", dir=target.parent)
        published = False
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
                published = True
            except FileExistsError:
                raise PersistenceFailure("storage_reference_exists") from None
            os.chmod(target, 0o600)
            self._fsync_directory(target.parent)
        except PersistenceFailure:
            if published:
                try:
                    target.unlink()
                except OSError:
                    pass
            raise
        except Exception:
            if published:
                try:
                    target.unlink()
                except OSError:
                    pass
            raise PersistenceFailure("storage_write_failed") from None
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return len(content), hashlib.sha256(content).hexdigest()

    def verify(self, reference: str, expected_size: int, expected_sha256: str) -> bool:
        path = self._path(reference)
        try:
            stat = path.stat()
            if not path.is_file() or path.is_symlink() or stat.st_size != expected_size:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    digest.update(chunk)
            return digest.hexdigest() == expected_sha256
        except OSError:
            return False

    def read_verified(
        self,
        reference: str,
        *,
        expected_size: int,
        expected_sha256: str,
        max_bytes: int,
    ) -> bytes:
        """Read an opaque object only after bounded size and digest checks."""
        if (
            type(expected_size) is not int
            or type(max_bytes) is not int
            or expected_size < 0
            or max_bytes < 1
            or expected_size > max_bytes
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
        ):
            raise PersistenceFailure("invalid_blob_read_contract")
        path = self._path(reference)
        try:
            stat = path.stat()
            if path.is_symlink() or not path.is_file() or stat.st_size != expected_size:
                raise PersistenceFailure("stored_blob_integrity_mismatch")
            content = path.read_bytes()
        except PersistenceFailure:
            raise
        except OSError:
            raise PersistenceFailure("stored_blob_unavailable") from None
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_sha256:
            raise PersistenceFailure("stored_blob_integrity_mismatch")
        return content

    def remove(self, reference: str) -> None:
        path = self._path(reference)
        try:
            path.unlink()
            self._fsync_directory(path.parent)
        except FileNotFoundError:
            return
        except OSError:
            raise PersistenceFailure("storage_cleanup_failed") from None

    def object_count(self) -> int:
        return sum(1 for path in self._objects.glob("*/*") if path.is_file())

    def cleanup_unreferenced(
        self, referenced: set[str], limit: int
    ) -> tuple[int, int, int, int, bool]:
        """Remove only recognized unreferenced objects/staging files under an external maintenance lock."""
        if type(referenced) is not set or type(limit) is not int or limit < 0:
            raise PersistenceFailure("invalid_cleanup_contract")
        for reference in referenced:
            if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
                raise PersistenceFailure("invalid_cleanup_reference")
        removed_objects = removed_staging = logical_bytes = physical_bytes = 0
        candidates: list[tuple[Path, str | None]] = []
        for path in sorted(self._objects.glob("*/*")):
            try:
                parent = path.parent
                if (
                    parent.is_symlink()
                    or parent.resolve(strict=True).parent != self._objects
                    or path.is_symlink()
                    or not path.is_file()
                ):
                    continue
            except OSError:
                continue
            if path.name.startswith(".staging-"):
                candidates.append((path, None))
                continue
            match = re.fullmatch(r"([0-9a-f]{32})\.(raw|dataset)", path.name)
            if match:
                candidates.append((path, f"private:v1:{match.group(1)}:{match.group(2)}"))
        eligible = [(path, ref) for path, ref in candidates if ref is None or ref not in referenced]
        for path, reference in eligible[:limit]:
            try:
                stat = path.stat()
                allocated = getattr(stat, "st_blocks", 0) * 512
                path.unlink()
                self._fsync_directory(path.parent)
            except FileNotFoundError:
                continue
            except OSError:
                raise PersistenceFailure("storage_cleanup_failed") from None
            logical_bytes += stat.st_size
            physical_bytes += allocated
            if reference is None:
                removed_staging += 1
            else:
                removed_objects += 1
        return (
            removed_objects,
            removed_staging,
            logical_bytes,
            physical_bytes,
            len(eligible) > limit,
        )

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

"""Controlled UTF-8 local loader. No URLs, PDF, symlinks or external calls."""
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import stat
from urllib.parse import quote
import uuid
from .models import Document
from .chunking import ChunkConfig, canonical, digest

LOADER_VERSION = 'utf8-local-v1'
MAX_FILE_BYTES = 1024 * 1024


class IngestionError(ValueError):
    """Safe fixed codes; never include file contents or credentials."""


class LocalDocumentLoader:
    def __init__(self, root: Path, namespace: str, config=ChunkConfig()):
        self.root = Path(root)
        if not namespace or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in namespace):
            raise IngestionError('invalid_source_namespace')
        self.namespace, self.config = namespace, config

    def load(self, path: Path, *, tenant_id: str) -> Document:
        path = Path(path)
        if not tenant_id.strip():
            raise IngestionError('invalid_tenant')
        if '\x00' in str(path) or path.is_absolute() or not path.parts or any(p in ('.','..') or p.startswith('.') for p in path.parts):
            raise IngestionError('unsafe_relative_path')
        if path.suffix.lower() not in ('.txt', '.md', '.markdown'):
            raise IngestionError('unsupported_file_type')
        fds = []
        try:
            # The root is operator-owned; all traversal below it uses directory FDs.
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            fds.append(fd)
            for part in path.parts[:-1]:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                fds.append(fd)
            file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            fds.append(file_fd)
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                raise IngestionError('not_regular_file')
            if before.st_size > MAX_FILE_BYTES:
                raise IngestionError('file_too_large')
            blocks, count = [], 0
            while True:
                block = os.read(file_fd, min(65536, MAX_FILE_BYTES + 1 - count))
                if not block:
                    break
                blocks.append(block); count += len(block)
                if count > MAX_FILE_BYTES:
                    raise IngestionError('file_too_large')
            after = os.fstat(file_fd)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise IngestionError('source_changed_during_read')
        except OSError:
            raise IngestionError('source_unreadable_or_unsafe') from None
        finally:
            for fd in reversed(fds): os.close(fd)
        raw = b''.join(blocks)
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise IngestionError('invalid_utf8') from None
        if '\x00' in text:
            raise IngestionError('nul_character')
        if not text.strip():
            raise IngestionError('empty_document')
        source = f'local://{self.namespace}/{quote(path.as_posix(), safe="/")}'
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, canonical([tenant_id, source])))
        source_hash = hashlib.sha256(raw).hexdigest()
        revision = digest(canonical([LOADER_VERSION, source_hash, self.config.sha256]))
        metadata = {'loader_version': LOADER_VERSION, 'source_namespace': self.namespace,
                    'relative_path': path.as_posix(), 'source_sha256': source_hash,
                    'text_sha256': digest(text), 'source_bytes': len(raw),
                    'config': asdict(self.config), 'config_sha256': self.config.sha256}
        return Document(identity, revision, tenant_id, path.name, source, source_hash, text, metadata)

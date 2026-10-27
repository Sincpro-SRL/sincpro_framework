"""`LocalObjectStore`: objects on a directory of this machine — object storage emulated on a disk, not a
filesystem's own semantics — a volume a service mounts.

    LocalObjectStore("/data/objects").put("exports/invoices.csv", data, "text/csv", {}, False)
        ->  /data/objects/exports/invoices.csv, and beside it invoices.csv.object-meta.json

Context: a key is a path under the root, and never leaves it — `../` is refused
(`ProgrammingError`), since it would read or write outside what the service was given. The
content type and the metadata are a JSON file beside each object; `keys` never answers them. A
put `if_absent` creates the file exclusively (`O_CREAT | O_EXCL`), the one primitive a disk
holds atomic across processes.
"""

import json
import shutil
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from sincpro_framework.data_layer.object_storage.domain import exceptions
from sincpro_framework.data_layer.object_storage.domain.object_store import (
    ObjectRef,
    ObjectStore,
)

META_SUFFIX = ".object-meta.json"
"""Beside each object, the file that says its content type and metadata."""


class LocalObjectStore(ObjectStore):
    """Objects under one directory, created when missing."""

    def __init__(self, root: str | Path) -> None:
        """Objects under `root`."""
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        """Where `key` lives — under the root, or `ProgrammingError`."""
        path = (self.root / key).resolve()
        if path != self.root and self.root not in path.parents:
            raise exceptions.ProgrammingError(f"the key {key!r} leaves the store's directory")
        return path

    def _meta(self, path: Path) -> Path:
        """The file beside `path` that says what it is."""
        return path.with_name(path.name + META_SUFFIX)

    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        content_type: str,
        metadata: Mapping[str, str],
        if_absent: bool,
        **options: Any,
    ) -> bool:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb" if if_absent else "wb") as written:
                if isinstance(data, bytes):
                    written.write(data)
                else:
                    shutil.copyfileobj(data, written)
        except FileExistsError:
            return False
        said = {"content_type": content_type, "metadata": dict(metadata)}
        self._meta(path).write_text(json.dumps(said))
        return True

    def get(self, key: str, **options: Any) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def info(self, key: str, **options: Any) -> ObjectRef | None:
        path = self._path(key)
        if not path.is_file():
            return None
        meta = self._meta(path)
        said = json.loads(meta.read_text()) if meta.is_file() else {}
        stat = path.stat()
        return ObjectRef(
            key=key,
            size=stat.st_size,
            content_type=said.get("content_type") or "application/octet-stream",
            metadata=said.get("metadata") or {},
            modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
        )

    def delete(self, key: str, **options: Any) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)
        self._meta(path).unlink(missing_ok=True)

    def keys(self, prefix: str, **options: Any) -> Iterator[str]:
        for path in sorted(self.root.rglob("*")):
            key = path.relative_to(self.root).as_posix()
            if path.is_file() and not key.endswith(META_SUFFIX) and key.startswith(prefix):
                yield key

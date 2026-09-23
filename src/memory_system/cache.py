"""Persistent cache primitives for phase-one concept generation.

The cache is deliberately independent from a particular model client or
concept-card implementation.  A :class:`CacheKey` identifies the complete
input/configuration of one generation, while :class:`ConceptCache` stores the
JSON-compatible generated concepts.  Only generated concepts and cache
metadata are persisted: source text and credential-like fields are omitted.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any


_CACHE_SCHEMA_VERSION = 1
_SENSITIVE_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "auth_token",
        "authorization",
        "credentials",
        "password",
        "secret",
        "token",
        "source",
        "source_code",
        "source_content",
        "source_excerpt",
        "source_text",
        "raw_source",
        "code",
        "code_excerpt",
        "code_text",
        "prompt",
        "prompt_text",
    }
)


def _normalised_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")


def _is_sensitive_name(name: str) -> bool:
    normalised = _normalised_name(name)
    return normalised in _SENSITIVE_KEY_NAMES or normalised.endswith(
        ("_api_key", "_access_token", "_auth_token", "_password")
    )


def _json_value(value: Any, *, omit_sensitive: bool) -> Any:
    """Convert a value to JSON data without retaining sensitive fields."""

    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    elif hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("cache values cannot contain non-finite floats")
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_name, raw_value in value.items():
            if not isinstance(raw_name, str):
                raise TypeError("cache mapping keys must be strings")
            if omit_sensitive and _is_sensitive_name(raw_name):
                continue
            result[raw_name] = _json_value(raw_value, omit_sensitive=omit_sensitive)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(item, omit_sensitive=omit_sensitive) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [_json_value(item, omit_sensitive=omit_sensitive) for item in value]
        return sorted(converted, key=lambda item: json.dumps(item, sort_keys=True))
    raise TypeError(f"cache values must be JSON-compatible, got {type(value).__name__}")


def _normalise_file_path(file_path: str | Path) -> str:
    if not isinstance(file_path, (str, Path)):
        raise TypeError("file_path must be a string or pathlib.Path")
    return str(Path(file_path).expanduser().resolve(strict=False))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class CacheKey:
    """Stable identity for one concept-generation request.

    ``file_path`` is optional metadata used by :meth:`ConceptCache.invalidate_file`;
    it does not participate in the content key, so identical source/configuration
    can be reused after a file is moved or copied.  ``generation_config`` is
    canonicalised with sorted JSON keys.  Credential
    and source-like fields are excluded before hashing and persistence.
    """

    source_digest: str
    model_name: str
    model_version: str | None = None
    prompt_version: str = "phase1-v1"
    generation_config: Mapping[str, Any] = field(default_factory=dict)
    # A file path identifies the active-version association, not generated
    # content.  Identical source/configuration can therefore be reused after a
    # file is moved or copied.
    file_path: str | None = field(default=None, compare=False, hash=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source_digest, str) or not self.source_digest.strip():
            raise ValueError("source_digest must be a non-empty string")
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise ValueError("model_name must be a non-empty string")
        if not isinstance(self.prompt_version, str) or not self.prompt_version.strip():
            raise ValueError("prompt_version must be a non-empty string")
        if self.model_version is not None and not isinstance(self.model_version, str):
            raise TypeError("model_version must be a string or None")
        if not isinstance(self.generation_config, Mapping):
            raise TypeError("generation_config must be a mapping")
        # Copy the mapping so changing the caller's config cannot change a key
        # after it has been used by the cache.
        object.__setattr__(self, "generation_config", dict(self.generation_config))
        if self.file_path is not None:
            object.__setattr__(self, "file_path", _normalise_file_path(self.file_path))

    @property
    def model(self) -> str:
        """Alias matching the model field used by the synthesis config."""

        return self.model_name

    @property
    def version(self) -> str | None:
        """Alias for ``model_version``."""

        return self.model_version

    @property
    def generation_config_json(self) -> str:
        """Canonical JSON representation used in the stable key."""

        return json.dumps(
            _json_value(self.generation_config, omit_sensitive=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def payload(self) -> dict[str, Any]:
        """Return the secret-free fields that determine this key."""

        payload: dict[str, Any] = {
            "source_digest": self.source_digest,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "generation_config": json.loads(self.generation_config_json),
        }
        return payload

    def to_dict(self) -> dict[str, Any]:
        """Serialize this key without credentials or source content."""

        return self.payload()

    as_dict = to_dict

    @property
    def digest(self) -> str:
        """SHA-256 identifier of the canonical key payload."""

        encoded = json.dumps(
            self.payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def key(self) -> str:
        """Short, convenient alias for :attr:`digest`."""

        return self.digest

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CacheKey":
        """Restore a key from the cache's JSON representation."""

        return cls(
            source_digest=str(data["source_digest"]),
            model_name=str(data["model_name"]),
            model_version=data.get("model_version"),
            prompt_version=str(data["prompt_version"]),
            generation_config=dict(data.get("generation_config", {})),
            file_path=data.get("file_path"),
        )

    def __hash__(self) -> int:
        """Make the immutable key safe to use as a dictionary key."""

        return hash(self.digest)

    def __str__(self) -> str:
        return self.digest


class ConceptCache:
    """A small JSON-backed cache for generated concept results.

    Entries are keyed by the full :class:`CacheKey`, so changing any model,
    prompt, or generation setting naturally produces a miss.  Replacing the
    active entry for a file does not remove previous entries.  Consequently,
    ``invalidate_file`` removes only the file's active version and preserves
    historical versions for inspection or rollback.
    """

    def __init__(self, path: str | Path = ".memory_system/concept_cache.json") -> None:
        self.path = str(path) if str(path) == ":memory:" else Path(path).expanduser()
        self._memory = str(path) == ":memory:"
        self._entries: dict[str, dict[str, Any]] = {}
        self._active_by_file: dict[str, str] = {}
        self._hits = 0
        self._misses = 0
        self._load()

    def __enter__(self) -> "ConceptCache":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def hit_count(self) -> int:
        return self._hits

    @property
    def miss_count(self) -> int:
        return self._misses

    @property
    def stats(self) -> dict[str, float | int]:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total else 0.0,
        }

    @property
    def active_files(self) -> tuple[str, ...]:
        """Return normalized source paths with an active cached version."""

        return tuple(sorted(self._active_by_file))

    def prune_missing(
        self,
        existing_files: set[str | Path],
        *,
        root: str | Path | None = None,
    ) -> tuple[str, ...]:
        """Invalidate active entries below ``root`` that are no longer present.

        Historical entries remain available under their original cache keys;
        only active associations for deleted source files are removed.
        """

        existing = {_normalise_file_path(path) for path in existing_files}
        root_path = _normalise_file_path(root) if root is not None else None
        stale = [
            path
            for path in self._active_by_file
            if (root_path is None or _is_relative_to(Path(path), Path(root_path)))
            and path not in existing
        ]
        for path in stale:
            self.invalidate_file(path)
        return tuple(sorted(stale))

    def reset_stats(self) -> None:
        self._hits = 0
        self._misses = 0

    def get(self, key: CacheKey | str, default: Any = None) -> Any:
        """Return a cached result and record a hit or miss."""

        key_digest = self._key_digest(key)
        entry = self._entries.get(key_digest)
        if entry is None or "value" not in entry:
            self._misses += 1
            return default
        self._hits += 1
        return entry["value"]

    def put(
        self,
        key: CacheKey,
        concepts: Any,
        *,
        file_path: str | Path | None = None,
    ) -> CacheKey:
        """Store generated concepts and mark them active for ``file_path``.

        ``concepts`` must be JSON-compatible (dataclasses and objects exposing
        ``to_dict`` are also accepted).  Source- and credential-like mapping
        fields are omitted before writing, rather than being copied into the
        cache accidentally.
        """

        if not isinstance(key, CacheKey):
            raise TypeError("key must be a CacheKey")
        resolved_file = _normalise_file_path(file_path) if file_path is not None else key.file_path
        value = _json_value(concepts, omit_sensitive=True)
        entry = {
            "key": key.to_dict(),
            "file_path": resolved_file,
            "value": value,
        }
        self._entries[key.digest] = entry
        if resolved_file is not None:
            self._active_by_file[resolved_file] = key.digest
        self._save()
        return key

    def invalidate_file(self, file_path: str | Path) -> bool:
        """Remove only the active cached version associated with a file.

        Older versions remain available through their original keys.  The
        boolean result indicates whether an active association was removed.
        """

        resolved_file = _normalise_file_path(file_path)
        key_digest = self._active_by_file.pop(resolved_file, None)
        if key_digest is None:
            return False
        # A key can be shared by multiple files when callers omit file_path
        # from CacheKey.  Keep it until its last active owner is invalidated.
        if key_digest not in self._active_by_file.values():
            self._entries.pop(key_digest, None)
        self._save()
        return True

    # Descriptive alias for callers handling a deleted source file.
    delete_file = invalidate_file

    def clear(self) -> None:
        """Remove all entries and active associations."""

        self._entries.clear()
        self._active_by_file.clear()
        self._save()

    def __len__(self) -> int:
        return len(self._entries)

    def contains(self, key: CacheKey | str) -> bool:
        return self._key_digest(key) in self._entries

    def _key_digest(self, key: CacheKey | str) -> str:
        if isinstance(key, CacheKey):
            return key.digest
        if isinstance(key, str) and key:
            return key
        raise TypeError("key must be a CacheKey or digest string")

    def _load(self) -> None:
        if self._memory:
            return
        cache_path = self.path
        assert isinstance(cache_path, Path)
        if not cache_path.exists():
            return
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return
            if payload.get("schema_version") != _CACHE_SCHEMA_VERSION:
                return
            entries = payload.get("entries", {})
            active = payload.get("active_by_file", {})
            if not isinstance(entries, dict) or not isinstance(active, dict):
                return
            self._entries = {
                str(digest): entry
                for digest, entry in entries.items()
                if isinstance(entry, dict)
                and isinstance(entry.get("value"), (dict, list, str, int, float, bool, type(None)))
            }
            self._active_by_file = {
                str(file_path): str(digest)
                for file_path, digest in active.items()
                if str(digest) in self._entries
            }
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # A corrupt cache should degrade to misses, not prevent generation.
            self._entries = {}
            self._active_by_file = {}

    def _save(self) -> None:
        if self._memory:
            return
        cache_path = self.path
        assert isinstance(cache_path, Path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "entries": self._entries,
            "active_by_file": self._active_by_file,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=cache_path.parent,
                prefix=f".{cache_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(encoded)
                temporary.write("\n")
                temporary_path = temporary.name
            os.replace(temporary_path, cache_path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass


__all__ = ["CacheKey", "ConceptCache"]

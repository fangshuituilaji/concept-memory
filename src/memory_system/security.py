"""Local security boundaries for source-file analysis and model sending.

The first phase can read a source file locally without sending it anywhere.  A
separate online decision is therefore used for model transmission: ordinary
source is allowed by default, while paths that commonly contain secrets need an
explicit allowlist entry.  All boundary checks use a canonical real path, so a
symlink cannot be used to escape a configured project root.

This module intentionally has no model-provider dependency and never reads an
API key.  :class:`JsonlAuditRecorder` records only operational metadata; it is
safe to use around either an online or an offline pipeline.
"""

from __future__ import annotations

import fnmatch
import json
import os
import threading
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class SourceSendingPolicy(str, Enum):
    """Where source processing is allowed to happen.

    ``OFFLINE`` means that local reading/analysis remains available but no
    source is eligible for online transmission.  ``ONLINE`` applies the
    sensitive-path and allowlist checks in :class:`SecurityPolicy`.
    """

    OFFLINE = "offline"
    ONLINE = "online"

    # Useful spelling aliases for callers that use local/remote terminology.
    LOCAL = "offline"
    REMOTE = "online"

    @classmethod
    def coerce(cls, value: "SourceSendingPolicy | str | bool") -> "SourceSendingPolicy":
        """Convert common configuration spellings to a sending policy."""

        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            return cls.ONLINE if value else cls.OFFLINE
        normalized = str(value).strip().casefold()
        if normalized in {"offline", "off", "local", "none", "false", "0"}:
            return cls.OFFLINE
        if normalized in {"online", "on", "remote", "true", "1"}:
            return cls.ONLINE
        raise ValueError(f"unknown source sending policy: {value!r}")


@dataclass(frozen=True, slots=True)
class SecurityDecision:
    """Explainable result of a local-read or online-send policy check.

    The object is truthy exactly when the requested operation is allowed, which
    permits both explicit ``decision.allowed`` checks and convenient boolean
    checks without losing the reason for a decision.
    """

    path: str
    allowed: bool
    reason: str
    mode: SourceSendingPolicy
    operation: str = "send"
    sensitive: bool = False
    allowlisted: bool = False
    within_root: bool = True
    symlink_escape: bool = False
    requested_path: str | None = None

    @property
    def can_send(self) -> bool:
        """Boolean alias used by online-send callers."""

        return self.allowed if self.operation == "send" else False

    @property
    def can_read(self) -> bool:
        """Boolean alias used by local-read callers."""

        return self.allowed if self.operation == "read" else False

    @property
    def is_allowed(self) -> bool:
        """Boolean alias for integrations that use predicate naming."""

        return self.allowed

    @property
    def is_sensitive(self) -> bool:
        return self.sensitive

    @property
    def decision(self) -> str:
        """Stable audit-friendly decision label."""

        return "allow" if self.allowed else "deny"

    @property
    def denied(self) -> bool:
        return not self.allowed

    def __bool__(self) -> bool:
        return self.allowed

    def to_dict(self) -> dict[str, Any]:
        """Return metadata suitable for an audit record, never source text."""

        return {
            "path": self.path,
            "decision": self.decision,
            "allowed": self.allowed,
            "reason": self.reason,
            "mode": self.mode.value,
            "operation": self.operation,
            "sensitive": self.sensitive,
            "allowlisted": self.allowlisted,
            "within_root": self.within_root,
            "symlink_escape": self.symlink_escape,
        }


class SecurityBoundaryError(ValueError):
    """Raised when a path cannot be contained by the configured project root."""

    def __init__(self, path: str | Path, reason: str):
        self.path = str(path)
        self.reason = reason
        super().__init__(f"source path rejected ({reason}): {self.path}")


# These are deliberately path-based indicators.  The policy does not inspect
# source contents, which keeps the boundary deterministic and avoids putting
# source into an audit trail.
_SENSITIVE_EXTENSIONS = frozenset({".pem", ".key", ".crt", ".p12", ".pfx", ".jks"})
# Canonical credential files whose names carry no sensitive token and no
# listed extension (ssh keys, .netrc); ``.pub`` variants stay sendable.
_SENSITIVE_FILENAMES = frozenset(
    {
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        ".netrc",
    }
)
_SENSITIVE_COMPONENTS = frozenset(
    {
        "secret",
        "secrets",
        "credential",
        "credentials",
        "token",
        "tokens",
        "password",
        "passwords",
        "passwd",
        "config",
        "configs",
        "configuration",
        "configurations",
    }
)
_SENSITIVE_CONFIG_KEYS = frozenset(
    {
        "access_key",
        "access_token",
        "api_key",
        "apikey",
        "api_secret",
        "auth_token",
        "authorization",
        "client_secret",
        "credential",
        "credentials",
        "key",
        "password",
        "passwd",
        "private_key",
        "secret",
        "secret_key",
        "secrets",
        "token",
        "tokens",
    }
)
_NON_SOURCE_AUDIT_KEYS = frozenset(
    {
        "body",
        "code",
        "content",
        "message",
        "messages",
        "prompt",
        "prompts",
        "source",
        "source_code",
        "source_excerpt",
        "source_text",
        "text",
    }
)
_GLOB_CHARS = frozenset("*?[")


class SecurityPolicy:
    """Decide whether local source may be read or sent to an online model.

    Parameters are intentionally standard-library values so the class can be
    used independently of the pipeline or any particular model provider.

    ``root`` establishes a hard real-path boundary.  Relative paths passed to
    the policy, ``sensitive_paths`` and ``allowlist`` are interpreted relative
    to this root.  A symlink that resolves outside it is always rejected before
    allowlist evaluation; an allowlist cannot be used to bypass that boundary.
    """

    def __init__(
        self,
        root: str | Path | SourceSendingPolicy | None = None,
        *,
        source_sending_policy: SourceSendingPolicy | str | bool = SourceSendingPolicy.ONLINE,
        sending_policy: SourceSendingPolicy | str | bool | None = None,
        source_policy: SourceSendingPolicy | str | bool | None = None,
        mode: SourceSendingPolicy | str | bool | None = None,
        policy: SourceSendingPolicy | str | bool | None = None,
        allowlist: Iterable[str | Path] = (),
        allowed_paths: Iterable[str | Path] | None = None,
        allowlisted_paths: Iterable[str | Path] | None = None,
        sensitive_paths: Iterable[str | Path] = (),
        user_sensitive_paths: Iterable[str | Path] | None = None,
    ) -> None:
        # Accept SecurityPolicy(SourceSendingPolicy.OFFLINE) as a small
        # convenience while retaining the normal root-first constructor.
        if isinstance(root, SourceSendingPolicy):
            if any(
                value is not None
                for value in (sending_policy, source_policy, mode, policy)
            ):
                raise ValueError("source sending policy was specified more than once")
            source_sending_policy = root
            root = None

        selected_policies = [
            value
            for value in (sending_policy, source_policy, mode, policy)
            if value is not None
        ]
        if len(selected_policies) > 1:
            raise ValueError("specify only one of sending_policy, source_policy, mode, or policy")
        if selected_policies:
            source_sending_policy = selected_policies[0]

        self._mode = SourceSendingPolicy.coerce(source_sending_policy)
        self._root_lexical = (
            Path(os.path.abspath(os.fspath(Path(root).expanduser())))
            if root is not None
            else None
        )
        self._root = (
            Path(os.path.realpath(os.fspath(self._root_lexical)))
            if self._root_lexical is not None
            else None
        )
        if allowed_paths is not None:
            allowlist = tuple(allowlist) + tuple(allowed_paths)
        if allowlisted_paths is not None:
            allowlist = tuple(allowlist) + tuple(allowlisted_paths)
        if user_sensitive_paths is not None:
            sensitive_paths = tuple(sensitive_paths) + tuple(user_sensitive_paths)

        self._allowlist_exact, self._allowlist_patterns = self._compile_rules(allowlist)
        self._sensitive_exact, self._sensitive_patterns = self._compile_rules(
            sensitive_paths
        )

    @property
    def root(self) -> Path | None:
        """Canonical configured root, if a root boundary was configured."""

        return self._root

    @property
    def source_sending_policy(self) -> SourceSendingPolicy:
        return self._mode

    @property
    def sending_policy(self) -> SourceSendingPolicy:
        """Alias for :attr:`source_sending_policy`."""

        return self._mode

    @property
    def mode(self) -> SourceSendingPolicy:
        return self._mode

    def normalize_realpath(self, path: str | Path) -> Path:
        """Return ``path`` as an absolute canonical real path.

        This is explicit and public so integrations can normalize paths before
        displaying or auditing them.  ``strict=False`` behavior is intentional:
        a missing path can still be checked for root traversal and symlink
        escapes without opening it.
        """

        return Path(os.path.realpath(os.fspath(self._requested_path(path))))

    # Common aliases used by filesystem integrations.
    realpath = normalize_realpath
    canonicalize = normalize_realpath

    def is_path_within_root(self, path: str | Path) -> bool:
        """Return whether the canonical path is contained by ``root``."""

        if self._root is None:
            return True
        return _is_relative_to(self.normalize_realpath(path), self._root)

    def is_symlink_escape(self, path: str | Path) -> bool:
        """Return whether a path uses a symlink and resolves outside ``root``.

        With no configured root there is no project boundary to escape.  This
        method is intentionally separate from :meth:`can_send` so callers that
        only perform local analysis can explicitly enforce the same boundary.
        """

        if self._root is None:
            return False
        requested = self._requested_path(path)
        canonical = self.normalize_realpath(path)
        if _is_relative_to(canonical, self._root):
            return False
        lexical_root = self._root_lexical or self._root
        if not _is_relative_to(requested, lexical_root):
            return False
        relative_parts = requested.relative_to(lexical_root).parts
        return _contains_symlink_below(lexical_root, relative_parts)

    # An explicit verb-style alias for callers that prefer a positive check.
    rejects_symlink_escape = is_symlink_escape

    def ensure_within_root(self, path: str | Path) -> Path:
        """Normalize ``path`` or raise :class:`SecurityBoundaryError`."""

        canonical = self.normalize_realpath(path)
        if self._root is not None and not _is_relative_to(canonical, self._root):
            reason = "symlink_escape" if self.is_symlink_escape(path) else "path_outside_root"
            raise SecurityBoundaryError(canonical, reason)
        return canonical

    def can_read(
        self,
        path: str | Path,
        *,
        mode: SourceSendingPolicy | str | bool | None = None,
        source_sending_policy: SourceSendingPolicy | str | bool | None = None,
        sending_policy: SourceSendingPolicy | str | bool | None = None,
        policy: SourceSendingPolicy | str | bool | None = None,
    ) -> SecurityDecision:
        """Check local readability without applying sensitive-path blocking.

        Offline mode is not a reason to discard a locally readable sensitive
        file.  The root boundary still applies in every mode.
        """

        resolved_mode = self._resolve_mode(
            mode,
            source_sending_policy,
            sending_policy=sending_policy,
            policy=policy,
        )
        return self._evaluate(path, operation="read", mode=resolved_mode)

    def can_send(
        self,
        path: str | Path,
        *,
        mode: SourceSendingPolicy | str | bool | None = None,
        source_sending_policy: SourceSendingPolicy | str | bool | None = None,
        sending_policy: SourceSendingPolicy | str | bool | None = None,
        policy: SourceSendingPolicy | str | bool | None = None,
        online: bool | None = None,
    ) -> SecurityDecision:
        """Return an explainable decision for online model transmission.

        The result is false in offline mode because no source is sent there;
        use :meth:`can_read` to determine whether local analysis may proceed.
        """

        if online is not None:
            if any(
                value is not None
                for value in (
                    mode,
                    source_sending_policy,
                    sending_policy,
                    policy,
                )
            ):
                raise ValueError("online cannot be combined with a source sending policy")
            mode = SourceSendingPolicy.ONLINE if online else SourceSendingPolicy.OFFLINE
        resolved_mode = self._resolve_mode(
            mode,
            source_sending_policy,
            sending_policy=sending_policy,
            policy=policy,
        )
        return self._evaluate(path, operation="send", mode=resolved_mode)

    # Short aliases for integrations that call a policy check directly.
    check = can_send
    evaluate = can_send
    authorize = can_send

    def is_sensitive(self, path: str | Path) -> bool:
        """Return whether a canonical path matches built-in or user rules."""

        canonical = self.normalize_realpath(path)
        return self._is_sensitive_path(canonical)

    def is_allowlisted(self, path: str | Path) -> bool:
        """Return whether a canonical path matches an explicit allowlist."""

        canonical = self.normalize_realpath(path)
        return self._matches_rules(canonical, self._allowlist_exact, self._allowlist_patterns)

    def _resolve_mode(
        self,
        mode: SourceSendingPolicy | str | bool | None,
        source_sending_policy: SourceSendingPolicy | str | bool | None,
        *,
        sending_policy: SourceSendingPolicy | str | bool | None = None,
        policy: SourceSendingPolicy | str | bool | None = None,
    ) -> SourceSendingPolicy:
        selected_values = [
            value
            for value in (mode, source_sending_policy, sending_policy, policy)
            if value is not None
        ]
        if len(selected_values) > 1:
            raise ValueError(
                "specify only one of mode, source_sending_policy, sending_policy, or policy"
            )
        return self._mode if not selected_values else SourceSendingPolicy.coerce(selected_values[0])

    def _evaluate(
        self,
        path: str | Path,
        *,
        operation: str,
        mode: SourceSendingPolicy,
    ) -> SecurityDecision:
        requested = self._requested_path(path)
        canonical = self.normalize_realpath(path)
        display_path = str(canonical)
        symlink_escape = self.is_symlink_escape(path)
        within_root = self._root is None or _is_relative_to(canonical, self._root)
        sensitive = self._is_sensitive_path(canonical)
        allowlisted = self._matches_rules(
            canonical, self._allowlist_exact, self._allowlist_patterns
        )

        # Boundary checks are hard denials, including for an allowlisted path.
        if not within_root:
            reason = "symlink_escape" if symlink_escape else "path_outside_root"
            return SecurityDecision(
                path=display_path,
                allowed=False,
                reason=reason,
                mode=mode,
                operation=operation,
                sensitive=sensitive,
                allowlisted=allowlisted,
                within_root=False,
                symlink_escape=symlink_escape,
                requested_path=str(requested),
            )

        if not os.path.exists(canonical):
            return SecurityDecision(
                path=display_path,
                allowed=False,
                reason="path_not_found",
                mode=mode,
                operation=operation,
                sensitive=sensitive,
                allowlisted=allowlisted,
                within_root=True,
                requested_path=str(requested),
            )

        if operation == "read":
            return SecurityDecision(
                path=display_path,
                allowed=True,
                reason="offline_local_read" if mode is SourceSendingPolicy.OFFLINE else "local_read",
                mode=mode,
                operation=operation,
                sensitive=sensitive,
                allowlisted=allowlisted,
                requested_path=str(requested),
            )

        if mode is SourceSendingPolicy.OFFLINE:
            return SecurityDecision(
                path=display_path,
                allowed=False,
                reason="offline_mode",
                mode=mode,
                operation=operation,
                sensitive=sensitive,
                allowlisted=allowlisted,
                requested_path=str(requested),
            )

        if sensitive and not allowlisted:
            reason = (
                "user_sensitive_path_requires_allowlist"
                if self._matches_rules(
                    canonical, self._sensitive_exact, self._sensitive_patterns
                )
                else "sensitive_path_requires_allowlist"
            )
            return SecurityDecision(
                path=display_path,
                allowed=False,
                reason=reason,
                mode=mode,
                operation=operation,
                sensitive=True,
                allowlisted=False,
                requested_path=str(requested),
            )

        reason = "explicit_allowlist" if sensitive else "ordinary_source"
        return SecurityDecision(
            path=display_path,
            allowed=True,
            reason=reason,
            mode=mode,
            operation=operation,
            sensitive=sensitive,
            allowlisted=allowlisted,
            requested_path=str(requested),
        )

    def _requested_path(self, path: str | Path) -> Path:
        raw = Path(path).expanduser()
        if not raw.is_absolute() and self._root_lexical is not None:
            raw = self._root_lexical / raw
        # Keep lexical ``..`` components until realpath resolves symlinks.  If
        # these are normalized first, ``link/../file`` could hide a traversal
        # through a symlink and incorrectly appear to stay inside the root.
        return Path(os.fspath(raw))

    def _compile_rules(
        self,
        rules: Iterable[str | Path],
    ) -> tuple[tuple[Path, ...], tuple[str, ...]]:
        exact: list[Path] = []
        patterns: list[str] = []
        for raw_rule in rules:
            text = os.fspath(raw_rule)
            if not isinstance(text, str) or not text.strip():
                continue
            if any(character in text for character in _GLOB_CHARS):
                rule_path = self._requested_path(text)
                patterns.append(os.path.normpath(os.fspath(rule_path)))
            else:
                exact.append(Path(os.path.realpath(os.fspath(self._requested_path(text)))))
        return tuple(exact), tuple(patterns)

    def _is_sensitive_path(self, canonical: Path) -> bool:
        if self._matches_rules(canonical, self._sensitive_exact, self._sensitive_patterns):
            return True
        relative_parts = self._parts_for_detection(canonical)
        if not relative_parts:
            return False
        filename = relative_parts[-1].casefold()
        if (
            filename == ".env"
            or filename == ".envrc"
            or filename.startswith(".env.")
        ):
            return True
        if Path(filename).suffix.casefold() in _SENSITIVE_EXTENSIONS:
            return True
        if filename in _SENSITIVE_FILENAMES:
            return True
        return any(
            token in _SENSITIVE_COMPONENTS
            for component in relative_parts
            for token in _name_tokens(component)
        )

    def _parts_for_detection(self, canonical: Path) -> tuple[str, ...]:
        if self._root is not None and _is_relative_to(canonical, self._root):
            return canonical.relative_to(self._root).parts
        # Without a configured root, all non-anchor components are candidates.
        return tuple(part for part in canonical.parts if part not in {canonical.anchor, ""})

    @staticmethod
    def _matches_rules(
        canonical: Path,
        exact_rules: Sequence[Path],
        pattern_rules: Sequence[str],
    ) -> bool:
        if any(_is_relative_to(canonical, rule) for rule in exact_rules):
            return True
        candidate = os.path.normpath(os.fspath(canonical))
        return any(
            fnmatch.fnmatchcase(candidate, pattern)
            or fnmatch.fnmatchcase(candidate, pattern.rstrip(os.sep) + os.sep + "*")
            for pattern in pattern_rules
        )


def _name_tokens(component: str) -> tuple[str, ...]:
    """Split a path component at punctuation without broad substring matches."""

    token: list[str] = []
    tokens: list[str] = []
    for character in component.casefold():
        if character.isalnum():
            token.append(character)
        elif token:
            tokens.append("".join(token))
            token = []
    if token:
        tokens.append("".join(token))
    return tuple(tokens)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _contains_symlink_below(root: Path, relative_parts: Sequence[str]) -> bool:
    """Check only path components below a configured lexical root."""

    current = root
    for component in relative_parts:
        current = current / component
        try:
            if current.is_symlink():
                return True
        except OSError:
            # A later policy check will report the canonical boundary result;
            # an unreadable component must not make a symlink look safe.
            return True
    return False


class JsonlAuditRecorder:
    """Append non-secret operational decisions to a JSONL file.

    Each line contains only ``files``, ``decision`` metadata, model, timestamp,
    sanitized configuration and ``run_id``.  Source contents, prompts and
    credential-like configuration values are omitted rather than redacted into
    a value that might accidentally be mistaken for usable data.
    """

    def __init__(self, path: str | Path, *, run_id: str | None = None) -> None:
        self.path = Path(path).expanduser()
        self.run_id = run_id or uuid.uuid4().hex
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> "JsonlAuditRecorder":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Release recorder resources (the recorder does not hold a file open)."""

    def record(
        self,
        files: str | Path | Iterable[str | Path] | None = None,
        decision: SecurityDecision | bool | str | Mapping[str, Any] | None = None,
        *,
        decisions: SecurityDecision | Iterable[SecurityDecision] | None = None,
        model: str | None = None,
        timestamp: str | None = None,
        time: str | None = None,
        config: Mapping[str, Any] | Any | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Append one audit event and return the exact serialized payload.

        ``decision`` accepts a single :class:`SecurityDecision`, a boolean or a
        simple label.  ``decisions`` is useful when one model call considers
        several files; its per-file reason metadata is retained without source
        contents.
        """

        if timestamp is not None and time is not None:
            raise ValueError("specify only one of timestamp or time")
        event_time = timestamp or time or _utc_timestamp()
        file_list = _string_list(files)
        decision_items = _decision_items(decisions if decisions is not None else decision)
        if not file_list and decision_items:
            file_list = [item["path"] for item in decision_items if item.get("path")]

        overall = _overall_decision(decision, decision_items)
        payload: dict[str, Any] = {
            "files": file_list,
            "decision": overall,
            "allowed": overall == "allow",
            "reasons": [
                item["reason"] for item in decision_items if item.get("reason")
            ],
            "path_decisions": decision_items,
            "model": _safe_model(model),
            "timestamp": event_time,
            "config": _safe_config(config),
            "run_id": run_id or self.run_id,
        }
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.write("\n")
                stream.flush()
        return payload

    # Explicit aliases make call sites read naturally and keep the recorder
    # useful if an integration distinguishes policy decisions from generic events.
    record_decision = record
    record_send = record
    write = record


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _string_list(value: str | Path | Iterable[str | Path] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        return [os.fspath(value)]
    return [os.fspath(item) for item in value]


def _decision_items(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, SecurityDecision):
        return [value.to_dict()]
    if isinstance(value, Mapping):
        # Mapping decisions are accepted only as metadata and copied through a
        # strict scalar filter; arbitrary mappings could otherwise include source.
        result = {
            str(key): item
            for key, item in value.items()
            if str(key).casefold() not in _NON_SOURCE_AUDIT_KEYS
        }
        return [_safe_mapping(result)]
    if isinstance(value, bool):
        return [{"decision": "allow" if value else "deny", "allowed": value}]
    if isinstance(value, str):
        normalized = value.strip().casefold()
        allowed = normalized in {"allow", "allowed", "true", "yes"}
        return [{"decision": "allow" if allowed else "deny", "allowed": allowed}]
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        items: list[dict[str, Any]] = []
        for item in value:
            items.extend(_decision_items(item))
        return items
    return [{"decision": "deny", "allowed": False, "reason": "invalid_decision"}]


def _overall_decision(
    original: Any,
    items: Sequence[Mapping[str, Any]],
) -> str:
    if items:

        def _granted(item: Mapping[str, Any]) -> bool:
            allowed = item.get("allowed", item.get("decision") == "allow")
            if isinstance(allowed, str):
                return allowed.strip().casefold() in {"allow", "allowed", "true", "yes"}
            return bool(allowed)

        return "allow" if all(_granted(item) for item in items) else "deny"
    if isinstance(original, bool):
        return "allow" if original else "deny"
    if isinstance(original, str):
        return "allow" if original.strip().casefold() in {"allow", "allowed", "true", "yes"} else "deny"
    # A record with no decision is not an authorization grant.
    return "deny"


def _safe_model(model: Any) -> str | None:
    if model is None:
        return None
    value = str(model)
    return value[:256]


def _safe_config(config: Any) -> dict[str, Any]:
    if config is None:
        return {}
    if is_dataclass(config) and not isinstance(config, type):
        config = {field.name: getattr(config, field.name) for field in fields(config)}
    if not isinstance(config, Mapping):
        return {}
    return _safe_mapping(config)


def _safe_mapping(value: Mapping[Any, Any], *, depth: int = 0) -> dict[str, Any]:
    if depth > 5:
        return {}
    safe: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        normalized = key.casefold().replace("-", "_")
        if normalized in _NON_SOURCE_AUDIT_KEYS or normalized in _SENSITIVE_CONFIG_KEYS:
            continue
        if normalized.endswith("_key") or normalized.endswith("_token"):
            continue
        cleaned = _safe_value(raw_value, depth=depth + 1)
        if cleaned is not _OMIT:
            safe[key] = cleaned
    return safe


class _Omit:
    pass


_OMIT = _Omit()


def _safe_value(value: Any, *, depth: int) -> Any:
    if depth > 5:
        return _OMIT
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        # Configuration strings are bounded, and key-like payloads are never
        # accepted even when nested under an otherwise harmless list.
        return value[:1024]
    if isinstance(value, Mapping):
        return _safe_mapping(value, depth=depth)
    if isinstance(value, (list, tuple, set, frozenset)):
        values = [_safe_value(item, depth=depth + 1) for item in value]
        return [item for item in values if item is not _OMIT]
    return _OMIT


__all__ = [
    "JsonlAuditRecorder",
    "SecurityBoundaryError",
    "SecurityDecision",
    "SecurityPolicy",
    "SourceSendingPolicy",
]

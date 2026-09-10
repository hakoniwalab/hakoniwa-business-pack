"""Cross-platform path helpers for Recipe contract tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def canonical_path(value: str | os.PathLike[str]) -> str:
    """Return a case-normalized real path, including Windows 8.3 expansion."""
    return os.path.normcase(os.path.realpath(os.fspath(value)))


def same_path(actual: str | os.PathLike[str], expected: str | os.PathLike[str]) -> bool:
    return canonical_path(actual) == canonical_path(expected)


def iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from iter_strings(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from iter_strings(nested)


def contains_path(value: Any, expected_root: str | os.PathLike[str]) -> bool:
    """Return true when a structured value contains an absolute path under root."""
    expected = canonical_path(expected_root)
    for text in iter_strings(value):
        candidate = Path(text)
        if not candidate.is_absolute():
            continue
        actual = canonical_path(candidate)
        try:
            if os.path.commonpath([actual, expected]) == expected:
                return True
        except ValueError:
            # Different drives on Windows cannot share a common path.
            continue
    return False


def path_endswith(value: str | os.PathLike[str], *parts: str) -> bool:
    path_parts = Path(value).parts
    if len(path_parts) < len(parts):
        return False
    return tuple(path_parts[-len(parts) :]) == tuple(parts)

"""Validate untrusted JSON at the filesystem and subprocess boundaries."""

from __future__ import annotations

import json

from bitcoin_block_archive.errors import ArchiveError


def require_object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ArchiveError(f"{context} must contain an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ArchiveError(f"{context} contains a non-string key")
        result[key] = item
    return result


def load_object(text: str, context: str) -> dict[str, object]:
    try:
        payload: object = json.loads(text)
    except ValueError as error:
        raise ArchiveError(f"Invalid JSON in {context}") from error
    return require_object(payload, context)


def require_int(value: object, context: str) -> int:
    if type(value) is not int:
        raise ArchiveError(f"{context} must be an integer")
    return value

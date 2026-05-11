"""Shared path prefix normalization for app and runnable factories (FR-011)."""

from __future__ import annotations

_ASCII_WHITESPACE = " \t\n\r\x0b\x0c"

_UNRESERVED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
_SUB_DELIMS = frozenset("!$&'()*+,;=")
_PCHAR_SINGLE = _UNRESERVED | _SUB_DELIMS | frozenset(":@")
_HEX = frozenset("0123456789abcdefABCDEF")


def _normalize_prefix(prefix: str) -> str:
    """Normalize ``prefix`` to the mount path for routers (FR-011).

    Order: trim ASCII whitespace; reject if ``//`` appears anywhere; empty after trim is
    treated as ``/`` for the remaining steps; require a leading ``/``; strip trailing
    slashes until root ``/`` or no trailing slash; root ``/`` yields ``\"\"`` (no base
    segment). Non-root paths are validated for RFC 3986 ``pchar`` (including ``pct-encoded``).
    """
    s = prefix.strip(_ASCII_WHITESPACE)
    if "//" in s:
        raise ValueError("prefix must not contain '//'")
    if not s:
        s = "/"
    if not s.startswith("/"):
        raise ValueError("prefix must start with '/'")
    while len(s) > 1 and s.endswith("/"):
        s = s[:-1]
    if s == "/":
        return ""
    _validate_prefix_pchars(s)
    return s


def _validate_prefix_pchars(path: str) -> None:
    i = 0
    n = len(path)
    while i < n:
        c = path[i]
        if c == "/":
            i += 1
        elif c == "%":
            if i + 2 >= n or path[i + 1] not in _HEX or path[i + 2] not in _HEX:
                raise ValueError("prefix contains invalid character: '%'")
            i += 3
        elif c in _PCHAR_SINGLE:
            i += 1
        else:
            raise ValueError(f"prefix contains invalid character: {c!r}")

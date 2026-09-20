"""Outbound HTTP, with the URL scheme pinned.

`urllib.request.urlopen` is not a web client. It is a URL opener, and it will
happily open `file:///etc/passwd`, `ftp://`, or any scheme a handler is
registered for, returning the contents as though a server had sent them.

Every URL this project opens is assembled from configuration -- `HS_API_BASE`
out of a `.env` file, a dataset host, an Internet Archive identifier. None of
those is a boundary we control, and the consequence of getting it wrong is
that a detection "response" is really the contents of a local file. So the
scheme is checked once, here, rather than trusted four times in four modules.

This is the only place in the project that opens a socket.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

# HTTPS only. There is no case in this project for reading a local file or an
# unencrypted endpoint through the same call that fetches detection verdicts,
# and an audit trail built over plaintext is not an audit trail.
ALLOWED_SCHEMES = frozenset({"https"})

DEFAULT_TIMEOUT_S = 60.0


class UnsafeURLError(ValueError):
    """Raised when a URL's scheme is not one we are willing to open."""


def check_url(url: str) -> str:
    """Return `url` if its scheme is allowed, else raise.

    Rejecting rather than silently rewriting: a configuration that asks for
    `http://` is a mistake somebody should be told about, not one to paper
    over by upgrading it and carrying on.
    """
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(
            "Refusing to open %r: scheme %r is not in %s."
            % (str(url)[:120], parsed.scheme or "(none)",
               ", ".join(sorted(ALLOWED_SCHEMES)))
        )
    if not parsed.netloc:
        raise UnsafeURLError("Refusing to open %r: no host." % str(url)[:120])
    return url


def urlopen(request, timeout: float = DEFAULT_TIMEOUT_S):
    """`urllib.request.urlopen` with the scheme checked first.

    Accepts a `Request` or a string, and checks the URL that will actually be
    opened rather than the one that was passed in.
    """
    url = request.full_url if isinstance(
        request, urllib.request.Request) else request
    check_url(url)
    # The scheme is constrained to https immediately above, which is the
    # condition B310 exists to check.
    return urllib.request.urlopen(request, timeout=timeout)  # nosec B310

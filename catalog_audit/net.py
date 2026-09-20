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

# Nothing this project fetches as text is large: a detection verdict is a few
# kilobytes and an archive index is a few megabytes. Reading a response whole
# without a bound means the far end decides how much memory we allocate, and
# a Content-Length header is a claim rather than a constraint.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class ResponseTooLargeError(ValueError):
    """Raised when a response exceeds MAX_RESPONSE_BYTES."""


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


def is_safe_link(url: str) -> bool:
    """Whether a URL is safe to put in an href we render for someone else.

    Escaping stops an attacker closing the attribute. It does nothing about
    the scheme, so `javascript:alert(1)` survives html.escape intact and
    becomes a live link in a document somebody opens in a browser. The memo
    embeds URLs that arrive in API responses, so the scheme is checked before
    the link is written, not after.
    """
    try:
        check_url(url)
    except (UnsafeURLError, ValueError, AttributeError):
        return False
    return True


def read_capped(response, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read a response body, refusing to allocate more than `limit`.

    Reads one byte past the limit so an oversized body is detected rather
    than silently truncated, which would be worse: a JSON parse failure on a
    truncated payload looks like a schema problem and sends whoever is
    debugging in exactly the wrong direction.
    """
    data = response.read(limit + 1)
    if len(data) > limit:
        raise ResponseTooLargeError(
            "Response exceeded %d bytes and was not read." % limit)
    return data


# Headers that authenticate us, and must not follow a redirect to a host we
# did not choose to talk to.
_CREDENTIAL_HEADERS = frozenset({"authorization", "x-api-key", "api-key",
                                 "proxy-authorization", "cookie"})


class _PinnedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-check every redirect, and never carry credentials to a new host.

    The standard handler permits redirects to http, https and ftp, and copies
    the request headers onto the new request. Two consequences worth closing:

    * An https endpoint can 302 to plain http, and the Authorization header
      goes along in cleartext. Pinning only the first URL pins nothing.
    * An https endpoint can 302 to a different https host, and the bearer
      token goes with it -- the same class of bug as CVE-2018-20060.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None

        if _host(newurl) != _host(req.full_url):
            for name in list(new.headers):
                if name.lower() in _CREDENTIAL_HEADERS:
                    del new.headers[name]
            for name in list(getattr(new, "unredirected_hdrs", {})):
                if name.lower() in _CREDENTIAL_HEADERS:
                    del new.unredirected_hdrs[name]
        return new


def _host(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url))
    return (parsed.hostname or "").lower()


_OPENER = urllib.request.build_opener(_PinnedRedirectHandler)


def urlopen(request, timeout: float = DEFAULT_TIMEOUT_S):
    """Open a URL, with the scheme pinned across redirects too.

    Accepts a `Request` or a string, and checks the URL that will actually be
    opened rather than the one that was passed in.
    """
    url = request.full_url if isinstance(
        request, urllib.request.Request) else request
    check_url(url)
    # The scheme is constrained to https here and re-checked on every
    # redirect by the opener, which is the condition B310 exists to check.
    return _OPENER.open(request, timeout=timeout)  # nosec B310

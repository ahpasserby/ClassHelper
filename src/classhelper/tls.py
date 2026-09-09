"""TLS trust configuration.

The first thing this project failed on, on a real machine, was not the API --
it was certificate verification, with the message "self-signed certificate in
certificate chain". The chain was fine. The Python installed from python.org
simply ships with no trust anchors configured until you run its
`Install Certificates.command`, so *every* root looks self-signed to it.

`certifi` is therefore a hard dependency rather than a suggestion: relying on
whatever OpenSSL happens to find is how a tool works on the author's laptop and
fails on everyone else's.

The override path is real too. Campus and corporate networks terminate TLS at a
proxy and re-sign with their own CA, which by design is not in certifi. Those
users need to point at their own bundle, and they should be told so in the error
rather than left with an OpenSSL string to search for.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path

import certifi

# The two names already used by curl, requests and the wider Python ecosystem.
# Honouring them means anyone whose other tools already work here needs no
# further setup.
_ENV_VARS = ("CLASSHELPER_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


class TrustError(Exception):
    """Raised with guidance the user can act on directly."""


def create_context(ca_bundle: str | None = None) -> ssl.SSLContext:
    """An SSL context that verifies properly on a stock machine.

    Precedence: explicit configuration, then the standard environment
    variables, then certifi.
    """
    path = ca_bundle or next(
        (os.environ[name] for name in _ENV_VARS if os.environ.get(name)), None
    )

    if path:
        if not Path(path).exists():
            raise TrustError(
                f"CA bundle {path!r} does not exist. Fix ca_bundle in "
                "~/.classhelper/config.toml, or unset it to use the built-in "
                "certificate store."
            )
        return ssl.create_default_context(cafile=path)

    return ssl.create_default_context(cafile=certifi.where())


def explain(error: ssl.SSLError | Exception) -> str:
    """Turn a verification failure into something actionable.

    Almost always a TLS-intercepting proxy, which the user can confirm in
    seconds and fix in one line -- but only if told what to look for.
    """
    return (
        f"TLS certificate verification failed ({error}).\n\n"
        "This usually means a proxy on your network is intercepting HTTPS and "
        "re-signing it with its own certificate authority.\n\n"
        "If that is expected, export its CA certificate and point classhelper "
        "at it:\n\n"
        "  [translate]\n"
        '  ca_bundle = "/path/to/your-proxy-ca.pem"\n\n'
        "in ~/.classhelper/config.toml, or set SSL_CERT_FILE in the "
        "environment."
    )

# httpclient.py
#
# Copyright 2026 Bottles Contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, in version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""The one HTTP client Bottles uses.

Every outbound request goes through here so the user agent, proxy handling,
redirect policy and TLS trust store are decided once. requests is the
backend: it honours HTTP(S)_PROXY and NO_PROXY in either case and SOCKS
proxies through PySocks, which neither pycurl's manual proxy option nor
urllib did.
"""

import os
import threading
from typing import Callable, Optional
from urllib.parse import urlsplit
from urllib.request import url2pathname

import requests

from bottles.backend.params import APP_VERSION

USER_AGENT = f"Bottles/{APP_VERSION}"

# The runtime maintains this bundle and it is where a user's own CA (a
# corporate proxy, a self-signed mirror) is installed; certifi's copy is the
# fallback when a host has no system bundle.
SYSTEM_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"

# (connect, read) seconds, per caller, unchanged from what each used before.
CATALOG_TIMEOUT = (10, 30)
INDEX_TIMEOUT = (5, 10)
PING_TIMEOUT = (5, 10)
DOWNLOAD_TIMEOUT = (10, 30)
ARTWORK_TIMEOUT = (10, 10)
UMU_DATABASE_TIMEOUT = (15, 15)
ISSUES_TIMEOUT = (10, 10)
ICON_TIMEOUT = (10, 10)

# Callers catch these rather than naming the backend library.
HttpError = requests.RequestException
HttpStatusError = requests.HTTPError
SSLError = requests.exceptions.SSLError

CHUNK_SIZE = 64 * 1024


class Cancelled(Exception):
    """A transfer stopped because its cancel callback asked for it."""


def ca_bundle():
    return SYSTEM_CA_BUNDLE if os.path.exists(SYSTEM_CA_BUNDLE) else True


def new_session() -> requests.Session:
    """A session carrying Bottles' policy, for callers that need their own."""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.verify = ca_bundle()
    return session


_shared: Optional[requests.Session] = None
_shared_lock = threading.Lock()


def session() -> requests.Session:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = new_session()
        return _shared


def get(url: str, **kwargs) -> requests.Response:
    """A GET on the shared session; the seam tests replace."""
    return session().get(url, **kwargs)


def fetch(
    url: str,
    *,
    timeout,
    max_bytes: Optional[int] = None,
    cancel: Optional[Callable[[], bool]] = None,
    headers: Optional[dict] = None,
) -> bytes:
    """Return a response body, or raise.

    HttpStatusError for an HTTP error status, HttpError for any transport
    failure including timeouts, Cancelled when `cancel` returns True between
    chunks, ValueError when the body exceeds max_bytes, OSError for a
    missing file:// source. file:// is served by reading the path, which
    libcurl did natively and the personal repository feature relies on.
    """
    if url.startswith("file://"):
        with open(url2pathname(urlsplit(url).path), "rb") as source:
            data = source.read(max_bytes + 1 if max_bytes is not None else -1)
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError(f"{url} is larger than {max_bytes} bytes")
        return data

    with get(url, stream=True, timeout=timeout, headers=headers, allow_redirects=True) as response:
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_content(CHUNK_SIZE):
            if cancel is not None and cancel():
                raise Cancelled(url)
            body += chunk
            if max_bytes is not None and len(body) > max_bytes:
                raise ValueError(f"{url} is larger than {max_bytes} bytes")
        return bytes(body)


def head_ok(url: str, *, timeout, cancel: Optional[Callable[[], bool]] = None) -> bool:
    """True when a HEAD request, following redirects, ends in HTTP 200."""
    if cancel is not None and cancel():
        raise Cancelled(url)
    response = session().head(url, timeout=timeout, allow_redirects=True)
    response.close()
    return response.status_code == 200

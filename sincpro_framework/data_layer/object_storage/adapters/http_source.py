"""`HttpSource`: the bytes of a URL as its server sends them, read by a store — the standard library's
`urllib`, a redirect followed only when told and each hop judged, the bytes counted against a size
and a time as they come.

    body = HttpSource().open("https://cdn.acme.bo/scan.pdf", timedelta(seconds=30), {}, False,
                             judged, max_bytes=50 * MB)
    body.content_type            ->  "application/pdf"
    store.put(key, io.BufferedReader(body), …)     read to its end, or refused on the way

Context: what `urllib` raises is said in object storage's words — a redirect not followed or a
client error (4xx) is `FetchRefused`, a server error (5xx), a host that cannot be reached or a time
run out is `FetchFailed` — so the service judges a fetch without knowing HTTP. A refusal raised
while a store reads is also kept on the body (`refusal`): a store that wraps what its reader raised
(a bucket's `ExternalServiceError`) is seen through.
"""

import io
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from sincpro_framework.data_layer.object_storage.domain.exceptions import (
    FetchFailed,
    FetchRefused,
)
from sincpro_framework.data_layer.object_storage.domain.fetch import FetchRefusal


class Redirects(urllib.request.HTTPRedirectHandler):
    """A redirect followed only when told, each new URL judged first."""

    def __init__(self, follow: bool, judged: Callable[[str], None]) -> None:
        """`follow` the redirects or not; `judged` raises for a URL the fetch does not take."""
        super().__init__()
        self.follow = follow
        self.judged = judged

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        """The request to the new URL once it is judged — none when redirects are not followed,
        which `urllib` answers as the redirect's own `HTTPError`."""
        if not self.follow:
            return None
        self.judged(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HttpBody(io.RawIOBase):
    """A URL's bytes as they come, refused past `max_bytes` or once its time ran out."""

    def __init__(
        self, response: Any, url: str, max_bytes: int | None, deadline: float
    ) -> None:
        """`response` the server's, of `url`; `deadline` on `time.monotonic()`."""
        super().__init__()
        self.response = response
        self.url = url
        self.max_bytes = max_bytes
        self.deadline = deadline
        self.received = 0
        self.refusal: FetchRefused | FetchFailed | None = None
        self.content_type: str | None = response.headers.get("Content-Type")

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        """The next bytes into `buffer` — `0` at the end.

        1. Past its deadline: `FetchFailed`.
        2. What has arrived, up to the buffer's size — never waiting for it to fill, so the
           deadline is judged between every part a slow server sends; a connection that fails
           or times out while reading: `FetchFailed`.
        3. Past `max_bytes`: `FetchRefused` (`TOO_LARGE`).
        Final: what was read; a refusal is kept in `refusal` before it is raised.
        """
        if time.monotonic() > self.deadline:
            self.refusal = FetchFailed(self.url, f"{self.url} did not send its bytes in time")
            raise self.refusal
        view = memoryview(buffer).cast("B")
        try:
            part = self.response.read1(len(view))
        except OSError as error:
            self.refusal = FetchFailed(self.url, f"{self.url} could not be read: {error}")
            raise self.refusal from error
        read = len(part)
        view[:read] = part
        self.received += read
        if self.max_bytes is not None and self.received > self.max_bytes:
            self.refusal = FetchRefused(
                FetchRefusal.TOO_LARGE,
                self.url,
                f"{self.url} sends more than {self.max_bytes} bytes",
            )
            raise self.refusal
        return read

    def close(self) -> None:
        """Close the server's response with it."""
        if not self.closed:
            self.response.close()
        super().close()


class HttpSource:
    """URLs opened with the standard library."""

    def open(
        self,
        url: str,
        timeout: timedelta,
        headers: Mapping[str, str],
        follow_redirects: bool,
        judged: Callable[[str], None],
        max_bytes: int | None = None,
    ) -> HttpBody:
        """The body of `url`, asked with `headers`, for a store to read — within `timeout` in
        all, each read of the connection too.

        1. Opened with `follow_redirects`, each redirect's URL `judged` first.
        2. A redirect not followed or a client error (4xx): `FetchRefused`; a server error
           (5xx), a host not reached, a time run out: `FetchFailed`.
        3. A `Content-Length` past `max_bytes`: `FetchRefused` before a byte is read.
        Final: the body, counted against `max_bytes` and the time left as it is read.
        """
        deadline = time.monotonic() + timeout.total_seconds()
        opener = urllib.request.build_opener(Redirects(follow_redirects, judged))
        try:
            response = opener.open(
                urllib.request.Request(url, headers=dict(headers)),
                timeout=timeout.total_seconds(),
            )
        except urllib.error.HTTPError as error:
            error.close()
            if 300 <= error.code < 400:
                raise FetchRefused(
                    FetchRefusal.REDIRECTED,
                    url,
                    f"{url} answered a redirect ({error.code}) to {error.headers.get('Location')}",
                    error.code,
                ) from error
            if error.code < 500:
                raise FetchRefused(
                    FetchRefusal.ANSWERED, url, f"{url} answered {error.code}", error.code
                ) from error
            raise FetchFailed(url, f"{url} answered {error.code}", error.code) from error
        except (urllib.error.URLError, OSError) as error:
            raise FetchFailed(url, f"{url} could not be reached: {error}") from error
        declared = response.headers.get("Content-Length")
        if (
            max_bytes is not None
            and declared
            and declared.isdigit()
            and int(declared) > max_bytes
        ):
            response.close()
            raise FetchRefused(
                FetchRefusal.TOO_LARGE,
                url,
                f"{url} sends {declared} bytes, more than {max_bytes}",
            )
        return HttpBody(response, url, max_bytes, deadline)

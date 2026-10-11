"""Per-host wire bytes from Chrome's own Network domain.

Why this exists: the pcap reference has to guess which peer IPs carried the
media, because pcap sees addresses and a media flow is usually named only by a
TLS SNI that QUIC-only connections never send. Measured over 13 runs, no
peer-selection rule put every run in band -- single-dominant-peer fell BELOW
1.00 twice, which is impossible for a complete reference.

Chrome does not have to guess: it knows the URL of every request it made. This
reads `encodedDataLength` per request and groups by hostname, which is the same
quantity the player appended plus response headers, and nothing else.

Two figures are kept deliberately, because their difference is informative:

  finished_bytes  from Network.loadingFinished.encodedDataLength
  received_bytes  from summed Network.dataReceived.encodedDataLength

`loadingFinished` fires only when a response completes. A response still open
when the run ends contributes to `received_bytes` and NOT to `finished_bytes`,
so a gap between them is evidence of exactly that -- which is the untested
candidate for YouTube's Resource Timing shortfall.

The bands these figures are judged against were committed in THRESHOLDS.md
before any of this data existed.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import urlparse


def host_of(url: str) -> Optional[str]:
    try:
        h = (urlparse(url).hostname or "").lower()
    except Exception:
        return None
    return h or None


def matches(host: Optional[str], suffixes: tuple[str, ...]) -> bool:
    """Suffix match on label boundaries, so `evilgooglevideo.com` cannot pass."""
    if not host:
        return False
    for s in suffixes:
        s = s.lower().lstrip(".")
        if host == s or host.endswith("." + s):
            return True
    return False


class CdpByteLedger:
    """Accumulates per-request bytes from CDP Network events.

    Fed one decoded CDP message at a time via `feed`, in any order: a
    `dataReceived` can arrive before the `requestWillBeSent` that names its
    URL (it does not normally, but the performance log is a buffer and a
    reordering must not silently drop bytes). Unresolved request ids are kept
    and attributed once the URL is learned.
    """

    def __init__(self) -> None:
        self.url_of: dict[str, str] = {}
        self.received: dict[str, int] = {}
        self.finished: dict[str, int] = {}
        self.finished_seen: set[str] = set()
        # Events the log dropped are invisible by definition; what IS visible
        # is a byte event for a request whose URL never arrived. Counting those
        # keeps an undercount detectable instead of silent.
        self.events = 0
        self.ignored_events = 0

    def feed(self, msg: dict[str, Any]) -> None:
        method = msg.get("method") or ""
        p = msg.get("params") or {}
        rid = p.get("requestId")
        if not method.startswith("Network."):
            return
        self.events += 1
        if method == "Network.requestWillBeSent":
            url = ((p.get("request") or {}).get("url")) or ""
            if rid and url:
                self.url_of[rid] = url
            # A redirect reuses the request id; the final URL is what counts.
            elif rid and p.get("redirectResponse"):
                pass
        elif method == "Network.responseReceived":
            url = ((p.get("response") or {}).get("url")) or ""
            if rid and url:
                self.url_of.setdefault(rid, url)
        elif method == "Network.dataReceived":
            n = p.get("encodedDataLength")
            if rid and isinstance(n, (int, float)) and n > 0:
                self.received[rid] = self.received.get(rid, 0) + int(n)
            else:
                self.ignored_events += 1
        elif method == "Network.loadingFinished":
            n = p.get("encodedDataLength")
            if rid and isinstance(n, (int, float)) and n >= 0:
                self.finished[rid] = int(n)
                self.finished_seen.add(rid)
            else:
                self.ignored_events += 1
        else:
            self.events -= 1  # not an event we account for

    def totals(self, suffixes: tuple[str, ...]) -> dict[str, Any]:
        fin = rec = 0
        n_fin = n_rec = 0
        unresolved_bytes = 0
        unresolved_ids = 0
        open_bytes = 0
        open_ids = 0
        for rid, n in self.received.items():
            url = self.url_of.get(rid)
            if url is None:
                unresolved_bytes += n
                unresolved_ids += 1
                continue
            if not matches(host_of(url), suffixes):
                continue
            rec += n
            n_rec += 1
            if rid not in self.finished_seen:
                open_bytes += n
                open_ids += 1
        for rid, n in self.finished.items():
            url = self.url_of.get(rid)
            if url is None:
                unresolved_ids += 1
                continue
            if not matches(host_of(url), suffixes):
                continue
            fin += n
            n_fin += 1
        return {
            "cdp_finished_bytes": fin,
            "cdp_received_bytes": rec,
            "cdp_finished_requests": n_fin,
            "cdp_received_requests": n_rec,
            # Requests that delivered bytes but never finished. If
            # cdp_received_bytes exceeds cdp_finished_bytes, this is the amount
            # by which, and it is the direct test of the never-finished-response
            # explanation for a Resource Timing shortfall.
            "cdp_open_at_end_bytes": open_bytes,
            "cdp_open_at_end_requests": open_ids,
            "cdp_unresolved_bytes": unresolved_bytes,
            "cdp_unresolved_requests": unresolved_ids,
            "cdp_events_seen": self.events,
            "cdp_events_ignored": self.ignored_events,
        }


def drain_performance_log(driver: Any, ledger: CdpByteLedger) -> int:
    """Pull buffered CDP messages out of the performance log into `ledger`.

    Must be called periodically during the run, not only at the end: the log is
    a bounded buffer and Chrome discards the oldest entries once it is full, so
    a 180 s run read once at teardown can lose events with no error raised.
    Returns how many messages were consumed.
    """
    try:
        entries = driver.get_log("performance")
    except Exception:
        return 0
    n = 0
    for e in entries or []:
        try:
            msg = json.loads(e.get("message") or "{}").get("message") or {}
        except Exception:
            continue
        ledger.feed(msg)
        n += 1
    return n

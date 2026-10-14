"""An in-page event log, drained over a CDP connection of its own.

Why this exists. The normal sampler asks the page for its state once a second
over the WebDriver session. On the concurrent YouTube cell that read sometimes
blocks for 14-24 s, which breaks the sampling cadence and tells us nothing
about what the page was doing meanwhile -- the reading that would have told us
is the reading that failed to arrive.

This module separates the recording from the reading:

  * The page records itself. A timer installed at document start appends a
    small entry every INPAGE_TICK_MS with `performance.now()` on it. If the
    page's main thread stalls, that timer cannot run, so the stall is visible
    as a gap in the page's own clock. A requestAnimationFrame counter runs
    alongside it for the same reason.

  * The reading happens on a separate CDP websocket with a hard deadline, not
    through the WebDriver session. A read that times out is recorded as a
    timed-out read and the connection is rebuilt; it cannot stretch a sampling
    interval the way a blocking `execute_script` does.

Together those two tell apart the case the single sampler cannot:

    in-page clock gap AND read timed out -> the page itself was blocked
    in-page clock continuous, read timed out -> only the read path was blocked

`performance.now()` keeps advancing while a thread is blocked, so a *timestamp*
alone never shows a stall. What shows it is a missing entry: the timer did not
get to run. That is why the page writes a log instead of being asked the time.
"""

import json
import threading
import time

try:
    import websocket  # websocket-client
except Exception:  # pragma: no cover - surfaced by the caller
    websocket = None

# How often the page appends an entry to its own log. Fine enough that a
# one-second stall is unmistakable, coarse enough not to be a load of its own.
INPAGE_TICK_MS = 200

# A stall is called when the page's clock jumps by more than this between
# consecutive entries. Three ticks, so ordinary timer jitter does not qualify.
INPAGE_STALL_MS = INPAGE_TICK_MS * 3

INPAGE_LOGGER_JS = """
(function () {
  if (window.__pramana_log) { return; }
  var buf = [];
  var MAX = 40000;          // ~2.2 h at 200 ms; the reader keeps it far below
  var dropped = 0;
  var seq = 0;              // never reset; the reader's cursor refers to it
  var raf = 0;
  var started = (performance && performance.now) ? performance.now() : 0;

  function now() { return (performance && performance.now) ? performance.now() : 0; }

  function rec(kind) {
    try {
      var e = { i: ++seq, t: Math.round(now() * 10) / 10, k: kind, r: raf };
      var v = document.querySelector('video');
      if (v) {
        e.ct = v.currentTime;
        e.p = v.paused ? 1 : 0;
        e.w = v.videoWidth;
        e.h = v.videoHeight;
        if (v.buffered && v.buffered.length) {
          e.be = v.buffered.end(v.buffered.length - 1);
        }
        try {
          var q = v.getVideoPlaybackQuality();
          e.tf = q.totalVideoFrames;
          e.df = q.droppedVideoFrames;
        } catch (_) {}
      }
      if (buf.length < MAX) { buf.push(e); } else { dropped++; }
    } catch (_) {}
  }

  // The page's own heartbeat. A blocked main thread cannot service this, so a
  // missing entry is the evidence we are after.
  var timer = setInterval(function () { rec('tick'); }, __TICK_MS__);

  // rAF stops entirely while the thread is blocked, and is throttled when the
  // tab is hidden, so it corroborates the timer without being trusted alone.
  (function loop() {
    raf++;
    try { requestAnimationFrame(loop); } catch (_) {}
  })();

  // Long tasks. A freeze that contains a long task means the thread was
  // RUNNING something; a freeze with none means no JS task was on the thread
  // at all. That distinction is the point: it separates "the page is busy"
  // from "the page is not being scheduled". Note a task that is descheduled
  // mid-flight still accumulates wall-clock duration, so a long entry is not
  // by itself proof of work -- it is read together with the host-side CPU
  // probe, which says whether the process got any CPU during the window.
  try {
    var po = new PerformanceObserver(function (list) {
      list.getEntries().forEach(function (en) {
        try {
          var a = (en.attribution && en.attribution[0]) || {};
          if (buf.length < MAX) {
            buf.push({
              i: ++seq,
              t: Math.round(en.startTime * 10) / 10,
              k: 'longtask',
              d: Math.round(en.duration * 10) / 10,
              n: en.name || null,
              ct: a.containerType || null,
              cs: a.containerSrc || null
            });
          }
        } catch (_) {}
      });
    });
    po.observe({ entryTypes: ['longtask'] });
    window.__pramana_longtask = 1;
  } catch (e) { window.__pramana_longtask = 0; }

  // Player events, which carry their own meaning and are not sampled.
  try {
    var v0 = document.querySelector('video');
    var hook = function (v) {
      if (!v || v.__pramana_hooked) { return; }
      v.__pramana_hooked = 1;
      ['waiting', 'playing', 'stalled', 'pause', 'play', 'seeking',
       'seeked', 'ratechange', 'resize', 'ended', 'error'].forEach(function (n) {
        try { v.addEventListener(n, function () { rec(n); }); } catch (_) {}
      });
    };
    hook(v0);
    // The element can be replaced after load, so keep looking for a new one.
    setInterval(function () {
      try { hook(document.querySelector('video')); } catch (_) {}
    }, 1000);
  } catch (_) {}

  // Reading must not destroy what the reader has not received. A read whose
  // reply is lost to a timeout still RUNS on the page once the thread frees, so
  // a destructive drain would delete precisely the entries proving the thread
  // was blocked. Instead the reader passes the last sequence number it holds:
  // only that acknowledges delivery. Entries at or below it are dropped,
  // everything above is returned and kept until a later read acknowledges it.
  window.__pramana_log = {
    read: function (from) {
      from = from || 0;
      while (buf.length && buf[0].i <= from) { buf.shift(); }
      var d = dropped; dropped = 0;
      return {
        events: buf.slice(0), dropped: d, raf: raf,
        started: started, now: now(), seq: seq
      };
    },
    pending: function () { return buf.length; },
    stop: function () { try { clearInterval(timer); } catch (_) {} }
  };
  rec('installed');
})();
""".replace("__TICK_MS__", str(INPAGE_TICK_MS))


def read_js(from_seq: int) -> str:
    return (
        "JSON.stringify((window.__pramana_log && window.__pramana_log.read(%d)) || null)"
        % int(from_seq)
    )


class CdpTimeout(Exception):
    """A CDP read did not answer inside its own deadline."""


class CdpChannel:
    """One websocket to a page target, with a deadline the caller sets.

    Deliberately not the WebDriver session: a read that blocks here is bounded
    and discarded, instead of holding up the sampling loop. After a timeout the
    socket is thrown away, because the late reply would otherwise arrive against
    the next request's id.
    """

    def __init__(self, ws_url: str, connect_timeout_s: float = 5.0):
        if websocket is None:
            raise RuntimeError("websocket-client is not installed")
        self.ws_url = ws_url
        self.connect_timeout_s = connect_timeout_s
        self._ws = None
        self._id = 0
        self._lock = threading.Lock()
        self.timeouts = 0
        self.reconnects = 0
        # Last sequence number actually received. Only a reply that reaches us
        # moves this, so a timed-out read costs a repeat, never data.
        self.seq = 0

    def connect(self):
        self.close()
        self._ws = websocket.create_connection(
            self.ws_url,
            timeout=self.connect_timeout_s,
            max_size=64 * 1024 * 1024,
            suppress_origin=True,
        )
        return self

    def close(self):
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def evaluate(self, expression: str, timeout_s: float):
        """Runtime.evaluate with a hard wall-clock deadline.

        Raises CdpTimeout if the answer has not arrived by then. The deadline is
        enforced against the clock, not against one socket read, so a dribble of
        unrelated CDP events cannot extend it.
        """
        with self._lock:
            if self._ws is None:
                self.connect()
            self._id += 1
            want = self._id
            msg = {
                "id": want,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": False,
                },
            }
            deadline = time.monotonic() + timeout_s
            try:
                self._ws.send(json.dumps(msg))
                while True:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    self._ws.settimeout(max(0.02, left))
                    try:
                        raw = self._ws.recv()
                    except Exception:
                        break
                    try:
                        got = json.loads(raw)
                    except ValueError:
                        continue
                    if got.get("id") != want:
                        continue  # an event, or a reply we no longer want
                    res = (got.get("result") or {}).get("result") or {}
                    if got.get("result", {}).get("exceptionDetails"):
                        raise RuntimeError(str(got["result"]["exceptionDetails"])[:200])
                    return res.get("value")
            except CdpTimeout:
                raise
            except Exception:
                pass
            # Timed out (or the socket broke): drop it, so a late reply cannot
            # be mistaken for the answer to the next question.
            self.timeouts += 1
            self.reconnects += 1
            try:
                self.close()
            except Exception:
                pass
            raise CdpTimeout("no reply to Runtime.evaluate within %.2fs" % timeout_s)

    def read(self, timeout_s: float):
        """Return the page's un-acknowledged events, or raise CdpTimeout.

        Idempotent: on timeout the cursor does not move, so the next read
        returns the same entries plus whatever arrived meanwhile.
        """
        raw = self.evaluate(read_js(self.seq), timeout_s)
        if not raw:
            return None
        try:
            got = json.loads(raw)
        except ValueError:
            return None
        if got and got.get("events"):
            self.seq = max(
                [self.seq]
                + [e["i"] for e in got["events"] if isinstance(e.get("i"), int)]
            )
        return got


def page_ws_url(debugger_address: str, match: str = None, timeout_s: float = 5.0):
    """Find the websocket URL of a page target on an existing Chrome.

    `debugger_address` is host:port, e.g. the value chromedriver reports in
    capabilities as goog:chromeOptions.debuggerAddress.
    """
    import urllib.request

    url = "http://%s/json" % debugger_address
    with urllib.request.urlopen(url, timeout=timeout_s) as fh:
        targets = json.loads(fh.read().decode("utf-8", "replace"))
    pages = [
        t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")
    ]
    if match:
        hit = [t for t in pages if match in (t.get("url") or "")]
        if hit:
            pages = hit
    if not pages:
        raise RuntimeError("no page target at %s" % debugger_address)
    return pages[0]["webSocketDebuggerUrl"]


def stall_report(
    events, prev_t=None, tick_ms: int = INPAGE_TICK_MS, stall_ms: int = INPAGE_STALL_MS
):
    """Gaps in the page's own clock, i.e. ticks the page failed to run.

    `prev_t` is the last tick timestamp the caller saw in an EARLIER read, and
    it matters: once a read is acknowledged those entries are gone from the
    page, so a stall that begins just after one read and ends in the next spans
    a boundary with no surviving pair to compare. Without it such a stall --
    which is the common shape, since a blocked thread is what delays the read --
    is silently invisible. Pass it and the boundary is covered.

    Only 'tick' entries count: player events fire when they fire, and their
    spacing says nothing about whether the thread was free.
    """
    ticks = sorted(
        e["t"]
        for e in events
        if e.get("k") == "tick" and isinstance(e.get("t"), (int, float))
    )
    gaps = []
    seq = ([prev_t] if isinstance(prev_t, (int, float)) else []) + ticks
    for i in range(1, len(seq)):
        d = seq[i] - seq[i - 1]
        if d > stall_ms:
            gaps.append(
                {
                    "at_page_ms": round(seq[i - 1], 1),
                    "gap_ms": round(d, 1),
                    "across_reads": i == 1 and prev_t is not None,
                }
            )
    longs = sorted(
        (
            {"at_page_ms": e.get("t"), "dur_ms": e.get("d"), "name": e.get("n")}
            for e in events
            if e.get("k") == "longtask" and isinstance(e.get("d"), (int, float))
        ),
        key=lambda x: -x["dur_ms"],
    )
    return {
        "ticks": len(ticks),
        "longtasks": len(longs),
        "longest_task_ms": round(longs[0]["dur_ms"], 1) if longs else 0.0,
        "top_longtasks": longs[:5],
        "page_span_ms": round(ticks[-1] - ticks[0], 1) if len(ticks) > 1 else 0.0,
        "max_gap_ms": round(
            max((g["gap_ms"] for g in gaps), default=float(tick_ms)), 1
        ),
        "stalls": gaps,
        "last_tick_t": ticks[-1] if ticks else prev_t,
    }

# Why some events happen before the first point on a chart

Measured on YouTube at 1 Mbps, and it affects how every time-series chart here
should be read.

## What happens, in order

1. The probe is installed at **document-start**, before any page script runs,
   so its event log covers the page from the very beginning.
2. The page loads. At 1 Mbps this is slow: `DOMContentLoaded` was measured at
   **41.1 s** on one run.
3. **YouTube autoplays on its own**, before the collector's synchronized start.
   First presented frame was at page t = **20.5 s**.
4. The collector waits for the app to be ready, passes the multi-app sampling
   barrier, calls `start_playback()`, and only then begins the per-second
   series. On that run the **first QoE sample was at page t = 52.4 s**.

So the per-second series began ~32 s after the first frame was already on
screen.

## What this means for each metric

| metric | covers the whole session? | why |
|---|---|---|
| `startup_delay_ms` | **yes** | from the document-start probe's own clock |
| `initial_buffering_ms` | **yes** | from the media-event log |
| `rebuffer_events` / `rebuffer_duration_ms` | **yes** | from `waiting`→`playing` events, logged from document-start |
| `switch_count`, init-segment configs | **yes** | `appendBuffer` is hooked from document-start |
| buffer level series | **no** — starts at the first sample | sampled, not evented |
| resolution series | **no** | sampled |
| delivered-bitrate series | **no** | derived from sampled cumulative byte counts |
| presented-fps series | **no** | derived from sampled frame counts |

The run totals are therefore **more complete than the charts**. On the 1 Mbps
run above, one real rebuffer (18.1 s, starting at `currentTime` 11.9 s) occurred
at page t = 32.6-50.7 s — entirely before the first sample. It is counted in
`rebuffer_events`, and `qoe_buffer.png` carries a caption saying so rather than
showing an unshaded chart that would read as "no rebuffering".

## Why it was not "fixed" by sampling earlier

Sampling is deliberately started after the synchronized barrier so that, in a
multi-app run, every app's series shares one start instant. Moving the start
earlier would break that comparability. The event log already covers the gap,
so the honest fix is to say which metrics are evented and which are sampled —
which is what the table above does — rather than to quietly change the window.

# Validation thresholds — fixed before any Step 3 data was collected

Committed ahead of the runs deliberately. Choosing a tolerance after seeing the
numbers is how a validation passes itself.

## References, and how strong each one is

| Metric | Reference | Strength | Why |
|---|---|---|---|
| Buffer level | `sfn_buffer_health_seconds` | **strong** | YouTube's own buffer figure, in seconds, same quantity as ours, read at the same 1 s cadence |
| Decoded frames / fps | `sfn_dims_and_frames` ("N dropped of M") | **strong** | YouTube's own cumulative decoded-frame count |
| Delivered video bitrate | per-app pcap bytes over the same window | **medium** | different layer: pcap carries TCP/TLS/HTTP framing and both tracks, so this is a bounded-ratio check, not equality |
| Rebuffer events | `sfn_player_state == 3` (IFrame BUFFERING) episodes | **medium** | vendor state machine, 1 s sampled, so brief stalls can fall between samples |
| Switch events | `sfn_codecs` itag changes | **medium** | itag identifies the rendition, so a change is a real switch, but 1 s sampling can merge two fast switches |
| Startup delay | pcap first media byte → first presented frame | **weak** | no in-page reference exists; the two clocks are different and the pcap start includes manifest/DNS |
| Rendered fps (`presentedFrames`) | **none** | — | stats-for-nerds counts decoded frames only; there is no vendor figure for frames submitted for composition |

## Pass/fail, per metric

1. **Buffer level** — PASS if MAE ≤ **1.0 s** and Pearson r ≥ **0.90**.
   One sampling interval of skew is expected between two independent readers.
2. **Decoded frame count** — PASS if the per-sample match rate (|Δ| ≤ **2
   frames**) is ≥ **90 %** and MAE ≤ **2.0 frames**.
   Two frames is under one sampling interval at 24 fps.
3. **Delivered video bitrate vs pcap** — PASS if
   `pcap_bytes / (mse_video + mse_audio)` lies in **[1.00, 1.25]**.
   Lower bound 1.00: the wire cannot carry fewer bytes than were appended.
   Upper bound 1.25: TCP/IP + TLS + HTTP framing on large segments, plus
   manifests and the player page itself, should stay under 25 %.
4. **Rebuffer events** — PASS if |our count − SFN buffering episodes| ≤ **1**
   per run. A 1 s-sampled state machine can miss a sub-second stall.
5. **Switch events** — PASS if |our count − itag changes| ≤ **1** per run.
6. **Startup delay** — **reported, not thresholded.** The reference is too weak
   to fail a measurement against; it is quoted with its own basis instead.
7. **Rendered fps** — **reported, no reference.** Cross-checked only for
   internal consistency: it must not exceed decoded fps by more than 5 %,
   since a frame cannot be presented without being decoded.

## Run matrix

Solo, 50 ms, pfifo / cubic / 0 % loss, 180 s, one trial per cell.
Ladder: **0.5, 1, 1.5, 3, 6, 10 Mbps**. YouTube is expected to sit at 720p from
about 3 Mbps up, so switching behaviour should appear at the low rungs.

Plus one **step profile**: 10 → 1 → 10 Mbps, 60 s per leg, which is confirmed
feasible — `POST /shape` re-applies tc on the live interface, verified by
driving it 10 → 1 → 10 → 3 and reading `tc class show` back each time.

`merge_vendor_stats` is **off** for every validation run, so stats-for-nerds
stays in `sfn_*` and never reaches the generic channel it is validating.

## Disclosed limits

The VM is 2 vCPU. Any cell where host CPU demand approaches saturation is
labelled VM-limited rather than read as a network result.

---

# Addendum, 2026-10-10 — two independent byte references for YouTube

Written and committed **before any CDP or NetLog data existed**, and before any
of the runs it governs were started. The reason is on the record: the previous
pcap reference was tuned against the same 13 runs it was judged on, which is
how a reference ends up fitting the data instead of testing it. Nothing in this
section may be edited once the first run completes; a failure under it is a
failure, to be reported as one.

## Why two more references

The existing pcap reference needs a rule to decide which peer IPs carried the
media. Over 13 runs no such rule passed: single-dominant-peer fails low on 2
runs (0.9855, 0.8583 — impossible for a complete reference), and adding the
next UDP peer fixes those but pushes a third run to 1.279. The defect is that
pcap sees addresses, not hosts, so the mapping has to be guessed whenever TLS
SNI is absent — which on YouTube is 5 runs out of 6 (measured).

Both new references avoid that guess entirely: each is told the hostname by
Chrome itself.

## Reference A — CDP Network domain

**Procedure.** Enable the Network domain over CDP for the collector's own
Chrome before navigation. Record `Network.requestWillBeSent` (for `request.url`)
and `Network.loadingFinished` (for `encodedDataLength`), keyed by `requestId`.
Also accumulate `Network.dataReceived.encodedDataLength` per `requestId`, which
is reported as bytes arrive rather than only at completion.

**Figure under test.** Sum of bytes over requests whose URL host ends in
`googlevideo.com`, as:
- `cdp_finished_bytes` — from `loadingFinished.encodedDataLength`
- `cdp_received_bytes` — from summed `dataReceived.encodedDataLength`

Both are recorded. They are expected to be close; where they differ, the
difference is itself the finding (a request that never finishes contributes to
the second and not the first).

**Layer.** `encodedDataLength` is on-the-wire body bytes including response
headers and transfer encoding, and excludes TCP/IP/TLS/QUIC framing. It is
therefore *closer* to appended bytes than pcap is, so its band is tighter.

## Reference B — Chrome NetLog

**Procedure.** Launch Chrome with
`--log-net-log=<path> --net-log-capture-mode=Default`. After the run, parse the
JSON event log and join, per connection/session:
- `HTTP2_SESSION` / `QUIC_SESSION` events carrying `host` or `origin`
- byte-accounting events on that session
  (`QUIC_SESSION_PACKET_RECEIVED` lengths, `HTTP2_SESSION_RECV_DATA` /
  `HTTP3_...` frame payload lengths, and `URL_REQUEST_JOB_BYTES_READ` totals)

**Figure under test.** `netlog_body_bytes` = sum of response-body bytes on
sessions whose host ends in `googlevideo.com`. Recorded separately,
`netlog_session_bytes` = sum of QUIC/H2 packet-level bytes on those sessions
(includes protocol framing, so it is a different layer and compared to pcap,
not to MSE).

NetLog's value here is that it names QUIC sessions, which is exactly where the
pcap reference is blind.

**Known risk, stated in advance:** NetLog's capture modes differ in what byte
counts they include, and `Default` strips some payload detail. If the required
fields are absent under `Default`, that is reported as "NetLog cannot answer
this at this capture mode" — not worked around by escalating to a mode that
logs cookies and credentials.

## Pass bands, fixed now

Let `MSE = mse_video_bytes + mse_audio_bytes` for the run.

| # | check | band | reasoning for the band |
|---|---|---|---|
| A1 | `cdp_finished_bytes / MSE` | **[1.00, 1.15]** | same bytes plus response headers and any non-media googlevideo request. Floor 1.00: the network cannot deliver fewer body bytes than the player appended. 15 % ceiling because header overhead on multi-hundred-KB segments is small |
| A2 | `cdp_received_bytes / MSE` | **[1.00, 1.15]** | as A1 |
| B1 | `netlog_body_bytes / MSE` | **[1.00, 1.15]** | as A1; body bytes are the same quantity by a different route |
| B2 | `netlog_session_bytes / pcap_media_bytes` | **[0.90, 1.10]** | both include framing; they should agree within 10 % if the session-to-host mapping is right |
| C1 | **cross-reference agreement**: max pairwise relative difference among `cdp_finished_bytes`, `cdp_received_bytes`, `netlog_body_bytes` | **≤ 5 %** | three readings of one quantity. If they disagree by more than 5 % the references are not interchangeable and *none* of them is adopted |

**C1 is the gate**, not A1/B1. A reference is only proven if an independent
reference agrees with it. If A and B disagree with each other, that is reported
as "no proven reference" even if one of them happens to put the metric in band.
Picking whichever reference passes is the specific failure this addendum exists
to prevent.

## Verdict rules, fixed now

1. **PASS (reference proven)** requires C1 to hold **and** A1, A2, B1 all in
   band, on **every** run in the matrix below. One run out of band = FAIL.
2. **FAIL** is reported with the ratios as measured. The bands above are not
   adjusted afterwards, and no run is excluded as an outlier.
3. If a reference cannot be collected at all (CDP domain unavailable, NetLog
   field missing), the result is **UNKNOWN** for that reference, and C1 is
   evaluated on whatever remains — with it stated that a 2-way agreement is
   weaker evidence than a 3-way one.
4. Agreement with pcap is **reported but not part of the verdict**. pcap is the
   reference under suspicion; it cannot also be the judge.

## Run matrix for this addendum

At least 3 fresh YouTube runs, including **1 Mbps and 3 Mbps**, solo, 50 ms,
pfifo/cubic, 0 % loss, 180 s, with CDP and NetLog both on in the same run so
all references describe the same traffic. Target: 1, 1, 3, 3 Mbps (4 runs), so
each of the two rungs has a repeat, since YouTube's rendition choice at 3 Mbps
was measured to be bimodal.

`merge_vendor_stats` stays **off**.

## Step 3b gate

Step 3b (concurrent cells) stays blocked until C1 holds and A1/B1 pass on every
run. A pass obtained by selecting a reference after seeing the numbers does not
open the gate.

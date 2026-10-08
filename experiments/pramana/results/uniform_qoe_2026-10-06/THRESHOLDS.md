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

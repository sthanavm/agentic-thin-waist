# SUPERSEDED — the Zoom column here is not a measurement of the call

Replaced by `../zoom_vimeo_concurrent_180s_2026-10-05`. Kept for provenance.

The Zoom side of all four cells measured a **207x117** element while the remote
speaker occupied the `1458x820` main view. One cell (`3M_50ms`) measured
`1280x720@207x117` in all 168 samples with 0.0 % dropped frames across 180 s of
a contended 3 Mbps link — the signature of a local source, not a delivered
stream. `pickVideo` ranked candidates by intrinsic resolution at the time, and
the collector joins with its camera on, so its own self-view outranked the
downscaled remote tile.

The **Vimeo** column in these cells is a real measurement, but it was taken
while Zoom's decode load was different from the replacement sweep, so it is not
comparable with it either. The AV1 block was in force here as well.

See the replacement sweep's README for the corrected method: elements are now
classified by `getUserMedia` track identity and track label rather than by size,
and every `record.json` records which element was measured.

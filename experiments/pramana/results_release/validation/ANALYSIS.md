# Release analysis

23 run(s) analysed. Every claim below is derived from the per-second
samples and the captures of those runs.

## A. Byte references: MSE vs CDP vs pcap

Bands fixed in THRESHOLDS.md before any of this data existed:
A1/A2 `CDP/MSE` in [1.00, 1.15]; C1 cross-reference agreement <= 5%.
pcap is reported but is NOT part of the verdict: it is the reference
under suspicion, so it cannot also be the judge.

| run | MSE bytes | CDP finished | A1 | CDP received | A2 | C1 | pcap peer | pcap/MSE |
|---|---|---|---|---|---|---|---|---|
| youtube_0.5M_t10 | 5086439 | 4909299 | **FAIL** 0.9652 | 5096512 | PASS 1.0020 | PASS 3.6734% | 198.189.66.13 | 1.1558 |
| youtube_1.5M_t10 | 13684445 | 13316540 | **FAIL** 0.9731 | 14159437 | PASS 1.0347 | **FAIL** 5.9529% | 198.189.66.13 | 1.0518 |
| youtube_10M_t10 | 21119460 | 21131401 | PASS 1.0006 | 21130597 | PASS 1.0005 | PASS 0.0038% | 198.189.66.13 | 1.0679 |
| youtube_1M_t10 | 9439778 | 8441591 | **FAIL** 0.8943 | 9449503 | PASS 1.0010 | **FAIL** 10.6663% | 198.189.66.13 | 1.0378 |
| youtube_3M_t10 | 21119460 | 20366845 | **FAIL** 0.9644 | 21129025 | PASS 1.0005 | PASS 3.6073% | 198.189.66.13 | 0.9907 |
| youtube_3M_t21 | 13307722 | 13318123 | PASS 1.0008 | 13317569 | PASS 1.0007 | PASS 0.0042% | 198.189.66.13 | 1.0762 |
| youtube_3M_t22 | 13307722 | 13317785 | PASS 1.0008 | 13317305 | PASS 1.0007 | PASS 0.0036% | 198.189.66.13 | 1.0748 |
| youtube_3M_t23 | 13307722 | 13318287 | PASS 1.0008 | 13317706 | PASS 1.0008 | PASS 0.0044% | 198.189.66.13 | 1.0756 |
| youtube_3M_t24 | 13307722 | 13317958 | PASS 1.0008 | 13317453 | PASS 1.0007 | PASS 0.0038% | 198.189.66.13 | 1.0761 |
| youtube_3M_t25 | 13307722 | 13318922 | PASS 1.0008 | 13318217 | PASS 1.0008 | PASS 0.0053% | 198.189.66.13 | 1.0776 |
| youtube_6M_t10 | 21119460 | 21130276 | PASS 1.0005 | 21129722 | PASS 1.0005 | PASS 0.0026% | 198.189.66.13 | 0.9166 |

**Verdict: 7/11 YouTube runs pass A1, A2 and C1 together.**

At least one run fails, so the gate is NOT met and Step 3b stays
blocked. The failing ratios are reported above as measured.

## B. Startup delay vs the capture, on one clock

`startup_delay_ms` is `presentationTime - probe install`, so it is put on
the page timeline by adding `probe_t0_page_ms`, then onto wall time via
`page_now_ms`. Only packets AFTER navigationStart are counted: capture
begins before navigation, which is what invalidated an earlier attempt.

| run | probe_t0 (ms) | startup_delay_ms | navStart->frame | 1st media pkt (page ms) | pkt->frame | consistent |
|---|---|---|---|---|---|---|
| tubi_10M_t10 | n/a | None | - | - | - | no page-clock fields |
| tubi_3M_t10 | n/a | None | - | - | - | no page-clock fields |
| tubi_6M_t10 | n/a | None | - | - | - | no page-clock fields |
| twitch_10M_t10 | 360 | 12683 | 13043 | +10520 | 2523 | yes |
| twitch_3M_t10 | 1122 | 16434 | 17556 | +15936 | 1620 | yes |
| twitch_6M_t10 | 298 | 13287 | 13585 | +11134 | 2451 | yes |
| vimeo_0.5M_t10 | 698 | 21504 | 22202 | +11523 | 10679 | yes |
| vimeo_1.5M_t10 | 737 | 8095 | 8832 | +5469 | 3363 | yes |
| vimeo_10M_t10 | 412 | 2049 | 2461 | +1633 | 828 | yes |
| vimeo_1M_t10 | 1343 | 10508 | 11851 | +7179 | 4672 | yes |
| vimeo_3M_t10 | 475 | 4084 | 4559 | +2728 | 1831 | yes |
| vimeo_6M_t10 | 434 | 2854 | 3288 | +1973 | 1315 | yes |
| youtube_0.5M_t10 | 463 | 40507 | 40970 | +2430 | 38540 | yes |
| youtube_1.5M_t10 | 433 | 14261 | 14694 | +4386 | 10308 | yes |
| youtube_10M_t10 | 487 | 8127 | 8614 | +2435 | 6179 | yes |
| youtube_1M_t10 | 445 | 20521 | 20966 | +1439 | 19527 | yes |
| youtube_3M_t10 | 424 | 7608 | 8032 | +3639 | 4393 | yes |
| youtube_3M_t21 | 513 | 7631 | 8144 | +893 | 7251 | yes |
| youtube_3M_t22 | 476 | 7822 | 8298 | +868 | 7430 | yes |
| youtube_3M_t23 | 445 | 7528 | 7973 | +746 | 7227 | yes |
| youtube_3M_t24 | 391 | 8165 | 8556 | +768 | 7788 | yes |
| youtube_3M_t25 | 649 | 7988 | 8637 | +1006 | 7631 | yes |
| youtube_6M_t10 | 478 | 6335 | 6813 | +2248 | 4565 | yes |

## C. Why the mime switch log stayed empty while renditions changed

`mse_mime_switch_log` only records `addSourceBuffer` and `changeType`
calls. If a player changes rendition WITHOUT calling `changeType`, the
log is empty however many times the rendition changed. The init segment
is the independent witness: the decoder is configured from it.

| run | declared mime | mime switches | distinct init video configs | decoding codec | WxH | resolutions seen |
|---|---|---|---|---|---|---|
| tubi_10M_t10 | video/mp4;codecs=avc1.4D401E | 1 | **2** | avc1.640028 | 854x480 | 240,480 |
| tubi_3M_t10 | video/mp4;codecs=avc1.4D401E | 1 | **3** | avc1.640028 | 854x480 | 240,360,480 |
| tubi_6M_t10 | video/mp4;codecs=avc1.4D401E | 1 | **2** | avc1.640028 | 854x480 | 240,480 |
| twitch_10M_t10 | None | 0 | **0** | None | NonexNone | 1080,720 |
| twitch_3M_t10 | None | 0 | **0** | None | NonexNone | 480,720 |
| twitch_6M_t10 | None | 0 | **0** | None | NonexNone | 1080,720 |
| vimeo_0.5M_t10 | video/mp4;codecs=av01.0.08M.08.0.1 | 0 | **2** | av01.0.00M.08 | 320x240 | 720,240 |
| vimeo_1.5M_t10 | video/mp4;codecs=av01.0.01M.08.0.1 | 0 | **2** | av01.0.00M.08 | 320x240 | 360,240,360,240,360,240,360,240 |
| vimeo_10M_t10 | video/mp4;codecs=av01.0.01M.08.0.1 | 0 | **4** | av01.0.08M.08 | 960x720 | 360,540,1080,540,720 |
| vimeo_1M_t10 | video/mp4;codecs=av01.0.01M.08.0.1 | 0 | **2** | av01.0.01M.08 | 480x360 | 360,240,360 |
| vimeo_3M_t10 | video/mp4;codecs=av01.0.01M.08.0.1 | 0 | **3** | av01.0.05M.08 | 720x540 | 360,240,360,240,540 |
| vimeo_6M_t10 | video/mp4;codecs=av01.0.05M.08.0.1 | 0 | **3** | av01.0.08M.08 | 960x720 | 540,720,360,720 |
| youtube_0.5M_t10 | video/mp4; codecs="av01.0.04M.08" | 0 | **3** | av01.0.00M.08 | 426x240 | 480,144 |
| youtube_1.5M_t10 | video/mp4; codecs="av01.0.04M.08" | 0 | **1** | av01.0.04M.08 | 854x480 | 480 |
| youtube_10M_t10 | video/webm; codecs="vp09.00.51.08. | 0 | **1** | vp09 | 1280x720 | 720 |
| youtube_1M_t10 | video/mp4; codecs="av01.0.04M.08" | 0 | **3** | av01.0.04M.08 | 854x480 | 480,240,360,480 |
| youtube_3M_t10 | video/webm; codecs="vp09.00.51.08. | 0 | **1** | vp09 | 1280x720 | 720 |
| youtube_3M_t21 | video/mp4; codecs="av01.0.04M.08" | 0 | **1** | av01.0.04M.08 | 854x480 | 480 |
| youtube_3M_t22 | video/mp4; codecs="av01.0.04M.08" | 0 | **1** | av01.0.04M.08 | 854x480 | 480 |
| youtube_3M_t23 | video/mp4; codecs="av01.0.04M.08" | 0 | **1** | av01.0.04M.08 | 854x480 | 480 |
| youtube_3M_t24 | video/mp4; codecs="av01.0.04M.08" | 0 | **1** | av01.0.04M.08 | 854x480 | 480 |
| youtube_3M_t25 | video/mp4; codecs="av01.0.04M.08" | 0 | **1** | av01.0.04M.08 | 854x480 | 480 |
| youtube_6M_t10 | video/webm; codecs="vp09.00.51.08. | 0 | **1** | vp09 | 1280x720 | 720 |

A run where `mime switches` is 0 while `distinct init video configs` or
`resolutions seen` is greater than 1 is the direct demonstration: the
rendition changed and `changeType` was never called.

## D. The Resource Timing gap vs never-finished responses

Hypothesis under test: the missing RT bytes belong to responses that
never ended, because a Resource Timing entry is only created at response
end. `cdp_open_at_end_bytes` counts exactly those bytes independently.
If the RT shortfall is real but open bytes are ~0, the hypothesis is
REFUTED.

| run | MSE | rt_all | rt_all/MSE | RT shortfall | cdp_open_at_end | explains? |
|---|---|---|---|---|---|---|
| tubi_10M_t10 | 53711449 | 892883 | 0.0166 | 52818566 | 0 | **REFUTED** (0 open bytes) |
| tubi_3M_t10 | 53360252 | 927284 | 0.0174 | 52432968 | 0 | **REFUTED** (0 open bytes) |
| tubi_6M_t10 | 54380714 | 898805 | 0.0165 | 53481909 | 0 | **REFUTED** (0 open bytes) |
| vimeo_0.5M_t10 | 9176797 | 9261241 | 1.0092 | -84444 | 115329 | no shortfall |
| vimeo_1.5M_t10 | 10044718 | 10269471 | 1.0224 | -224753 | 55578 | no shortfall |
| vimeo_10M_t10 | 72307111 | 72412191 | 1.0015 | -105080 | 66697 | no shortfall |
| vimeo_1M_t10 | 11342800 | 11413355 | 1.0062 | -70555 | 37633 | no shortfall |
| vimeo_3M_t10 | 28096968 | 28183949 | 1.0031 | -86981 | 79472 | no shortfall |
| vimeo_6M_t10 | 56274457 | 56391920 | 1.0021 | -117463 | 568114 | no shortfall |
| youtube_0.5M_t10 | 5086439 | 4927297 | 0.9687 | 159142 | 187514 | **CONSISTENT** |
| youtube_1.5M_t10 | 13684445 | 192489 | 0.0141 | 13491956 | 843200 | explains 6% |
| youtube_10M_t10 | 21119460 | 14356584 | 0.6798 | 6762876 | 0 | **REFUTED** (0 open bytes) |
| youtube_1M_t10 | 9439778 | 8569790 | 0.9078 | 869988 | 1008277 | **CONSISTENT** |
| youtube_3M_t10 | 21119460 | 13397209 | 0.6344 | 7722251 | 762558 | explains 10% |
| youtube_3M_t21 | 13307722 | 8886828 | 0.6678 | 4420894 | 0 | **REFUTED** (0 open bytes) |
| youtube_3M_t22 | 13307722 | 9873939 | 0.7420 | 3433783 | 0 | **REFUTED** (0 open bytes) |
| youtube_3M_t23 | 13307722 | 9098383 | 0.6837 | 4209339 | 0 | **REFUTED** (0 open bytes) |
| youtube_3M_t24 | 13307722 | 9505114 | 0.7143 | 3802608 | 0 | **REFUTED** (0 open bytes) |
| youtube_3M_t25 | 13307722 | 9098764 | 0.6837 | 4208958 | 0 | **REFUTED** (0 open bytes) |
| youtube_6M_t10 | 21119460 | 13391677 | 0.6341 | 7727783 | 0 | **REFUTED** (0 open bytes) |

## E. YouTube 3 Mbps with the player box enforced

The rendition at 3 Mbps was measured to be bimodal (480p AV1 vs 720p VP9)
across trials at an identical box, which could still have been our box
varying. Here the box is ENFORCED and logged, so a remaining spread is
the site's own choice.

| trial | enforced window | measured box | res | decoding codec | video Mbps | switches |
|---|---|---|---|---|---|---|
| 10 | not enforced | 1331x749 | 720p | vp09 | 0.5846 | 0 |
| 21 | 1280x720 | 867x488 | 480p | av01.0.04M.08 | 0.2991 | 0 |
| 22 | 1280x720 | 867x488 | 480p | av01.0.04M.08 | 0.3113 | 0 |
| 23 | 1280x720 | 867x488 | 480p | av01.0.04M.08 | 0.3066 | 0 |
| 24 | 1280x720 | 812x457 | 480p | av01.0.04M.08 | 0.3176 | 0 |
| 25 | 1280x720 | 867x488 | 480p | av01.0.04M.08 | 0.3098 | 0 |

resolutions: [720, 480, 480, 480, 480, 480] -> **DIFFER**
measured boxes: ['1331x749', '812x457', '867x488'] -> **differ**
delivered video Mbps: min=0.2991 max=0.5846 mean=0.3548  range/mean=80.5%

## Step 3b gate

**NOT MET — concurrent cells must not run.**

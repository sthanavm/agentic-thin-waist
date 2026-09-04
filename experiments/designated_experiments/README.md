# Designated video experiments

This directory is the index for the maintained QoE report notebooks. Raw data,
HTML reports, and exported PNG graphs remain grouped by experiment and bandwidth
under the result directories linked below.

| Experiment | Primary notebook | Result folders |
| --- | --- | --- |
| YouTube + Vimeo | `youtube_vimeo/youtube_vimeo_qoe_report.ipynb` | `../../youtube_vimeo_experiment/{3,6,10}mbps/` |
| YouTube + Tubi | `youtube_tubi/youtube_tubi_qoe_report.ipynb` | `../../youtube_tubi_experiment/{3,6,10}mbps/` |
| YouTube + Google Meet | `youtube_google_meet/youtube_google_meet_qoe_report.ipynb` | `../../youtube_google_meet_100ms_pfifo/{3,6,10}mbps/` |

The Google Meet directory also keeps the older 3 Mbps PCAP-only notebook and
the combined 6/10 Mbps comparison notebook, with names that identify their
scope directly.

Each bandwidth folder now follows this layout:

```text
<bandwidth>mbps/
├── qoe_report.html
├── graphs/
│   ├── 01_<descriptive_graph_name>.png
│   └── ...
└── raw experiment artifacts (when retained)
```

The primary notebooks install `graph_output.py`, which saves every newly
rendered Matplotlib figure into that run's `graphs/` directory. Graph filenames
are numbered in report order and describe the metric being plotted; the plot
titles identify the metric and bandwidth tier.

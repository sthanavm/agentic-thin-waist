"""Single source of truth for the applications the platform can drive.

Every component that needs to know *how an app is driven and measured* reads
this registry: the QoE collector (which player API to sample), the experiment
runner (which URL to open, whether a peer is required), the summarizer in
``shared.qoe`` (how to reduce the samples), and the notebook client library.

Adding a new application must be a registry entry, not new code.

Fields
------
kind            How the app is driven and measured:
                ``html5_video`` — a page with an HTMLVideoElement; sampled via
                    the generic ``<video>`` API (plus vendor extras when the
                    player exposes them, e.g. YouTube's ``getStatsForNerds``).
                ``webrtc``      — a conferencing app; sampled via
                    ``RTCPeerConnection.getStats()`` inbound-rtp.
                ``shell``       — no browser, no player; transfer metrics only.
default_url     The URL opened when the caller supplies none.
needs_peer      True when the app produces no inbound media unless a remote
                participant is publishing. Runs without a configured peer are
                SKIPPED with a reason rather than reporting fabricated numbers.
live            True for continuous live streams: no finite duration, no seek,
                so "delivered fraction of the video" is meaningless and startup
                is measured as time-to-first-frame only.
bitrate_source  Where a real media bitrate can come from, if anywhere:
                ``stats_for_nerds`` | ``webrtc_stats`` | ``bytes`` | ``none``.
                ``none`` means the summarizer emits ``null`` — never a guess.
domains         Hostname suffixes identifying this app's traffic in a capture,
                used for per-app PCAP attribution.
plot_directions Which directions get their own throughput plot. Video is
                download-only (upload is ACKs; summing inflates the rate);
                conferencing plots both, separately.
notes           Operator-facing caveats.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

HTML5_VIDEO = "html5_video"
WEBRTC = "webrtc"
SHELL = "shell"

PLAYER_KINDS = (HTML5_VIDEO, WEBRTC)


@dataclass(frozen=True)
class AppSpec:
    """How one application is driven and measured."""

    name: str
    kind: str
    default_url: str = ""
    needs_peer: bool = False
    live: bool = False
    bitrate_source: str = "none"
    domains: tuple[str, ...] = ()
    plot_directions: tuple[str, ...] = ("download",)
    color: str = "#2196F3"
    display_name: str = ""
    notes: str = ""

    @property
    def has_player_qoe(self) -> bool:
        """Whether this app can produce player-side QoE at all."""
        return self.kind in PLAYER_KINDS

    @property
    def label(self) -> str:
        return self.display_name or self.name.title()


REGISTRY: dict[str, AppSpec] = {
    "youtube": AppSpec(
        name="youtube",
        kind=HTML5_VIDEO,
        # Long, stable, always-available video; autoplay + muted so playback
        # starts without a user gesture under --autoplay-policy.
        default_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ&autoplay=1&mute=1",
        bitrate_source="stats_for_nerds",
        domains=(
            "googlevideo.com",
            "youtube.com",
            "youtu.be",
            "ytimg.com",
            "ggpht.com",
            "youtube-nocookie.com",
        ),
        color="#FF0000",
        display_name="YouTube",
        notes=(
            "Exposes getStatsForNerds(); parsed defensively (undocumented, keys "
            "change between releases). Note its bandwidth_kbps field is a NETWORK "
            "estimate, not the media bitrate — see shared/qoe.py."
        ),
    ),
    "vimeo": AppSpec(
        name="vimeo",
        kind=HTML5_VIDEO,
        default_url="https://player.vimeo.com/video/76979871?autoplay=1&muted=1",
        bitrate_source="none",
        domains=("vimeocdn.com", "vimeo.com"),
        color="#1AB7EA",
        display_name="Vimeo",
        notes="Generic <video>; no vendor stats API, so bitrate is null unless byte-derived.",
    ),
    "tubi": AppSpec(
        name="tubi",
        kind=HTML5_VIDEO,
        default_url="https://tubitv.com/movies/100014/the-fast-and-the-furious",
        bitrate_source="none",
        domains=("tubitv.com", "adrise.tv", "tubi.video", "tubi.io"),
        color="#FBC02D",
        display_name="Tubi",
        notes=(
            "Ad-supported: pre-roll ads play before the title, so early samples "
            "describe the AD, not the content. The summarizer reports "
            "startup to first frame of whatever plays first — treat the opening "
            "seconds as ad, not content QoE."
        ),
    ),
    "twitch": AppSpec(
        name="twitch",
        kind=HTML5_VIDEO,
        # Must be a LIVE channel; the bare twitch.tv home page plays no video.
        default_url="https://www.twitch.tv/",
        live=True,
        bitrate_source="none",
        domains=(
            "twitch.tv",
            "ttvnw.net",
            "jtvnw.net",
            "twitchcdn.net",
            "twitchsvc.net",
            "live-video.net",
        ),
        color="#9146FF",
        display_name="Twitch",
        notes=(
            "LIVE: no finite duration and no seek. Supply a channel that is "
            "actually streaming via app_urls; the default home page yields no "
            "<video> and the app is reported as no_playback."
        ),
    ),
    "roku": AppSpec(
        name="roku",
        kind=HTML5_VIDEO,
        default_url="https://therokuchannel.roku.com/",
        bitrate_source="none",
        domains=("roku.com", "rokucdn.com", "roku-services.com"),
        color="#662D91",
        display_name="Roku Channel",
        notes="Generic <video>; bitrate null unless byte-derived. Title URL recommended.",
    ),
    "puffer": AppSpec(
        name="puffer",
        kind=HTML5_VIDEO,
        default_url="https://puffer.stanford.edu/player/",
        live=True,
        bitrate_source="none",
        domains=("puffer.stanford.edu", "stanford.edu"),
        color="#8C1515",
        display_name="Puffer",
        notes=(
            "Stanford research player (live TV streams). Generic <video>. May "
            "require accepting participation terms before playback starts."
        ),
    ),
    "meet": AppSpec(
        name="meet",
        kind=WEBRTC,
        default_url="",  # supply a room URL
        needs_peer=True,
        live=True,
        bitrate_source="webrtc_stats",
        domains=("meet.google.com", "googleusercontent.com", "stun.l.google.com"),
        plot_directions=("download", "upload"),
        color="#00897B",
        display_name="Google Meet",
        notes=(
            "Needs a remote participant publishing video: with nobody else in "
            "the room the only track is the LOCAL preview, which is not a "
            "network measurement. Without a configured peer the app is SKIPPED."
        ),
    ),
    "zoom": AppSpec(
        name="zoom",
        kind=WEBRTC,
        default_url="",  # supply a web-client join URL
        needs_peer=True,
        live=True,
        bitrate_source="webrtc_stats",
        domains=("zoom.us", "zoomgov.com", "zoom.com"),
        plot_directions=("download", "upload"),
        color="#2D8CFF",
        display_name="Zoom",
        notes=(
            "Web client availability varies by meeting settings and often "
            "requires a signed-in Chrome profile; some meetings refuse the web "
            "client outright. Failure to join is reported as SKIPPED with the "
            "reason — the run never hangs waiting."
        ),
    ),
    "wget": AppSpec(
        name="wget",
        kind=SHELL,
        default_url="http://speedtest.tele2.net/100MB.zip",
        bitrate_source="none",
        domains=("tele2.net",),
        color="#607D8B",
        display_name="Bulk download (wget)",
        notes="No browser and no player: transfer summary only, never player fields.",
    ),
}


# ── lookup helpers (import these rather than touching REGISTRY directly) ─────
def get(app: str) -> AppSpec:
    """Registry entry for `app`. Raises KeyError with the known names listed."""
    key = (app or "").strip().lower()
    if key not in REGISTRY:
        raise KeyError(f"Unknown app '{app}'. Known: {sorted(REGISTRY)}")
    return REGISTRY[key]


def known_apps() -> list[str]:
    return sorted(REGISTRY)


def url_for(app: str, overrides: Optional[dict[str, str]] = None) -> str:
    """URL to open for `app`, honouring a caller-supplied override map."""
    if overrides and app in overrides and overrides[app]:
        return overrides[app]
    return get(app).default_url


def kind_of(app: str) -> str:
    return get(app).kind


def has_player_qoe(app: str) -> bool:
    return get(app).has_player_qoe


def needs_peer(app: str) -> bool:
    return get(app).needs_peer


def is_live(app: str) -> bool:
    return get(app).live


def domains(app: str) -> list[str]:
    return list(get(app).domains)


def plot_directions(app: str) -> list[str]:
    return list(get(app).plot_directions)


def color(app: str) -> str:
    return get(app).color


def display_name(app: str) -> str:
    return get(app).label


def browser_apps(apps: Iterable[str]) -> list[str]:
    """Subset of `apps` that runs in a browser (and can yield player QoE)."""
    return [a for a in apps if has_player_qoe(a)]


def support_matrix() -> list[dict[str, object]]:
    """Rows for the REALQOE.md support table / runtime introspection."""
    return [
        {
            "app": s.name,
            "kind": s.kind,
            "player_qoe": s.has_player_qoe,
            "needs_peer": s.needs_peer,
            "live": s.live,
            "bitrate_source": s.bitrate_source,
            "notes": s.notes,
        }
        for s in (REGISTRY[k] for k in known_apps())
    ]

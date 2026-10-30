"""Tests for QUIC Initial SNI recovery and the media-host suffix tables.

Why this exists: a shared capture names a media endpoint only by the TLS SNI it
sees in the clear, and YouTube's media rides QUIC. Before this code, the name
was learned only when a stray TCP connection to the same address happened to be
captured -- which made per-app attribution correct by luck. The two concurrent
release runs hit the SAME server (198.189.66.13) and disagreed: the 6 Mbps run
caught a TCP ClientHello and attributed 14.3 MB correctly, the 3 Mbps run saw
no TCP packets to it at all and lost the same 14.3 MB into `other`.

The crypto is pinned against published test vectors (FIPS-197 for AES-128,
RFC 9001 Appendix A.1 for the Initial key schedule) rather than against our own
output. The framing is then exercised by building a packet and reading it back,
which is what proves the varint, header-protection and CRYPTO-reassembly paths
agree with each other.

Measured provenance of the decrypt path, not reproducible in a unit test: run
against the committed release captures it recovers 26 and 27 untruncated
hostnames respectively, including
`198.189.66.13 -> rr2---sn-jxopj-n5oe.googlevideo.com` in the 3 Mbps capture,
which is the exact hostname Chrome's own CDP ledger counted bytes under.
"""

import pathlib
import struct
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[2] / "experiments/pramana")
)

import pramana_helpers as H  # noqa: E402


def test_aes128_matches_fips197_vector():
    """FIPS-197 C.1: AES-128 of a known block under a known key."""
    out = H.aes128_ecb_encrypt(
        bytes(range(16)), bytes.fromhex("00112233445566778899aabbccddeeff")
    )
    assert out.hex() == "69c4e0d86a7b0430d8cdb78070b4c55a"


def test_quic_initial_keys_match_rfc9001_vector():
    """RFC 9001 A.1: client Initial keys for dcid 0x8394c8f03e515708."""
    key, iv, hp = H.quic_initial_client_keys(bytes.fromhex("8394c8f03e515708"))
    assert key.hex() == "1f369613dd76d5467730efcbe3b1a22d"
    assert iv.hex() == "fa044b2f42a3fd3b46fb255c"
    assert hp.hex() == "9f50449e04a0e810283a1e9933adedd2"


def test_aes_ctr_is_its_own_inverse():
    key = bytes(range(16))
    ctr = bytes(range(16))
    msg = bytes(range(64))
    assert H.aes128_ctr(key, ctr, H.aes128_ctr(key, ctr, msg)) == msg


# ── helpers that build a client Initial packet the same way Chrome does ──────


def _client_hello(host: str) -> bytes:
    """A minimal but structurally valid ClientHello carrying `host` as SNI."""
    sni = host.encode()
    name = b"\x00" + struct.pack(">H", len(sni)) + sni
    server_name = struct.pack(">H", len(name)) + name
    ext = struct.pack(">HH", 0, len(server_name)) + server_name
    body = (
        b"\x03\x03"  # legacy_version
        + bytes(32)  # random
        + b"\x00"  # session id
        + struct.pack(">H", 2)
        + b"\x13\x01"  # cipher suites
        + b"\x01\x00"  # compression methods
        + struct.pack(">H", len(ext))
        + ext
    )
    return b"\x01" + len(body).to_bytes(3, "big") + body


def _initial_packet(dcid: bytes, crypto_payload: bytes, pad_to: int = 1200) -> bytes:
    """Protect a client Initial carrying `crypto_payload` in a CRYPTO frame."""
    key, iv, hp = H.quic_initial_client_keys(dcid)
    frame = (
        b"\x06"
        + b"\x00"
        + (struct.pack(">H", len(crypto_payload) | 0x4000))
        + crypto_payload
    )
    first = 0xC0  # long header, Initial, pn_len = 1
    header = (
        bytes([first])
        + struct.pack(">I", 1)
        + bytes([len(dcid)])
        + dcid
        + b"\x00"  # zero-length scid
        + b"\x00"  # zero-length token
    )
    pn = b"\x00"
    plain = frame
    pad = max(0, pad_to - (len(header) + 2 + len(pn) + len(plain) + 16))
    plain = plain + bytes(pad)
    nonce = bytes(a ^ b for a, b in zip(iv, (0).to_bytes(12, "big")))
    ct = H.aes128_ctr(key, nonce + b"\x00\x00\x00\x02", plain) + bytes(16)
    length = len(pn) + len(ct)
    pkt = bytearray(header + struct.pack(">H", length | 0x4000) + pn + ct)
    pn_off = len(header) + 2
    mask = H.aes128_ecb_encrypt(hp, bytes(pkt[pn_off + 4 : pn_off + 20]))
    pkt[0] ^= mask[0] & 0x0F
    pkt[pn_off] ^= mask[1]
    return bytes(pkt)


def _udp(payload: bytes) -> bytes:
    return struct.pack(">HHHH", 50000, 443, 8 + len(payload), 0) + payload


def test_sni_recovered_from_one_initial_packet():
    out, state = {}, {}
    pkt = _initial_packet(
        b"\x01\x02\x03\x04\x05\x06\x07\x08",
        _client_hello("rr2---sn-jxopj-n5oe.googlevideo.com"),
    )
    H._harvest_quic_sni(_udp(pkt), "198.189.66.13", out, state)
    assert out == {"198.189.66.13": "rr2---sn-jxopj-n5oe.googlevideo.com"}


def test_hello_split_across_two_initials_is_reassembled():
    """Chrome's hello does not fit one Initial, so fragments must be joined.

    Parsing the first fragment alone must yield nothing rather than a truncated
    hostname: a truncated name can never match a domain suffix, and because
    names are recorded with setdefault it would also block the correct one.
    """
    hello = _client_hello("rr2---sn-jxopj-n5oe.googlevideo.com")
    cut = len(hello) // 2
    dcid = b"\xaa" * 8
    out, state = {}, {}
    first = bytearray(_initial_packet(dcid, hello[:cut]))
    H._harvest_quic_sni(_udp(bytes(first)), "1.2.3.4", out, state)
    assert out == {}, "a partial hello must not produce a name"

    # second fragment, at the right CRYPTO offset
    frame = (
        b"\x06"
        + struct.pack(">H", cut | 0x4000)
        + struct.pack(">H", len(hello[cut:]) | 0x4000)
        + hello[cut:]
    )
    H._harvest_quic_sni(
        _udp(_initial_packet_with_frame(dcid, frame)), "1.2.3.4", out, state
    )
    assert out == {"1.2.3.4": "rr2---sn-jxopj-n5oe.googlevideo.com"}


def _initial_packet_with_frame(dcid: bytes, frame: bytes) -> bytes:
    key, iv, hp = H.quic_initial_client_keys(dcid)
    header = (
        bytes([0xC0])
        + struct.pack(">I", 1)
        + bytes([len(dcid)])
        + dcid
        + b"\x00"
        + b"\x00"
    )
    pn = b"\x00"
    pad = max(0, 1200 - (len(header) + 2 + len(pn) + len(frame) + 16))
    plain = frame + bytes(pad)
    nonce = bytes(a ^ b for a, b in zip(iv, (0).to_bytes(12, "big")))
    ct = H.aes128_ctr(key, nonce + b"\x00\x00\x00\x02", plain) + bytes(16)
    pkt = bytearray(header + struct.pack(">H", (len(pn) + len(ct)) | 0x4000) + pn + ct)
    pn_off = len(header) + 2
    mask = H.aes128_ecb_encrypt(hp, bytes(pkt[pn_off + 4 : pn_off + 20]))
    pkt[0] ^= mask[0] & 0x0F
    pkt[pn_off] ^= mask[1]
    return bytes(pkt)


def test_non_quic_and_short_payloads_are_ignored():
    out, state = {}, {}
    for junk in (b"", b"\x00" * 4, b"\x45" + b"\x00" * 200, bytes(range(80))):
        H._harvest_quic_sni(_udp(junk), "9.9.9.9", out, state)
    assert out == {}


# ── host classification ─────────────────────────────────────────────────────


def test_youtube_media_host_classifies_to_youtube():
    assert (
        H.classify_host("rr2---sn-jxopj-n5oe.googlevideo.com", ["youtube", "vimeo"])
        == "youtube"
    )


def test_gvt1_ggc_host_is_browser_infra_not_youtube():
    """`rrN---sn-<id>.gvt1.com` is Chrome's component updater, not YouTube.

    Measured on both concurrent release runs: it delivered 973 kB in a 4-5 s
    burst at t=1..5 s, before the page had loaded, redirected from
    redirector.gvt1.com, while YouTube's own media ran 14.3 MB from
    t=16..120 s on a sibling address in the same /24. Classifying it as
    YouTube because the names look alike would add ~1 MB of browser traffic to
    every run's app bucket.
    """
    assert (
        H.classify_host("rr5---sn-jxopj-n5oe.gvt1.com", ["youtube", "vimeo"])
        == "browser_infra"
    )


def test_media_suffixes_match_the_collector_ledger():
    """R4 compares bytes counted with both lists, so they must not drift."""
    src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "services/orchestration/scripts/selenium_video_qoe/collect.py"
    ).read_text()
    start = src.index("_CDP_HOSTS = {")
    end = src.index("}", start) + 1
    ns: dict = {}
    exec(src[start:end], ns)  # noqa: S102 - a literal dict from our own source
    assert ns["_CDP_HOSTS"] == H.MEDIA_HOST_SUFFIXES

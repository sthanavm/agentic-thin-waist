import asyncio, json, urllib.request
from aiortc import RTCPeerConnection, RTCSessionDescription

async def main():
    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")
    done = asyncio.Event(); frames = []

    @pc.on("track")
    def on_track(track):
        async def pump():
            try:
                for _ in range(40):
                    frames.append(await track.recv())
            finally:
                done.set()
        asyncio.ensure_future(pump())

    await pc.setLocalDescription(await pc.createOffer())
    body = json.dumps({"sdp": pc.localDescription.sdp,
                       "type": pc.localDescription.type}).encode()
    req = urllib.request.Request("http://127.0.0.1:8090/offer", data=body,
                                 headers={"Content-Type": "application/json"})
    ans = json.loads(urllib.request.urlopen(req, timeout=25).read())
    await pc.setRemoteDescription(RTCSessionDescription(**ans))
    await asyncio.wait_for(done.wait(), timeout=60)
    f0, f1 = frames[0], frames[-1]
    print("  frames received : %d" % len(frames))
    print("  frame size      : %dx%d" % (f1.width, f1.height))
    print("  pts advancing   : %s (%s -> %s)" % (f1.pts > f0.pts, f0.pts, f1.pts))
    codecs = [l for l in ans["sdp"].splitlines() if l.startswith("a=rtpmap")]
    print("  negotiated      : %s" % "; ".join(c.split()[-1] for c in codecs[:4]))
    stats = await pc.getStats()
    for v in stats.values():
        if getattr(v, "type", "") == "inbound-rtp":
            print("  inbound-rtp     : bytes=%s packets=%s" % (
                getattr(v, "bytesReceived", "?"), getattr(v, "packetsReceived", "?")))
    await pc.close()

asyncio.get_event_loop().run_until_complete(main())

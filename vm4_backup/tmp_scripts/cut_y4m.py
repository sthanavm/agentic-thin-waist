import sys
# Stream a y4m, keep the header and exactly N frames, cutting on a frame
# boundary. A ragged tail would corrupt the loop Chrome plays, so this counts
# frames rather than bytes.
out_path, n_frames = sys.argv[1], int(sys.argv[2])
r = sys.stdin.buffer
hdr = b""
while not hdr.endswith(b"\n"):
    c = r.read(1)
    if not c:
        sys.exit("stream ended inside header")
    hdr += c
fields = {}
for tok in hdr.decode().split():
    if tok[:1] in "WHF":
        fields[tok[0]] = tok[1:]
w, h = int(fields["W"]), int(fields["H"])
fsz = 6 + w * h * 3 // 2          # b"FRAME\n" + YUV420 planes
with open(out_path, "wb") as f:
    f.write(hdr)
    for i in range(n_frames):
        buf = r.read(fsz)
        if len(buf) < fsz:
            sys.exit("stream ended at frame %d" % i)
        f.write(buf)
print("  %dx%d  %d frames  frame=%dB  fps=%s" % (w, h, n_frames, fsz, fields.get("F")))

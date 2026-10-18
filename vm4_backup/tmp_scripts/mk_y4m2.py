"""640x480 y4m with real motion (moving bar + drifting gradient) so the encoder
produces a realistic bitrate. A flat frame would compress to nearly nothing and
make the inbound-bitrate reading meaningless."""
import sys
W,H,N = 640,480,60
path = sys.argv[1] if len(sys.argv)>1 else "/tmp/bot.y4m"
with open(path,"wb") as f:
    f.write(f"YUV4MPEG2 W{W} H{H} F30:1 Ip A1:1 C420mpeg2\n".encode())
    for i in range(N):
        bar = int((i / N) * W)
        row = bytearray(W)
        frame = bytearray()
        for y in range(H):
            base = (y * 255 // H + i * 4) % 200 + 20
            for x in range(W):
                row[x] = 235 if abs(x - bar) < 40 else (base + ((x * 7) % 35))
            frame += row
        f.write(b"FRAME\n"); f.write(bytes(frame))
        f.write(bytes([(128 + i) % 256]) * ((W//2)*(H//2)))
        f.write(bytes([(128 - i) % 256]) * ((W//2)*(H//2)))
import os
print(f"  y4m: {path}  {os.path.getsize(path)/1e6:.1f} MB  {W}x{H} {N} frames @30fps", flush=True)

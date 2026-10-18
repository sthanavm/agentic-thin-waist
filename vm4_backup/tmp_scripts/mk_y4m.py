"""Write a y4m whose Nth frame has luma = 16+N*8 (flat), so a reader can tell
exactly which frame is on screen — proving the file is the capture source."""
import sys
W,H,N = 320,240,40
path = sys.argv[1] if len(sys.argv)>1 else "/out/testpattern.y4m"
with open(path,"wb") as f:
    f.write(f"YUV4MPEG2 W{W} H{H} F30:1 Ip A1:1 C420mpeg2\n".encode())
    for i in range(N):
        y = 16 + (i*8) % 220
        f.write(b"FRAME\n")
        f.write(bytes([y])*(W*H))       # flat luma, value encodes the frame index
        f.write(bytes([128])*((W//2)*(H//2)))  # neutral U
        f.write(bytes([128])*((W//2)*(H//2)))  # neutral V
import os
print(f"  wrote {path}  {os.path.getsize(path)} bytes  {W}x{H} {N} frames")

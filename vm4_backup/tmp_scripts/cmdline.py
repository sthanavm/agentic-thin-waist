import sys, os, subprocess, time
sys.path.insert(0, "/app")
from collect import build_driver, start_display
start_display("99"); os.environ["DISPLAY"] = ":99"
d = build_driver()
time.sleep(2)
pid = subprocess.run(["bash","-c","pgrep -f 'chrome --' | head -1"],capture_output=True,text=True).stdout.strip()
raw = open(f"/proc/{pid}/cmdline","rb").read().split(b"\0")
args = [a.decode(errors="replace") for a in raw if a]
print("  total argv entries:", len(args))
for a in args:
    if any(k in a for k in ("webrtc","host-resolver","disable-features","fake","policy")):
        print("   ", a)
d.quit()

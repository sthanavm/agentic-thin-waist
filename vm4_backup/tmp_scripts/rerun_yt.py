import sys
sys.path.insert(0, "/tmp")
import run_matrix as M
for bw in (1, 3, 6):
    for t in (1, 2):
        M.run_cell("youtube", bw, t)
print("YT_DONE", flush=True)

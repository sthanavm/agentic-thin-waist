import sys
sys.path.insert(0, "/tmp")
import run_matrix as M
M.run_cell("youtube", 1, 2)
for bw in (1, 3, 6):
    for t in (1, 2):
        M.run_cell("vimeo", bw, t)
print("REST_DONE", flush=True)

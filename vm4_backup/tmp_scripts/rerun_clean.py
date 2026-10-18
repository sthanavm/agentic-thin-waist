import sys
sys.path.insert(0, "/tmp")
import run_matrix as M
for bw, trials in ((3, (1, 2)), (6, (1, 2)), (1, (1,))):
    for t in trials:
        M.run_cell("youtube", bw, t)
print("CLEAN_RERUN_DONE", flush=True)

# Restoring the Pramana work on a new VM

Taken from `docker-vm-4` on 2026-10-17, before the machine is replaced or
restarted. This branch holds everything that existed **only** on that VM and is
small enough to version. It is a backup branch: no PR, and it is not meant to
be merged.

## What is here, and what is deliberately not

```
vm4_backup/
  uncommitted/      the 6 tracked files that were modified but never committed
  untracked/        the 54 untracked files (notebooks, scripts) from the repo
  home_scripts/     ~/pramana_helpers.py, run_yt_solo.sh, run_yt_zoom.sh,
                    shaping_sanity_check.sh
  tmp_scripts/      the analysis and probe scripts that lived in /tmp
  vm4_uncommitted.patch   the same uncommitted changes as a single diff
  VM4_INVENTORY.md        what was on the machine, with sizes
  vm4_pcap_manifest.csv   every pcap on the VM: name, size, sha256, location
```

**Not here, on purpose:** packet captures (too large to version), anything over
5 MB, `.env` files, keys and tokens. The captures are backed up separately —
see below.

## What was already safe without this branch

The VM's checked-out branch, `pramana-self-contained-notebook`, was **fully
pushed** (0 commits ahead, 0 behind `jenil/pramana-self-contained-notebook`),
there were no stashes, and no local-only branches. So the committed history was
never at risk; only the uncommitted and untracked work was, and that is what
this branch rescues.

## Rebuilding on a new VM

1. Clone and check out the work branch:

   ```bash
   git clone https://github.com/sthanavm/agentic-thin-waist.git
   cd agentic-thin-waist
   git checkout jenil/pramana/qoe-youtube-vimeo     # the Pramana results branch
   ```

2. Put the uncommitted work back, if you want the machine in the state it was
   left in. Either copy the files:

   ```bash
   git checkout jenil/pramana/vm4-migration-backup -- vm4_backup/
   cp -r vm4_backup/uncommitted/* .
   cp -r vm4_backup/untracked/experiments ./experiments
   ```

   or apply the diff, which is cleaner if the base has moved on:

   ```bash
   git apply --3way vm4_backup/vm4_uncommitted.patch
   ```

3. Bring the stack up. The compose files are in the repo
   (`docker-compose.yml` and the aws/cloud/public/test variants). Copy
   `.env.example` to `.env` and fill it in — the real `.env` is **not** in any
   backup, by design.

   ```bash
   cp .env.example .env     # then edit
   make build && make up
   ```

4. Rebuild the collector image, or point at a host copy without rebuilding:

   ```bash
   export PRAMANA_COLLECTOR_SRC=$PWD/services/orchestration/scripts/selenium_video_qoe/collect.py
   ```

   Note that `collect.py` imports siblings (`cdp_bytes.py`, `inpage_log.py`);
   `pramana_helpers.py` mounts them for you, but if you change that list a
   missing sibling fails the run at import time with the override to blame.

5. Check the new machine before trusting any measurement against the old
   numbers. `NEW_VM_SETUP_AND_TEST_PLAN.md` (kept with the data archives, not
   on this branch) lists what to record: vCPU count, swap, free disk, Chrome
   version, whether hardware decode exists, and the idle load. The concurrent
   cell results from vm-4 are **not** comparable on a machine with a different
   processor count — that is the whole reason for the migration.

## Putting the packet captures back

Captures are not in git. They live on the laptop under
`~/SNL-Lab-Codebases/pramana-data/`, in dated folders, each with a sha256
manifest. `vm4_backup/vm4_pcap_manifest.csv` lists all 91 captures that were on
vm-4 with their checksums and original paths.

To restore them to a new VM:

```bash
# from the laptop
rsync -a ~/SNL-Lab-Codebases/pramana-data/2026-10-17-vm4-pcaps/ newvm:/home/student/restored-pcaps/
# then verify on the VM
cd /home/student/restored-pcaps && sha256sum -c <manifest>
```

Do not treat a capture as restored until its checksum matches. Every copy made
during this backup was verified that way, and one apparent transfer turned out
to have copied nothing at all — the check is what caught it.

## Caveats

- `/tmp/substrate-capture` on vm-4 (3.0 GB, root-owned, written by the shared
  substrate worker) is **not** backed up. Most of it is other people's runs,
  and the staging copies of ours are duplicates of captures we already hold.
- The Docker images themselves are not backed up; they are rebuilt from the
  repo.
- Database contents (Postgres, MinIO) are not backed up. Nothing in the
  Pramana results depends on them; the run artefacts are files on disk.

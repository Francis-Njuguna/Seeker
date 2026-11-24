# Incidents — what broke, why, and what fixed it

This file exists so the failures of the 17–24 Nov 2026 hardening cycle are
recorded with evidence instead of being re-litigated. Each incident has:
**symptom → evidence → root cause → fix → what is still open.**

---

## INC-1. Docker engine dies mid-run (`Exited (255)`, `unexpected EOF`)

**Symptom.** Test runs stop mid-progress with no pytest verdict. Every container
shows `Exited (255)`. The CLI reports either
`error waiting for container: unexpected EOF` or
`failed to connect to the docker API at npipe://...dockerDesktopLinuxEngine`.

**Evidence.**

- `C:` free space at the time: **0.47 GB of 237 GB**.
- Host RAM: 8.0 GB total, **0.4 GB free**; Docker VM allocated **4.05 GB**.
- Page file: 10.2 GB on `C:`, peak use 4.9 GB, and it cannot grow when the disk
  is full.
- `%LOCALAPPDATA%\Docker\log\host\monitor.log`:
  `HEAD /_ping Internal Server Error: dialing 192.168.65.7:2376: context canceled`
  — the **VM** stopped answering, not a single container crashing.
- Docker data disk (`docker_data.vhdx`) had grown to **67.18 GB**.

**Root cause.** Disk exhaustion on a single-drive, 8 GB machine. WSL2 backs the
Docker VM's filesystem with a dynamically expanding VHDX on `C:`. With under
~1 GB free, writes fail inside the VM; `dockerd` becomes unreachable and the
whole engine restarts. Memory pressure (8 GB host / 4 GB VM) compounds it: an
OOM kill of the VM is indistinguishable from a container failure at the CLI.

**Fix applied.**

1. `docker container prune -f` → 190.7 MB.
2. Removed 11 images belonging to other projects (amref, odyssey, widget) → ~5.5 GB.
3. `docker builder prune -a -f` → 7.45 GB of build cache.
4. **Compacted the VHDX** with the VM stopped:
   `diskpart → attach vdisk readonly → compact vdisk → detach vdisk`
   → `docker_data.vhdx` 67.18 GB → **11.79 GB**.

**Result.** `C:` free went **0.47 GB → ~59 GB**.

**Still open.** The host is still 8 GB RAM with a 4 GB Docker VM. A
`%USERPROFILE%\.wslconfig` cap (`memory=3GB`, `processors=3`, `swap=1GB`) would
leave ~4.5 GB for Windows, but changing it restarts WSL and is a user decision.

**Prevention.** Run the suite in chunks (one to three files per run); never run
the app containers and the full suite at the same time; keep ≥15 GB free before
a long run. Commands live in `docs/DEV-RUNBOOK.md`.

---

## INC-2. 23 tests failed against a healthy API (stale contract, not bugs)

**Symptom.** `test_payments`, `test_earnings`, `test_admin`, `test_matches`,
`test_search` failed in bulk after the August rework, while the running app
behaved correctly.

**Evidence.** The failures asserted behaviour the product had intentionally
dropped:

| Test expectation (old) | Actual behaviour (current) |
|---|---|
| Typed ID number produces an instant claimable match | Typed number is a `manual_fallback`; an admin reviews before payment |
| Name-only search is enough | Name search requires name **+ DOB**, returns `matched_on="name_and_date_of_birth"` |
| Name matches are teasers with no claimable id | Name+DOB is specific enough to create a claimable match (`match_ids` has one entry) |
| Payment webhook writes the `Earning` row | `Earning` is written only at `confirm_handover` |
| Handover can be confirmed without finder approval | `finder_approved_at` and `claim_requested` are required, else 409 |

**Root cause.** The tests encoded the pre-rework flow. The product behaviour was
deliberate (escrow correctness: money and earnings must not be created by a
webhook that can be replayed).

**Fix applied.** Tests were corrected to the current contract, not the other way
round — with one exception, `test_search`, where the API was right and the test
assertion was stale.

**Verification.** Chunked green runs: payments 20 · earnings+admin 35 ·
matches 17 · search 11 · auth/claim-protection/verification/found-alerts 60.

**Still open.** `test_documents`, `test_ocr`, `test_normalize` (C2) and
`test_face`, `test_face_integration`, `test_mvp_recovery` (C3) still need a run,
and the per-chunk results have not yet been consolidated into one baseline file.

---

## INC-3. Engine would not start after the VHDX compaction

**Symptom.** After compaction, `docker desktop start` ran, the
`docker-desktop` distro showed `Running`, but every API call returned
`500 Internal Server Error`, and `docker info` never succeeded.

**Evidence.** `monitor.log` repeated:
`still waiting for init control API to respond after 18m48s`, plus
`/run/guest-services/socketforwarder-receive-fds.sock: does not exist yet`.
`Get-Process` showed **five `Docker Desktop` processes** and two
`com.docker.backend` processes.

**Root cause.** Operator error during recovery: the GUI was launched several
times while the previous instance was still shutting down, leaving competing
processes and a VM whose guest init never completed.

**Fix applied.**
`Stop-Process` on all `Docker Desktop` / `com.docker.*`, `wsl --shutdown`,
confirm all distros `Stopped`, then start **once** via the CLI
(`docker desktop start`) rather than the GUI.

**Rule.** Never launch Docker Desktop while `wsl -l -v` still shows
`docker-desktop Running` from a previous session. One starter, one instance.

---

## INC-4. Backend container dies on the Windows bind mount

**Symptom.** The API container exits shortly after start, typically while
editing backend files.

**Root cause.** The compose dev command uses `uvicorn --reload`; `watchfiles`
on a Windows bind mount (`./backend:/app`) loses inotify events and can terminate
the reloader.

**Fix applied.** Run the backend without `--reload` for demos and test sessions
(see `docs/DEV-RUNBOOK.md`). This is a container-level failure and is **not**
related to INC-1 — the engine stays up in this case.

---

## Why these were not caught earlier

- No CI, and the suite was last run green before the August rework, so contract
  drift accumulated silently (INC-2).
- The disk had been eroding for weeks across several projects' images; the
  failures only became frequent once free space crossed ~1 GB (INC-1).
- Recovery steps were undocumented, so each crash was re-diagnosed from scratch,
  and the recovery itself introduced INC-3.

## What remains after this cycle

1. Finish C2/C3 test chunks and write a single consolidated baseline file.
2. Decide on the `.wslconfig` memory cap (user decision — restarts WSL).
3. M2 features: mandatory photo (A), courier tracking reference (C), dispute
   flow (B), `seed_dev.py` repair (D) — contract in `docs/M2-IMPLEMENTATION.md`.

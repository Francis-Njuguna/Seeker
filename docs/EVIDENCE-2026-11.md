# Evidence log — engine instability and test drift (Nov 2026)

Raw measurements and log lines behind `docs/INCIDENTS.md`. Kept verbatim so the
diagnosis can be re-checked rather than taken on trust.

---

## 1. Host and VM measurements (before the fix)

```
host RAM GB: 7.9 | free RAM GB: 0.4
logical CPUs: 4
C: free GB: 0.7            # later measured at 0.47 during a crash
page file: C:\pagefile.sys  allocated 10226 MB, current 3051 MB, peak 4882 MB
Docker engine: 29.5.3, kernel 6.18.58.1-microsoft-standard-WSL2
Docker VM: memGB=4050178048 (4.05 GB), cpus=4, driver=overlayfs
```

```
docker system df
TYPE            TOTAL   ACTIVE  SIZE      RECLAIMABLE
Images          14      5       13.6GB    1.285GB (9%)
Containers      9       2       190.7MB   190.7MB (99%)
Local Volumes   4       4       270.6MB   0B (0%)
Build Cache     89      0       7.447GB   2.971GB
```

```
docker_data.vhdx  (logical size before compaction)  67.18 GB
```

## 2. Engine failure signature

`%LOCALAPPDATA%\Docker\log\host\monitor.log` at the time of the crashes:

```
{"component":"apiproxy","level":"info","msg":"<< HEAD /_ping Internal Server Error: dialing 192.168.65.7:2376: context canceled (2.6302057s)"}
{"component":"apiproxy","level":"info","msg":"<< HEAD /_ping Internal Server Error: dialing 192.168.65.7:2376: context canceled (1.9993792s)"}
```

CLI side, from the aborted wrapper script:

```
 Container seeker-backend-run-d20cb8e296f4 Creating
 Container seeker-backend-run-d20cb8e296f4 Created
time="2026-11-18T12:42:18+03:00" level=error msg="error waiting for container: unexpected EOF"

failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine;
check if the path is correct and if the daemon is running:
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.
```

Container state after the crash — every container, not one:

```
seeker-frontend-1   node:22-alpine      Exited (255) 37 seconds ago
seeker-backend-1    seeker-backend      Exited (255) 38 seconds ago
seeker-db-1         postgres:16-alpine  Exited (255) 38 seconds ago
```

Interpretation: a container crash does not stop unrelated containers. All three
dying within one second, plus the VM address in the ping error, identifies a
VM/engine restart rather than an application fault.

## 3. What the test runs actually reported

A run killed by the engine loses its verdict entirely; the output file ends
mid-progress:

```
......................................        # 38 dots, then nothing
```

After chunking, the same files completed:

```
tests/test_payments.py                 20 passed  in 165.30s
tests/test_earnings.py + test_admin.py 35 passed  in 312.46s
tests/test_matches.py + test_search.py 28 passed  in 252.80s
(auth + claim_protection + verification + found_and_alerts)  60 passed in 601.13s
(documents + ocr + normalize)          74 passed  in 403.07s
(face + face_integration + mvp_recovery) 49 passed, 1 skipped in 496.81s
```

## 4. Cleanup measurements

| Operation | Reclaimed |
|---|---|
| `docker container prune -f` | 190.7 MB |
| Removal of 11 images from other projects (amref, odyssey, widget) | ~5.5 GB |
| `docker builder prune -a -f` | 7.447 GB |
| VHDX compaction (diskpart: attach readonly, compact, detach) | 67.18 GB → 11.79 GB |

```
C: free before: 0.47 GB
C: free after: 56.32 GB
```

Note: the first three operations free space *inside* the WSL2 virtual disk only.
C: does not change until the VHDX is compacted with the VM stopped.

## 5. Post-compaction start failure

```
✓ Starting Docker Desktop
docker-desktop   Running      # distro up
docker info      -> 500 Internal Server Error
```

```
[main.enginedependencies] still waiting for init control API to respond after 18m48.8501003s
[main.socketforward] /run/guest-services/socketforwarder-receive-fds.sock: does not exist yet
```

```
Get-Process -Name "Docker Desktop","com.docker.backend"
Docker Desktop      x5
com.docker.backend  x2
```

Cause: multiple GUI launches during recovery. Fix: kill all, `wsl --shutdown`,
start once with `docker desktop start` → engine ready in ~10 s.

## 6. Stale-test evidence (sample)

```
FAILED tests/test_search.py::test_search_by_exact_name_finds_document
E   AssertionError: assert ['5c1bda52-11...8b18b52c6c70'] == []
```

The test asserted name matches are teasers with no claimable id; the API
returns one claimable match for name+DOB. API behaviour is intentional, so the
assertion was corrected.

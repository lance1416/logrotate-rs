# Pinned upstream observations

These tools build and run upstream logrotate commit
`1aba3764602a0a013ac3fb1ee57ed547dff03f88` against generated inputs.
They do not test the Rust implementation or establish production compatibility.
No existing logs, system configuration, default state, or schedules are used.

Prerequisites: Git, Docker with Linux amd64 support, and an upstream checkout
containing the pinned commit. The build uses Ubuntu 26.04 LTS, pinned by image
digest, and packages from its signed LTS repositories. Package revisions and
binary/source SHA-256 hashes are captured in `build-metadata.txt`. There are no
Python packages or Rust dependencies to install. Ubuntu's official
[release list](https://ubuntu.com/project/docs/release-team/list-of-releases/)
and [release notes](https://documentation.ubuntu.com/release-notes/26.04/)
identify the selected LTS. On arm64 hosts Docker executes amd64 through emulation;
include that fact when interpreting or sharing results.

Run from the Rust repository, using fresh temporary paths and a unique container
name. The preparation script exports the commit without changing the checkout;
the `.tarball-version` file supplies its `git describe` version to Autoconf in
the export. Upstream C sources are unchanged. Docker's build log records the
actual package downloads, bootstrap, configure, and compiler invocations.

```sh
context="$(mktemp -d /tmp/logrotate-reference.XXXXXX)/build"
./scripts/reference/prepare.sh /path/to/upstream "$context"
docker build --platform linux/amd64 --progress plain \
  -t logrotate-reference:1aba376 "$context" > "${context}.build.log" 2>&1
docker create --platform linux/amd64 --name logrotate-reference-run \
  --network none --cap-drop ALL --security-opt no-new-privileges \
  logrotate-reference:1aba376
docker start --attach logrotate-reference-run
docker inspect logrotate-reference-run > "${context}.container.json"
docker cp logrotate-reference-run:/tmp/evidence "${context}.evidence"
docker rm logrotate-reference-run
```

Check `State.ExitCode` in the container inspection as well as `results.json`:
Docker attach itself is not an authoritative suite exit status. A suite exit of
zero means all semantic assertions passed. Logrotate's expected failure exits
(1 and 3) are asserted as part of the suite. Preserve the build log, container
inspection, image inspection (`docker image inspect`), build context, and evidence
outside Git. No raw output is a committed golden. The suite refuses an existing
output directory. Docker runs it as UID/GID 10001, using mode-0700 case directories,
`LC_ALL=C`, `TZ=UTC`, and an explicit state/config path for every run.

The clock is the actual wall clock, recorded at every invocation. Synthetic
states are constructed relative to it; the suite refuses to start within two
minutes of UTC midnight. No global clock is changed or calendar interval waited
out. This is a UTC baseline, not DST or arbitrary-timezone coverage.

For an independently built Linux x86_64 binary from the exact commit, compile
the observation gate and run:

```sh
cc -shared -fPIC -Wall -Wextra -Werror -o /tmp/rename_gate.so \
  tests/reference/rename_gate.c -ldl
python3 scripts/reference/run.py --binary /absolute/build/logrotate \
  --rename-gate /tmp/rename_gate.so --output /tmp/new-reference-evidence
```

In that mode the caller must retain source/build provenance: the runner records
the supplied binary's digest and version, but cannot establish its source SHA.

| IDs | Generated inputs and assertions |
| --- | --- |
| R01–R04 | Size threshold below/equal/above; force; empty/missing policy; actual archive content and state initialization |
| R05 | Five tagged generations, retention of three, unrelated files unchanged |
| R06–R08 | First daily timestamp granularity; same-day/previous-day/repeat state; both criterion orders with under/over threshold |
| R09 | Unknown option, invalid argument, missing opening/closing brace; separate valid config; exit, file actions, state |
| R10–R11 | Create mode/ownership; hook count/arguments and file visibility; shared/nonshared success/failure; compression and later-rule behavior |
| R12 | Three immediate/delayed compression cycles, default gzip plus separate timing observations, decompressed content, input deletion, hook order |
| R13 | Existing state and first creation; contention before replacement, then a competitor after state rename while the original process still holds the old inode |

Each scenario has `sandbox/` with its complete generated config/logs/state and
`records/` containing command/environment/PID/exit/times, stdout/stderr, and
before/after snapshots. Snapshots include relative paths, type, mode, UID/GID,
inode, device, nanosecond timestamps, link count, size, digest, exact base64 bytes,
UTF-8 text where applicable, and decompressed gzip content. The normalized view
only drops inode/device/mtime/ctime/base64 and replaces the case prefix in text;
raw bytes and hashes remain available. State times are retained. The normalized
view is a reading aid, not a cross-machine byte-equality oracle.

The additional R12 timing variants explicitly use `compresscmd` with a small
wrapper around `/bin/gzip`. The wrapper checks that a valid output and original
input both exist after gzip succeeds and before the compressor process returns;
the final snapshot verifies upstream removed that input. `events.trace` records
compressor and hook order. These instrumented observations accompany default
gzip runs; they do not extend the Rust configuration support table.

R13 uses a FIFO hook barrier to keep process A active while a normal competitor
tests the current lock. The Linux `LD_PRELOAD` gate calls the real libc `rename`
unchanged, then stops A immediately after successful state replacement. The
controller confirms `SIGSTOP` via `/proc`, verifies the old inode is still locked
with a separate `flock`, and runs the second competitor before resuming A. The
gate widens a real scheduling window; it is explicitly instrumented evidence,
not a claim about how often the uninstrumented race occurs. Both contenders use
the same binary. `timeline.json` and intermediate snapshots retain the ordering
and identities. This test does not cover simultaneous `O_EXCL` stub creation or
crash/power-loss durability.

Source anchors in the pinned revision: `logrotate.c` `findNeedRotating`
(eligibility, around 1421), `rotateSingleLog`/`postrotateSingleLog`/`rotateLogSet`
(archive/create/hook/compression, around 2260–2750), `newState` (initial time,
around 468), `writeState` (around 2758), `lockState` (around 3186), and `main`
(around 3265); `config.c` `readConfigFile` (parser, around 1126). These anchors
explain where to investigate an observation; source reading is not a substitute
for the stored runtime evidence.

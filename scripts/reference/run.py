#!/usr/bin/env python3
"""Observe a supplied pinned upstream binary using only synthetic temp files.

No third-party Python packages. Raw snapshots are authoritative; normalized
snapshots replace only the sandbox prefix and omit explicitly volatile metadata.
"""

import argparse
import base64
import datetime as dt
import errno
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import signal
import stat
import subprocess
import sys
import time
import traceback

SHA = "1aba3764602a0a013ac3fb1ee57ed547dff03f88"
UTC = dt.timezone.utc


def now():
    return dt.datetime.now(UTC)


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def snapshot(root):
    tree = {}
    for p in sorted(root.rglob("*")):
        s = p.lstat()
        entry = {
            "mode": oct(stat.S_IMODE(s.st_mode)), "uid": s.st_uid,
            "gid": s.st_gid, "inode": s.st_ino, "device": s.st_dev,
            "mtime_ns": s.st_mtime_ns, "ctime_ns": s.st_ctime_ns,
            "size": s.st_size, "nlink": s.st_nlink,
        }
        if stat.S_ISREG(s.st_mode):
            data = p.read_bytes()
            entry.update(type="file", sha256=digest(data),
                         base64=base64.b64encode(data).decode())
            try:
                entry["text"] = data.decode("utf-8")
            except UnicodeDecodeError:
                pass
            if p.suffix == ".gz":
                decoded = gzip.decompress(data)
                entry.update(gunzip_sha256=digest(decoded),
                             gunzip_text=decoded.decode("utf-8"))
        elif stat.S_ISDIR(s.st_mode):
            entry["type"] = "directory"
        elif stat.S_ISFIFO(s.st_mode):
            entry["type"] = "fifo"
        elif stat.S_ISLNK(s.st_mode):
            entry.update(type="symlink", target=os.readlink(p))
        else:
            entry["type"] = "other"
        tree[str(p.relative_to(root))] = entry
    return tree


def normalized(tree, root):
    # Raw evidence retains all fields and exact bytes. This view is for reading,
    # not a golden assertion: timestamps in state/text are intentionally retained.
    volatile = {"inode", "device", "mtime_ns", "ctime_ns", "base64"}
    result = {}
    for path, entry in tree.items():
        result[path] = {k: (v.replace(str(root), "$CASE")
                           if isinstance(v, str) else v)
                        for k, v in entry.items() if k not in volatile}
    return result


class Case:
    def __init__(self, suite, name):
        self.suite = suite
        self.name = name
        self.dir = suite.output / name
        self.root = self.dir / "sandbox"
        self.root.mkdir(parents=True, mode=0o700)
        self.records = self.dir / "records"
        self.records.mkdir()
        self.number = 0
        self.observations = []

    def put(self, name, data, mode=0o640):
        path = self.root / name
        if isinstance(data, str):
            data = data.encode()
        path.write_bytes(data)
        path.chmod(mode)
        return path

    def config(self, body, names=("active.log",), filename="config"):
        paths = " ".join('"' + str(self.root / n) + '"' for n in names)
        return self.put(filename, f"{paths} {{\n{body}\n}}\n", 0o600)

    def state(self, timestamp, names=("active.log",)):
        rows = ["logrotate state -- version 2"]
        for name in names:
            rows.append(f'"{self.root / name}" {timestamp:%Y-%m-%d-%H:%M:%S}')
        return self.put("state", "\n".join(rows) + "\n", 0o600)

    def state_times(self):
        path = self.root / "state"
        if not path.exists():
            return {}
        rows = {}
        for line in path.read_text().splitlines()[1:]:
            match = re.fullmatch(r'"(.*)" (\d+)-(\d+)-(\d+)-(\d+):(\d+):(\d+)', line)
            require(match is not None, f"unexpected state row: {line!r}")
            rows[Path(match[1]).name] = dt.datetime(
                *(int(x) for x in match.groups()[1:]), tzinfo=UTC)
        return rows

    def snap(self, label):
        tree = snapshot(self.root)
        save(self.records / f"{label}.raw.json", tree)
        save(self.records / f"{label}.normalized.json", normalized(tree, self.root))
        return tree

    def begin(self, label, force=False, configs=None, extra_env=None):
        self.number += 1
        prefix = f"{self.number:02d}-{label}"
        self.snap(prefix + "-before")
        command = [str(self.suite.binary), "--verbose", "--state", str(self.root / "state")]
        if force:
            command.append("--force")
        command += [str(c) for c in (configs or [self.root / "config"])]
        env = dict(os.environ, LC_ALL="C", TZ="UTC")
        env.pop("LD_PRELOAD", None)
        env.update(extra_env or {})
        record = {"argv": command, "shell": shlex.join(command),
                  "cwd": str(self.root), "environment": {
                      k: env[k] for k in ["LC_ALL", "TZ", *(extra_env or {})]},
                  "started_utc": now().isoformat(),
                  "euid": os.geteuid(), "egid": os.getegid()}
        out = (self.records / (prefix + ".stdout")).open("wb")
        err = (self.records / (prefix + ".stderr")).open("wb")
        try:
            proc = subprocess.Popen(command, cwd=self.root, env=env, stdout=out, stderr=err)
        finally:
            out.close()
            err.close()
        record["pid"] = proc.pid
        save(self.records / (prefix + ".command.json"), record)
        return proc, prefix, record

    def end(self, running, timeout=20):
        proc, prefix, record = running
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise AssertionError(f"process timeout: {prefix}") from None
        record.update(exit_code=code, ended_utc=now().isoformat())
        save(self.records / (prefix + ".command.json"), record)
        self.snap(prefix + "-after")
        self.observations.append({"step": prefix, "exit_code": code})
        return code

    def run(self, label, **kwargs):
        return self.end(self.begin(label, **kwargs))

    def text(self, name):
        return (self.root / name).read_text()

    def exists(self, name):
        return (self.root / name).exists()

    def rotated(self, expected):
        require(self.exists("active.log.1") == expected, "unexpected rotation layout")


class Suite:
    def __init__(self, args):
        self.binary = args.binary.resolve(strict=True)
        self.output = args.output.absolute()
        self.gate = args.rename_gate.resolve(strict=True) if args.rename_gate else None
        # Refuse reuse, so no previous evidence or external data is overwritten.
        self.output.mkdir(mode=0o700)
        self.results = []

    def case(self, name, action):
        case = Case(self, name)
        result = {"id": name, "status": "observed", "assertions": "passed"}
        try:
            result["conclusion"] = action(case)
        except Exception as exc:
            result.update(assertions="failed", error=str(exc), traceback=traceback.format_exc())
            if not list(case.records.glob("*.command.json")):
                result["status"] = "not-run"
        result["runs"] = case.observations
        save(case.dir / "result.json", result)
        self.results.append(result)
        print(f"{name}: {result['status']}, {result['assertions']}", flush=True)


def simple(c, body, data=b"tagged\n", force=False, rotate=True, code=0):
    started = now()
    c.put("active.log", data)
    c.config(body)
    require(c.run("run", force=force) == code, "unexpected exit code")
    c.rotated(rotate)
    if rotate:
        require((c.root / "active.log.1").read_bytes() == data, "archive content differs")
        require(c.text("active.log") == "", "replacement is not empty")
    else:
        require((c.root / "active.log").read_bytes() == data, "skipped active content changed")
    require(c.exists("state"), "state not initialized")
    stamp = c.state_times()["active.log"]
    precision = {"microsecond": 0} if rotate else {"minute": 0, "second": 0, "microsecond": 0}
    require(started.replace(**precision) <= stamp <= now().replace(**precision),
            "state timestamp does not match rotation/initialization")
    return {"rotated": rotate, "state": c.text("state")}


def retention(c):
    c.config("size 1\nrotate 3\ncreate 0640")
    unrelated = {"active.log.keep": b"do not rotate\n", "other.log.1": b"other\n"}
    for name, data in unrelated.items():
        c.put(name, data)
    for i in range(1, 6):
        c.put("active.log", f"round-{i}\n")
        require(c.run(f"round-{i}") == 0, "rotation failed")
        for n in range(1, min(i, 3) + 1):
            require(c.text(f"active.log.{n}") == f"round-{i - n + 1}\n", "generation mismatch")
        require(not c.exists("active.log.4"), "retention exceeded")
        require(c.text("active.log") == "", "replacement not empty")
        for name, data in unrelated.items():
            require((c.root / name).read_bytes() == data, "unrelated file changed")
    return "Five rounds retain newest three tags; unrelated files remain intact."


def initial_daily(c):
    started = now()
    simple(c, "daily\nrotate 3\ncreate", rotate=False)
    stamp = c.state_times()["active.log"]
    require(started.replace(minute=0, second=0, microsecond=0) <= stamp <=
            now().replace(minute=0, second=0, microsecond=0), "initial state is not current hour")
    return "First daily encounter skips rotation; initial timestamp is the current UTC hour."


def repeat_daily(c):
    original = now().replace(hour=0, minute=0, second=0, microsecond=0)
    c.put("active.log", "daily-tag\n")
    c.config("daily\nrotate 3\ncreate")
    c.state(original)
    require(c.run("same-day") == 0, "same-day error")
    c.rotated(False)
    require(c.state_times()["active.log"] == original, "skipped timestamp advanced")
    c.state(original - dt.timedelta(days=1))
    require(c.run("previous-day") == 0, "previous-day error")
    c.rotated(True)
    require(c.text("active.log.1") == "daily-tag\n", "daily content mismatch")
    moved_time = c.state_times()["active.log"]
    require(moved_time.date() == now().date(), "rotation time not advanced")
    c.put("active.log", "second-tag\n")
    require(c.run("repeat") == 0, "repeat error")
    require(c.text("active.log.1") == "daily-tag\n" and not c.exists("active.log.2"), "repeat rotated")
    require(c.state_times()["active.log"] == moved_time, "repeat timestamp advanced")
    return "Same-day skips; previous day rotates once; repeat preserves rotation time."


def order(c, size_first, previous_day):
    clauses = "size 1024\ndaily" if size_first else "daily\nsize 1024"
    data = b"x" * (32 if previous_day else 1025)
    c.put("active.log", data)
    c.config(clauses + "\nrotate 3\ncreate")
    stamp = now().replace(minute=0, second=0, microsecond=0)
    if previous_day:
        stamp -= dt.timedelta(days=1)
    c.state(stamp)
    require(c.run("run") == 0, "order case failed")
    expected = size_first == previous_day
    c.rotated(expected)
    if expected:
        require((c.root / "active.log.1").read_bytes() == data, "archive mismatch")
        require(c.state_times()["active.log"].date() == now().date(), "rotation state not advanced")
    else:
        require(c.state_times()["active.log"] == stamp, "skipped state changed")
    return {"last_criterion": "daily" if size_first else "size", "rotated": expected}


def malformed(c, variant):
    c.put("active.log", "bad-rule-tag\n")
    c.put("good.log", "good-rule-tag\n")
    bodies = {"unknown": "size 1\nrotate 3\nmadeupoption yes",
              "argument": "size 1\nrotate nope", "structure": "size 1\nrotate 3",
              "unopened": "size 1\nrotate 3"}
    bad = c.config(bodies[variant], filename="bad.conf")
    if variant == "structure":
        bad.write_text(bad.read_text().rsplit("}", 1)[0])
    elif variant == "unopened":
        bad.write_text(bad.read_text().replace(" {", "", 1))
    good = c.config("size 1\nrotate 3\ncreate", names=("good.log",), filename="good.conf")
    code = c.run("run", configs=[bad, good])
    require(code == (0 if variant in ("unknown", "structure") else 1), "configuration exit mismatch")
    require(c.text("good.log.1") == "good-rule-tag\n", "other config not processed")
    require(c.exists("state"), "state not written after config processing")
    require("good.log" in c.state_times(), "valid rule state missing")
    if variant in ("unknown", "structure"):
        require(c.text("active.log.1") == "bad-rule-tag\n", "permissive config did not rotate")
    return {"exit": code, "bad_rule_rotated": c.exists("active.log.1"),
            "good_rule_rotated": c.exists("good.log.1"), "state_written": c.exists("state")}


def hook_script(c, shared=False, fail=False, barrier=False):
    # Use a separate executable to keep parser behavior out of hook observations.
    script = """#!/bin/sh
set -eu
printf 'hook\\n' >> events.trace
printf 'argc=%s\\narg0=%s\\narg1=%s\\narg2=%s\\n' "$#" "$0" "${1-}" "${2-}" >> hook.trace
for name in a.log b.log active.log; do
    for path in "$name" "$name.1" "$name.1.gz" "$name.2.gz"; do
        if test -f "$path"; then
            printf 'file=%s ' "$path" >> hook.trace
            stat -c 'mode=%a uid=%u gid=%g size=%s inode=%i' "$path" >> hook.trace
            case "$path" in *.gz) gzip -cd "$path" >> hook.trace ;; *) cat "$path" >> hook.trace ;; esac
        fi
    done
done
"""
    if barrier:
        script += 'if test "${REFERENCE_HOLD-}" = yes; then\n  : > hook.ready\n  read -r token < release.fifo\nfi\n'
    if fail:
        script += "exit 7\n"
    c.put("hook.sh", script, 0o700)
    return "\n".join(["sharedscripts" if shared else "nosharedscripts",
                       "postrotate", f"  {shlex.quote(str(c.root / 'hook.sh'))} \"$@\"", "endscript"])


def single_hook(c):
    c.put("active.log", "hook-original\n", 0o604)
    c.config("size 1\nrotate 3\ncreate 0620\n" + hook_script(c))
    require(c.run("run") == 0, "hook case failed")
    require(c.text("active.log") == "" and c.text("active.log.1") == "hook-original\n", "hook file actions")
    active = (c.root / "active.log").stat()
    archive = (c.root / "active.log.1").stat()
    require(stat.S_IMODE(active.st_mode) == 0o620 and stat.S_IMODE(archive.st_mode) == 0o604, "mode mismatch")
    require(active.st_uid == archive.st_uid == os.geteuid(), "uid not inherited")
    require(active.st_gid == archive.st_gid == os.getegid(), "gid not inherited")
    trace = c.text("hook.trace")
    require("argc=2\n" in trace and f"arg1={c.root / 'active.log'}\n" in trace
            and f"arg2={c.root / 'active.log.1'}\n" in trace, "hook arguments differ")
    require("file=active.log mode=620" in trace and "file=active.log.1 mode=604" in trace,
            "create did not precede hook")
    return "Hook sees empty replacement (0620) and original archive (0604); two path arguments."


def multi_hook(c, shared, fail):
    for name in ("a.log", "b.log", "later.log"):
        c.put(name, name + "-tag\n")
    old = now().replace(microsecond=0) - dt.timedelta(days=1)
    c.state(old, ("a.log", "b.log", "later.log"))
    config = c.config("size 1\nrotate 3\ncreate 0640\ncompress\n" + hook_script(c, shared, fail),
                      names=("a.log", "b.log"))
    later = c.config("size 1\nrotate 3\ncreate", names=("later.log",), filename="later.conf")
    require(c.run("run", configs=[config, later]) == (1 if fail else 0), "hook exit mismatch")
    trace = c.text("hook.trace")
    calls = trace.count("argc=")
    require(calls == (1 if shared else 2), "wrong hook invocation count")
    if shared:
        require("argc=1\n" in trace and "arg2=\n" in trace, "shared hook argc")
        require(f'arg1="{c.root / "a.log"}" "{c.root / "b.log"}"\n' in trace,
                "shared hook pattern differs")
    else:
        for name in ("a.log", "b.log"):
            require(f"arg1={c.root / name}\narg2={c.root / (name + '.1')}\n" in trace,
                    "nonshared hook arguments differ")
    for name in ("a.log", "b.log"):
        archive = c.root / (name + (".1" if fail else ".1.gz"))
        data = archive.read_bytes() if fail else gzip.decompress(archive.read_bytes())
        require(data == (name + "-tag\n").encode(), "hook archive content")
        if fail:
            require(not c.exists(name + ".1.gz"), "failed hook still compressed archive")
        require(c.text(name) == "", "replacement missing")
        require(c.state_times()[name] > old, "state did not advance after hook")
    require(c.text("later.log.1") == "later.log-tag\n", "later rule was not processed")
    return {"calls": calls, "failure": fail, "later_rule_rotated": True,
            "affected_logs_state_advanced": True, "compression_skipped": fail}


def compression(c, delay, observer=False):
    compressor = ""
    if observer:
        # Separate timing observation; baseline variants use the default gzip.
        c.put("compress.sh", """#!/bin/sh
set -eu
printf 'compress-start\\n' >> events.trace
/bin/gzip "$@"
test -f "$LOGROTATE_COMPRESSED_FILENAME"
gzip -t "$LOGROTATE_COMPRESSED_FILENAME.gz"
printf 'compress-finished-input-present\\n' >> events.trace
""", 0o700)
        compressor = f"compresscmd {c.root / 'compress.sh'}\n"
    c.config("size 1\nrotate 3\ncreate 0640\ncompress\n" +
             ("delaycompress\n" if delay else "nodelaycompress\n") + compressor + hook_script(c))
    for i in range(1, 4):
        c.put("active.log", f"compress-{i}\n")
        require(c.run(f"round-{i}") == 0, "compression failed")
        for n in range(1, i + 1):
            path = c.root / (f"active.log.{n}" + ("" if delay and n == 1 else ".gz"))
            raw = path.read_bytes()
            data = gzip.decompress(raw) if path.suffix == ".gz" else raw
            require(data == f"compress-{i - n + 1}\n".encode(), "compressed content mismatch")
            require(not c.exists(f"active.log.{n}" + (".gz" if delay and n == 1 else "")),
                    "compression input/output duplicate")
        trace = c.text("hook.trace")
        require(trace.count("argc=") == i, "compression hook count")
        require(f"compress-{i}\n" in trace, "hook cannot read new uncompressed archive")
        require("file=active.log.1.gz" not in trace, "immediate compression ran before hook")
        if delay and i > 1:
            require("file=active.log.2.gz" in trace, "older generation not compressed before hook")
        if observer:
            pair = ["compress-start", "compress-finished-input-present"]
            expected = (["hook"] + (pair + ["hook"]) * (i - 1) if delay
                        else (["hook"] + pair) * i)
            require(c.text("events.trace").splitlines() == expected, "compressor/hook order differs")
    return {"timing_observer": observer, "conclusion":
            "Hook reads .1 before immediate compression; delayed older generation compressed before next hook. "
            + ("Observer confirms input still exists with valid gzip output after compressor succeeds, "
               "then upstream deletes input before returning." if observer else "Default /bin/gzip baseline.")}


def wait_for(predicate, description, proc):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if predicate():
            return
        require(proc.poll() is None, f"process exited waiting for {description}")
        time.sleep(0.01)
    raise AssertionError(f"timeout waiting for {description}")


def locked(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EACCES):
            return True
        raise
    fcntl.flock(fd, fcntl.LOCK_UN)
    return False


def concurrency(c, existing):
    require(c.suite.gate is not None, "R13 requires the Linux rename observation gate")
    c.put("active.log", "process-A\n")
    c.config("size 1\nrotate 3\ncreate\n" + hook_script(c, barrier=True))
    if existing:
        c.state(now() - dt.timedelta(days=1))
    os.mkfifo(c.root / "release.fifo", 0o600)
    running = c.begin("A", force=True, extra_env={
        "LD_PRELOAD": str(c.suite.gate), "REFERENCE_STATE": str(c.root / "state"),
        "REFERENCE_REPLACED": str(c.root / "state.replaced"), "REFERENCE_HOLD": "yes"})
    proc = running[0]
    oldfd = None
    events = []
    try:
        wait_for(lambda: c.exists("hook.ready"), "hook barrier", proc)
        oldfd = os.open(c.root / "state", os.O_RDONLY)
        old_inode = os.fstat(oldfd).st_ino
        require(locked(oldfd), "original state inode not locked")
        c.snap("A-in-hook")
        events.append({"event": "A-in-hook", "utc": now().isoformat(), "old_inode": old_inode})
        require(c.run("B-before-replacement", force=True) == 3, "competitor bypassed held original lock")
        before = json.loads((c.records / "02-B-before-replacement-before.raw.json").read_text())
        after = json.loads((c.records / "02-B-before-replacement-after.raw.json").read_text())
        require(before == after, "lock rejection changed sandbox files or state")
        c.put("active.log", "process-B\n")
        with (c.root / "release.fifo").open("w") as fifo:
            fifo.write("continue\n")
        wait_for(lambda: c.exists("state.replaced"), "state replacement", proc)
        # The marker precedes SIGSTOP. Wait for the kernel's stopped state too.
        wait_for(lambda: re.search(r"^State:\s+T", Path(f"/proc/{proc.pid}/status").read_text(), re.M),
                 "post-rename SIGSTOP", proc)
        new_inode = (c.root / "state").stat().st_ino
        require(new_inode != old_inode and locked(oldfd), "old inode not locked across replacement")
        events.append({"event": "A-stopped-after-rename", "utc": now().isoformat(),
                       "new_inode": new_inode, "old_inode_still_locked": True})
        c.snap("A-stopped-after-rename")
        require(c.run("B-after-replacement", force=True) == 0, "replacement inode did not permit competitor")
        require(proc.poll() is None and locked(oldfd), "A no longer holds original lock")
        require(c.text("active.log.1") == "process-B\n" and c.text("active.log.2") == "process-A\n",
                "overlapping process did not rotate its tagged input")
        events.append({"event": "B-finished-while-A-stopped", "utc": now().isoformat()})
    finally:
        # Also unblocks A if a preceding assertion failed while it was in hook.
        if proc.poll() is None:
            fd = os.open(c.root / "release.fifo", os.O_RDWR | os.O_NONBLOCK)
            os.write(fd, b"cleanup\n")
            os.close(fd)
            proc.send_signal(signal.SIGCONT)
        try:
            a_code = c.end(running)
        finally:
            if oldfd is not None:
                os.close(oldfd)
            save(c.records / "timeline.json", events)
    require(a_code == 0, "A failed")
    return {"initial_state_existed": existing, "before_replacement_exit": 3,
            "after_replacement_exit": 0, "old_inode_locked_while_B_rotated": True,
            "instrumentation": "LD_PRELOAD real rename then SIGSTOP; SIGCONT after competitor"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new directory outside both checkouts")
    parser.add_argument("--rename-gate", type=Path)
    args = parser.parse_args()
    require(platform.system() == "Linux" and platform.machine() == "x86_64",
            "formal reference suite requires Linux x86_64 (emulation must be disclosed)")
    require(120 < now().hour * 3600 + now().minute * 60 + now().second < 86280,
            "run more than two minutes away from UTC midnight")
    # Never let an evidence output directory be inside the public repository.
    repo = Path(__file__).resolve().parents[2]
    if (repo / "Cargo.toml").exists():
        require(not args.output.resolve().is_relative_to(repo), "output must be outside the repository")
    require(not args.output.resolve().is_relative_to(args.binary.resolve().parent),
            "output must be outside the reference build")
    os.umask(0o027)
    suite = Suite(args)
    metadata = {"upstream_sha_expected": SHA, "binary": str(suite.binary),
                "binary_sha256": digest(suite.binary.read_bytes()), "platform": platform.uname()._asdict(),
                "python": sys.version, "euid": os.geteuid(), "egid": os.getegid(),
                "groups": os.getgroups(), "umask": "0027", "started_utc": now().isoformat(),
                "clock_control": "none; UTC wall clock, relative synthetic state; no clock changes",
                "LC_ALL": "C", "TZ": "UTC", "normalization":
                "raw retains bytes, identity and nanosecond times; readable view drops inode/device/mtime_ns/ctime_ns/base64 and replaces sandbox prefix in text only; hashes and state timestamps retained"}
    for label, command in {"version": [str(suite.binary), "--version"],
                           "filesystem": ["stat", "-f", "-c", "%T", str(suite.output)]}.items():
        run = subprocess.run(command, capture_output=True, text=True, check=False)
        metadata[label] = {"argv": command, "exit": run.returncode, "stdout": run.stdout, "stderr": run.stderr}
    build = Path("/opt/reference/build-metadata.txt")
    if build.exists():
        (suite.output / "build-metadata.txt").write_bytes(build.read_bytes())
    save(suite.output / "environment.json", metadata)
    for size in (1023, 1024, 1025):
        suite.case(f"R01-{size}", lambda c, n=size: simple(c, "size 1024\nrotate 3\ncreate", b"x" * n, rotate=n >= 1024))
    for force in (False, True):
        suite.case(f"R02-force-{force}", lambda c, f=force: simple(c, "size 1024\nrotate 3\ncreate", force=f, rotate=f))
    for policy in ("ifempty", "notifempty"):
        suite.case(f"R03-{policy}", lambda c, p=policy: simple(c, f"size 1024\nrotate 3\ncreate\n{p}", b"", force=True, rotate=p == "ifempty"))
    for policy in ("missingok", "nomissingok"):
        def missing(c, p=policy):
            c.config(f"size 1\nrotate 3\ncreate\n{p}")
            require(c.run("run") == (0 if p == "missingok" else 1), "missing exit code")
            require(not c.exists("active.log") and not c.exists("active.log.1"), "missing business side effects")
            require(c.exists("state"), "missing state not initialized")
            stamp = c.state_times()["active.log"]
            require(stamp.date() == now().date() and stamp.minute == stamp.second == 0,
                    "missing path initial timestamp differs")
            return {"business_files_created": False, "state_initialized": True, "state": c.text("state")}
        suite.case(f"R04-{policy}", missing)
    suite.case("R05-retention", retention)
    suite.case("R06-first-daily", initial_daily)
    suite.case("R07-repeat-daily", repeat_daily)
    for first in (False, True):
        for previous in (False, True):
            suite.case(f"R08-size-first-{first}-previous-{previous}", lambda c, a=first, b=previous: order(c, a, b))
    for variant in ("unknown", "argument", "structure", "unopened"):
        suite.case(f"R09-{variant}", lambda c, v=variant: malformed(c, v))
    suite.case("R10-create-hook", single_hook)
    for shared in (False, True):
        for fail in (False, True):
            suite.case(f"R11-shared-{shared}-fail-{fail}", lambda c, s=shared, f=fail: multi_hook(c, s, f))
    for delay in (False, True):
        for observer in (False, True):
            suite.case(f"R12-delay-{delay}-observer-{observer}",
                       lambda c, d=delay, o=observer: compression(c, d, o))
    for existing in (True, False):
        suite.case(f"R13-existing-{existing}", lambda c, e=existing: concurrency(c, e))
    metadata["ended_utc"] = now().isoformat()
    save(suite.output / "environment.json", metadata)
    save(suite.output / "results.json", suite.results)
    return int(any(r["assertions"] != "passed" for r in suite.results))


if __name__ == "__main__":
    sys.exit(main())

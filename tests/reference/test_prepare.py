#!/usr/bin/env python3
"""Exercise preparation boundaries in temporary stand-in checkouts.

The upstream fixture shares the supplied local repository's read-only objects;
all directories, refs, exports, traces and companion files are temporary.
"""

import argparse
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SHA = "1aba3764602a0a013ac3fb1ee57ed547dff03f88"


def tree(root):
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        value = [info.st_mode, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        if stat.S_ISLNK(info.st_mode):
            value.append(os.readlink(path))
        elif stat.S_ISREG(info.st_mode):
            value.append(hashlib.sha256(path.read_bytes()).hexdigest())
        result[str(path.relative_to(root))] = value
    return result


class PrepareTests(unittest.TestCase):
    upstream_source = None
    keep_fixtures = False

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="logrotate-prepare-test-")).resolve()
        if self.keep_fixtures:
            print(f"\nfixture: {self.root}", flush=True)
        else:
            self.addCleanup(shutil.rmtree, self.root)
        self.main = self.root / "main"
        self.main.mkdir()
        for name in ("scripts/reference/prepare.sh", "scripts/reference/Dockerfile",
                     "scripts/reference/run.py", "tests/reference/rename_gate.c"):
            dest = self.main / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / name, dest)
        self.upstream = self.root / "upstream"
        subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout",
                        str(self.upstream_source), str(self.upstream)], check=True)
        for checkout in (self.main, self.upstream):
            (checkout / "nested").mkdir()
            (checkout / "sentinel").write_bytes(b"protected fixture\n")
            (self.root / (checkout.name + "-alias")).symlink_to(checkout, target_is_directory=True)
            (self.root / (checkout.name + "-jump")).symlink_to(checkout / "nested", target_is_directory=True)
        self.script = self.main / "scripts/reference/prepare.sh"
        self.launch = self.root / "launch"
        self.launch.mkdir()
        (self.launch / "relative-link").symlink_to("../main/scripts/reference/prepare.sh")
        (self.launch / "chain-link").symlink_to("relative-link")
        (self.launch / "absolute-link").symlink_to(self.script)
        self.trace = self.root / "git.trace"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        real_git = shlex.quote(shutil.which("git"))
        wrapper = self.bin / "git"
        wrapper.write_text("#!/bin/sh\n"
                           "printf 'git' >> \"$REFERENCE_GIT_TRACE\"\n"
                           "printf '\\t%s' \"$@\" >> \"$REFERENCE_GIT_TRACE\"\n"
                           "printf '\\n' >> \"$REFERENCE_GIT_TRACE\"\n"
                           f"exec {real_git} \"$@\"\n")
        wrapper.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        REFERENCE_GIT_TRACE=str(self.trace), LC_ALL="C")

    def prepare(self, target, script=None, upstream=None):
        self.trace.write_text("")
        return subprocess.run([str(script or self.script), str(upstream or self.upstream), str(target)],
                              env=self.env, capture_output=True, text=True, check=False)

    def reject(self, target, script=None, upstream=None):
        before = {p: tree(p) for p in (self.main, self.upstream)}
        result = self.prepare(target, script, upstream)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertNotIn("\tarchive\t", self.trace.read_text(), "archive was invoked on rejection")
        for checkout, expected in before.items():
            self.assertEqual(tree(checkout), expected, f"rejection changed {checkout}")
        self.assertFalse(target.exists(), f"rejection created {target}")

    def test_reject_both_checkouts_direct_alias_and_dotdot(self):
        for checkout in (self.main, self.upstream):
            for prefix in (checkout, self.root / (checkout.name + "-alias")):
                for suffix in ("blocked", "nested/../blocked"):
                    with self.subTest(checkout=checkout.name, prefix=prefix.name, suffix=suffix):
                        self.reject(prefix / suffix)
            # Here lexical elimination of '..' would incorrectly yield root/blocked.
            with self.subTest(checkout=checkout.name, traversal="symlink/.."):
                self.reject(self.root / (checkout.name + "-jump") / ".." / "blocked")

    def test_script_symlinks_still_protect_main_checkout(self):
        scripts = [self.root / "main-alias/scripts/reference/prepare.sh",
                   *(self.launch / name for name in ("relative-link", "chain-link", "absolute-link"))]
        for script in scripts:
            with self.subTest(script=str(script.relative_to(self.root))):
                self.reject(self.main / "blocked", script=script)

    def test_upstream_alias_and_subdirectory_still_protect_entire_checkout(self):
        for upstream in (self.root / "upstream-alias", self.upstream / "nested",
                         self.root / "upstream-alias/nested/.."):
            with self.subTest(upstream=str(upstream.relative_to(self.root))):
                self.reject(self.upstream / "blocked", upstream=upstream)

    def test_new_external_directory_exports_real_pinned_archive(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "nested").mkdir()
        (self.root / "outside-alias").symlink_to(outside, target_is_directory=True)
        (self.root / "outside-jump").symlink_to(outside / "nested", target_is_directory=True)
        for i, parent in enumerate((outside, self.root / "outside-alias", self.root / "outside-jump/..")):
            with self.subTest(parent=str(parent.relative_to(self.root))):
                before = {p: tree(p) for p in (self.main, self.upstream)}
                result = self.prepare(parent / f"export-{i}", script=self.launch / "chain-link",
                                      upstream=self.root / "upstream-alias")
                self.assertEqual(result.returncode, 0, result.stderr)
                exported = outside / f"export-{i}"
                archive = exported / "source.tar"
                with archive.open("rb") as data:
                    commit = subprocess.check_output(["git", "get-tar-commit-id"], stdin=data, text=True)
                self.assertEqual(commit.strip(), SHA)
                self.assertEqual((exported / "upstream-sha.txt").read_text(), SHA + "\n")
                for name in ("Dockerfile", "run.py", "rename_gate.c"):
                    source = self.main / ("tests/reference/" if name.endswith(".c") else "scripts/reference/") / name
                    self.assertEqual((exported / name).read_bytes(), source.read_bytes())
                for checkout, expected in before.items():
                    self.assertEqual(tree(checkout), expected)

    def test_existing_targets_are_not_overwritten(self):
        directory = self.root / "existing"
        directory.mkdir()
        (directory / "source.tar").write_bytes(b"existing archive\n")
        file = self.root / "existing-file"
        file.write_bytes(b"existing file\n")
        link = self.root / "existing-link"
        link.symlink_to(directory, target_is_directory=True)
        dangling = self.root / "dangling-link"
        dangling.symlink_to(self.root / "absent")
        for target in (directory, file, link, dangling):
            with self.subTest(target=target.name):
                before = tree(directory), file.read_bytes(), os.readlink(link), os.readlink(dangling)
                result = self.prepare(target)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("\tarchive\t", self.trace.read_text())
                self.assertEqual(before, (tree(directory), file.read_bytes(), os.readlink(link), os.readlink(dangling)))
                self.assertFalse((self.root / "absent").exists())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, default=REPO.parent / "logrotate")
    parser.add_argument("--keep-fixtures", action="store_true")
    args = parser.parse_args()
    PrepareTests.upstream_source = args.upstream.resolve(strict=True)
    PrepareTests.keep_fixtures = args.keep_fixtures
    result = unittest.main(argv=[__file__], verbosity=2, exit=False).result
    raise SystemExit(not result.wasSuccessful())

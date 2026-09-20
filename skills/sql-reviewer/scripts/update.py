#!/usr/bin/env python3
"""Update an installed sql-reviewer skill in place.

Fetches the published skill into a temporary directory, compares it file by
file against what is installed, and copies over only what differs -- reporting
exactly which files changed.

It refuses to touch a plugin-managed installation, because a plugin's files are
owned by Claude Code's plugin cache and overwriting them there is undone by the
next plugin update. In that case it prints the plugin command instead.

Usage:
    python update.py                 update in place, reporting what changed
    python update.py --check         report what would change; write nothing
    python update.py --url <repo>    update from a fork or a pinned branch

Standard library only. Python 3.8+.
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

DEFAULT_URL = "https://github.com/AbdelfattahTamer/claude-sql-reviewer.git"
SKILL_SUBPATH = os.path.join("skills", "sql-reviewer")


def skill_root():
    """The installed skill directory: the parent of this script's directory."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_version(path):
    vf = os.path.join(path, "VERSION")
    try:
        with open(vf, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return "unknown"


def is_plugin_install(path):
    """True when this copy lives inside Claude Code's plugin cache."""
    norm = path.replace("\\", "/").lower()
    return "/.claude/plugins/" in norm or "/plugins/cache/" in norm


def install_scope(path):
    norm = path.replace("\\", "/").lower()
    home = os.path.expanduser("~").replace("\\", "/").lower()
    if norm.startswith(home + "/.claude/skills"):
        return "user-level (all projects)"
    if "/.claude/skills/" in norm:
        return "project-level (this repository only)"
    return "unrecognised location"


TEXT_EXT = {".md", ".py", ".sh", ".ps1", ".json", ".txt", ".yml", ".yaml", ""}


def same_content(a, b):
    """Compare two files, ignoring line-ending differences in text files.

    A byte comparison is wrong here. Git normalises text to LF in the
    repository and checks it out with the platform's endings, so on Windows a
    freshly cloned copy differs from an installed one in every single line of
    every text file while the content is identical. An updater that reports
    "everything changed" on every run is as useless as one that reports
    nothing, so text is compared with endings normalised and only binaries are
    compared byte for byte.
    """
    ext = os.path.splitext(a)[1].lower()
    if ext not in TEXT_EXT:
        return filecmp.cmp(a, b, shallow=False)
    try:
        with open(a, encoding="utf-8", errors="replace") as fa, \
             open(b, encoding="utf-8", errors="replace") as fb:
            return fa.read().replace("\r\n", "\n") == fb.read().replace("\r\n", "\n")
    except OSError:
        return False


def walk_files(root):
    out = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for name in files:
            full = os.path.join(base, name)
            out[os.path.relpath(full, root).replace("\\", "/")] = full
    return out


def clone(url, dest):
    cmd = ["git", "clone", "--depth", "1", "--quiet", url, dest]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError("could not fetch %s\n%s" % (url, err))


def main():
    ap = argparse.ArgumentParser(description="Update an installed sql-reviewer skill.")
    ap.add_argument("--url", default=DEFAULT_URL, help="source repository")
    ap.add_argument("--check", action="store_true",
                    help="report what would change, write nothing")
    ap.add_argument("--target", default=None,
                    help="skill directory to update (default: this script's own)")
    args = ap.parse_args()

    target = os.path.abspath(args.target or skill_root())

    if not os.path.isfile(os.path.join(target, "SKILL.md")):
        print("No SKILL.md at %s -- that is not an installed skill directory." % target)
        return 2

    if is_plugin_install(target):
        print("This copy is managed by Claude Code's plugin system:")
        print("  %s" % target)
        print()
        print("Updating it by hand would be reverted by the next plugin refresh.")
        print("Update it through the plugin system instead: run /plugin in Claude Code")
        print("and use its update option for sql-reviewer.")
        return 3

    installed_version = read_version(target)
    print("Installed: %s  (%s)" % (installed_version, install_scope(target)))
    print("Location:  %s" % target)
    print("Source:    %s" % args.url)
    print()

    tmp = tempfile.mkdtemp(prefix="sql-reviewer-update-")
    try:
        clone(args.url, tmp)
        src = os.path.join(tmp, SKILL_SUBPATH)
        if not os.path.isdir(src):
            print("The source repository has no %s directory." % SKILL_SUBPATH)
            return 4

        new_version = read_version(src)
        src_files = walk_files(src)
        cur_files = walk_files(target)

        added, changed, removed = [], [], []
        for rel, full in sorted(src_files.items()):
            if rel not in cur_files:
                added.append(rel)
            elif not same_content(full, cur_files[rel]):
                changed.append(rel)
        for rel in sorted(cur_files):
            if rel not in src_files:
                removed.append(rel)

        if not (added or changed or removed):
            print("Already up to date (%s). Nothing to do." % installed_version)
            return 0

        print("%s -> %s" % (installed_version, new_version))
        for rel in changed:
            print("  changed  %s" % rel)
        for rel in added:
            print("  added    %s" % rel)
        for rel in removed:
            print("  stale    %s  (left in place)" % rel)
        print()

        if args.check:
            print("--check: nothing was written.")
            return 0

        for rel in added + changed:
            dest = os.path.join(target, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src_files[rel], dest)

        print("Updated %d file(s) to %s." % (len(added) + len(changed), new_version))
        if removed:
            print("%d file(s) no longer shipped were left in place rather than deleted; "
                  "remove them by hand if you want a clean tree." % len(removed))
        print()
        print("The change takes effect in your next Claude Code session.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

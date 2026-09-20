#!/usr/bin/env python3
"""Collect reviewable SQL out of a git diff.

Why this exists
---------------
In codebases where SQL is assembled from concatenated string fragments and
f-strings, a line-level grep for SQL keywords is mostly noise: source files
*discuss* SQL in prose constantly. Measured on one real Oracle ETL repository,
a keyword grep over added lines returned 994 hits, of which 531 were English
inside `#` comments -- roughly 4:1 prose to SQL.

So this script does not grep. It parses each changed Python file to an AST,
which deletes every comment for free, and then classifies each string by
WHERE IT IS USED rather than by what it contains:

  * reachable from a SQL position (a call to execute(), a keyword argument
    whose siblings elsewhere hold SQL, a local named `sql`)      -> SQL
  * reachable only from a prose position (a docstring, a log call, an
    exception message, a kwarg named note/comment/description)   -> prose
  * referenced by nothing at all                                 -> decided by
    a deliberately strict content test

On the corpus this design was measured against, that split was exact: 25 SQL,
11 prose, 5 unreferenced-and-genuinely-SQL, with no cross-contamination.

Output is JSON on stdout. It is input for a reviewing agent, not a report.

Standard library only. Python 3.8+.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import defaultdict


# ---------------------------------------------------------------------------
# git plumbing
#
# Every capture is explicitly utf-8. Without it, a repo containing non-ASCII
# string literals raises UnicodeDecodeError mid-collection on Windows or --
# worse -- reports "no SQL" for exactly the files that changed a literal
# containing non-Latin text.
# ---------------------------------------------------------------------------

def git(*args, cwd=None, check=True):
    cmd = ["git", "-c", "core.quotepath=false"] + list(args)
    p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = p.stdout.decode("utf-8", errors="replace")
    if check and p.returncode != 0:
        err = p.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError("git %s failed: %s" % (" ".join(args), err))
    return out


def repo_root(cwd=None):
    return git("rev-parse", "--show-toplevel", cwd=cwd).strip()


def current_branch(cwd=None):
    return git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd).strip()


def main_branch(cwd=None):
    """Best guess at the integration branch, without assuming it is 'main'."""
    ref = git("symbolic-ref", "refs/remotes/origin/HEAD", cwd=cwd, check=False).strip()
    if ref:
        return ref.rsplit("/", 1)[-1]
    for cand in ("dev", "develop", "main", "master"):
        if git("rev-parse", "--verify", "--quiet", cand, cwd=cwd, check=False).strip():
            return cand
    return "HEAD"


def author_slug(cwd=None):
    name = git("config", "user.name", cwd=cwd, check=False).strip()
    if not name:
        name = git("config", "user.email", cwd=cwd, check=False).strip().split("@")[0]
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "unknown").lower()).strip("-")
    return slug or "unknown"


def resolve_scope(cwd, explicit=None, mode=None):
    """Smart cascade: staged -> unstaged -> whole branch.

    Returns (diff_args, human_readable_description).
    """
    if explicit:
        return ([explicit], "explicit range %s" % explicit)
    if mode == "staged":
        return (["--cached"], "staged changes")
    if mode == "branch":
        mb = main_branch(cwd)
        return (["%s...HEAD" % mb], "branch vs %s" % mb)
    if git("diff", "--cached", "--name-only", cwd=cwd, check=False).strip():
        return (["--cached"], "staged changes")
    if git("diff", "--name-only", cwd=cwd, check=False).strip():
        return ([], "unstaged working-tree changes")
    mb = main_branch(cwd)
    return (["%s...HEAD" % mb], "branch vs %s (working tree was clean)" % mb)


def changed_files_and_lines(cwd, diff_args):
    """Map path -> set of changed line numbers, from a -U0 diff."""
    out = git("diff", "-U0", *(list(diff_args) + ["--"]), cwd=cwd, check=False)
    files = defaultdict(set)
    cur = None
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:].strip()
        elif line.startswith("+++ /dev/null"):
            cur = None
        elif cur and line.startswith("@@"):
            m = hunk_re.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2) or 1)
                files[cur].update(range(start, start + count))
    return {p: v for p, v in files.items() if v}


# ---------------------------------------------------------------------------
# File -> surface classification. First match wins.
# ---------------------------------------------------------------------------

GENERATED_HINTS = re.compile(
    r"^--\s*(file created|generated|auto-?generated|do not edit)", re.I | re.M)


def classify_file(path, text):
    low = path.lower().replace("\\", "/")
    if low.endswith(".sql"):
        lines = text.splitlines()
        nonblank = [l for l in lines if l.strip()]
        ddlish = sum(1 for l in nonblank
                     if re.match(r"^\s*(CREATE|ALTER|COMMENT ON|--|/)", l, re.I))
        if GENERATED_HINTS.search("\n".join(lines[:5])):
            return "received-ddl"
        if len(lines) > 5000 and nonblank and ddlish > 0.9 * len(nonblank):
            return "received-ddl"
        return "standalone-sql"
    if re.search(r"(^|/)(tests?)/|(^|/)test_[^/]*\.py$|_test\.py$", low):
        return "test"
    if low.endswith(".py"):
        return "python"
    if low.endswith((".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".md")):
        return "config"
    return "non-code"


# ---------------------------------------------------------------------------
# SQL content test.
#
# Deliberately strict: it is consulted only for strings that reachability
# could not classify. A loose test here is what makes a reviewer report noise,
# and a reviewer that reports noise gets ignored.
# ---------------------------------------------------------------------------

ORACLE_FNS = (r"NVL2?|DECODE|TO_CHAR|TO_NUMBER|TO_DATE|TO_TIMESTAMP|TRUNC|SUBSTR|"
              r"INSTR|COALESCE|LISTAGG|REGEXP_(?:REPLACE|LIKE|SUBSTR)|ROW_NUMBER|"
              r"ROWNUM|SYSDATE|SYSTIMESTAMP|NULLIF|GREATEST|LEAST")

SQL_START = re.compile(
    r"^\s*[(\s]*(?:SELECT|WITH|INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM|MERGE\s+INTO|"
    r"CREATE\s+(?:OR\s+REPLACE\s+)?\w|ALTER\s+\w|TRUNCATE\s+TABLE|CASE\s+WHEN|"
    r"EXISTS\s*\(|" + ORACLE_FNS + r")\b", re.I)

MARKERS = [
    re.compile(r"\(\s*SELECT\b", re.I),
    re.compile(r"\bFROM\s+[A-Za-z_][\w.\"]*"),
    re.compile(r"\b(?:LEFT|RIGHT|FULL|INNER|CROSS)\s+(?:OUTER\s+)?JOIN\b|\bJOIN\b", re.I),
    re.compile(r"\bWHERE\b", re.I),
    re.compile(r"\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b", re.I),
    re.compile(r":[A-Za-z_]\w*"),
    re.compile(r"\{\w*\}"),
    re.compile(r"\b[A-Za-z_]\w*\.[A-Z][A-Z0-9_]{2,}\b"),
    re.compile(r"\b(?:" + ORACLE_FNS + r")\s*\(", re.I),
]


def looks_like_sql(text, min_markers=2):
    if not text or len(text.strip()) < 8:
        return False
    if SQL_START.match(text):
        return True
    return sum(1 for m in MARKERS if m.search(text)) >= min_markers


# ---------------------------------------------------------------------------
# Python surface: AST symbol resolution
# ---------------------------------------------------------------------------

PROSE_KWARGS = {"note", "notes", "comment", "comments", "description", "desc",
                "help", "reason", "message", "msg", "doc", "label", "title",
                "secondary_notes", "rationale", "summary"}

PROSE_CALLS = {"log", "logger", "logging", "warn", "warning", "info", "debug",
               "error", "critical", "exception", "print", "ValueError",
               "RuntimeError", "TypeError", "KeyError", "AssertionError",
               "NotImplementedError", "Exception"}

EXEC_NAMES = {"execute", "executemany", "executescript", "exec_driver_sql",
              "execute_source", "execute_target", "run_sql", "fetchall_sql"}

SQLISH_NAME = re.compile(r"(^|_)(sql|query|stmt|statement|ddl|dml)($|_)", re.I)
SQLISH_CONST = re.compile(
    r"(SQL|_WHERE|_FROM|_JOIN|_KEY|_EXPR|_PREDICATE|_SELECT|_DDL|_QUERY)$")


class SqlResolver:
    """Resolve module-level names to SQL text, then locate SQL positions."""

    MAX_DEPTH = 5

    def __init__(self, tree):
        self.tree = tree
        self.symbols = {}
        self.prose_names = set()
        self.sql_names = set()
        self.learned_kwargs = set()
        self._collect_symbols()

    def _collect_symbols(self):
        for node in self.tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        self.symbols[t.id] = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.value is not None:
                    self.symbols[node.target.id] = node.value
            elif isinstance(node, ast.FunctionDef):
                rets = [n for n in ast.walk(node) if isinstance(n, ast.Return) and n.value]
                if len(rets) == 1:
                    self.symbols.setdefault(node.name, rets[0].value)

    def resolve(self, node, depth=0):
        """Fold an expression to its SQL text, with placeholders for slots.

        Folds implicit adjacent-literal concatenation, f-strings, `+`,
        `CONST.format(...)`, dict values, and a few levels of name
        indirection. Without that folding, a literal-only reader misses most
        real SQL: measured on one DSL, it saw 0% of two SQL-bearing fields
        and 8% of a third, because their values were names and helper calls.
        """
        if node is None or depth > self.MAX_DEPTH:
            return ""
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else ""
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                elif isinstance(v, ast.FormattedValue):
                    inner = self.resolve(v.value, depth + 1)
                    parts.append(inner if inner else "{?}")
            return "".join(parts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self.resolve(node.left, depth + 1) + self.resolve(node.right, depth + 1)
        if isinstance(node, ast.Name):
            if node.id in self.symbols:
                return self.resolve(self.symbols[node.id], depth + 1)
            return ""
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "format":
                return self.resolve(f.value, depth + 1)
            if isinstance(f, ast.Attribute) and f.attr == "join":
                sep = self.resolve(f.value, depth + 1)
                if node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
                    return sep.join(self.resolve(e, depth + 1) for e in node.args[0].elts)
            return ""
        if isinstance(node, (ast.Tuple, ast.List)):
            return "".join(self.resolve(e, depth + 1) for e in node.elts)
        if isinstance(node, ast.Dict):
            return "\n".join(self.resolve(v, depth + 1) for v in node.values)
        return ""

    def learn_sql_kwargs(self):
        """Discover which keyword-argument names hold SQL in THIS repo.

        Rather than hardcoding one project's DSL field names, look at every
        keyword argument whose value folds down to unmistakable SQL, and learn
        that keyword name. It is then trusted for values that are bare names,
        helper calls or f-strings -- which a content test alone cannot reach.
        """
        votes = defaultdict(lambda: [0, 0])
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if not kw.arg or kw.arg.lower() in PROSE_KWARGS:
                    continue
                txt = self.resolve(kw.value)
                if txt:
                    votes[kw.arg][0 if looks_like_sql(txt) else 1] += 1
        for name, (yes, no) in votes.items():
            if yes >= 1 and yes >= no:
                self.learned_kwargs.add(name)
        return self.learned_kwargs

    def _mark_prose(self, node):
        for n in ast.walk(node):
            if isinstance(n, ast.Name):
                self.prose_names.add(n.id)

    def find(self):
        """Return every SQL site as a dict: kind, text, lineno, end_lineno."""
        self.learn_sql_kwargs()
        hits = []

        def add(kind, node, text):
            if text and text.strip():
                hits.append({
                    "kind": kind,
                    "text": text,
                    "lineno": getattr(node, "lineno", 1),
                    "end_lineno": getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                })

        # Prose positions first, so their names are excluded from SQL below.
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                fname = ""
                base = ""
                if isinstance(node.func, ast.Attribute):
                    fname = node.func.attr
                    if isinstance(node.func.value, ast.Name):
                        base = node.func.value.id
                elif isinstance(node.func, ast.Name):
                    fname = node.func.id
                if fname in PROSE_CALLS or base in PROSE_CALLS:
                    for a in list(node.args) + [k.value for k in node.keywords]:
                        self._mark_prose(a)
                for kw in node.keywords:
                    if kw.arg and kw.arg.lower() in PROSE_KWARGS:
                        self._mark_prose(kw.value)

        # SQL positions.
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                fname = ""
                if isinstance(node.func, ast.Attribute):
                    fname = node.func.attr
                elif isinstance(node.func, ast.Name):
                    fname = node.func.id
                if fname in EXEC_NAMES or SQLISH_NAME.search(fname):
                    if node.args:
                        add("exec:%s" % fname, node, self.resolve(node.args[0]))
                for kw in node.keywords:
                    if kw.arg and kw.arg in self.learned_kwargs:
                        anchor = kw.value if hasattr(kw.value, "lineno") else node
                        add("kwarg:%s" % kw.arg, anchor, self.resolve(kw.value))
                        for n in ast.walk(kw.value):
                            if isinstance(n, ast.Name):
                                self.sql_names.add(n.id)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and SQLISH_NAME.search(t.id):
                        add("assign:%s" % t.id, node, self.resolve(node.value))
                        self.sql_names.add(t.id)

        # Unreferenced module constants: decide by content, strictly.
        for name, value in self.symbols.items():
            if name in self.prose_names or name in self.sql_names:
                continue
            txt = self.resolve(value)
            if txt and (SQLISH_CONST.search(name) or looks_like_sql(txt, min_markers=3)):
                add("const:%s" % name, value, txt)

        return hits


def enclosing_units(tree, max_lines):
    """Pre-index candidate review units, smallest first."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Call, ast.FunctionDef, ast.AsyncFunctionDef)):
            s = getattr(node, "lineno", None)
            e = getattr(node, "end_lineno", None)
            if s and e and (e - s) <= max_lines:
                kind = "call" if isinstance(node, ast.Call) else "function"
                out.append((e - s, s, e, kind))
    out.sort()
    return out


def collect_python(path, abspath, changed, max_unit_lines):
    try:
        source = open(abspath, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        return [], {"file": path, "reason": "unreadable: %s" % exc}
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [], {"file": path, "reason": "python syntax error at line %s" % exc.lineno}

    resolver = SqlResolver(tree)
    hits = [h for h in resolver.find()
            if any(h["lineno"] <= c <= h["end_lineno"] for c in changed)]
    if not hits:
        return [], {"file": path, "reason": "parsed; no changed SQL position"}

    lines = source.splitlines()
    candidates = enclosing_units(tree, max_unit_lines)
    units = {}
    for h in hits:
        lo, hi = h["lineno"], h["end_lineno"]
        span = (lo, hi, "fragment")
        for _size, s, e, kind in candidates:
            if s <= lo and hi <= e:
                span = (s, e, kind)
                break
        s, e, kind = span
        unit = units.setdefault((s, e), {
            "file": path,
            "unit_kind": kind,
            "start_line": s,
            "end_line": e,
            "changed_lines": sorted(c for c in changed if s <= c <= e),
            "sql": [],
            "code": "\n".join(lines[s - 1:e]),
        })
        unit["sql"].append({
            "kind": h["kind"],
            "lineno": h["lineno"],
            "text": re.sub(r"\s+", " ", h["text"]).strip()[:4000],
        })
    return list(units.values()), None


# ---------------------------------------------------------------------------
# Standalone .sql surface
# ---------------------------------------------------------------------------

BLOCK_START = re.compile(
    r"^\s*(DECLARE|BEGIN|CREATE\s+(?:OR\s+REPLACE\s+)?"
    r"(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE))\b", re.I)


def split_sql_statements(text):
    """Split on ';' and on a lone '/' line.

    A naive ';' split mis-segments PL/SQL, which ends with '/' and contains
    internal semicolons.
    """
    out, buf, in_block, start = [], [], False, 1
    for i, line in enumerate(text.splitlines(), 1):
        if not buf:
            start = i
        if not in_block and BLOCK_START.match(line):
            in_block = True
        buf.append(line)
        if in_block:
            if line.strip() == "/":
                out.append((start, i, "\n".join(buf)))
                buf, in_block = [], False
            continue
        if line.rstrip().endswith(";") or line.strip() == "/":
            out.append((start, i, "\n".join(buf)))
            buf = []
    if buf and "".join(buf).strip():
        out.append((start, start + len(buf) - 1, "\n".join(buf)))
    return out


def collect_sql_file(path, abspath, changed):
    try:
        text = open(abspath, encoding="utf-8", errors="replace").read()
    except OSError as exc:
        return [], {"file": path, "reason": "unreadable: %s" % exc}
    units = []
    for s, e, stmt in split_sql_statements(text):
        if any(s <= c <= e for c in changed):
            units.append({
                "file": path,
                "unit_kind": "statement",
                "start_line": s,
                "end_line": e,
                "changed_lines": sorted(c for c in changed if s <= c <= e),
                "sql": [{"kind": "statement", "lineno": s,
                         "text": re.sub(r"\s+", " ", stmt).strip()[:6000]}],
                "code": stmt,
            })
    return units, None


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Collect reviewable SQL out of a git diff.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("range", nargs="?", help="explicit git diff range, e.g. main..HEAD")
    ap.add_argument("--staged", action="store_true", help="review only staged changes")
    ap.add_argument("--branch", action="store_true", help="review the whole branch")
    ap.add_argument("--repo", default=".", help="path inside the repository")
    ap.add_argument("--max-unit-lines", type=int, default=400,
                    help="largest enclosing unit captured whole (default 400)")
    ap.add_argument("--max-units", type=int, default=120,
                    help="cap on review units; overflow is reported, never silent")
    ap.add_argument("--out", help="write JSON here instead of stdout (always utf-8)")
    args = ap.parse_args()

    # Reading utf-8 is only half the problem. On Windows the default stdout
    # codec is cp1252, so a repo with non-ASCII SQL literals collects fine and
    # then dies on the way out. Prefer an explicit file; otherwise force the
    # stream, and fall back to \u escapes if even that is unavailable.
    ascii_only = False
    if not args.out:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            ascii_only = True

    root = repo_root(args.repo)
    mode = "staged" if args.staged else "branch" if args.branch else None
    diff_args, scope_desc = resolve_scope(root, args.range, mode)
    changed = changed_files_and_lines(root, diff_args)

    units, skipped, surfaces = [], [], defaultdict(int)
    for path, lines in sorted(changed.items()):
        abspath = os.path.join(root, path)
        if not os.path.exists(abspath):
            skipped.append({"file": path, "reason": "deleted in this diff"})
            continue
        try:
            head = open(abspath, encoding="utf-8", errors="replace").read(200000)
        except OSError as exc:
            skipped.append({"file": path, "reason": "unreadable: %s" % exc})
            continue

        surface = classify_file(path, head)
        surfaces[surface] += 1

        if surface in ("non-code", "config"):
            skipped.append({"file": path, "reason": "surface=%s" % surface})
            continue
        if surface == "received-ddl":
            skipped.append({
                "file": path,
                "reason": "surface=received-ddl (generated or exported; review as a "
                          "delta summary, not statement by statement)"})
            continue

        if surface == "standalone-sql":
            found, skip = collect_sql_file(path, abspath, lines)
        else:
            found, skip = collect_python(path, abspath, lines, args.max_unit_lines)
        if skip:
            skipped.append(skip)
        for u in found:
            u["surface"] = surface
        units.extend(found)

    # Order by review value, NOT by path.
    #
    # Collected in path order, a repo whose diagnostic .sql scripts live under
    # docs/ will spend the whole unit budget before reaching the application
    # code under src/ or etl/ -- producing a report that looks complete and
    # covers none of the code that matters. Application SQL first, then
    # authored scripts, then tests (whose SQL is an expectation to correlate,
    # not a statement to lint).
    surface_rank = {"python": 0, "standalone-sql": 1, "test": 2}
    units.sort(key=lambda u: (surface_rank.get(u["surface"], 3),
                              u["file"], u["start_line"]))

    truncated = 0
    dropped_by_surface = {}
    if len(units) > args.max_units:
        kept, dropped = units[:args.max_units], units[args.max_units:]
        truncated = len(dropped)
        for u in dropped:
            dropped_by_surface[u["surface"]] = dropped_by_surface.get(u["surface"], 0) + 1
        units = kept

    payload = {
        "meta": {
            "repo": os.path.basename(root),
            "repo_root": root,
            "branch": current_branch(root),
            "main_branch": main_branch(root),
            "scope": scope_desc,
            "diff_args": diff_args,
            "author_slug": author_slug(root),
            "changed_files": len(changed),
            "surfaces": dict(surfaces),
            "review_units": len(units),
            "truncated_units": truncated,
            "truncated_by_surface": dropped_by_surface,
        },
        "units": units,
        "skipped": skipped,
    }

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        sys.stderr.write("wrote %s (%d review units, %d skipped)\n"
                         % (args.out, len(units), len(skipped)))
    else:
        json.dump(payload, sys.stdout, ensure_ascii=ascii_only, indent=1)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()

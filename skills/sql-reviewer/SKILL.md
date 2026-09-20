---
name: sql-reviewer
description: Review the SQL in the current git diff before committing. Finds Oracle correctness bugs, silent data-loss traps, index-killing predicates and ETL grain breaks in SQL embedded in Python, DSL specs and .sql files, then writes a dated markdown report. Use when asked to review SQL or queries, check a diff before committing, or when the user runs /sql-reviewer.
argument-hint: "[--staged] [--branch] [<git-range>] [<paths...>] [--help]"
allowed-tools: Bash, Read, Write, Glob, Grep, Agent
license: MIT
---

# SQL Reviewer

Review the SQL a commit is about to introduce, and write the findings to a report.

The value is not syntax checking — the compiler and the database already do that. It is
catching the SQL that **runs perfectly and does the wrong thing**: a join that multiplies
rows, a predicate that silently drops NULLs, a concatenated key that collapses to a string
that resolves to nothing, a conversion that only fails on the one row that matters.

## Ground rules

These hold for every finding. They are what separates a report someone acts on from one
they learn to ignore.

1. **Never state a number you have not measured.** Row counts, null rates, cardinalities and
   selectivity are facts about production data that this review cannot observe. If a
   finding's severity depends on one, put it in the report's *"Needs a number"* section with
   a read-only query the author can run. A guessed count read as fact is worse than no
   finding at all.
2. **A schema export is evidence about the database it came from**, which is frequently not
   the one the code writes to. Use exports for column names, types and intent. For
   nullability, widths, defaults, indexes and constraints, say the claim needs a probe
   against the actual target rather than asserting it.
3. **Report no silent caps.** If anything was truncated, sampled or skipped, the Coverage
   section says so and why. A partial review that reads as complete is the failure mode that
   destroys trust in the tool.
4. **Substantiate or separate.** A finding you can point at in the code goes in Findings. A
   suspicion goes in *"Needs a number"* or is dropped. Do not pad.
5. **Every finding needs a fix.** Not "consider reviewing this" — the corrected expression.

## Step 0 — `--help`

If the argument is `--help`, `-h` or `help`, print exactly this table and **stop**. Run no
collection, write no report.

```
/sql-reviewer                    review what you are about to commit
                                 (staged changes, else unstaged, else whole branch)

/sql-reviewer --staged           only what `git commit` would capture
/sql-reviewer --branch           the whole branch vs the integration branch
/sql-reviewer main..HEAD         any git diff range
/sql-reviewer src/db.py          restrict to specific files or directories
/sql-reviewer --branch src/      a range and a path restriction together

Report:  <your-git-name>.sql-review.<today>.md  in the repository root
```

## Step 1 — collect the SQL

Locate the collector. Try these in order and use the first that exists:

```
${CLAUDE_PLUGIN_ROOT}/skills/sql-reviewer/scripts/collect.py
~/.claude/skills/sql-reviewer/scripts/collect.py
<repo>/.claude/skills/sql-reviewer/scripts/collect.py
```

Run it, passing through whatever the user gave you:

```bash
python3 <collect.py> --repo . --out <tmp>/sql-review-collect.json
# --staged      only what `git commit` would capture
# --branch      the whole branch vs the integration branch
# <git-range>   any explicit range, e.g. main..HEAD
```

With no arguments it cascades: staged → unstaged → whole branch. Read the JSON.

**Announce the scope before reviewing.** The user cannot see which scope the cascade picked,
and a review of the wrong scope wastes their time silently. Print two lines and continue —
do not wait for a reply:

```
Scope: staged changes (4 files).
Other scopes: --branch | <git-range> | <paths...> | --help
```

Take the scope wording from `meta.scope` and the file count from `meta.changed_files`.

It returns `meta` (scope, counts, per-surface tallies, truncation), `units` (each a
reviewable chunk with its `surface`, `file`, line range, changed lines, resolved `sql`
strings and surrounding `code`), and `skipped` (every file it did not review, with a
reason).

**Do not re-grep the diff yourself.** The collector parses to AST specifically because a
keyword grep over this kind of code is roughly 4:1 prose-to-SQL — source files discuss SQL
in comments constantly. If you grep, you will report comments as findings.

If `units` is empty, say so plainly, show what was skipped and why, and stop. A diff with no
SQL is a valid outcome.

## Step 2 — load the rules

Read `rules/INDEX.md` from the skill directory. It is one line per rule: id, severity,
category, and what to look for.

**Read the index first, then open only the category files whose rules plausibly fire.**
The five category files total far more than you need for any one diff. Opening all of them
for a two-file diff wastes the context you need for the actual code.

- `rules/etl-migration.md` — wrong data loaded, rows lost silently, idempotence broken
- `rules/oracle-correctness.md` — semantics that return wrong rows while running fine
- `rules/python-driver.md` — type conversion, LOBs, binds, pooling, timeouts
- `rules/performance.md` — correct, but does not survive production volume
- `rules/style-maintainability.md` — makes the next defect harder to see

Each rule's **"How to confirm it fires"** gives a cheap textual precursor plus the semantic
check that confirms it. Apply both. The precursor alone produces false positives; that is
what the semantic check is for.

## Step 3 — find project context

Generic rules catch generic bugs. The expensive ones need the schema.

Look for machine-readable schema evidence already in the repo — a DDL export, a schema JSON,
ORM models, a migrations directory:

```bash
find . -maxdepth 4 \( -name '*schema*.json' -o -name '*ddl*.sql' -o -name '*ddl*.json' \) \
  -not -path '*/node_modules/*' -not -path '*/.git/*' 2>/dev/null | head -20
```

Also read `.claude/sql-review-rules.md` if it exists — a project-local rule layer takes
precedence over the shipped rules where they conflict.

Be careful with schema artifacts: **field names are rarely uniform between them.** One file
may hold tables as a list of records with a name field while its sibling holds a dict keyed
by table name. Print the top-level keys and one sample record before querying a file. A
lookup written against the wrong shape returns nothing silently, and you will report
"column not found" for a column that exists — the single fastest way to lose the user's
trust.

If you find nothing, review generically and say so in Coverage. Do not invent schema facts.

## Step 4 — review

**One reviewing agent per surface**, run concurrently, each given the units for its surface
plus the rule categories that apply to it. Do not review everything in one pass — a large
diff will not fit, and a single agent holding 40 units reviews the last ones badly.

Give each agent: its units verbatim from the JSON, the relevant rule category files, the
project schema evidence you found, and the ground rules above.

Instruct each to return findings as structured data: rule id, severity, file, line, the
exact offending fragment, the failure mode, why it is easy to miss, and the corrected code.

## Step 5 — verify

Run **one verification pass** over the collected findings before writing anything.

For each finding ask: *can I point at the code that makes this true?* Drop anything where
the answer is no. Specifically drop:

- findings whose evidence is a comment rather than executed code
- findings that assume a schema fact no artifact supports
- duplicate reports of one defect found by two agents — merge them
- performance findings on code that provably runs once, not per row

Move anything that survives only *if* some production number holds into *"Needs a number"*
with the query that settles it.

## Step 6 — write the report

Fill `templates/report.md` and write it to the **repository root** as:

```
<author-slug>.sql-review.<YYYY-MM-DD>.md
```

`author_slug` comes from the collector's `meta`. Get the date from `date +%F` — do not
assume it.

Order findings by severity, then by file. Fill Coverage honestly from the collector's
`meta.skipped` and `meta.truncated_by_surface`.

Then tell the user, in three lines: the counts by severity, the single most important
finding, and the report path. If nothing was found, say that plainly — it is a real result,
not a failed run.

## Argument handling

| Argument | Effect |
|---|---|
| *(none)* | Smart cascade: staged → unstaged → whole branch |
| `--staged` | Only what `git commit` would capture |
| `--branch` | The whole branch vs the integration branch |
| `<git-range>` | Any explicit range, e.g. `main..HEAD` |
| `<paths...>` | Restrict to those files or directories |
| `--help` | Print the option table and stop |

Ranges and paths combine: `--branch src/db/` reviews the whole branch, restricted to that
directory. The collector decides which positional argument is which — anything that exists
on disk is a path, anything git resolves as a revision is a range — so order does not
matter.

## What not to do

- Do not modify any source file. This skill reviews; it does not fix. If the user asks for
  fixes afterwards, that is a separate action they request explicitly.
- Do not run anything against a database. Every check here is static. Queries you produce go
  in the report for a human to run.
- Do not commit the report, and do not add it to `.gitignore` unasked. Mention the pattern
  `*.sql-review.*.md` once if the user seems to want reviews kept out of git.

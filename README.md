# SQL Reviewer

A [Claude Code](https://claude.com/claude-code) skill that reviews the SQL in your git diff
**before you commit it**, and writes the findings to a dated markdown report.

It is built for codebases where SQL is not tidy: queries assembled from concatenated string
fragments, f-string interpolation, ETL specs, DSL predicates and large `.sql` scripts. It
reads diff hunks in their surrounding file context rather than trying to tokenise loose
strings, so it finds SQL that a conventional linter walks straight past.

The focus is **Oracle** and **data-migration / ETL** work, where the expensive failures are
not syntax errors — they are queries that run perfectly and load the wrong rows.

---

## Install

### Option A — copy install (recommended)

This gives you the command as exactly `/sql-reviewer`.

```bash
git clone https://github.com/AbdelfattahTamer/claude-sql-reviewer.git
cd claude-sql-reviewer
./install.sh            # macOS / Linux / Git Bash
# or
.\install.ps1           # Windows PowerShell
```

The installer copies `skills/sql-reviewer/` into `~/.claude/skills/`, making the skill
available in **every** project on your machine.

To install into a **single project** instead, copy it there:

```bash
cp -r skills/sql-reviewer <your-project>/.claude/skills/
```

A project-level copy takes precedence over the user-level one, which is useful if you want
to pin a specific version for one repo.

### Option B — plugin marketplace

Versioned, and updates in place when this repo is updated.

```
/plugin marketplace add AbdelfattahTamer/claude-sql-reviewer
/plugin install sql-reviewer@claude-sql-reviewer
```

Note that Claude Code namespaces plugin-provided skills, so installed this way the command
may be `/sql-reviewer:sql-reviewer` rather than the bare form. If you want the short
command, use Option A.

### Option C — just hand the link to Claude

Paste this in Claude Code:

> Install the SQL reviewer skill from https://github.com/AbdelfattahTamer/claude-sql-reviewer

Claude will read the [instructions for Claude](#instructions-for-claude) section below and
do it.

---

## Use

```
/sql-reviewer
```

With no arguments it works out what you are about to commit, in this order:

1. **Staged changes**, if anything is staged — this is the usual pre-commit case.
2. **Unstaged working-tree changes**, if nothing is staged.
3. **The whole branch** against the repository's main branch, if the working tree is clean.

You can also aim it explicitly:

```
/sql-reviewer --staged           review only what git commit would capture
/sql-reviewer --branch           review the whole branch vs the main branch
/sql-reviewer main..HEAD         review any git diff range
/sql-reviewer path/to/file.py    review specific files
```

---

## What you get

A markdown report at the repository root:

```
<your-name>.sql-review.<date>.md      e.g.  jane-doe.sql-review.2026-09-20.md
```

The name comes from your `git config user.name`, slugified, so that on a shared branch it is
obvious whose review a report is — and two people reviewing on the same day do not overwrite
each other.

Each finding carries a severity, the exact `file:line`, the offending SQL, the failure mode
in plain language, and a concrete fix. Findings the reviewer could not substantiate are
reported separately rather than mixed in with the confirmed ones.

**Add the report pattern to your `.gitignore`** if you do not want reviews committed:

```gitignore
*.sql-review.*.md
```

---

## How it learns your project

Generic Oracle knowledge catches generic Oracle bugs. The expensive bugs are specific to
your schema — a join that multiplies rows because the child table is not unique on the join
key, a filter on a column that has no index, a reference to a column that was renamed last
sprint.

So on first run the skill looks for machine-readable schema evidence already in your repo
(DDL exports, schema JSON, ORM models, migration directories) and builds a local project
layer from it. That layer is written **into your project, never into this repository**, and
it is what lets the reviewer check a predicate's columns actually exist and are indexed.

If it finds nothing, it still reviews — it just reviews generically, and tells you what it
could not verify instead of guessing.

---

## Privacy

This skill runs entirely inside your Claude Code session, against your local repository.

- It does not send your schema, your queries or your data anywhere.
- Reports are written to your working directory and are yours to keep, delete or ignore.
- The project layer it generates describes **your** codebase and is deliberately excluded
  from this repository's `.gitignore` patterns, so contributing back to this repo cannot
  leak it.

Nothing in this repository contains schema, table names or data from any real system.

---

## Requirements

- Claude Code
- `git`
- Python 3.8+ (standard library only — no packages to install)

---

## Instructions for Claude

If a user has asked you to install this skill, do the following:

1. Clone this repository, or if it is already cloned, use the local copy.
2. Copy the directory `skills/sql-reviewer/` to `~/.claude/skills/sql-reviewer/`.
   - On Windows that is `%USERPROFILE%\.claude\skills\sql-reviewer\`.
   - Create the `skills` directory first if it does not exist.
   - If a `sql-reviewer` directory is already there, ask the user before overwriting it.
3. Confirm the copy succeeded by checking that `~/.claude/skills/sql-reviewer/SKILL.md`
   exists.
4. Tell the user the skill is installed and is invoked with `/sql-reviewer`, and that the
   command becomes available in a new session.

Do not modify the user's `settings.json`, and do not install anything else.

---

## License

MIT — see [LICENSE](LICENSE).

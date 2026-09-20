# SQL Reviewer

A [Claude Code](https://claude.com/claude-code) skill that reviews the SQL in your git diff
**before you commit it**, and writes the findings to a dated markdown report.

It is built for codebases where SQL is not tidy: queries assembled from concatenated string
fragments, f-string interpolation, ETL specs, DSL predicates and large `.sql` scripts. It
parses changed files to an AST and reads each SQL site in its surrounding context, rather
than grepping for keywords — so it finds SQL a conventional linter walks past, and does not
report the comments *about* SQL that a grep cannot tell apart from the real thing.

The focus is **Oracle** and **data-migration / ETL** work, where the expensive failures are
not syntax errors. They are queries that run perfectly and load the wrong rows.

---

## Install

### Option A — copy install (recommended)

This gives you the command as exactly `/sql-reviewer`.

```bash
git clone https://github.com/AbdelfattahTamer/claude-sql-reviewer.git
cd claude-sql-reviewer
bash install.sh
```

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The `-ExecutionPolicy Bypass` applies to that single run only. Without it, a default Windows
install refuses to run any script.

The installer copies `skills/sql-reviewer/` into `~/.claude/skills/`, making the skill
available in **every** project on your machine.

To install into a **single project** instead:

```bash
bash install.sh --project /path/to/your-project
```

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Project C:\path\to\your-project
```

A project-level copy takes precedence over the user-level one.

Claude Code watches skill directories, so the command appears **without restarting** — with
one exception: if `~/.claude/skills/` did not exist when your session started, that session
is not watching it, and you need to restart once.

### Option B — plugin marketplace

Versioned, and manageable from `/plugin`.

```
/plugin marketplace add AbdelfattahTamer/claude-sql-reviewer
/plugin install sql-reviewer@claude-sql-reviewer
```

Two things to know before choosing this route:

- Claude Code namespaces plugin-provided skills, so the command may be
  `/sql-reviewer:sql-reviewer` rather than the bare form. If you want the short command, use
  Option A.
- **Auto-update is off by default for third-party marketplaces.** You will not get new
  versions until you either run the update commands below or enable auto-update in
  `/plugin` → Marketplaces → this marketplace → Enable auto-update.

### Option C — hand the link to Claude

Paste this into Claude Code:

> Install the SQL reviewer skill from https://github.com/AbdelfattahTamer/claude-sql-reviewer

Claude follows the [instructions for Claude](#instructions-for-claude) at the bottom of this
file.

---

## Use

```
/sql-reviewer
```

With no arguments it works out what you are about to commit, in this order:

1. **Staged changes**, if anything is staged — the usual pre-commit case.
2. **Unstaged working-tree changes**, if nothing is staged.
3. **The whole branch** against the integration branch, if the working tree is clean.

It announces which scope it picked before reviewing, so a run against the wrong scope is
visible rather than silent.

You can also aim it explicitly:

```
/sql-reviewer --staged           review only what git commit would capture
/sql-reviewer --branch           review the whole branch vs the integration branch
/sql-reviewer main..HEAD         review any git diff range
/sql-reviewer path/to/file.py    restrict to specific files or directories
/sql-reviewer --branch src/      a range and a path restriction together
/sql-reviewer --help             print the option table and stop
```

Ranges and paths combine, and order does not matter: anything that exists on disk is treated
as a path, anything git resolves as a revision is treated as a range.

---

## What you get

A markdown report at the repository root:

```
<your-name>.sql-review.<date>.md      e.g.  jane-doe.sql-review.2026-09-20.md
```

The name comes from your `git config user.name`, slugified, so on a shared branch it is
obvious whose review a report is, and two people reviewing on the same day do not overwrite
each other.

Each finding carries a severity, the exact `file:line`, the offending SQL, the failure mode
in plain language, and a concrete fix.

Two sections exist to keep the report honest:

- **Needs a number** — findings whose severity depends on a fact about production data the
  review cannot observe. Instead of guessing a row count, it gives you a read-only query
  that settles the question.
- **Coverage** — what was reviewed *and what was skipped, with reasons*. A partial review
  that reads as complete is the failure mode that makes people stop trusting a tool.

**Add the report pattern to your `.gitignore`** if you do not want reviews committed:

```gitignore
*.sql-review.*.md
```

---

## Update

```
/sql-reviewer update
```

It fetches the published skill, compares it against what you have installed, copies over
only what differs, and names every file it changed:

```
Installed: 0.2.0  (user-level (all projects))
0.2.0 -> 0.3.0
  changed  rules/oracle-correctness.md
  added    rules/postgres.md
Updated 2 file(s) to 0.3.0.
```

`/sql-reviewer update --check` shows what would change without writing anything.
`/sql-reviewer version` shows what you have and where it is installed.

**If you installed before 0.2.0**, your copy has no updater. Re-clone and run
`bash install.sh --force` once; `/sql-reviewer update` works from then on.

**If you installed via the plugin marketplace**, update through the plugin system — the
updater detects a plugin-managed install and stops rather than editing files the plugin
system owns, since a hand-edit there is reverted by the next refresh:

```
/plugin marketplace update claude-sql-reviewer
/plugin update sql-reviewer@claude-sql-reviewer
```

Then `/reload-plugins`, or it loads on your next launch.

---

## Uninstall

Copy install, user-level:

```bash
rm -rf ~/.claude/skills/sql-reviewer
```

```powershell
Remove-Item -Recurse -Force $env:USERPROFILE\.claude\skills\sql-reviewer
```

Project-level: `rm -rf <your-project>/.claude/skills/sql-reviewer`

Plugin install:

```
/plugin uninstall sql-reviewer@claude-sql-reviewer
/plugin marketplace remove claude-sql-reviewer
```

Removing a marketplace also uninstalls the plugins you installed from it.

Reports already written are left alone in every case — they are yours.

---

## How it uses your project's schema

Generic rules catch generic bugs. The expensive ones need to know your schema: whether a
join key is actually unique, whether a filtered column is indexed, whether a referenced
column still exists.

On each run it looks for schema evidence already in your repository — files matching
`*schema*.json`, `*ddl*.sql` or `*ddl*.json` — and reads `.claude/sql-review-rules.md` if you
have written one. That file is a project-local rule layer: anything in it takes precedence
over the shipped rules, and it is the right place for your own table conventions, known
traps and past incidents.

**It generates no files and modifies no source.** If it finds no schema evidence, it reviews
generically and records in Coverage what it could not verify, rather than guessing.

---

## Privacy

This skill runs entirely inside your Claude Code session, against your local repository.

- It does not send your schema, your queries or your data anywhere.
- Reports are written to your working directory and are yours to keep, delete or ignore.
- Nothing in this repository contains schema, table names or data from any real system.

---

## Requirements

- Claude Code
- `git`
- Python 3.8+, reachable as `python3`, `python`, or `py -3` (standard library only — there
  is nothing to `pip install`)

---

## Instructions for Claude

If a user asks you to install this skill, do this:

1. **Clone into a temporary directory, never into the user's current project** — they are
   usually sitting in their own repository when they ask, and a stray clone there shows up
   in their next `git status`:

   ```bash
   git clone --depth 1 https://github.com/AbdelfattahTamer/claude-sql-reviewer.git "$(mktemp -d)/csr"
   ```

2. **Prefer the bundled installer**, which handles directory creation, overwrite and
   project-scoped installs:

   ```bash
   bash install.sh --force                      # user-level
   bash install.sh --force --project <path>     # project-level
   ```

   On Windows: `powershell -ExecutionPolicy Bypass -File .\install.ps1 -Force`.

   Fall back to a manual copy only if the installer cannot run. If you copy by hand, create
   the destination first — `mkdir -p ~/.claude/skills` — because copying into a directory
   that does not exist silently produces a skill named `skills` that will never load.

3. **If a `sql-reviewer` directory is already installed**, do not overwrite it blindly. Run
   `python <installed>/scripts/update.py --check` to show the user what would change, and
   let them decide. If that script is absent, the install predates 0.2.0 and a
   `--force` reinstall is the correct move.

4. **Verify** `~/.claude/skills/sql-reviewer/SKILL.md` exists, then delete the temporary
   clone.

5. **Tell the user** the skill is installed, invoked as `/sql-reviewer`, and available
   immediately — Claude Code watches skill directories — unless that directory did not exist
   when their session started, in which case they should restart once.

Do not modify the user's `settings.json`, and do not install anything else.

---

## License

MIT — see [LICENSE](LICENSE).

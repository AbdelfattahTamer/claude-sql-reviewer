# Style and maintainability

Things that make the next defect harder to see.

30 rules. Severity: High 8, Medium 16, Low 6.

---

## `a-check-that-can-silently-check-nothing`

**High** — Prove every guard is non-vacuous: zero checks must be impossible to mistake for all-clear

**Look for.** A validator that iterates a collection obtained by getattr, a glob, or an attribute that might be a bound method rather than the declared data; a parametrised test over a possibly-empty glob; a bound-cache test that never asserts an eviction occurred.

**What goes wrong.** This is the most common way a guard stops guarding, and it always presents as success. Reading a bound method instead of the declared dict found nothing and made a preflight report zero checks on every run — which looks exactly like 'all links fine'. A glob that matches no files makes every parametrised test pass vacuously. A cache test whose data fits under the cap would pass against an unbounded dict, i.e. against the very bug it exists to catch. None of these fail; all of them go green.

**Fix.** For every such guard add a test that asserts the guard produced something AND that it fires against a known-bad input: for the preflight, run the REAL registry against an empty state and assert at least one check is not-OK; for a file lint, assert the glob is non-empty and feed it a hand-built corrupted sample; for a bound, assert the eviction counter increased. Prefer reading declared data over reflection, so 'present but empty' cannot be confused with 'absent'.

**How to confirm it fires.** for any new validator/lint in a diff, ask what it does when its input collection is empty. Then check the test file for (a) a non-empty assertion on the input, and (b) a negative case built by hand. getattr(obj, 'name', ()) over a name that is also a method is a specific high-value tell.

---

## `check-the-framework-before-hardening-against-it`

**High** — Check whether the framework already handles the trap before adding your own guard

**Look for.** A local workaround for a known driver or platform quirk — converting values to text, coercing types, catching a specific error — added inside one module of a pipeline that has a central place for that concern.

**What goes wrong.** A fix layered on an existing fix is not twice as safe. The local guard changes the value's shape, which makes the central guard's inverse step (the parse-back, the coercion, the type restoration) miss it — so the doubled protection produces the failure neither layer would have produced alone. It is hard to see in review because both halves look correct in isolation.

**Fix.** Before writing the guard, find where the concern is already handled and read what it does on both sides of the boundary. If the central handling is sufficient, delete the local one. If it is not, extend the central one so there is still exactly one place, and say in the commit why the central path was inadequate.

**How to confirm it fires.** Regex precursor: `TO_CHAR(`, an explicit cast, or a narrow `except`/error-code check appearing in a leaf module. Semantic confirm: does a shared layer in the same codebase already apply the same transformation? Duplication of a known-quirk workaround fires the rule.

---

## `concatenated-sql-defeats-a-single-literal-regex`

**High** — Fold concatenated and interpolated fragments into one statement before matching

**Look for.** A statement assembled from adjacent string literals or f-string parts, where each individual part is not a valid or even recognisable statement — a fragment that is just `"CASE WHEN "` or `" ELSE TO_CHAR("`.

**What goes wrong.** A lint that matches per string literal never sees a pattern that straddles a join, so a forbidden construct split across two source lines passes silently. This is not hypothetical formatting trivia: line-wrapping a long expression is exactly what a developer does to a statement that has grown, and growth is what makes it worth linting. The same fragmentation makes a human reviewer judge half a predicate and conclude it is fine.

**Fix.** Treat every `Constant` / `JoinedStr` / `BinOp('+')` chain reachable from one SQL position as ONE unit, and concatenate it before any regex runs. Substitute a neutral placeholder (for example `:x` or `X`) for each interpolation so the assembled text stays lexable, and keep a note that the placeholder stands for injected text — an identifier injected there is its own finding.

**How to confirm it fires.** In the AST, never iterate `ast.Constant` nodes directly for SQL. The symptom of having done so: fragments shorter than ~20 characters that end in an operator, a comma or an opening parenthesis. Structurally: any SQL position whose value node is `JoinedStr` or a concatenation of more than one `Constant`.

---

## `spec-hunk-unreviewable-without-its-header`

**High** — Expand a diff hunk inside a declarative spec to the whole enclosing spec object

**Look for.** A hunk in a large declarative migration file that changes a column mapping or a SQL expression, where the hunk window contains no `source_table` / `target_table` / key declaration — the hunk header line is the opening of the file-level `SPECS = (` tuple and names nothing.

**What goes wrong.** The changed expression names columns. Whether those columns exist on the relation being read is only answerable from the spec's source-table declaration, which sits tens to hundreds of lines above the hunk. A reviewer reading the hunk alone can check the SQL's syntax and nothing else: a column belonging to a sibling spec's table, copy-pasted from the spec above, reads as perfectly correct and fails at extract time with an invalid-identifier error on the whole table. The same blindness hides a predicate written against the wrong grain.

**Fix.** The collector parses the changed file to an AST, finds every `Call` node that constructs the spec class, and maps hunk line ranges onto those node ranges. The review unit is the whole spec call, with its identifying kwargs (`id`, source table, target table, key, entity) hoisted to the top of the unit, and the changed lines marked. This also deduplicates: several hunks inside one spec become one unit.

**How to confirm it fires.** For a changed file under the specs package: AST-walk for `Call` nodes whose `func` is a `Name` in the spec-class set; for each hunk `(start, count)`, find the enclosing node. If `start - min(lineno of id/source/target kwargs) > diff_context_width`, the hunk is under-contexted and must be expanded. Cheap precursor: a diff whose `@@` trailing function context is a bare collection opener such as `SPECS: tuple[...] = (`.

---

## `split-constant-duplicated-across-modules`

**High** — The constant that decides a split must be defined once and imported

**Look for.** A magic value re-declared in a second module — a type code, a discriminator, a threshold — that also appears in a sibling module's filter for the same underlying split.

**What goes wrong.** Two modules that must agree on where a split falls, but each carrying its own copy, are one edit away from disagreeing. When they do, one side routes a record into a category the other side never wrote — so a lookup against the other category returns nothing. If the target column is nullable, that is not even an error: the rows load with an empty foreign key, and the result is records that reference nobody. The failure has no symptom until someone counts.

**Fix.** Define the constant once, in the module that owns the split, and import it everywhere else. Where the split's evidence lives in one place (a source lookup table, a service's branching logic), record that evidence next to the definition. Prefer reading the discriminator from the SAME row the other side keyed on, so the two agree by construction and the question cannot arise.

**How to confirm it fires.** Find integer/string literals embedded in filter predicates and cross-reference them against named constants defined in sibling modules. Fire on a literal that duplicates an exported constant's value, and on a constant re-declared in two modules with the same name or meaning.

---

## `sql-field-holds-a-name-not-a-string`

**High** — Resolve the symbol table before deciding a hunk carries no SQL

**Look for.** A SQL-carrying field assigned a bare identifier, `CONST.format(...)`, a `**SPREAD` of a shared dict, or a helper call, rather than a string literal — `extract_from=_SOME_JOIN`, `sql_defaults=SHARED_DEFAULT`, `extract_where=_scope_predicate(...)`.

**What goes wrong.** A text scan of the change site sees an identifier and reports the hunk as containing no SQL, so the statement is never reviewed. Worse, repointing such a field from one constant to another is a complete rewrite of the emitted statement inside a one-token diff — the kind of change that looks like a rename and is not. In practice the escape-hatch fields (raw FROM clause, raw WHERE predicate, INSERT-time defaults) are the ones almost never written inline, because they are shared, so the scan misses precisely the riskiest SQL.

**Fix.** Build a symbol table for the module before classifying — `{name: resolved SQL text}` — folding implicit concatenation, f-strings (interpolations replaced by a neutral placeholder), `.format()` receivers, dict values and one level of constant-to-constant indirection, and following relative `from .sibling import NAME` so a shared constant defined in a base module still resolves. Where the package is importable, prefer importing the built spec objects and reading the already-resolved fields; that gets helper calls and spreads for free.

**How to confirm it fires.** `grep -nE '\b(derived_columns|extract_where|extract_from|sql_defaults)\s*='` as the precursor, then inspect the value node type in the AST: anything that is not `Constant` or `JoinedStr` needs a resolve. Flag a diff that changes only the identifier on the right-hand side of such a field as a full SQL change, not a rename.

---

## `sql-lint-hardcodes-its-field-list`

**High** — Derive a SQL lint's field list from the spec type, never from a hand-written list

**Look for.** A repo-wide SQL guard (a test or lint) walks every migration spec and collects "all the SQL" from a hand-written list of two or three field names, while the spec type itself declares more fields whose contents are inlined verbatim into a statement. Look for a helper that yields `getattr(spec, "<literal field name>")` for a fixed set of names.

**What goes wrong.** The guard reads as total coverage, so the next reviewer stops checking the thing it claims to check. A SQL-carrying field added to the spec type later — a raw WHERE predicate, an INSERT-time default expression — is never scanned, and the exact defect the guard exists to prevent ships through the unscanned field. Nothing fails, no warning is printed; the coverage just shrinks. It is worse than no guard, because no guard leaves the reviewer suspicious.

**Fix.** Make the spec type publish its own SQL-bearing field names (a module-level `SQL_FIELDS` tuple that both the dataclass docs and the guard import, or a dataclass-field metadata flag), have the guard iterate that, and add a meta-test asserting the guard's field set equals the type's declared set: SQL_FIELDS = ("derived_columns", "extract_where", "extract_from", "sql_defaults") def _sql_fragments(spec): for name in SQL_FIELDS: value = getattr(spec, name, None) if isinstance(value, str): yield name, value elif isinstance(value, Mapping): for k, v in value.items(): yield f"{name}[{k}]", v def test_the_guard_covers_every_sql_field(): assert set(SQL_FIELDS) == {f.name for f in fields(TableSpec) if f.metadata.get("sql")}

**How to confirm it fires.** Cheap precursor: in test/lint code, `grep -nE 'getattr\(spec, "[a-z_]+"' -A2` or any generator yielding two or more literal field names. Confirm semantically: list the spec dataclass's fields whose type is `str | None` or `Mapping[str, str]` and whose docstring says the value is "inlined verbatim" / "never validated" / "raw SQL". If the type declares a SQL field the helper does not yield, the rule fires. Also fires when a diff ADDS a SQL-carrying field to the spec type without touching the guard.

---

## `test-the-real-path-not-an-equivalent`

**High** — A guard must exercise the shipped code path, not a hand-written equivalent of it

**Look for.** A start-up probe, smoke check or regression test that reconstructs the behaviour it is verifying — writing its own version of the query, the conversion or the call — instead of invoking the function that production uses.

**What goes wrong.** The test then passes while the shipped path is broken, which is precisely how a fix ships twice without working. It is the most expensive kind of green test, because it converts 'we verified this' into evidence that is false. The related failure is the assertion that survives the bug it claims to pin: if it never went red against the broken version, it is not pinning anything.

**Fix.** Have the check call the production function and assert on its output. Where a behaviour is asserted against an internal format (a key separator, a rendering), assert it against the function that produces that format so a change fails the test rather than silently changing every result. Mutation-test new assertions: run them against the pre-fix code and confirm they fail.

**How to confirm it fires.** Regex precursor: a SQL string literal or a re-implemented transformation inside a test/probe file. Semantic confirm: does the production module export a function that does the same thing? If the test does not call it, the rule fires.

---

## `an-optimisation-hook-with-no-call-site`

**Medium** — Wire the prefetch hook in, or say in its docstring that it is not wired

**Look for.** A `prefetch()` / `warm()` / `preload()` method whose docstring says the caller 'may' invoke it, with no call site anywhere in the tree; or a hook documented as an extension point that nothing ever calls.

**What goes wrong.** Implementers pay the cost of writing and maintaining it and keep paying the per-row cost it was meant to remove. Worse, the docstring reads as if the optimisation is live, so the next performance investigation looks somewhere else entirely — and the surrounding comments about cache behaviour quietly become fiction.

**Fix.** When adding or reviewing such a hook, grep for a call site. Either wire it into the loop it was written for, or state plainly in the docstring that it is NOT wired, as of when, and where the call belongs — so the next reader can act on it in one step instead of rediscovering it.

**How to confirm it fires.** Precursor: for each public method whose name matches `prefetch|warm|preload|prime|hint`, grep the repository for `\.<name>(`. Zero call sites outside its own definition and tests fires. Second signal: a docstring saying the caller 'may' or 'can' call it, with no statement of whether anyone does.

---

## `diagnostics-must-never-cost-a-row`

**Medium** — An operator notice must not be able to fail the data path, and must be emitted once with the facts

**Look for.** A log/notice call added inside a transform or per-row function that is contractually non-raising, with no guard — or a degradation notice emitted per row rather than once.

**What goes wrong.** The context object is usually a Protocol; a caller (a test, a replay tool, a future runner) may pass something with no log method, and one that has a log may still raise when its sink is gone. Losing production rows to a log line is an absurd trade. Separately, when a safety mechanism degrades, the notice is the only evidence the operator gets, so it must carry everything needed to check the claim — which object, what took over the work, how the failure will now present, and which knob changes it — and it must NOT overstate: if part of the work is still being done in-process, the notice has to say so, or it reads as reassurance that everything is covered.

**Fix.** Wrap the emission in try/except Exception and fire it once per instance behind a flag. Assert both: that a context with no log and a context whose log raises still return a normal (non-rejected) result, and that the notice text names the takeover, the expected error code and the knob.

**How to confirm it fires.** grep the diff for ctx.log / logger calls inside transform/process_row. Check for try/except and a once-only flag. Then read the message text: if a mechanism degraded partially, the message must not imply full coverage.

---

## `diff-text-must-be-read-as-utf8`

**Medium** — Read git output and source files with an explicit UTF-8 decode

**Look for.** A collector that shells out to `git diff` / `git show` and reads the result with the platform default encoding — `subprocess.run(..., text=True)` with no `encoding=`, or `open(path)` with no `encoding=`.

**What goes wrong.** SQL literals that carry non-ASCII — a comparison against a localized description, a crosswalk keyed on a business label — make the read raise a decode error, and the collector reports "no SQL found" for exactly the files where a crosswalk literal changed. It never reproduces on a UTF-8 default platform, so it ships as a mystery: reviewers on one OS see findings and reviewers on another see silence.

**Fix.** Pass `encoding="utf-8", errors="replace"` on every `subprocess.run` that captures git output and on every `open()` of a source file, and add `-c core.quotepath=false` to the git invocation so non-ASCII path names come back unescaped rather than as octal. Never catch the decode error and continue — that turns the bug into silent under-collection.

**How to confirm it fires.** `grep -n 'subprocess.run' <collector> | grep -v 'encoding='` and `grep -nE 'open\([^)]*\)' <collector> | grep -v encoding`. Any git capture or source read without an explicit encoding fires. Regression test: run the collector against a file containing a non-ASCII string literal.

---

## `fragment-boundary-whitespace`

**Medium** — Concatenated SQL string literals must carry their own boundary whitespace

**Look for.** A multi-line SQL fragment built from adjacent Python string literals where a line does not end in a space and the next does not begin with one — or where both do, doubling the space inside a quoted literal.

**What goes wrong.** Adjacent Python literals concatenate with NO separator, so `"...WHERE a = 1"` followed by `"AND b = 2"` produces `a = 1AND b = 2` — a syntax error at run time, in a fragment nothing validates, discovered only when the extract runs. The mirror case matters too: a doubled space inside a quoted string literal or an identifier changes the VALUE rather than breaking the statement, so it fails silently.

**Fix.** Adopt one convention and apply it uniformly — every continuation line begins with a space, or every line ends with one, never both. Keep the fragment readable as SQL when the literals are mentally joined. Assert the assembled statement in a test rather than trusting the eye, especially for fragments assembled conditionally, where the boundary that breaks is the one that only appears on some paths.

**How to confirm it fires.** For each Python expression that concatenates adjacent string literals into SQL: join them and check that every boundary falls on whitespace or on a token that legally abuts (an opening parenthesis, a comma). Fire on a boundary between two word characters, and on a doubled space that falls inside a quoted literal.

---

## `generated-sql-edited-by-hand`

**Medium** — A hand edit to a generated .sql file is silently reverted by the next generator run

**Look for.** A diff that changes a `.sql` file whose header says it was generated by a script, or whose body is hundreds of near-identical blocks, without a matching change to the generator. Tells: a 'GENERATED <date> by <tool>' banner, a 'DO NOT EDIT BY HAND' line, or one block in a long repetitive file formatted differently from its neighbours.

**What goes wrong.** The edit works until someone re-runs the generator, at which point it disappears with no conflict and no warning, because the generator writes the whole file. Worse in reporting SQL than in code: a gap or reconciliation view that has drifted from the specs it was generated from reports work as done that is not done, and nothing distinguishes a correct zero from a stale one. The 'one block formatted differently' tell is the reliable one — a generator produces uniform output, so any block that does not match the pattern was typed.

**Fix.** Change the generator and regenerate. If the generated output must be corrected out of band, add the correction as an explicit override input the generator consumes, not as an edit to its output. Keep the banner and the generator path at the top of the file so a reviewer can see in the first hunk that the file is generated.

**How to confirm it fires.** Regex precursor on the file's first 40 lines: `GENERATED|DO NOT EDIT|autogenerated|generated by`. Fires on any diff hunk in such a file that is not accompanied by a change to the named generator. Secondary check for files with no banner: if a file consists of many structurally identical blocks and the hunk's block differs in whitespace or ordering from its neighbours, treat it as a hand edit.

---

## `know-which-schema-facts-are-read-at-runtime`

**Medium** — State which target-schema facts the pipeline re-reads at run time and which are compiled into the mapping

**Look for.** A change document or commit that applies a DDL change and declares it complete, without saying whether the pipeline picks the change up on its own.

**What goes wrong.** Pipelines typically re-read some catalog facts before each table runs — column widths, number precision — and hard-code others — unique keys, required columns, the column list itself. A DDL change in the first group needs nothing else; one in the second needs a code change, a rebuild and a reinstall, or the run keeps rejecting exactly the rows the DDL was meant to release, with the change recorded as applied. The two look identical from the DBA's side.

**Fix.** For every DDL change, state explicitly: does the tool re-read this, or is it declared? Where it is declared, list the code change in the same document as a required step, in order. Keep the runtime-read list short and written down, because 'the tool reads the schema' is the assumption that causes this.

**How to confirm it fires.** Regex precursor: `ALTER TABLE` in a change document or migration file. Semantic confirm: does the corresponding fact (width/precision vs constraint/column-list/required-set) appear as a literal in a spec or config? If it is declared in code and the change note claims no code change is needed, the rule fires.

---

## `malformed-configuration-must-not-silently-default`

**Medium** — Decide, and state, what a malformed or negative configuration value does — never swallow it

**Look for.** A knob parsed with a bare try/except returning a default, or normalised with max(0, value), or accepted as a blank/whitespace string, when the value controls memory, timeouts or batch sizes.

**What goes wrong.** An operator who writes a thousand-separator, a unit suffix or an exponent gets a run that ignores them and never says so — the same shape of failure as a setting applied to the wrong thing, discovered many hours in. The sign trap is nastier: for a timeout, max(0, -5000) is 0, and 0 there does not mean 'immediately', it means 'wait forever' — so a typo turns the ceiling OFF. Note the policies legitimately differ by knob (a memory cap should refuse junk; a timeout whose default is ON should fall back to the default rather than to off), which is exactly why the choice must be explicit and tested.

**Fix.** Parse strictly and raise at start-up, naming the variable and quoting the offending value, before any row moves — and ensure the raise happens during registry/config construction, not at first use. Treat unset and whitespace-only as 'not configured' (that is not a typo). Refuse negatives explicitly rather than clamping. Where a fallback is the intended policy, log a line naming the knob so the operator learns their setting did not take. Clear such knobs in an autouse test fixture: half these tests assert default values, and an operator's exported variable in the shell they run pytest from otherwise reports a fault in the code that is really in the environment.

**How to confirm it fires.** grep the diff for os.environ.get / getenv followed by int()/float() and check the surrounding except clause. `except (TypeError, ValueError): return DEFAULT` on a memory or timeout knob is the finding; so is max(0, x) on a timeout.

---

## `module-level-state-needs-a-complete-reset-fixture`

**Medium** — Reset every field of shared run-scoped state between tests, not just the obvious one

**Look for.** An autouse fixture that clears a cache dict but leaves a companion flag, counter or shared budget object untouched — or process-global state with no reset at all.

**What goes wrong.** Run-scoped state that leaks across tests makes the NEXT test run in a degraded mode, and where the degradation WITHDRAWS a check rather than adding one, the leak makes tests pass that should fail. A flag left set hands the next migration's duplicate detection to a database that is not there, silently. The same class of problem hits ambient environment variables read at import time.

**Fix.** Make the reset a single function next to the state definition, clearing every field (contents, flag and shared counter), and call it from the autouse fixture; assert in a test that after the reset the object behaves as newly constructed. Clear relevant env knobs in the same fixture.

**How to confirm it fires.** when a diff adds a field to a run-scoped/global state object, grep conftest and the test module for the reset helper and check it clears the new field too. Module-level singletons plus a new boolean attribute is the pattern.

---

## `prose-field-beside-a-sql-field`

**Medium** — Classify SQL by syntactic position, not by keyword content

**Look for.** The same object declares a free-text notes/rationale/blocker field and a SQL-expression field, and the notes routinely quote SQL back at the reader ("run SELECT COUNT(*) ... before the load", "the sheet's rule says UPDATE ... which we do not do").

**What goes wrong.** Any keyword-based SQL detector scores both the same, and the prose outnumbers the SQL by roughly two to one. A reviewer fed that mix learns the tool is noisy and starts skimming; a CI gate fires "SQL changed" when someone reflows a comment. Both failure modes end with the real SQL change going unread. Content alone cannot separate them, because a note that quotes a query is textually a query.

**Fix.** Decide from the AST position and only then look at the text. Exclude up front: docstrings (the first statement of module/class/function), notes/rationale fields, and the arguments of logging and exception constructors. Include only: the declared SQL fields, the first argument of execute/executemany and of the project's own statement-running wrappers, and module constants reachable from one of those.

**How to confirm it fires.** Build a census of string literals containing a SQL keyword, bucketed by AST parent (docstring / keyword-arg name / enclosing call name). Any prose-typed field holding more than about a tenth of the hits belongs on the exclusion list before the tool ships. If the collector has no exclusion list at all, this rule fires.

---

## `raw-sql-fragment-unvalidated`

**Medium** — A raw SQL fragment is inlined verbatim — justify it and pin it with a test

**Look for.** A hand-written WHERE predicate, FROM clause or VALUES expression passed through as a raw string, where the declarative filter mechanism could have expressed the same rule.

**What goes wrong.** Nothing validates these strings. A typo surfaces as a database error at run time — often hours in, and on a fragment that is assembled conditionally it may only appear on some paths. A semantically WRONG predicate is worse: it silently changes what migrates and the run reports success. The declarative path, by contrast, is type-checked, renders NULL-safe complements, and keeps the transform-time guard consistent with the extract-time filter.

**Fix.** Use the declarative filter whenever it can express the rule. Reach for raw SQL only where it cannot — an aggregate or EXISTS-shaped scope rule, a pre-aggregating FROM clause — and say in a comment WHY. Assert the assembled statement in a test, and keep the per-row guard in step with the extract predicate so a spec driven from elsewhere (a test, a replayed row) behaves the same way.

**How to confirm it fires.** Flag every raw SQL string field on a spec. Fire when the predicate is a simple column-equals-literal or IS [NOT] NULL comparison that the declarative mechanism supports, or when no comment explains why the declarative form was insufficient. Also fire when a raw extract predicate has no corresponding per-row guard.

---

## `received-ddl-export-reviewed-as-authored-sql`

**Medium** — Classify a .sql file by provenance before reading a line of it

**Look for.** A very large `.sql` file under a reference or documentation path, headed by a tool-generated banner, replaced wholesale in one commit (one file added, a previous one deleted, no line-level overlap).

**What goes wrong.** It is a received artifact, not authored SQL. A diff-driven SQL reviewer treats it as tens of thousands of new statements: it either exhausts its budget there or buries the handful of statements a human actually wrote this cycle. Reviewing it is also pointless — nobody on this side can change it, and any finding against it is a message to a different team.

**Fix.** Bucket `.sql` by path and header before content. Reference/docs trees and files whose first lines carry an export-tool banner are `received-ddl`: record the delta summary (tables added/dropped/altered) and skip statement review. Authored script trees (bundle/seed/probe/tooling SQL) get full review.

**How to confirm it fires.** Precursor: `.sql` under a docs/reference path, OR first 5 lines matching a generator banner such as `^--\s+File created - `, OR >5,000 lines with >90% of non-blank lines beginning `CREATE`/`ALTER`/`COMMENT ON`/`--`. Confirm with the diff shape: a paired add+delete of near-equal magnitude with no shared hunks is a re-export, not an edit.

---

## `script-has-no-error-stop`

**Medium** — A DDL or migration script with no WHENEVER SQLERROR keeps running after a failed statement

**Look for.** A `.sql` file meant to be run with `@script` that issues CREATE/ALTER/GRANT statements and contains no `WHENEVER SQLERROR EXIT FAILURE` and no `WHENEVER OSERROR EXIT FAILURE`. Extra risk when the script ends with a query rather than a terminator, so the last thing on screen is output rather than a status.

**What goes wrong.** SQL*Plus and SQLcl default to continuing after an error. A failed CREATE VIEW scrolls past, every later statement runs, the final query prints results, and the operator reports success — while one object does not exist or still holds its previous definition. For a migration script that is the worst outcome available: partial application with a clean-looking transcript, discovered later as inconsistent behaviour rather than as a failure.

**Fix.** Open the script with `WHENEVER SQLERROR EXIT FAILURE ROLLBACK` (and `WHENEVER OSERROR EXIT FAILURE`), so the first error stops the run with a non-zero exit code a wrapper can see. Where partial application is genuinely acceptable — a set of independent read-only probes — say so explicitly in the header instead of relying on the default. Add a final verification query whose output makes the success condition obvious.

**How to confirm it fires.** Regex precursor: file contains `CREATE\s+(OR\s+REPLACE\s+)?(TABLE|VIEW|INDEX|TRIGGER|SEQUENCE)|ALTER\s+TABLE|GRANT\s` and does NOT contain `WHENEVER\s+SQLERROR`. Suppress when a header comment explicitly declares statements independent.

---

## `sql-emitted-by-a-helper-function`

**Medium** — Pull a SQL-emitting helper's body into the review unit, and list its callers

**Look for.** A SQL-expression field whose value is a call to a module-local helper that returns a string or a dict of SQL — `derived_columns=_audit_expressions("SOURCE_TABLE")`, `extract_where=_scope_predicate(t, on=...)`.

**What goes wrong.** The helper's body is the only place the statement text exists, so a scan of call sites finds nothing to review. And a change inside the helper rewrites every call site's statement at once while touching one hunk — the blast radius is invisible in the diff. Helpers of this shape usually interpolate a table name from their argument, so one of them can emit a reference to a relation that is not in a given caller's FROM clause, which fails on that one caller only.

**Fix.** When a SQL position's value is a `Call` to a `Name` defined in the same module, pull that function's line range into the review unit and enumerate its other callers. Present a change to the helper as touching every spec that calls it. Where the package imports cleanly, importing the built objects sidesteps this entirely — the returned SQL is already materialised on the spec.

**How to confirm it fires.** `grep -nE '(derived_columns|extract_where|extract_from|sql_defaults)\s*=\s*_?[a-z][a-z0-9_]*\('` for the precursor; confirm by resolving the callee to a module-local `FunctionDef` whose return value contains SQL keywords. Also fires on `**helper(...)` spread inside a SQL dict.

---

## `sql-lint-must-ignore-comments-and-literals`

**Medium** — Strip comments and string literals before counting SQL keywords

**Look for.** A regex-based SQL/PL-SQL check counting BEGIN/END/IF against raw file text.

**What goes wrong.** It lies in both directions. EXECUTE IMMEDIATE 'ALTER TABLE ... IF ...' puts keywords inside a literal and inflates the count, while header comments of exactly these files are full of prose like 'IF YOU EVER RAN THE BROKEN VERSION'. Either one produces a failure on a correct script or, worse, a pass on a corrupt one — and a lint that cries wolf gets deleted, which is how a guard goes missing.

**Fix.** Normalise first: drop full-line comments, then replace every single-quoted literal (handling the doubled-quote escape) with an empty literal, and count against the result. Count the block terminator against the RAW text, since '/' is not code.

**How to confirm it fires.** read any new SQL-text regex in the diff: if it is applied to the file text directly rather than to a normalised copy, flag. Look specifically for the doubled-quote escape in the literal pattern.

---

## `sqlplus-substitution-variables`

**Medium** — An unescaped & in a SQL script becomes a substitution variable prompt

**Look for.** A `.sql` script containing `&` inside a string literal, a comment or a URL, with no `SET DEFINE OFF`. Also: `&&var` used as a schema prefix, and the `&&var..OBJECT` double-dot form.

**What goes wrong.** SQL*Plus and SQLcl treat `&name` as a substitution variable by default. An ampersand in a literal makes the tool prompt for a value — or, run non-interactively, substitute empty string and corrupt the statement — so a script that is correct SQL fails or silently changes meaning depending on how it is invoked. The double-dot form is the other half of the same mechanism and reads like a typo: `&&var.` ends the variable name with a period, so `&&meta_schema..OBJECT` expands to `SCHEMA.OBJECT`; write a single dot and the tool looks for a variable named `meta_schema.OBJECT`. And because substitution happens before the SQL is parsed, the created object stores the expanded text — the same script run with different variable values silently produces objects that differ.

**Fix.** Put `SET DEFINE OFF` at the top of any script whose literals or comments can contain `&`, and turn it back on only around the sections that need substitution. Where substitution is used for schema names, use the `&&var..OBJECT` form consistently and state at the top what the variables must be set to. Prefer `ALTER SESSION SET CURRENT_SCHEMA` over threading a schema variable through every object name.

**How to confirm it fires.** Regex precursor: `&` appearing in a `.sql` file. Fires when it occurs inside a quoted literal or a comment and the file has no `SET DEFINE OFF`. Separately flag `&&\w+\.[A-Za-z]` (single dot before an object name) as a likely expansion bug.

---

## `uppercase-prose-matches-uppercase-sql-regexes`

**Medium** — Require a second structural marker before trusting an uppercase-anchored SQL regex

**Look for.** A SQL detector anchored on `FROM <UPPERCASE_IDENT>` or `JOIN <UPPERCASE_IDENT>`, applied to a codebase whose comments and notes shout in capitals for emphasis.

**What goes wrong.** `FROM A LOOKUP`, `DERIVED FROM SOME_COLUMN`, `COMES FROM THE PARENT` all match the pattern. Precision collapses hardest in the most heavily commented files — which are the ones carrying the most reasoning and therefore the most worth reviewing. The detector then looks broken exactly where it matters, and gets tuned down or switched off.

**Fix.** Require the token after the keyword to be an identifier that is not an English function word (THE, A, AN, IT, ITS, THAT, THIS, EACH, EVERY, BOTH, OUR, THEIR, SOME, MOST, OTHER, NOT, HERE, THERE, NOW), and require one further independent structural marker on the same assembled statement: an opening `(SELECT`, a bind `:name`, a format slot `{name}`, a qualified `alias.COLUMN`, or an Oracle scalar function immediately followed by `(`. One marker alone is not enough in either direction; two markers plus a non-prose AST position is.

**How to confirm it fires.** Measure the corpus first: `grep -cE '\b[A-Z]{2,}[[:space:]]+[A-Z]{2,}[[:space:]]+[A-Z]{2,}\b'`. If shouted prose is a meaningful fraction of the lines, an uppercase-anchored regex is unusable on its own and the rule fires on any collector that relies on one.

---

## `a-closed-enumeration-in-a-comment-goes-stale`

**Low** — Do not write "and nothing else" about call sites you found by grepping one object

**Look for.** A comment or docstring that enumerates the call sites of a method and asserts the list is complete — 'called from exactly two places', 'and nothing else' — used to derive a numeric bound, a cache size, a batch budget or a safety argument.

**What goes wrong.** Call sites found by grepping one object's method name miss the ones that arrive through a different object in a different module — a helper that forwards, a step module that holds its own reference. The enumeration reads as settled fact and the number derived from it is then used to size something real. In one measured case the true worst-case rate was five times the figure the closed list produced.

**Fix.** When a bound depends on how many times something happens, go looking for the WAYS IN, not just the ways it is called where you were already reading: search for the method name, for forwarders, and for the objects that hold a reference. Write the enumeration as evidence with its method stated, not as a closed set, and separate the RATE from the HAZARD rate — the two are not the same, and conflating them makes the number wrong in both directions. If the safe value cannot be settled from the source, say so and leave the floor where it is until it is measured, rather than changing behaviour on an estimate on the eve of a production run.

**How to confirm it fires.** Precursor: grep comments for `nothing else|only two|exactly N|the only caller|no other`. When the claim is load-bearing (a constant, a cap, a budget is derived from it), re-derive it: grep the method name repository-wide, then grep for objects that hold the receiver. Fires when any call site is outside the enumerated list, or when the comment does not say how the list was obtained.

---

## `config-knob-renames-must-not-fail-silently`

**Low** — Name every operator knob with the project's established prefix, and do not silently accept the old spelling

**Look for.** A new environment variable whose prefix differs from the ones already in the runbooks, or a rename that leaves the old name quietly working.

**What goes wrong.** Runbooks and wrapper scripts set the established family of knobs; one spelled differently is one knob an operator sets and then wonders why nothing changed. A silently accepted alias is the same trap in the other direction — two names, no error, and no way to tell which one took effect. This is a project naming convention rather than a portable Oracle fact, but it is checked here because the failure is indistinguishable from a bug.

**Fix.** Use the established prefix, assert the exact knob name in a test so it cannot drift, and assert that any superseded spelling is IGNORED. If a knob was already published, honour the old name explicitly with a deprecation line rather than silently.

**How to confirm it fires.** grep the diff for os.environ/getenv string literals and compare their prefix with the knobs already present in the same module. Flag a mismatch, and flag a rename with no test asserting the new name.

---

## `implicit-comma-joins`

**Low** — Comma-separated FROM lists hide which predicates are joins and which are filters

**Look for.** A FROM clause listing three or more tables separated by commas, with all join conditions mixed into one WHERE clause alongside row filters. Usually appears together with `(+)`.

**What goes wrong.** With ten tables in a comma list there is no syntactic marker separating the nine join conditions from the filters, so a missing join condition — the accidental cartesian product — is invisible: the query returns more rows than it should and the reviewer has to count predicates against tables to notice. It also makes the outer-join rules above unverifiable at a glance, because a table's join type is spread across several unrelated lines of WHERE. In a large file this is the main reason a hunk cannot be reviewed without pulling in the whole statement.

**Fix.** Convert to ANSI joins, one `JOIN ... ON ...` per table, with join conditions in ON and filters in WHERE. A missing join condition then becomes a syntax error rather than a silent cross product, and each table's join type is on one line. Do this as its own change, separate from any behaviour change, so the diff is reviewable.

**How to confirm it fires.** Regex precursor: a `FROM` clause with two or more top-level commas and no `JOIN` keyword. Count distinct table aliases n; count equality predicates in WHERE that reference two different aliases j. If j < n-1 there is a cross product — report it as correctness, not style.

---

## `repeated-case-expression-in-group-by`

**Low** — A CASE expression duplicated between SELECT and GROUP BY will drift

**Look for.** The same multi-branch `CASE` expression written out twice — once in the select list, once in the GROUP BY — particularly when it spans several lines, and doubly so when a third copy appears in an ORDER BY.

**What goes wrong.** Oracle will not accept a select-list alias in GROUP BY in most versions, so the expression gets copied. Two copies is two things to edit: change one branch and not the other and the statement either fails with 'not a GROUP BY expression' — the good outcome — or, if the copies remain individually valid, groups by something different from what it displays, which is a silently mis-aggregated report. Long CASE ladders repeated twice also swamp a diff: a one-branch change shows as a large hunk and reviewers skim it.

**Fix.** Compute the expression once in an inline view or a WITH clause and group by the resulting column name in the outer block: `SELECT bucket, COUNT(*) FROM (SELECT CASE ... END AS bucket FROM SOURCE_TABLE) GROUP BY bucket`. The diff then shows exactly one changed branch and the two copies cannot disagree.

**How to confirm it fires.** Regex precursor: `GROUP\s+BY` followed by `CASE`. Fires when a CASE expression of more than two branches appears both before FROM and in the GROUP BY of the same block. Cheap textual check: normalise whitespace and compare the two substrings.

---

## `sql-constant-name-is-not-a-type`

**Low** — Classify a module constant as SQL by where it is USED, not by what it is called

**Look for.** A collector that decides which module-level constants hold SQL from a name suffix — `_SQL`, `_QUERY`, `_WHERE`, `_STMT`.

**What goes wrong.** It fails in both directions and both are silent. Constants named for what they mean rather than what they are (a join clause, a key expression, a text-coercion snippet) are missed, so real SQL goes unreviewed. Constants holding analyst notes that happen to quote SQL get picked up, so the reviewer reads prose and learns to skip. Naming conventions are not a type system and drift the moment someone writes a constant whose meaning is the interesting part.

**Fix.** Classify by reachability. Build `{constant -> set of positions that reference it}`; a constant referenced from a SQL-carrying field or an execute call is SQL, one referenced only from a notes field is prose. Fall back to a content test only for the unreferenced remainder: treat it as SQL if the assembled text starts with a SQL construct (optionally parenthesised SELECT / CASE WHEN / an Oracle scalar function / a bare relation name followed by JOIN) or carries two or more structural markers.

**How to confirm it fires.** `grep -cE '^[[:space:]]*_?[A-Z][A-Z0-9_]*(SQL|_WHERE|_FROM|_JOIN|_KEY|_EXPR|_QUERY|_STMT)[[:space:]]*(:[^=]*)?='` gives the population the name rule would select; compare it against the reachability set. If they differ materially, the name rule is unsafe here.

---

## `test-expectation-sql-is-a-separate-surface`

**Low** — Pair a changed test SQL expectation with the production statement, don't lint it

**Look for.** SQL substrings inside assertion strings and fixtures in test files — `assert "FROM TARGET_TABLE alias" in sql`, `("sql", "SELECT COUNT(*) FROM some_dictionary_view")`.

**What goes wrong.** These are expectations, not statements that will run, so linting them reviews the wrong artifact and produces findings nobody can act on. But dropping test files from the collector entirely loses the one cheap consistency check available: a diff that changes a production statement without touching the expectation that pins it, or changes the expectation while the statement stands still, is very likely a guard being loosened to match a change rather than a change being verified.

**Fix.** Classify test paths as their own surface. Do not run SQL lint rules on them. Instead extract the asserted substrings, match them against the production SQL units changed in the same diff, and report the two asymmetries: production SQL changed with no expectation touched, and expectation changed with no production SQL touched.

**How to confirm it fires.** Path matches the project's test convention (a `tests/` directory or a `test_*.py` name). Within it, a SQL keyword appearing inside an `assert` statement, a comparison operand, or a fixture literal. Correlate on the enclosing production symbol name where the test names one.

---

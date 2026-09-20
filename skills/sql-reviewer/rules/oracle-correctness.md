# Oracle correctness

Semantics that make a query return the wrong rows while running perfectly.

58 rules. Severity: Critical 7, High 33, Medium 17, Low 1.

---

## `arm-write-timeouts-not-read-timeouts`

**Critical** — Arm call timeouts on every database the run WRITES — including the bookkeeping one — and on none that it only reads

**Look for.** A lock/call timeout applied to the source pool, or applied to the primary target while the metadata/audit/bookkeeping connection is left unbounded.

**What goes wrong.** An Oracle row-lock wait never times out and never raises: the blocked INSERT or COMMIT simply sits there, so the run shows no error, no retry and no progress. A long SELECT, by contrast, is legitimate — arming the source aborts honest work. The forgotten case is the bookkeeping database: it is written once per row, so when it stops answering the call waits forever, and the run hangs with nothing in the log but a heartbeat. In one incident a batch sat on a single row for 15 hours for exactly that reason, because the original rule had lumped the bookkeeping connection in with the source.

**Fix.** Derive the timeout per connection ROLE: writable connections (target and metadata) take the configured ceiling, read-only source connections take zero. Keep it opt-in (default off), since turning it on by default fails batches on any database slower than someone's guess. Assert all three settings in one test so a shared config builder cannot blur them.

**How to confirm it fires.** in the diff, find where each pool's timeout is set and enumerate the roles. Flag a nonzero timeout on the read pool, and flag a zero/absent one on any pool the run writes to — the audit/metadata one is the one usually missed.

---

## `concat-key-null-collapse`

**Critical** — Guard every operand of a concatenated composite key against NULL

**Look for.** A projection or derived column that builds a composite key or a display value by concatenating two or more columns with `||` and a separator — `TO_CHAR(a) || '|' || TO_CHAR(b)`, `a || '/' || b` — with no test that both operands are non-NULL.

**What goes wrong.** Oracle treats NULL as the empty string inside `||`, so the expression does not return NULL when an operand is missing: it returns a string that still LOOKS like a key ('|1635', '384/', '/2023'). Downstream the value passes an IS NOT NULL / `required` check, is used to look a parent up, matches nothing, and the row is either lost or written with a fabricated-looking identifier. The failure is silent in both directions: nothing raises, and the bad value is indistinguishable from a real one afterwards.

**Fix.** Either add the non-null test to the producing subquery's WHERE (`AND t.some_column IS NOT NULL`), or wrap the whole expression so it collapses to NULL when any part is missing: CASE WHEN a IS NOT NULL AND b IS NOT NULL THEN TRIM(TO_CHAR(a)) || '/' || TRIM(TO_CHAR(b)) END NULL is the honest answer; a `required`-style guard then rejects the row BY NAME instead of letting a half-formed key travel.

**How to confirm it fires.** Regex precursor: `\|\|` appearing in a Python string literal that also contains a quoted separator (`'|'`, `'/'`, `'-'`). Then check semantically: does every operand's column appear in a NOT NULL test — either in the same expression's CASE, or in the subquery's WHERE, or as a declared NOT NULL column? If any operand is nullable and untested, the rule fires.

---

## `dedupe-shortcut-needs-a-real-constraint-behind-it`

**Critical** — Only stop detecting duplicates in code where the target really enforces that uniqueness

**Look for.** A cap, sampling, TTL or 'saturation' added to an in-process duplicate check, applied unconditionally to every declared unique key.

**What goes wrong.** An in-process check bounded unconditionally assumes every uniqueness rule has a database constraint behind it. Where the rule is enforced by an application service the pipeline never calls, the duplicate past the cap is ACCEPTED, inserted, given its own PK, crosswalk entry and lineage row — with no reject and no ORA-00001 to find it by afterwards. In an insert-only pipeline nothing can take it back, and the damage propagates into every child table that resolves through it. The bounded version is therefore worse than the MemoryError it fixes, because it fails silently.

**Fix.** Make the bound opt-in PER KEY: the spec must name the database constraint it is handing over to. Undeclared keys keep the unbounded, always-detecting behaviour. In a mixed spec, bound only the declared key and go on memoising the others in the same rows. Refuse at construction a declaration that names a key the spec does not declare, or a blank constraint name.

**How to confirm it fires.** when a diff adds any limit to a uniqueness/dedupe structure, check whether the limit is conditional on a per-key declaration. An unconditional `if len(seen) >= cap: return` on a dedupe path is the finding. Then ask, for each key, whether the target constraint exists — service-enforced rules are the dangerous ones.

---

## `not-in-over-nullable-subquery`

**Critical** — Never use NOT IN against a subquery column that can be NULL — use NOT EXISTS

**Look for.** A predicate of the form `x NOT IN (SELECT some_column FROM SOURCE_TABLE)` where `some_column` has no NOT NULL constraint. Extra-suspicious variants: the subquery column wrapped in NVL/COALESCE to a sentinel (`NOT IN (SELECT NVL(some_column,0) FROM ...)`), or a NOT IN and a NOT EXISTS for the same relationship sitting side by side in one WHERE clause.

**What goes wrong.** If the subquery returns even one NULL, `NOT IN` evaluates to UNKNOWN for every outer row and the predicate filters out the entire result set. The query does not error — it silently returns zero rows, or, inside a larger WHERE, silently drops a whole population. The usual field fix is worse than the bug: wrapping the subquery column in `NVL(some_column, 0)` restores non-empty output but now every outer row whose value is genuinely 0 is excluded, because it matches the sentinel. A NOT IN and a NOT EXISTS left on the same relationship is the fossil of someone hitting this and never removing the broken half — and it costs a second full pass over the subquery table.

**Fix.** Rewrite as an anti-join: `AND NOT EXISTS (SELECT 1 FROM SOURCE_TABLE s WHERE s.some_column = t.some_column)`. NOT EXISTS has the NULL semantics people actually intend, needs no sentinel, and Oracle costs it as a proper anti-join. If NOT IN must stay for some reason, add `AND some_column IS NOT NULL` inside the subquery — never an NVL sentinel. Delete the redundant duplicate predicate.

**How to confirm it fires.** Regex precursor: `NOT\s+IN\s*\(\s*SELECT`. Then confirm semantically: is the selected column declared NOT NULL? If you cannot prove it is, the rule fires. Fires at Critical if the column is nullable or unknown; also fires if the subquery column is wrapped in NVL/COALESCE (sentinel bug), or if a NOT EXISTS on the same two columns appears within the same WHERE clause.

---

## `outer-join-plus-filter-without-plus`

**Critical** — In legacy (+) syntax, any predicate on the optional table without (+) silently converts the outer join to an inner join

**Look for.** Oracle's pre-ANSI outer-join operator `(+)` in a WHERE clause, where the same table also appears in another predicate WITHOUT `(+)` — typically a filter like `AND child.some_flag = 1` or an equijoin onto a third table `AND child.parent_id = lookup.id`. Also look for `(+)` inside an OR or an IN list.

**What goes wrong.** The null-extended rows the outer join produces carry NULL in every column of the optional table. Any predicate on that table that is not itself marked `(+)` therefore evaluates to UNKNOWN for exactly those rows and discards them — the join degrades to an inner join with no error, no warning, and no visible syntax change. Rows the author believed were preserved disappear, which in a migration means a source population is silently under-extracted. `(+)` also has hard limitations that surprise people: it is not permitted in a predicate joined by OR or in an IN list (ORA-01719), and a table may not be outer-joined to more than one other table (ORA-01417), so the only way to express such a shape in this syntax is to break the outer semantics.

**Fix.** Convert the whole query to ANSI join syntax: `FROM PARENT_TABLE p LEFT JOIN CHILD_TABLE c ON p.id = c.parent_id AND c.some_flag = 1`. Filters on the optional table then go in the ON clause, where they keep the outer semantics, and the reader can see join conditions separated from row filters. If the predicate really is meant to filter the joined result, put it in WHERE deliberately and say so in a comment.

**How to confirm it fires.** Regex precursor: `\(\+\)`. For each table that appears with `(+)`, collect every other predicate in the same WHERE clause mentioning that table alias; if any of them lacks `(+)` on that table's column, the rule fires. Fires at Critical when the un-marked predicate is a filter (compares to a literal) rather than a further join.

---

## `outer-join-predicate-in-where`

**Critical** — A WHERE predicate on the right table of a LEFT JOIN collapses it into an inner join

**Look for.** ANSI `LEFT JOIN CHILD_TABLE c ON ...` followed by a WHERE clause referencing `c.some_column` with anything other than `IS NULL` / `IS NOT NULL` used as a deliberate anti-join test. Also fires when a filter that belongs to the optional side has been moved out of ON into WHERE during a refactor.

**What goes wrong.** Same failure as the `(+)` case, in modern clothing. WHERE is applied after the join produces null-extended rows, so `c.some_column = 'X'` (or `> 0`, or `IN (...)`) is UNKNOWN for every unmatched row and removes it. The query still says LEFT JOIN, so a reviewer skimming the FROM clause sees preserved-side semantics that the WHERE clause has already destroyed. In gap and reconciliation reporting this is especially damaging: the rows that matter most are exactly the unmatched ones.

**Fix.** Move the filter into the ON clause: `LEFT JOIN CHILD_TABLE c ON c.parent_id = p.id AND c.some_column = 'X'`. Keep in WHERE only predicates on the preserved table, or an explicit `c.key IS NULL` when an anti-join is intended. When you need both an outer join and a post-join filter, write the filter as `(c.some_column = 'X' OR c.key IS NULL)` so the intent is visible.

**How to confirm it fires.** Regex precursor: `LEFT\s+(OUTER\s+)?JOIN`. Capture the right-hand alias; then scan the statement's WHERE clause for that alias. Fires unless every occurrence is an `IS NULL` / `IS NOT NULL` test. In a diff, fires when a hunk adds an alias-qualified predicate to WHERE where that alias belongs to an outer-joined table declared outside the hunk.

---

## `partially-null-composite-unique`

**Critical** — A partially-NULL composite unique key DOES collide in Oracle

**Look for.** A duplicate pre-check (in ETL code or a staging query) that skips the comparison when ANY component of a composite key is NULL — `if any(v is None for v in key): continue` — or a spec that leaves one half of a composite unique key unmapped while the other half is a constant.

**What goes wrong.** Oracle's rule is that you cannot have identical values in the NON-NULL columns of a partially null composite unique key. Only an ALL-NULL key is exempt from the index. So (NULL, 1157) twice is a genuine ORA-00001. A pre-check that waves through any partially-null key reports success and lets every such row reach the database, where they fail one at a time with an anonymous constraint error. The worst shape is a two-column key where one column has no source (NULL on every row) and the other is a fixed constant: exactly one row in the whole table can then exist.

**Fix.** Compare the WHOLE key tuple including NULLs, and skip only when every component is NULL. Separately, when a target carries a composite unique key, check whether the migration actually supplies more than one of its columns before assuming the key discriminates anything.

**How to confirm it fires.** Two shapes. (a) Python: `any(` combined with `is None` inside a duplicate/seen-key check — fire unless it is `all(`. (b) Spec shape: a declared composite unique tuple where one column is written by a constant transform and another has no mapping at all — fire, because the key cannot discriminate.

---

## `absent-fk-accepts-a-wrong-parent-silently`

**High** — Do not rely on a foreign key the target does not actually declare

**Look for.** A transform that writes a resolved parent id into a *_ID column, with a comment or assumption that a bad value would be caught by the database, and no check that the FK exists in the deployed catalog.

**What goes wrong.** When the FK really is declared, an unresolved parent fails as ORA-02291 naming the constraint — loud, and easy to find. When it is not declared, the same wrong value inserts without a murmur and stays wrong forever: a raw source enum copied into a lookup FK column, a dangling id pointing at nothing. The two cases look identical in the code and opposite in production.

**Fix.** Verify FK presence in the deployed target catalog for every parent column the pipeline writes, and declare those columns required in the pipeline's own validation so an unresolved parent becomes a named reject regardless of what the database enforces. Pin the premise with a test that fails if someone later adds the FK, so the note gets re-read.

**How to confirm it fires.** Regex precursor: an assignment into a column named `*_ID` from a lookup/resolve/transform call. Semantic confirm: is that column listed as an FK in the target catalog the code will actually run against? If not, and it is not in the pipeline's required/validated set, the rule fires.

---

## `all-null-key-cannot-collide`

**High** — Treat an all-NULL unique key as uncheckable, not as a duplicate

**Look for.** Dedupe or uniqueness code that builds a key tuple and memoises/compares it without testing whether every component is NULL.

**What goes wrong.** Oracle does not index an entry whose key columns are ALL NULL, so two such rows genuinely cannot collide in the database. Memoising them produces false duplicate rejects on perfectly loadable rows and, worse, consumes any memo budget on behalf of keys that could actually use it. (A partially NULL composite key does still index, so the rule is all-NULL, not any-NULL.)

**Fix.** Skip memoisation and rejection when every component of the key tuple is NULL; assert it in a test that pushes many all-NULL rows through and checks that the memo stayed empty, no reject was produced and no notice fired.

**How to confirm it fires.** find the key construction in the diff (tuple(row.get(c) for c in cols)). Look for a guard such as `if all(v is None for v in key): return`. Missing guard on a dedupe path is the finding; an `any(... is None)` guard is a different and usually wrong rule.

---

## `bare-to-number-on-varchar`

**High** — Use DEFAULT NULL ON CONVERSION ERROR, never a bare TO_NUMBER, on a character column

**Look for.** `TO_NUMBER(col)` or `TO_DATE(col, ...)` in a projection or predicate where `col` is declared VARCHAR2/NVARCHAR2 in the source.

**What goes wrong.** A single unconvertible value anywhere in the table raises ORA-01722/ORA-01858 and fails the WHOLE extract, not the one row. Character columns in legacy systems routinely hold a name, a label or a stray code where the schema implies a number, and the bad row is usually invisible until the run dies on it hours in.

**Fix.** Oracle 12.2+ offers a per-value fallback; combine it with TRIM so padding does not itself cause a failure: TO_NUMBER(TRIM(t.some_column) DEFAULT NULL ON CONVERSION ERROR) The unconvertible value then becomes NULL and is handled by the row's own resolution/reject rules instead of killing the batch.

**How to confirm it fires.** Regex precursor: `TO_NUMBER\(` or `TO_DATE\(` inside a SQL string. Fire when the argument is a bare column reference (optionally wrapped in TRIM) and the string does not also contain `ON CONVERSION ERROR`. Confirm by checking the source column's declared type is character.

---

## `char-vs-byte-length-semantics`

**High** — Declare VARCHAR2(n CHAR) for multi-byte text, and verify CHAR_USED

**Look for.** A VARCHAR2(n) column with no CHAR qualifier that is intended to hold non-Latin text, especially when sibling name/description columns on the same table do carry (n CHAR).

**What goes wrong.** The default length semantics are bytes. On a multi-byte character set a non-Latin letter costs two or more bytes, so the column guaranteed to hold that text has roughly half the room the number claims. Worse, a client-side length guard that reads the declared width from the catalog gets CHAR_LENGTH, which reports the byte count when CHAR_USED = 'B' — so an over-long value passes every check the tool makes and then fails inside Oracle as ORA-12899, an error that names no column on a multi-column INSERT.

**Fix.** ALTER TABLE ... MODIFY (some_column VARCHAR2(n CHAR)), sized so the widest possible source value cannot reach it, then verify: SELECT char_length, char_used FROM USER_TAB_COLUMNS WHERE table_name = ... — a 'B' means the ALTER ran without char semantics and the problem was moved, not ended. Widening is safe with data present and safe to re-run. Any client-side width guard must read char_used too, not just char_length.

**How to confirm it fires.** Regex precursor: `VARCHAR2\(\d+\)` in DDL (no CHAR), or a catalog read of `CHAR_LENGTH` without `CHAR_USED`. Semantic confirm: is the column documented or named as holding non-Latin text, or do adjacent columns on the same table use `CHAR`? Either makes it fire.

---

## `clob-emptiness-test`

**High** — Test a LOB for content with DBMS_LOB.GETLENGTH, not IS NOT NULL or TRIM

**Look for.** A scope predicate or content test applying `IS NOT NULL` or `TRIM(col) IS NOT NULL` to a column whose declared type is CLOB/NCLOB/BLOB.

**What goes wrong.** `IS NOT NULL` is true for a zero-length LOB locator, so an 'empty' LOB passes the test and produces a target row that asserts content which is not there. `TRIM` on a CLOB only works through an implicit conversion to VARCHAR2 and is unreliable past 4,000 characters — the exact case a LOB exists for. Mixing the two across columns of different types in one predicate also produces a test that is correct for some columns and wrong for others.

**Fix.** Use `DBMS_LOB.GETLENGTH(col) > 0` for LOB columns, `TRIM(col) IS NOT NULL` for bounded VARCHAR2, and a bare `IS NOT NULL` for DATE/NUMBER — deliberately NOT a uniform test, because a uniform one is wrong on at least one of the three.

**How to confirm it fires.** Regex precursor: `IS NOT NULL` or `TRIM\(` inside a SQL fragment. Then resolve the column's declared type from the DDL/catalog; fire when it is a LOB type. Cheap secondary signal: the same fragment mentions another column already tested with `DBMS_LOB.GETLENGTH`, i.e. the author knew about LOBs but missed one.

---

## `correlated-subquery-unqualified-column`

**High** — An unqualified column inside a correlated subquery can silently bind to the outer query

**Look for.** A subquery whose WHERE clause mixes qualified and unqualified column references — `WHERE some_flag IS NULL AND c.parent_id = p.id` — particularly when the subquery joins two or more tables, or when the outer query has a table that also has a column of that name.

**What goes wrong.** Oracle resolves an unqualified name in the innermost block that has it, and if no table in the subquery has that column it silently looks outward to the enclosing block. The subquery then filters on an outer-row value instead of the inner row, and the query returns a plausible result that is wrong. The reverse is just as bad: the reference resolves inward today, and the day someone adds a same-named column to the other table in that subquery it either becomes ambiguous (ORA-00918) or, if only one side is in scope, flips meaning. Nothing in the diff will show it, because the change is in a different table's DDL.

**Fix.** Qualify every column reference with its table alias, without exception, inside any subquery — and give every table an alias. This is the single cheapest defence in a large SQL file and it is what makes a hunk reviewable in isolation.

**How to confirm it fires.** Regex precursor: locate parenthesised SELECT blocks; within each, find WHERE-clause identifiers with no `alias.` prefix. Fires whenever such an identifier exists and the subquery has more than one table in scope, or the outer block has any table at all. This is also the rule that makes 'review the hunk plus enough context to resolve aliases' non-negotiable.

---

## `count-distinct-drops-nulls`

**High** — COUNT(DISTINCT c) ignores NULLs — so does COUNT(c); only COUNT(*) counts every row

**Look for.** `COUNT(DISTINCT some_column)` used as a row or key count, especially in profiling, reconciliation and gap queries; a ratio built as `COUNT(*) / COUNT(DISTINCT some_key)`; `COUNT(DISTINCT a) = COUNT(*)` used as a uniqueness test.

**What goes wrong.** All aggregate functions except `COUNT(*)` skip NULL inputs. A grouping key that is NULL on some rows contributes zero to `COUNT(DISTINCT key)`, so a group made entirely of NULL-keyed rows reports zero distinct values and a duplication test of the shape `COUNT(*) = COUNT(DISTINCT key)` mis-classifies it. In a migration this is a source of numbers that are quietly too small: rows with a NULL business key are exactly the rows that will fail to load, and they are the ones the count leaves out. The same asymmetry makes `COUNT(some_column)` after an outer join a deliberate 'matched rows' count — useful, but easy to read as a row count.

**Fix.** Map NULL to an explicit sentinel before counting distinct values: `COUNT(DISTINCT NVL(TO_CHAR(some_column), '~NULL~'))`, using a sentinel the column cannot legally hold. Use `COUNT(*)` for row counts and `COUNT(some_column)` only where 'non-NULL rows' is what you mean — and say which in the column alias. Report the NULL count as its own column alongside, never folded in.

**How to confirm it fires.** Regex precursor: `COUNT\s*\(\s*DISTINCT` and `COUNT\s*\(\s*(?!\*)` . Fires unless the counted expression is provably NOT NULL (declared constraint) or is already wrapped in NVL/COALESCE to a sentinel. Highest severity when the result is labelled as a row count or feeds a subtraction against a `COUNT(*)`.

---

## `db-side-default-reads-its-own-target`

**High** — A database-evaluated default that reads its own target: check grain, schema qualification and re-run behaviour

**Look for.** A SQL expression injected into an INSERT's VALUES list that selects from the table being inserted into — a running counter such as `SELECT NVL(MAX(seq),0)+1 FROM {target} t WHERE t.parent = :PARENT`.

**What goes wrong.** Four distinct ways this goes wrong. (1) GRAIN: the counter's WHERE must match the target's actual unique index exactly; counting per the wrong parent gives each group its own counter starting at 1 and the second row of a group violates the constraint. (2) SCHEMA: a bare table name in an injected fragment resolves against the CONNECTING USER's schema, not the target schema — the subquery can silently read a different (empty) table. (3) CONCURRENCY: it works only because each row is its own statement and row N+1 sees row N's uncommitted insert; two concurrent writers of the same target break it. (4) RE-RUN: it is assigned on INSERT only, so a re-run does not recompute it.

**Fix.** Render the table name schema-qualified via a placeholder the sink substitutes, never as a bare name. Derive the WHERE from the target's real unique index (including conditional/function-based ones), not from what the counter 'ought' to be grouped by. State the single-writer assumption next to the expression. Guard the binds: if any bind column is NULL for a row, drop the expression for that row rather than raising and killing the batch.

**How to confirm it fires.** Regex precursor: a SQL string containing both `SELECT` and `MAX(` that is used as a column default/VALUES expression. Checks: does the FROM name a bare table rather than a qualified/placeholder name; does the WHERE column set equal the target unique index's column set; is there any comment about concurrency or re-runs.

---

## `dormant-conditional-unique-index`

**High** — Check function-based and conditional unique indexes before filling a previously-NULL column

**Look for.** A change that starts populating a target column which was NULL on every migrated row, on a table carrying a unique index built over CASE expressions or other function-based columns.

**What goes wrong.** Oracle does not treat two NULLs as equal, so a unique index whose expressions evaluate to NULL for every existing row is DORMANT — it exists, it is enabled, and it constrains nothing. The moment the column is populated the index becomes live and starts rejecting rows that have loaded fine for months. Looking only at `user_constraints` misses it entirely, because a function-based unique index is an INDEX, not a constraint.

**Fix.** Before populating a previously-empty column, list `user_indexes` / `user_ind_expressions` for the target as well as `user_constraints`, and read the index EXPRESSIONS — including any conditional clause, which tells you which rows are in the index at all (e.g. only active rows). Derive any counter or generated value from that expression's exact column set. Note that a column left to a DDL default may still put the row inside the index.

**How to confirm it fires.** Fire whenever a diff begins writing a target column that previously had no mapping. Then check the target's unique INDEXES (not just constraints) for that column, including function-based expressions. Precursor: a new entry appearing in a spec's column map or defaults for a column the same spec's comments describe as unmapped/NULL.

---

## `empty-string-is-null-in-oracle`

**High** — Do not use '' as a NOT NULL placeholder — Oracle stores it as NULL

**Look for.** A default, COALESCE/NVL fallback, or transform that supplies '' (or a Python "") for a NOT NULL character column that the source cannot fill.

**What goes wrong.** Oracle stores the zero-length string as NULL, so '' fails the very NOT NULL check the placeholder was added to satisfy. The row still rejects, and the reject reads as a missing-source problem rather than a wrong-placeholder problem.

**Fix.** Use a single space (or another explicitly chosen sentinel) and write down how to find those rows afterwards — WHERE some_column = ' '. Record that the placeholder makes fabricated rows indistinguishable from real ones except by that predicate, so the choice is a data decision, not a coding one.

**How to confirm it fires.** Regex precursor: `= ''`, `NVL(.*, '')`, `COALESCE(.*, '')`, or a Python default of `""` on a mapping entry. Semantic confirm: is the target column NOT NULL? Then the rule fires.

---

## `fetch-one-more-row-than-you-expect`

**High** — Detect a non-unique lookup with fetchmany(2) instead of silently taking fetchone()

**Look for.** A 'resolve one id' helper ending in `cur.fetchone()` where the WHERE clause is not provably unique — for example a lookup on (entity, key) when several sources can populate the same entity, or any lookup whose narrowing parameter is optional.

**What goes wrong.** `fetchone()` takes an arbitrary row and reports nothing when there were two. Where the answer becomes a foreign key, that is a perfectly populated column pointing at the wrong parent: no error, no rejected row, wrong data discovered months later. Overlapping ids across feeders are the normal case, not a corner one — two systems' sequences both start at 1.

**Fix.** `cur.fetchmany(2)` (or a COUNT) and raise a named error when a second row exists, quoting the competing sources so the caller can qualify the lookup. A failed run is the better outcome, and the real fix is to make the caller name the source. Where a caller legitimately wants the whole set, give it a separate `resolve_all` that returns an ordered list — and never let a cache in front of the lookup answer without passing through this guard.

**How to confirm it fires.** Precursor: grep for `fetchone()` in a function whose name or docstring says resolve/lookup/find/get_id. Semantic check: is the WHERE clause backed by a unique constraint covering exactly those columns? If a narrowing column is optional (defaults to None and is appended conditionally), the unnarrowed path is the one to judge. Fires when the unnarrowed path can return more than one row.

---

## `identifiers-cannot-be-bound-so-validate-them`

**High** — Validate every interpolated identifier; bind everything that is a value

**Look for.** An f-string putting a schema, table or column name into `ALTER SESSION`, DDL, or a FROM clause; or — the mirror image — a literal VALUE interpolated into a generated predicate (`WHERE x = '{entity}'`) where a bind would have worked.

**What goes wrong.** Bind variables cannot stand in for object names, so identifiers genuinely must be interpolated — which makes validating them the only defence there is. Interpolating a value is a different fault with two costs: it is an injection surface, and it produces a textually unique statement per value, so the shared-cursor benefit is lost and every variant hard-parses.

**Fix.** For identifiers: validate against a strict pattern before use (`[A-Za-z0-9_$#]{1,128}`, upper-cased) and RAISE on anything else rather than quoting and hoping. For values: bind them, including in predicates the engine generates on the fly. Keep the two paths visibly different in the code so a reviewer can tell which is which at a glance.

**How to confirm it fires.** Precursor: grep for an f-string or `%`/`.format` whose result is passed to `execute`. Classify each interpolated slot: identifier (table/column/schema/owner) or value. Identifier -> require a validating regex or allow-list on the path to that slot. Value -> fire, it should be a bind.

---

## `identity-generator-behind-seeded-data`

**High** — Re-seed the identity/sequence after inserting rows with explicit ids

**Look for.** A seed or data-fix script that inserts into an identity-column table with an explicit ID (often from MAX(ID)+1), with no follow-up that advances the generator.

**What goes wrong.** Rows inserted with explicit ids do not advance the identity column's underlying sequence. The next application insert asks the generator for an id, gets one already used, and fails with ORA-00001 on the primary key — in the application, days later, with nothing pointing back at the seed script. The same trap is why a seed script cannot simply omit the id: on a table already seeded past the generator, asking for one raises immediately.

**Fix.** Either take ids from MAX(ID)+1 in the seed and then re-seed the generator (ALTER TABLE t MODIFY (id GENERATED ... START WITH LIMIT VALUE)), or let the identity mint every id and never write one explicitly. Make the seed idempotent by matching on the business key, never on ID, and never rewrite ID on an existing row.

**How to confirm it fires.** Regex precursor: `INSERT INTO ... (ID,` or `MAX(ID)+1` / `MAX(ID) + 1` in a seed or migration SQL file. Semantic confirm: is the target's ID an identity column (or sequence-defaulted)? Then look for a `START WITH LIMIT VALUE` or equivalent re-seed in the same change; its absence fires the rule.

---

## `in-list-cap-and-never-inline-the-values`

**High** — Chunk IN lists below 1000 expressions and bind every value

**Look for.** `"WHERE col IN (" + ",".join(values) + ")"`, an IN list built from an unbounded key list, or a bulk close/update `WHERE id IN (...)` fed straight from a result set.

**What goes wrong.** Oracle caps a single IN list at 1000 expressions — more is ORA-01795, and it fails only once the list happens to get long, i.e. in production rather than in the test with five keys. Inlining the values is two faults at once: it is a SQL-injection surface, and it produces a textually different statement per chunk, so a large key list hard-parses every chunk instead of sharing one cursor.

**Fix.** Generate named binds (`:k0, :k1, ...`) and chunk below the cap — 900 is a common safe figure that leaves room for the other binds. For a composite key use the row-value form `(A, B) IN ((:k0_0, :k0_1), ...)`. Apply the same chunking to the UPDATE that closes the work list afterwards, not just to the SELECT that read it, and clamp the chunk size in one helper so a caller's larger batch size cannot break the statement.

**How to confirm it fires.** Precursor: grep for `IN (` built by `join(` , or for a loop appending placeholders. Two checks: (1) is the source list length bounded below 1000 by construction or by an explicit chunker? (2) are the elements `:name` placeholders, or values? Either failure fires.

---

## `inlined-literal-must-be-whitelisted-and-escaped`

**High** — Inline only whitelisted literal types into a SQL fragment, and escape quotes

**Look for.** f-string or `%`/`.format()` interpolation of a Python value into a SQL predicate or projection, with no type check on the value and no quote escaping — `f"{col} = '{value}'"`.

**What goes wrong.** These fragments are inlined VERBATIM into the statement and never validated, so whatever the value is becomes SQL. A value that is not a plain int or string produces SQL nobody has checked (a Python repr, a tuple, a None rendering as the literal 'None'); a string containing an apostrophe terminates the literal early and changes the predicate's meaning or breaks the statement. Analyst-authored values from a mapping workbook are exactly the kind of input this happens to. A booleanumber also renders as `True`/`False`, which is not valid Oracle.

**Fix.** Whitelist the inlinable types explicitly and RAISE on anything else rather than producing SQL nobody checked: ints emitted bare, strings quoted with `''` escaping (`"'" + v.replace("'", "''") + "'"`), booleans rejected (bool is an int subclass and must be excluded first). Prefer a declarative filter that renders the predicate for you over hand-written interpolation, and where a raw fragment is unavoidable, say in a comment WHY the declarative form could not express the rule.

**How to confirm it fires.** Regex precursor: an f-string or `.format(` whose template contains SQL keywords (`SELECT`, `WHERE`, `FROM`, `EXISTS`, `=`) and an interpolation slot. Fire when the interpolated value is not provably an int/str literal or an identifier from a fixed internal list. Fire hard when a slot sits inside single quotes with no escaping.

---

## `merge-on-clause-null-never-matches`

**High** — A MERGE ON clause comparing nullable columns never matches a NULL, so it appends forever

**Look for.** `MERGE ... ON (d.a = s.a AND d.b = s.b)` where any of those columns is nullable — typically an optional provenance/source column added by a later schema upgrade. Symptom: a queue or work-list table whose row count grows by the whole failing set on every re-run, and where closing one copy leaves the others open.

**What goes wrong.** `NULL = NULL` is UNKNOWN, not TRUE, so a row whose key component is NULL can never take the MATCHED branch. Every run inserts a fresh duplicate of the same logical fact. Counts read several times high, and any process that closes or acts on 'the' row acts on one copy of many.

**Fix.** Compare with a sentinel on BOTH sides — `NVL(d.col, '~') = NVL(s.col, '~')` — choosing a sentinel the column cannot legitimately hold, and build the unique index that keeps the table single-rowed on the SAME expression so the constraint and the MERGE's match agree. Where the identity genuinely does not exist (no natural key at all), fall back to a plain INSERT rather than merging on nothing: collapsing genuinely distinct failures loses information.

**How to confirm it fires.** Precursor: grep the hunk for `MERGE INTO` and read its `ON (` clause. For each compared column, check the DDL or the writer for nullability (an optional column, a column added in a later upgrade script, a parameter defaulting to `None`). Fires when a nullable column is compared with bare `=` and no NVL/DECODE sentinel on both sides. Bonus signal: a matching unique index that does NOT use the same expression.

---

## `mod-with-negative-dividend`

**High** — Oracle MOD returns a negative result for a negative dividend

**Look for.** `MOD(x, n)` where `x` is derived from date arithmetic (`TRUNC(some_date) - DATE '...'`), a difference of two columns, or any expression that is not provably non-negative — and the result is used as an index, a bucket number or a day-of-week.

**What goes wrong.** Oracle's `MOD` takes the sign of the dividend (it is a remainder, not a mathematical modulus), so `MOD(-3, 7)` is -3, not 4. Used to derive a weekday or a bucket, that produces negative bucket numbers for any row whose anchor date precedes the epoch you subtracted — and legacy date columns routinely hold values centuries away from the plausible range, which is exactly where the negatives come from. The query does not fail; it classifies a slice of rows into buckets that no lookup will match, and the mismatch is read as a data-quality finding rather than an arithmetic bug.

**Fix.** Normalise: `MOD(MOD(x, n) + n, n)` always lands in 0..n-1. Separately, never assume a date column is inside a sane range — bucket the extremes explicitly (`< DATE '1950-01-01'`, `> DATE '2100-01-01'`) before deriving anything from it, and check the actual MIN/MAX first.

**How to confirm it fires.** Regex precursor: `MOD\s*\(`. Fires unless the first argument is provably non-negative, or the expression is already the doubled `MOD(MOD(...)+n,n)` form. Treat any date-difference first argument as presumed negative-capable.

---

## `negated-predicate-three-valued-logic`

**High** — Negating a predicate that can be UNKNOWN drops rows from BOTH buckets

**Look for.** A reconciliation or bucketing query written as `COUNT(CASE WHEN <p> THEN 1 END)` and `COUNT(CASE WHEN NOT (<p>) THEN 1 END)`, where `<p>` contains a function that returns NULL for a NULL input (`DBMS_LOB.GETLENGTH`, `TO_NUMBER`, most scalar functions) OR a comparison against a nullable column.

**What goes wrong.** If `<p>` evaluates to UNKNOWN, `NOT <p>` is also UNKNOWN, so the row satisfies neither branch and is counted in neither bucket. The buckets silently fail to sum to the total, and any conclusion drawn from them ('zero rows are affected') is false. This is the same trap as testing a LOB with IS NOT NULL, one level up.

**Fix.** Make the predicate total before negating it — wrap NULL-producing functions in `NVL(...,0)`, or write each bucket as an explicit positive condition — and always assert that the buckets sum to `COUNT(*)`. Note the same UNKNOWN in a WHERE clause is usually CORRECT (it excludes the row); the bug is specific to counting or to a negated branch that is meant to be exhaustive.

**How to confirm it fires.** Regex precursor: `NOT\s*\(` inside a SQL string, or a pair of `CASE WHEN` counters whose conditions are textual negations of each other. Semantic check: does the inner predicate reference a nullable column or a scalar function without an NVL wrapper? If the review sees bucket counts quoted in a comment, check whether they sum to the stated total.

---

## `no-order-by-means-no-row-position`

**High** — Slice a re-runnable extract by key range, never by row number

**Look for.** Resume or slice logic expressed as 'restart at row N', an OFFSET, or a ROWNUM range over an extract that carries no ORDER BY. Also: an operator-supplied slice predicate that REPLACES the spec's own scope predicate instead of being ANDed with it.

**What goes wrong.** A SELECT without ORDER BY has no defined row order, and the database is free to return rows differently between runs — a different plan, a different degree of parallelism, or simply different blocks in cache. 'Row 4.8 million' is therefore not a position that survives a re-run: a resume built on it can skip and duplicate rows at the same time, and nothing in the output says so. A key RANGE is complete and disjoint by construction and does survive.

**Fix.** Slice by primary-key ranges, documented as such in the operator interface, and AND the slice with the spec's own scope predicate so the back door cannot migrate rows the mapping deliberately excludes. After a sliced run, do NOT advance the table's completion marker, and say in the log which ranges remain — otherwise the next incremental run treats the unread rows as done.

**How to confirm it fires.** Precursor: grep for `OFFSET`, `ROWNUM`, `row_number()`, or a CLI/config knob named skip/start/from-row. Confirm the underlying query has no ORDER BY over a unique key. Also check that any operator-supplied WHERE is appended with AND rather than replacing an existing predicate, and that the completion/watermark update is gated on the slice flag.

---

## `not-equal-drops-nulls`

**High** — `<>` and `NOT IN` exclude NULL rows — a complementary split loses them

**Look for.** A WHERE clause written as the complement of a value test — `some_column <> 7`, `some_column NOT IN (1,2)` — especially when a sibling query claims the other half with `some_column = 7`.

**What goes wrong.** In three-valued logic `NULL <> 7` is UNKNOWN, not TRUE, so a bare `<>` or `NOT IN` silently drops every row whose column is NULL. Where two queries are meant to partition a table (one loader takes the matches, another takes the rest), the two halves no longer add up to the whole and the NULL rows vanish with no error, no reject row and no count.

**Fix.** Spell the complement NULL-safely and render it from one place so both halves cannot drift: (some_column IS NULL OR some_column NOT IN (<values>)) The same applies to `NOT EXISTS` versus `EXISTS(... <> v)`: prefer the exact negation of the positive predicate, not a rewritten one.

**How to confirm it fires.** Regex precursor: `<>\s*[0-9']` or `\bNOT IN\b` inside a SQL string literal. Fires unless the same parenthesised expression also contains `IS NULL OR`. Escalate severity when another spec/query in the same module carries the matching positive predicate on the same column.

---

## `scalar-subquery-multi-row-risk`

**High** — A scalar subquery that is not keyed on a unique column is a latent ORA-01427

**Look for.** A scalar subquery in a SELECT list, a SET clause or a comparison whose WHERE does not fully match a declared primary or unique key of the subqueried table. DISTINCT, `ROWNUM = 1`, `MAX(...)` or `FETCH FIRST 1 ROW ONLY` bolted onto a scalar subquery is the strongest tell — someone already met the error.

**What goes wrong.** A scalar subquery must return at most one row. Oracle does not check this at parse time; it raises ORA-01427 at runtime, on the first row of data that has two matches. So the query passes review, passes test, runs in production for a long time, and then fails the day a duplicate lands in a lookup table with no unique constraint — which in migration work is common, because legacy lookup tables often have no constraints at all. The defensive spellings shift the failure rather than remove it: DISTINCT still raises if two rows differ in the selected column, and `ROWNUM = 1` returns an arbitrary row with no error at all, which is worse.

**Fix.** Join to the table instead of subquerying, on its full unique key, so the database enforces the cardinality. Where the lookup genuinely has no unique constraint, aggregate deliberately — `(SELECT MAX(some_column) FROM ...)` with a comment saying which duplicate you are choosing and why — or pre-aggregate in an inline view. Before relying on a scalar subquery, check that the correlation columns are actually a unique key on the target; an export that reports zero unique constraints for a schema usually means they were not captured, not that none exist.

**How to confirm it fires.** Regex precursor: a parenthesised `SELECT` used where one value is expected. Confirm: do the subquery's equality predicates cover a declared PK/UK of that table? If not, fires. Fires at High when DISTINCT / ROWNUM=1 / FETCH FIRST is present, because that is evidence the multi-row case already happened.

---

## `signed-year-and-precision-in-the-date-format-mask`

**High** — Render dates as text with SYYYY plus fractional seconds and zone, never plain YYYY

**Look for.** A `TO_CHAR(date_col, 'YYYY-MM-DD HH24:MI:SS')` or an `NLS_*_FORMAT` mask starting with `YYYY`; a TIMESTAMP rendered without `.FF`; a TIMESTAMP WITH [LOCAL] TIME ZONE rendered without `TZH:TZM`. Also a text-to-date regex that requires a digit as the first character of the year.

**What goes wrong.** Plain `YYYY` prints the ABSOLUTE year, so 3 BC and 3 AD both come back as '0003' — a corrupt date is silently converted into a plausible one and nothing downstream can tell. Dropping fractional seconds is silent precision loss, and a high-water mark that loses precision re-reads or skips rows on the next incremental pass. Dropping the zone offset turns an aware timestamp into a naive one, which is a different value.

**Fix.** Use `SYYYY-MM-DD HH24:MI:SS` for DATE, append `.FF9` for TIMESTAMP, append ` TZH:TZM` for the zone-carrying types, and parse the offset back into an aware datetime so the value matches what the driver would have returned. Note that `SYYYY` emits a LEADING SPACE in the sign position for AD years, so the parser must tolerate leading whitespace and an optional minus — that leading `\s*` is not cosmetic. Set the same sign-aware masks at session level too, so any hand-written implicit conversion elsewhere in the codebase is sign-aware as well.

**How to confirm it fires.** Precursor: grep for `TO_CHAR(` near a date column, and for `NLS_DATE_FORMAT|NLS_TIMESTAMP`. Inspect the mask literal: fires if it starts with `YYYY` rather than `SYYYY`, or if the column type is TIMESTAMP[_TZ|_LTZ] and the mask lacks `FF` / `TZH`. Second check: if a matching parser regex exists, does it accept an optional sign and leading whitespace?

---

## `structural-lint-for-hand-run-sql-scripts`

**High** — Structurally lint every shipped SQL/PL-SQL script the test suite otherwise treats as data

**Look for.** New or edited .sql files under the package (schema upgrades, seeds, DBA scripts) with no test that opens them.

**What goes wrong.** These files are data to a Python test suite — they are read by a person, not imported — so a corrupted one passes every test and fails on a server at upgrade time with an ORA-06550 and a line number, potentially weeks after the commit. The real incident: two upgrades interleaved line by line so PL/SQL blocks were spliced through each other, leaving more IF...THEN than END IF and one script's statements inside another's block. It could not compile. The other silent shape is a missing terminator: a PL/SQL block with no lone '/' is never executed at all, and the script reports success.

**Fix.** Add a structural check over every shipped script: BEGIN count equals END; count, IF...THEN count equals END IF; count, and one lone '/' terminator per block. State plainly that these checks are structural, not semantic — they prove only that Oracle will parse it. Require versioned upgrade scripts to name their version in the header, since a DBA picks them by name and runs them in order.

**How to confirm it fires.** list *.sql paths in the diff and check whether any test globs that directory. If a script changed, re-run the counts mentally on the hunk: an added IF without an added END IF, or an added BEGIN without a matching '/' line, is the finding.

---

## `sub-select-inside-insert-must-be-schema-qualified`

**High** — A sub-select embedded in a write statement must name the same schema-qualified object the write targets

**Look for.** An INSERT/UPDATE built with a schema prefix for the target but a BARE table name inside an embedded SELECT — e.g. INSERT INTO {schema}.TARGET_TABLE ... VALUES ((SELECT ... FROM TARGET_TABLE ...)).

**What goes wrong.** An unqualified name resolves against the CONNECTING user's schema (or whatever CURRENT_SCHEMA happens to be), not against the schema the statement is writing to. When the migration account differs from the target owner, the expression silently reads a different object — or raises ORA-00942 — while the INSERT half writes to the right place. It works in every environment where the two coincide, which is usually the developer's.

**Fix.** Use a single placeholder token (e.g. {target}) in the declared expression and substitute the SAME schema-qualified identifier the write path computed, so the two can never diverge. Assert in a test that the rendered statement contains the qualified name in BOTH halves and that no unexpanded token survives.

**How to confirm it fires.** in any diff adding SQL that both writes and reads the same table, check that each object reference is built from the same qualified-name helper. Flag a literal bare table name inside an expression whose enclosing statement is schema-qualified, and flag a leftover '{' in emitted SQL.

---

## `to-char-number-key-depends-on-nls`

**High** — TO_CHAR on a number without a format model produces a session-dependent string

**Look for.** `TO_CHAR(numeric_column)` with no second argument, used to build a key, a join value or anything compared against previously stored text. Also `TO_CHAR(date_column)` with no format model.

**What goes wrong.** With no format model, TO_CHAR uses the session's NLS settings — `NLS_NUMERIC_CHARACTERS` decides the decimal separator, so a value renders as '1.5' in one session and '1,5' in another, and large-magnitude or high-scale values render in scientific notation. The same is true for dates via `NLS_DATE_FORMAT`. If the ETL wrote a key under one session's settings and the reporting query recomputes it under another, the two strings differ and every lookup misses — which in a gap or reconciliation query reads as 'nothing was migrated' rather than as a formatting bug. Nothing errors; the numbers are simply wrong in a direction that looks like real work outstanding.

**Fix.** Always pass an explicit format model: `TO_CHAR(n, 'TM9')` or `TO_CHAR(n, 'FM999999999999999999')` for integers; `TO_CHAR(d, 'SYYYY-MM-DD HH24:MI:SS')` for dates, with the `S` prefix so BC/low years survive. Better still, keep numeric keys numeric and store them in a NUMBER column. Where a text key store is unavoidable, define the rendering once and reuse it at every site.

**How to confirm it fires.** Regex precursor: `TO_CHAR\s*\(\s*[^,)]+\s*\)` — a TO_CHAR call with exactly one argument. Fires whenever the result is used in a comparison, a join, a key expression or a GROUP BY, as opposed to pure display.

---

## `unaliased-join-column-collision`

**High** — Alias every column a joined view exposes, or a bare reference is ORA-00918

**Look for.** A joined inline view (or a second table in a FROM clause) exposing a column whose name also exists on the driver table — audit columns, `NOTES`, `STATUS`, an id column — combined with a projection or predicate that references bare column names.

**What goes wrong.** The surrounding machinery — the generated SELECT list, an appended delta predicate, a retry key list — emits BARE column names. A name present on both sides of the join then fails with ORA-00918 'column ambiguously defined', and it fails the WHOLE extract, not one row. Audit-column quartets (created/updated by/on) are the worst offenders because almost every table has them, so a join of three tables is near-certain to collide.

**Fix.** Alias every column the joined view exposes with a prefix unique to that view (`SELECT s.some_column AS SIDE_SOME_COLUMN ...`), and declare the aliases explicitly in the projection so each is emitted as `<expr> AS <alias>` rather than as a bare name. Before adding a join, diff the two tables' column names and treat any intersection as mandatory-to-alias.

**How to confirm it fires.** Regex precursor: `JOIN` in an extract FROM string. Then extract the joined relation's exposed column names and intersect them with the driver table's columns from the DDL. Fire on any intersection that is not aliased. Fast heuristic: flag any joined view exposing a column named like an audit column or a generic name (NOTES/STATUS/TYPE/ID).

---

## `union-branches-align-by-position`

**High** — UNION branches are aligned by column position, not by name or type

**Look for.** A multi-branch UNION/UNION ALL where the branches read different tables and the select lists are long. Red flags inside a branch: a bare `NULL` or empty-string `''` literal in a column position, and column names that differ between branches at the same position (`some_id` in one branch, `someid` in another), and a diff hunk that adds or removes one line from a single branch's select list.

**What goes wrong.** Set operators match branches strictly by ordinal position. Nothing checks that position 12 means the same thing in every branch, and Oracle will happily align two same-typed but semantically different columns. Adding or deleting one line in one branch shifts every column after it in that branch only, and because the types still line up the statement compiles and returns plausible, wrong data. The literal case is subtler: an untyped `NULL` or `''` takes its datatype from the other branches, so the result column's type is decided by a branch elsewhere in the file — add a branch with a different type there and a column that has been NUMBER for years becomes VARCHAR2, or the statement starts failing with an inconsistent-datatypes error far from the edit.

**Fix.** Give every branch an explicit alias on every column and keep the alias identical across branches, so a positional shift becomes visible as a name mismatch. Cast untyped literals: `CAST(NULL AS NUMBER) AS some_column`. When a view names its columns in the CREATE (`CREATE VIEW v (col_a, col_b, ...) AS`), that list is the contract — review any branch edit against it, column by column. Safest of all: never edit one branch of a generated UNION by hand; change the generator.

**How to confirm it fires.** Regex precursor: `UNION(\s+ALL)?`. Then count select-list items per branch (commas at branch top level) and compare; any inequality is a hard error, and equality is not proof — compare aliases position by position. Fires on any diff that changes the select-list line count of one branch of an existing set operator.

---

## `union-where-union-all-is-meant`

**High** — Use UNION ALL unless you can name the duplicate rows UNION is there to remove

**Look for.** A bare `UNION` between branches that are disjoint by construction — each branch reads a different source table, or each branch selects a distinct literal discriminator (`1 AS record_type`, `2 AS record_type`, …), or the branches are separated by mutually exclusive predicates on the same table.

**What goes wrong.** `UNION` is not a spelling variant of `UNION ALL`: it applies a global DISTINCT over the combined result. Two costs follow. First, performance — the whole result set is sorted or hashed for uniqueness, which blocks pipelining, inflates temp usage and turns a streaming scan into a blocking operation; on a large fact table that is the dominant cost of the query. Second, correctness in the other direction — if the branches can legitimately produce identical rows (two different source rows that happen to agree on every projected column), UNION silently deletes one of them, so a count taken from the view under-reports the source. When the branches carry a discriminator literal, UNION provably cannot remove anything, so you are paying the full dedup for zero rows removed.

**Fix.** Default to `UNION ALL`. Reach for `UNION` only when you can state which duplicate rows it removes and why removing them is correct; write that reason in a comment next to the keyword. Where branches must be disjoint, make it explicit with a literal discriminator column selected in every branch, which also lets the optimizer prune unread branches when the caller filters on it.

**How to confirm it fires.** Regex precursor: `^\s*UNION\s*$` or `UNION\s+SELECT` — i.e. `UNION` not immediately followed by `ALL`. Confirm semantically: do the branches select distinct literal values in a common position, or read disjoint tables? If yes the dedup is provably a no-op and the rule fires at High. If the branches could genuinely overlap, ask in review whether silently dropping the duplicate is intended.

---

## `unique-key-missing-a-scoping-column`

**High** — A unique key that omits the scoping column makes a per-parent value globally unique

**Look for.** A UNIQUE constraint whose column list contains the descriptive columns but not the owning parent's id — e.g. UNIQUE (type_id, from_date, to_date) on a table that also has a parent_id.

**What goes wrong.** Legitimately distinct rows collide. The source keys the value per parent — one entry per parent, repeated across parents — and the target declares it unique across the whole table, so everything after the first parent is refused with ORA-00001 naming a constraint, not a column. It reads as duplicate source data and gets 'fixed' by deduplicating real records. It also forbids future application behaviour the business requires.

**Fix.** Surface the mismatch as a schema question before the load, not a reject to triage: either add the scoping column to the key, or get an explicit ruling that the value really is meant to be global. Measure how many rows collide and say so. If the key is dropped instead, note that nothing then prevents genuine duplicates and ask who owns that.

**How to confirm it fires.** For each UNIQUE constraint touched by the diff, compare its column list against the table's FK columns. If the table has a parent FK column absent from the unique key, and the source's own key includes the equivalent scoping column, the rule fires.

---

## `unnamed-constraints-missing-from-exports`

**High** — Enumerate constraints from the catalog — a schema export omits system-named constraint indexes

**Look for.** A change script, a mapping, or an analysis that lists a table's unique keys from a committed DDL export file and then acts on that list.

**What goes wrong.** An unnamed (system-generated) unique constraint's index is not exported separately, so an export can show one unique key where two exist — and the one it hides can be the stricter of the two. Dropping only the exported one changes nothing, and the rows keep rejecting after a DDL change that was reported as applied.

**Fix.** Before acting, enumerate live: join ALL_IND_COLUMNS to ALL_INDEXES and LEFT JOIN ALL_CONSTRAINTS on index_name, with LISTAGG of the column list, filtered to the table in question, and work from that output. Keep the same query as the after-the-change verification so 'applied' is measured, not assumed.

**How to confirm it fires.** Regex precursor: a constraint or index name appearing in a new DDL/change script. Semantic confirm: was the constraint list sourced from a committed export file rather than from an ALL_CONSTRAINTS/ALL_IND_COLUMNS query run against the target? If the diff contains no live enumeration step, the rule fires.

---

## `user-catalog-views-ignore-current-schema`

**High** — Scope catalog probes by owner; USER_* views follow the login user, not CURRENT_SCHEMA

**Look for.** A catalog probe against `USER_TAB_COLUMNS`, `USER_CONSTRAINTS`, `USER_CONS_COLUMNS` or `USER_TAB_IDENTITY_COLS` in a codebase whose sessions run `ALTER SESSION SET CURRENT_SCHEMA = <configured schema>` or whose statements qualify objects with a configured schema name. Strong tell: the same file also does it the other way, with `owner = SYS_CONTEXT('USERENV','CURRENT_SCHEMA')`.

**What goes wrong.** `USER_*` views are scoped to the SESSION USER — the login. `ALTER SESSION SET CURRENT_SCHEMA` changes only how unqualified names RESOLVE; it does not change what the USER_* views return. When the objects live in a schema other than the login user's, the probe returns zero rows. If the probe's failure mode is 'assume the older/absent shape' (which it usually is, so the code survives a partial upgrade), the result is silent degradation: a column is dropped from a statement, a feature turns itself off, or a constraint that exists is reported missing — with no error anywhere.

**Fix.** Query `ALL_*` (or `DBA_*`) with an explicit owner predicate — `owner = SYS_CONTEXT('USERENV','CURRENT_SCHEMA')`, or the configured schema bound as a parameter — and make the 'could not tell' branch loud: log which probe failed and what degraded behaviour was chosen, rather than silently taking the safe-looking path.

**How to confirm it fires.** Precursor: grep for `USER_TAB_COLUMNS|USER_CONSTRAINTS|USER_CONS_COLUMNS|USER_TAB_IDENTITY_COLS|USER_INDEXES` (case-insensitive). Fires when the same codebase sets `CURRENT_SCHEMA` or carries a configurable schema for those objects. Confirm by reading the except/zero-row branch: if it silently selects a reduced statement shape, raise the severity.

---

## `varchar2-budgets-are-bytes-not-characters`

**High** — Truncate to a VARCHAR2 column on its byte budget, backing off to a character boundary

**Look for.** `str(value)[:N]` or `value[:400]` used to make a value fit a `VARCHAR2(N)` column, in a codebase that stores non-ASCII text.

**What goes wrong.** Oracle VARCHAR2 sizes are byte-based by default. Multi-byte characters take two to four bytes each, so a character-count truncation still overflows and raises ORA-12899 — on the first non-ASCII row, which in a migration means in the middle of production rather than in the test suite. The values most likely to be affected are exactly the ones you cannot control: a source row echoed into a diagnostic column, or an error message quoted verbatim from the database.

**Fix.** Encode, slice on the byte budget, and decode with errors ignored so the cut backs off to a clean character boundary: `b = text.encode('utf-8'); b[:maxbytes].decode('utf-8','ignore')`. Apply it centrally in one helper every writer goes through, and give each column its real byte budget rather than a shared round number.

**How to confirm it fires.** Precursor: grep the hunk for a slice on a string bound for the database — `[:\d+]` within a params dict, or `str(...)[:`. Confirm: is the target a VARCHAR2 whose size is in bytes (the default unless the column was declared CHAR semantics), and can the value be non-ASCII (user text, a source column, an exception message)? Fires unless the truncation encodes first.

---

## `whitespace-only-passes-not-null`

**High** — A whitespace-only value is not content — scope and normalise on TRIM, not on NULL

**Look for.** A load-scope predicate of the form `some_column IS NOT NULL` used to mean 'this row has content', or a text column copied straight across with no blank-normalisation.

**What goes wrong.** Legacy text columns routinely hold ' ' or ' ' — often a placeholder the source system itself wrote. Those rows pass `IS NOT NULL`, so they are selected as 'having content' and produce target rows whose every content column is effectively empty: a record asserting that something happened where nothing did, indistinguishable from a real one afterwards. The mirror hazard is over-correcting: applying `TRIM()` to the mapped VALUE also strips leading and trailing whitespace from every genuine value, which is a silent edit to text nobody asked for.

**Fix.** Scope with `TRIM(col) IS NOT NULL`. Normalise the value with a form that only touches rows that are entirely blank and passes everything else through byte for byte: CASE WHEN TRIM(t.some_column) IS NULL THEN NULL ELSE t.some_column END Also fold a whitespace-only mapped value to NULL before NOT NULL/default handling, since Oracle stores '' as NULL and such a value would otherwise look populated and then fail the very NOT NULL check it appears to satisfy.

**How to confirm it fires.** Regex precursor: `IS NOT NULL` in a scope/extract predicate string. Fire when the column is a character type and the predicate is being used as a content test rather than a key test. Separately, flag `TRIM\(` applied to a mapped VALUE expression (not a comparison) as the over-correction half.

---

## `aggregate-over-empty-set-returns-null`

**Medium** — SUM/MAX/AVG over an empty or all-NULL set returns NULL, not zero

**Look for.** `SUM(...)`, `MAX(...)` or `AVG(...)` in a query whose result is concatenated into a report line, subtracted from another number, or compared against a threshold — with no NVL/COALESCE around it. Especially in a query with a `HAVING COUNT(*) > 1` or a selective WHERE that can legitimately match nothing.

**What goes wrong.** An aggregate with no qualifying rows yields NULL. Concatenated into a report line, NULL makes the whole string vanish or collapse (Oracle treats NULL as an empty string in `||`), so a clean result reads as a truncated or missing line rather than as zero. Subtracted, it makes the difference NULL. Compared to a threshold, the comparison is UNKNOWN and a pass/fail check silently returns neither. The healthy case — 'no defects found' — is exactly the case that produces the confusing output.

**Fix.** Wrap every aggregate whose absence means zero: `NVL(SUM(n - 1), 0)`, `COALESCE(MAX(some_date), DATE '0001-01-01')`. When building a text line, wrap each interpolated value: `NVL(TO_CHAR(some_column), 'NULL')`, so a NULL prints as the word rather than disappearing.

**How to confirm it fires.** Regex precursor: `(SUM|MAX|MIN|AVG)\s*\(` . Fires when the aggregate appears inside a `||` concatenation, on either side of an arithmetic operator, or in a comparison, and is not already inside NVL/COALESCE.

---

## `an-anti-join-key-expression-must-mirror-the-writer-exactly`

**Medium** — Generate an anti-join key in SQL from the same canonical form the writer uses

**Look for.** A `NOT EXISTS (... WHERE stored_key = TO_CHAR(col) ...)`, or any join to a load-record table on a stringified key, written by hand next to a client-side canonicalisation function.

**What goes wrong.** The two must produce the same string or the anti-join matches nothing and every already-migrated row reads as outstanding — a wrong answer that looks like a large amount of work to do rather than like a bug. The drift points are the composite separator and numeric formatting. Oracle's default `TO_CHAR(NUMBER)` happens to agree with a client that strips a trailing '.0' (`TO_CHAR(3)` is '3', never '3.0'), but a fraction below 1 does not round-trip: the database writes '.5' where the client writes '0.5'.

**Fix.** Derive both sides from one documented canonical form, state the round-trip rules in a comment where the SQL is generated, and name the shapes that do NOT round-trip along with why they are acceptable (here: no key is fractional, and a mismatch shows up as a table whose outstanding count equals its whole row count, not as silent bad data). Scope the anti-join by SOURCE as well as key when several sources share a target, or one feeder's ids mask another's.

**How to confirm it fires.** Precursor: grep for `NOT EXISTS` / `LEFT JOIN ... IS NULL` against a bookkeeping table, and for `TO_CHAR(` inside a generated predicate. Compare the SQL expression with the client-side key function character for character: separator, numeric formatting, case, trimming. Fires on any difference, and on a missing source/provenance term when the target can have several loaders.

---

## `bind-a-key-as-the-columns-own-type`

**Medium** — Coerce replay keys to the source column's type — do not blanket-cast to text or to int

**Look for.** A retry/replay path that reads keys back out of text storage and binds them all as strings, or blanket-`int()`s them before binding.

**What goes wrong.** Two different failures. Binding text against a NUMBER column forces an implicit conversion on every row and can stop the index being used, turning a keyed re-read into a scan. Casting a zero-padded character code to an integer ('007' -> 7) changes the VALUE, so the predicate selects nothing and the rows are silently reported as 'no longer in the source'.

**Fix.** Ask the catalog which of the source table's columns are numeric (owner-scoped) and coerce per column: numeric columns bound as numbers, everything else bound as the literal text so a zero-padded code survives. Cache the answer per table. On any probe failure fall back to text binds for everything — correct, just less efficient — rather than guessing, and bound that probe like every other small catalog query.

**How to confirm it fires.** Precursor: grep for `int(` / `str(` applied to a value on its way into a binds dict in a retry, replay or resume path. Confirm: does the code consult column types anywhere? If every key is coerced the same way regardless of the column, fire — severity rises if any candidate key column is a character code rather than an id.

---

## `conditional-aggregate-else-clause`

**Medium** — COUNT(CASE ... ELSE 0 END) counts every row; COUNT needs no ELSE, SUM needs one

**Look for.** `COUNT(CASE WHEN cond THEN 1 ELSE 0 END)` — a CASE inside COUNT that has an ELSE branch returning a non-NULL value. The mirror image: `SUM(CASE WHEN cond THEN 1 END)` with no ELSE.

**What goes wrong.** COUNT counts non-NULL inputs, so an `ELSE 0` makes every row non-NULL and the expression degenerates to `COUNT(*)` — a conditional count that always equals the total. It is a silent off-by-everything: the number is plausible, monotonic, and completely wrong. The SUM form fails the other way only cosmetically (missing ELSE gives NULL for non-matching rows, and SUM skips them), but returns NULL rather than 0 when nothing matches, which is the previous rule.

**Fix.** Pick one idiom and use it consistently: `COUNT(CASE WHEN cond THEN 1 END)` — no ELSE — or `SUM(CASE WHEN cond THEN 1 ELSE 0 END)` with ELSE 0, wrapped in NVL if the set can be empty. Never mix the two halves.

**How to confirm it fires.** Regex precursor: `COUNT\s*\(\s*CASE` followed within the same parenthesis by `ELSE`, and `SUM\s*\(\s*CASE` with no `ELSE` before `END`. Both are mechanical and near-zero false positive.

---

## `diagnostics-must-not-need-privileges-or-attributes-you-lack`

**Medium** — Do not build diagnostics on privileges the migration account will not have, or on attributes a connection may not accept

**Look for.** Engine code querying v$session / v$transaction (or any V$ view) to diagnose blocking, or setting optional connection attributes without tolerating failure.

**What goes wrong.** A monitoring feature that needs a grant nobody will give fails at the exact moment it is wanted — the account running a migration is rarely granted SELECT on the dynamic performance views. Equally, an optional convenience like a session tag must never be able to make a pool unusable: a driver or double that rejects the attribute would take the whole run down for a feature that only helps a human read the database.

**Fix.** Move lock/blocker diagnosis into a standalone script a DBA runs, and keep only what the tool can do from inside its own session: stamp a run identifier on CLIENT_IDENTIFIER (write-only, never read back) so someone looking at the database can tell this run's sessions apart. Wrap every optional attribute set so a failure is ignored and the essential session setup still runs. Guard the boundary with a test that greps the source for V$ queries while allowing the view to be NAMED in prose hints.

**How to confirm it fires.** grep the diff for 'v$', 'v_$', gv$ and for dba_ views. Then check any new attribute assignment on a connection object for a surrounding try/except. The prose-vs-query distinction matters: a hint string naming v$session is fine.

---

## `dictionary-query-owner-and-current-schema`

**Medium** — Dictionary queries need the OWNER in every join and filter — and USER is not CURRENT_SCHEMA

**Look for.** A join between two `ALL_*` catalog views on name alone (`ON c.index_name = i.index_name`) with no owner predicate; a guarded-DDL block whose existence check reads `WHERE owner = USER` in a script that also issues `ALTER SESSION SET CURRENT_SCHEMA`; a query against `USER_*` views in a script that runs as a different user from the object owner.

**What goes wrong.** Object names are unique only within a schema. Joining `ALL_INDEXES` to `ALL_IND_COLUMNS` on index name alone matches rows across every schema the caller can see, multiplying results and attributing one schema's columns to another's index — and it looks fine in a test schema where no name collides. The USER/CURRENT_SCHEMA confusion breaks the other direction: `USER` is the connected account and does not change when you set CURRENT_SCHEMA, so an idempotence guard that checks `owner = USER` finds nothing, believes the column is absent, and re-issues DDL that then fails or, if it is a CREATE OR REPLACE, silently creates the object in the wrong schema. `USER_*` views likewise show only the connected account's objects, not the current schema's.

**Fix.** Join dictionary views on owner AND name (`ON c.index_owner = i.owner AND c.index_name = i.index_name`) and filter on an explicit owner. In a script that sets CURRENT_SCHEMA, write the guard as `WHERE owner = SYS_CONTEXT('USERENV','CURRENT_SCHEMA')`. Do not schema-qualify the dictionary views themselves — they are SYS-owned.

**How to confirm it fires.** Regex precursor: `ALL_\w+|DBA_\w+|USER_\w+` in a FROM clause. Fires if a join between two such views lacks an owner-to-owner equality, or if no owner predicate exists at all. Separately fires on `owner\s*=\s*USER` in any file that also contains `ALTER SESSION SET CURRENT_SCHEMA`.

---

## `divide-without-nullif-guard`

**Medium** — Guard every divisor with NULLIF — a division by zero aborts the whole statement

**Look for.** A `/` in a select list where the denominator is an aggregate or a column rather than a literal: `COUNT(*) / COUNT(DISTINCT some_key)`, `SUM(a) / SUM(b)`, `some_column / other_column`.

**What goes wrong.** ORA-01476 (divisor is equal to zero) is raised at fetch time and kills the entire statement, so one zero denominator on one row costs you every other column of a long-running profiling query — often after the expensive scan has already been paid for. The zero case is not exotic: it is the empty table, the all-NULL key, the filtered-to-nothing group, which is precisely what a profiling query is looking for.

**Fix.** `ROUND(numerator / NULLIF(denominator, 0), 4)` — NULLIF turns the zero into NULL, the division yields NULL, and the row still comes back carrying every other column. Add `NVL(..., 0)` outside only if a literal zero reads better than an empty cell.

**How to confirm it fires.** Regex precursor: `/` appearing between two expressions inside a SELECT list (exclude comment markers and `--`). Fires unless the denominator is a non-zero numeric literal or already wrapped in NULLIF.

---

## `drop-index-backing-a-constraint`

**Medium** — Drop the constraint, not its index — and add DROP INDEX for an adopted index

**Look for.** A DROP INDEX statement on an index whose name or columns match a unique/primary constraint, or an ALTER TABLE ... DROP CONSTRAINT / DROP UNIQUE with no DROP INDEX clause.

**What goes wrong.** An index that backs a constraint cannot be dropped on its own — ORA-02429. And when the constraint merely adopted a pre-existing index (created separately by CREATE UNIQUE INDEX), dropping the constraint alone leaves that index behind, still unique, still rejecting exactly the rows the change was supposed to release. The script reports success and nothing changes.

**Fix.** ALTER TABLE t DROP CONSTRAINT c DROP INDEX; for a named constraint, or ALTER TABLE t DROP UNIQUE (cols) DROP INDEX; for a system-named one. Afterwards re-query the catalog and assert no remaining index over those columns has uniqueness = 'UNIQUE' — a leftover non-unique index is harmless and may be there for lookup speed.

**How to confirm it fires.** Regex: `DROP INDEX` or `DROP (CONSTRAINT|UNIQUE)` in a DDL diff. Semantic confirm: for a DROP INDEX, does a constraint reference that index name? For a DROP CONSTRAINT/UNIQUE, is `DROP INDEX` absent from the statement?

---

## `filter-generated-not-null-check-constraints`

**Medium** — Filter Oracle-generated NOT NULL check constraints out of a constraint catalog

**Look for.** A query over ALL_CONSTRAINTS / USER_CONSTRAINTS with constraint_type = 'C' feeding a rule generator, a check-constraint inventory, or a count of business rules, with no filter on the GENERATED column.

**What goes wrong.** Oracle materialises every NOT NULL as a check constraint with condition "COL" IS NOT NULL and generated = 'GENERATED NAME'. Counted as business rules they swamp the real ones and inflate every derived figure; treated as domain constraints they generate meaningless validation rules.

**Fix.** Add AND generated = 'USER NAME' to the catalog query, and handle NOT NULL from ALL_TAB_COLUMNS.NULLABLE instead so it is counted exactly once.

**How to confirm it fires.** Regex: `constraint_type\s*=\s*'C'` or `CONSTRAINT_TYPE IN ('C'` in a catalog query. Semantic confirm: `GENERATED` does not appear in the WHERE clause.

---

## `implicit-number-to-char-nls`

**Medium** — Cast NUMBER to text explicitly when it feeds a character column or a text match

**Look for.** A numeric source column mapped straight into a VARCHAR2 target, or compared/matched against a text lookup key, with no TO_CHAR in the SQL and no explicit cast in code.

**What goes wrong.** Oracle will convert implicitly, but that conversion goes through the session's NLS settings — so the stored string depends on where and how the job ran (group separators, decimal marks, leading spaces for the sign). The same row can migrate to two different strings on two different sessions, and a text match against a lookup silently misses.

**Fix.** Do the conversion in the statement with `TRIM(TO_CHAR(col))`, or apply a deterministic cast in the transform layer. Doing it in SQL is preferable when the value is also used as a match key, because then the projected value and the compared value are the same expression.

**How to confirm it fires.** Compare the source column's type (NUMBER) against the target column's type (VARCHAR2/NVARCHAR2) in the catalog. Fire when the mapping is a plain copy with no `TO_CHAR` in the derived expression and no explicit cast flag on the transform.

---

## `inline-view-rewrite-preconditions`

**Medium** — A query rewritten into an inline view needs unique column names and no trailing semicolon

**Look for.** Code that wraps an arbitrary user-supplied statement as SELECT ... FROM (<original>) without first checking the cursor description for duplicate column names or stripping a trailing ';'.

**What goes wrong.** An inline view whose projection repeats a column name fails with ORA-00918 'column ambiguously defined', so a statement that worked before the rewrite starts failing after it — a rewrite that breaks working queries is a worse outcome than the one it was added to prevent. A trailing semicolon inside the parenthesised subquery is a syntax error. Both are structural facts of the rewrite, knowable before execution.

**Fix.** Detect duplicate names in the description and return the statement UNCHANGED in that case rather than wrapping it; strip a trailing semicolon before wrapping; leave a statement with nothing to rewrite byte-identical. Treat the describe step as an optimisation, never a gate: if the driver cannot pre-parse the statement, execute the original.

**How to confirm it fires.** grep the diff for f-strings or concatenations of the shape 'FROM (' + sql or 'FROM ({...})'. Confirm the surrounding function (a) dedupes/aborts on repeated column names, (b) rstrips ';', (c) has a fallback path when describe/parse raises.

---

## `listagg-without-on-overflow`

**Medium** — LISTAGG without ON OVERFLOW aborts the query when the aggregate exceeds the string limit

**Look for.** `LISTAGG(some_column, ',') WITHIN GROUP (ORDER BY ...)` with no `ON OVERFLOW TRUNCATE` clause, especially when aggregating column names, ids or free text over a group whose size is not bounded.

**What goes wrong.** LISTAGG returns VARCHAR2. When the concatenated result passes 4000 bytes (or 32767 with extended string types) the statement fails with ORA-01489, 'result of string concatenation is too long'. It fails for the whole statement, not the one row, so a catalogue or diagnostic query that worked on a narrow table dies on the first wide one — and the wide one is usually the interesting one. Because the limit depends on data, it passes every test on small samples.

**Fix.** `LISTAGG(some_column, ',' ON OVERFLOW TRUNCATE '...' WITH COUNT) WITHIN GROUP (ORDER BY ...)` — the result is truncated with a marker and a count instead of raising. Where completeness matters, return the rows unaggregated and let the consumer join them.

**How to confirm it fires.** Regex precursor: `LISTAGG\s*\(`. Fires unless `ON OVERFLOW` appears within the same call.

---

## `number-precision-caps-the-value`

**Medium** — Treat a NUMBER(p,s) precision as a business constraint, not a storage hint

**Look for.** A target column declared NUMBER(2,0) / NUMBER(3,0) / NUMBER(5,2) receiving a source value with no comparable bound, or a scale smaller than the source's.

**What goes wrong.** NUMBER(2,0) caps the value at 99 and raises ORA-01438 value larger than specified precision on the first row that exceeds it. A scale that is too small does not raise at all — it rounds, silently, which for a share or percentage means the migrated figures no longer sum to the original.

**Fix.** Compare source MIN/MAX and observed scale against the target precision before the run, and widen the target where the source is unbounded. Where scale is the issue, say what the rounding costs in business terms before accepting it — a truncated percentage is a wrong number, not a formatting choice.

**How to confirm it fires.** Regex precursor: `NUMBER\(\d,` (single-digit precision) or a declared scale in a target DDL. Semantic confirm: does the diff route a source column into it whose profiled max/scale exceeds the declaration? A source column with no CHECK and no comparable precision fires the rule.

---

## `rownum-with-order-by-in-same-block`

**Medium** — ROWNUM is assigned before ORDER BY — a top-N needs the ORDER BY in an inner block

**Look for.** `WHERE ROWNUM <= n` and `ORDER BY` in the same query block. Correct-but-fragile variant: `SELECT ... FROM (SELECT ... ORDER BY ...) WHERE ROWNUM <= n`.

**What goes wrong.** Oracle assigns ROWNUM as rows are produced, before the sort. In one block, `WHERE ROWNUM <= 20 ... ORDER BY x` therefore takes an arbitrary 20 rows and then sorts those 20 — it is not the top 20, and it returns a different arbitrary set whenever the plan changes. It never errors, so a 'worst offenders' listing quietly becomes a random sample. The inline-view form works, but is fragile: add a predicate to the outer block and the optimizer's freedom changes; the intent also is not obvious to a reader.

**Fix.** Use `FETCH FIRST n ROWS ONLY` after the ORDER BY (12c and later), or `ROW_NUMBER() OVER (ORDER BY ...)` filtered in an outer block. Keep the plain `WHERE ROWNUM <= n` only where an arbitrary sample is genuinely what you want — and then say 'arbitrary sample' in a comment, because the next reader will assume it is a top-N.

**How to confirm it fires.** Regex precursor: `ROWNUM`. Fires when an `ORDER BY` occurs in the same query block (no intervening closing parenthesis of a subquery). Also flag `ROWNUM = 1` scalar subqueries over an ordered inline view and suggest FETCH FIRST / ROW_NUMBER.

---

## `scalar-subquery-beside-aggregates`

**Medium** — Do not put a scalar subquery beside aggregates with no GROUP BY

**Look for.** A single SELECT that mixes aggregate expressions (COUNT/SUM/MIN) with a bare scalar subquery or a plain column reference, and has no GROUP BY.

**What goes wrong.** Oracle raises ORA-00937 not a single-group group function. In a hand-off script of numbered probe statements this costs a full re-run cycle every time, because the human running it cannot fix the SQL.

**Fix.** Build each aggregate as its own single-row inline view and CROSS JOIN them: SELECT a.n, b.m FROM (SELECT COUNT(*) n FROM t1) a CROSS JOIN (SELECT COUNT(*) m FROM t2) b.

**How to confirm it fires.** Regex precursor: a SELECT containing both `COUNT(`/`SUM(`/`MIN(`/`MAX(` and `(SELECT` in the same select list. Semantic confirm: no `GROUP BY` in that statement.

---

## `search-condition-is-a-long-column`

**Medium** — Read SEARCH_CONDITION_VC, not SEARCH_CONDITION

**Look for.** A catalog query selecting SEARCH_CONDITION from ALL_CONSTRAINTS/USER_CONSTRAINTS, typically alongside code that works around LONG fetch semantics.

**What goes wrong.** SEARCH_CONDITION is a LONG column. Driver support for LONG is awkward, the workarounds (setting long defaults, PL/SQL conversion) are fragile, and the value can silently truncate. On 12.2+ the same text is available as a VARCHAR2 view column with no LONG involved.

**Fix.** Select SEARCH_CONDITION_VC instead. Note it is capped at 4000 characters, so a very long condition still needs the LONG path — but that is a rare, explicit case rather than the default.

**How to confirm it fires.** Regex: `SEARCH_CONDITION\b` not followed by `_VC` in a SQL string.

---

## `varchar2-to-clob-needs-replacement`

**Medium** — VARCHAR2 cannot be widened to CLOB with ALTER ... MODIFY

**Look for.** A migration script containing ALTER TABLE ... MODIFY (some_column CLOB) where the column is currently VARCHAR2/NVARCHAR2.

**What goes wrong.** Oracle refuses it with ORA-22858 invalid alteration of datatype. A change script that assumes it works fails at the point a DBA runs it, mid-sequence, with earlier statements already committed.

**Fix.** Replace the column in four separate statements, each committing on its own: ADD (some_column_clob CLOB); UPDATE ... SET some_column_clob = TO_CLOB(some_column) WHERE some_column IS NOT NULL; COMMIT; DROP COLUMN some_column; RENAME COLUMN some_column_clob TO some_column. Re-apply any COMMENT. Note the column moves to the end of the column list, and confirm first that no index or constraint uses it.

**How to confirm it fires.** Regex: `MODIFY\s*\(?\s*\w+\s+N?CLOB` or `MODIFY .* BLOB` in DDL. Semantic confirm: current type of that column in the catalog is a character type.

---

## `user-tab-columns-vs-user-tab-cols`

**Low** — Prefer *_TAB_COLUMNS over *_TAB_COLS unless you need the hidden/virtual flags

**Look for.** A catalog query against USER_TAB_COLS / ALL_TAB_COLS that does not reference HIDDEN_COLUMN or VIRTUAL_COLUMN.

**What goes wrong.** *_TAB_COLS is the raw view: it returns system-internal columns (function-based index expressions, unused columns, invisible columns) that *_TAB_COLUMNS already filters out. A column inventory built on it over-counts, and an INSERT column list built on it names columns that cannot be written.

**Fix.** Use *_TAB_COLUMNS for ordinary column inventories. Use *_TAB_COLS only when the hidden/virtual flags are actually needed, and filter on them explicitly when you do.

**How to confirm it fires.** Regex: `(USER|ALL|DBA)_TAB_COLS\b`. Semantic confirm: neither `HIDDEN_COLUMN` nor `VIRTUAL_COLUMN` appears in the query.

---

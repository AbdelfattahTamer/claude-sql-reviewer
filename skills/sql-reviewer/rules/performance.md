# Performance

Shapes that are correct but do not survive production volume.

20 rules. Severity: Critical 2, High 12, Medium 5, Low 1.

---

## `correlated-subquery-unindexed-key`

**Critical** — A correlated subquery on an unindexed key is a full scan per driver row

**Look for.** A correlated scalar subquery in the projection of a large-table extract, correlating on a column of the side table that has no index — often a foreign key, since Oracle does not index a foreign key for you.

**What goes wrong.** The subquery is re-executed once per driver row. With no index on the correlation column, each execution full-scans the side table, so the cost is |driver| x |side| row reads. On a large fact table this turns a batch that should take seconds into hundreds of seconds, and the whole table into days. It is invisible in unit tests (which hand-build rows) and invisible on a small extract (where the side table fits in a few blocks).

**Fix.** Check for an actual INDEX, not a CONSTRAINT, on the correlation column. If none exists and the source owner will not add one, restructure: pre-aggregate the side table ONCE into an inline view that is unique on the join key, and LEFT JOIN it. Same rule, same result, one pass plus a hash join. A correlated subquery on a genuinely INDEXED key is fine and should stay one.

**How to confirm it fires.** Regex precursor: `\(SELECT` inside a `derived_columns`-style projection string. Then: identify the correlation column on the side table and check `user_indexes`/the DDL for a leading-column index on it. Fire when absent AND the driver table is large. Treat 'there is a foreign key constraint on it' as NOT satisfying the check.

---

## `unbounded-in-run-dedupe-memo`

**Critical** — An in-run duplicate memo grows with accepted rows — bound it only where the database can take over

**Look for.** An in-process `set`/`dict` of seen key tuples, populated once per accepted row and never cleared, on a migration whose source is a multi-million-row table.

**What goes wrong.** The structure is free on a small reference table and ruinous on a transactional one: at scale it is gigabytes of resident memory and the run dies with a bare MemoryError — potentially after tens of hours, leaving the target partially loaded. Batch size does not bound it. Clearing it per table often does not work either, because the orchestrator holds one migration instance per table for the whole run.

**Fix.** Bound the memo, but ONLY for key tuples the TARGET DATABASE also enforces with a real, enabled unique constraint or unique index — past the bound, duplicates then arrive as a constraint violation on that row's INSERT, which is a worse MESSAGE but never a lost reject. Where no such constraint exists, do NOT bound it: the duplicate would be inserted, keyed and permanent, with no error ever. Make the permission per-key-tuple, explicit, and require the constraint NAME so the claim is checkable. Measure bytes-per-key so the budget can actually be sized, and announce the hand-over once per table.

**How to confirm it fires.** Look for a long-lived collection keyed on row values inside a migration/transform class. Fire when it is appended to per accepted row with no ceiling AND the source table is large. Then check whether each memoised key tuple has a named, ENABLED unique constraint on the target — an unbounded memo is correct there only if it does not.

---

## `arraysize-must-follow-the-batch-size`

**High** — Set arraysize to the loader's batch size for a streaming extract, and time the pull not the call

**Look for.** A streaming read of a large table where the cursor's `arraysize`/`prefetchrows` is left at the driver default while the consumer processes in batches of thousands. Related tell: timing code wrapped around the call that BUILDS the generator rather than around the iteration.

**What goes wrong.** python-oracledb fetches `arraysize` rows per round trip (default 100). A multi-million-row extract consumed in batches of thousands therefore pays tens of thousands of avoidable round trips, and on a slow or remote link that cost dominates the run. The timing problem compounds it: a generator-based read does not execute the SELECT until the first row is pulled, so the query's whole latency is attributed to whatever phase happens to consume the first row — which reads in the log as a hang between 'extracting' and the first batch line.

**Fix.** Pass `arraysize` (and `prefetchrows` where the read helper supports it) equal to the loader's batch size, as a keyword with a `TypeError` fallback so an older read helper still works. Instrument the ITERATION: accumulate the time spent waiting on `next()`, report first-row latency separately, and split each batch's elapsed time into read / prefetch / load, because the fix for each is different.

**How to confirm it fires.** Precursor: grep for `arraysize|prefetchrows`. If absent, find the large-table read path and check the consumer's batch size. Fires when the consumer batches in the thousands and no arraysize is set. Second check: is any elapsed-time measurement taken around generator CONSTRUCTION rather than around iteration?

---

## `bound-the-small-probes-never-the-bulk-extract`

**High** — Arm a call timeout only where the cost is bounded by construction; leave the extract unbounded

**Look for.** Either extreme: a blanket statement timeout applied to every pool including the source, or no timeout anywhere. Also a catalog probe, a clock read or an existence check issued on an unbounded connection.

**What goes wrong.** An unbounded call has no end. A listener that accepts the socket and never answers, or a catalog query that wedges, blocks with no error and nothing in the log but the phase string set before the call — a run can sit there for hours having read zero rows. But a timeout on the bulk extract aborts honest work: a legitimately slow extract of a very large table is doing exactly what it was asked to, and killing it converts a slow success into a guaranteed failure.

**Fix.** Arm a call timeout on calls whose cost is bounded by construction — single-row catalog lookups, a clock read, an existence probe — and on the TARGET pool, where a row-lock wait otherwise never times out and a blocked write hangs the run with no error and no retry. Leave the source extract unbounded on purpose, and say so in the config next to the knob. Answer a probe timeout in the SAFE direction (treat the answer as unknown, full-reload, write no progress marker) and record separately that you did not find out: 'the table has no such column' and 'the database did not answer' are the same value in the code and must be different sentences in the log, or the operator is sent to fix the wrong thing.

**How to confirm it fires.** Precursor: grep for `call_timeout`, `wait_timeout`, `tcp_connect_timeout`. Map each pool to what it runs. Fires if the pool that runs the bulk extract has a non-zero call timeout, OR if small catalog/clock queries run on a pool with none. Third check: when a probe fails, does the log distinguish 'no' from 'no answer'?

---

## `bounded-cache-must-be-true-lru`

**High** — A bounded cache must promote on read AND on re-write, or it degrades to FIFO

**Look for.** An OrderedDict/dict used as an LRU where the read path does `store[key]` with no move_to_end, or the write path does `store[key] = value` for a key that may already exist without re-ranking it.

**What goes wrong.** Plain assignment does not re-order an existing key in an OrderedDict, and a read without promotion never refreshes rank — so a hot entry ages out on schedule regardless of use, and the cache turns over its whole working set while doing nothing for hit rate. The dangerous case is a two-phase write (remember at load time, then persist after the commit): without promotion the entry keeps the rank of its FIRST write and can be evicted while the batch is still using it.

**Fix.** Route every read through a helper that does move_to_end on a hit, and every write through one that does move_to_end when the key already exists. Pin it with a test that fails under FIFO: write keys 1..N at cap, re-write the OLDEST, add one more, and assert the re-written key survived while the next-oldest was evicted.

**How to confirm it fires.** grep the diff for OrderedDict / popitem(last=False). Then check both accessors for move_to_end. A cache whose only test asserts len(store) <= cap does not exercise the LRU property — look for a test that names which key survived.

---

## `correlated-count-star-as-existence-test`

**High** — Do not use a correlated COUNT(*) where EXISTS or an analytic count will do

**Look for.** A predicate of the form `(SELECT COUNT(*) FROM SOURCE_TABLE s WHERE s.key = outer.key) = 1` (or `> 0`, `<= 1`) used as a scope filter in a WHERE clause; worse when the correlation value itself comes from another nested scalar subquery.

**What goes wrong.** `COUNT(*) > 0` forces the subquery to count every matching row before the comparison can be made, whereas `EXISTS` stops at the first — on a wide child relationship that is the difference between reading one row and reading thousands, once per outer row. The `= 1` variant is not expressible as EXISTS and is genuinely a count, but it is still a per-outer-row aggregate over a table with, in the worst case, no index on the correlation column; as a filter inside a view it is paid on every branch every time the view is touched. Nesting a scalar subquery inside the correlation makes it worse and adds a silent failure mode: if the inner scalar returns NULL, the count compares against NULL, the predicate is UNKNOWN, and the row drops out of scope with no trace.

**Fix.** For existence use `EXISTS` / `NOT EXISTS`. For 'exactly one' use `NOT EXISTS (SELECT 1 FROM SOURCE_TABLE s2 WHERE s2.key = outer.key AND s2.rowid <> outer.rowid)`, or compute the count once with an analytic `COUNT(*) OVER (PARTITION BY key)` in an inline view and filter on it in the outer block — one pass instead of one pass per row. Never nest a scalar subquery inside the correlation predicate; join it in.

**How to confirm it fires.** Regex precursor: `\(\s*SELECT\s+COUNT\s*\(\s*\*\s*\)` inside a WHERE clause. Fires when the subquery is correlated to the outer block. Escalate when the comparison is `> 0` / `= 0` (should be EXISTS / NOT EXISTS) or when another `(SELECT` appears inside the same subquery's WHERE.

---

## `correlated-exists-against-a-large-queue-table`

**High** — Aggregate once and hash-join instead of a correlated EXISTS against a large table

**Look for.** WHERE EXISTS (SELECT 1 FROM <large log/queue/mapping table> WHERE ... = outer.col) in a diagnostic or reconciliation query.

**What goes wrong.** It executes once per outer row against a table that grows with every run, so a query written to answer a five-minute question runs for hours and gets killed — and the question stays unanswered. Queue, audit and id-mapping tables are exactly the ones that reach this size without anyone noticing.

**Fix.** Aggregate the large side once and join it: LEFT JOIN (SELECT DISTINCT key FROM big_table WHERE <scoped>) x ON ... . Scope both sides to the specific tables/runs in question rather than the whole history. Where the question is really 'how many', a single GROUP BY over the large table beats any per-row test.

**How to confirm it fires.** Regex: `EXISTS\s*\(\s*SELECT` with a correlation to the outer query. Semantic confirm: is the inner table a log/audit/queue/mapping table, or one known to be large? Unscoped inner queries over such a table fire the rule.

---

## `function-wrapped-join-or-filter-column`

**High** — A function around a column in a join or filter predicate suppresses its index

**Look for.** `TO_CHAR(col)`, `NVL(col, 0)`, `TRUNC(col)`, `UPPER(col)`, `SUBSTR(col, ...)` on the COLUMN side of a join condition or a WHERE predicate. The tell in an equality join is a function on one side and a plain column or literal on the other.

**What goes wrong.** An index on `some_column` indexes the column's values, not `f(some_column)`, so Oracle cannot use it for a predicate on the function — it falls back to a full scan of a table it could have probed. On a large fact table that turns a range scan into minutes of I/O. `NVL` on a join column is doubly bad: besides killing the index it changes the join's meaning, mapping every NULL to the sentinel and joining those rows to whichever parent genuinely carries that value. A function on the *probe* side (the value being looked up) is harmless; only the indexed-column side matters, which is why this needs a semantic check, not just a regex.

**Fix.** Move the function to the other side of the operator where possible (`col = TO_CHAR_of_the_literal`), rewrite `NVL(col,0) = x` as `(col = x OR (col IS NULL AND x = 0))` if the NULL handling is genuinely wanted, or create a function-based index if the expression is permanent and hot. For date ranges prefer `col >= DATE '...' AND col < DATE '...'` over `TRUNC(col) = ...`.

**How to confirm it fires.** Regex precursor: `(TO_CHAR|TO_NUMBER|NVL|COALESCE|TRUNC|UPPER|LOWER|SUBSTR)\s*\(\s*\w+\.?\w*\s*[,)]` appearing inside an ON or WHERE clause. Confirm semantically: is the wrapped operand a column of a table being probed (vs a literal or an already-materialised value)? Fires only for the probed-column side. Escalate to correctness if the function is NVL/COALESCE, because it also changes NULL matching.

---

## `one-commit-per-row-of-bookkeeping`

**High** — Batch bookkeeping writes, and choose the flush point by durability rather than by round-trip count

**Look for.** A helper that acquires a connection, executes one statement and commits, called once per processed row — audit trails, id maps, lineage, progress counters.

**What goes wrong.** Each commit is a synchronous redo flush on the server plus a round trip, so a batch of thousands can spend as long on bookkeeping as on the load itself — and all of it AFTER the data was already durable. On a remote metadata database it is routinely the single largest per-row cost in the pipeline.

**Fix.** Batch them: one statement per chunk, one commit per flush, same columns and same order as the single-row form so the two can never drift (share the SQL builder and the params builder between them). Then pick the flush point by DURABILITY, not by round-trip count. Writes that merely describe already-committed data can be buffered and flushed after the commit, and dropped wholesale when the batch rolls back. Writes that must survive a later failure of the same batch — a rejection, a deferral, an operator work-list entry — must stay where they are until the rollback path is also taught to flush rather than discard them; half-doing that loses exactly the records the failing run needed most.

**How to confirm it fires.** Precursor: grep for a private helper containing both `execute(` and `commit()`; then count its call sites inside row loops. Fires when any call site is per-row. Classify each such writer: does anything downstream need it to survive a failure of the batch it belongs to? That answer, not the row count, decides whether it may be buffered.

---

## `per-row-lookups-on-a-unique-key-can-never-hit-cache`

**High** — Prefetch a batch's lookup keys in one query when the key is unique per row

**Look for.** A per-row `resolve(key)` / `exists(key)` against another database inside the row loop, where the key is the row's own natural key or a one-to-one parent link. A cache sits in front of it and the code assumes that settles the cost.

**What goes wrong.** A cache in front of a key that is unique per row can never hit, so it is one network round trip per row — and against a metadata or lookup database on a different host, that is a full round trip each. At a batch size in the thousands that is thousands of round trips per batch, the difference between a minute and an hour. It hides for a long time because small parents mask it: a lookup table has tens of distinct keys, the cache warms in the first few rows and the cost disappears. It only bites when the parent is large AND the key is unique per row.

**Fix.** Prefetch the whole batch's keys in one bulk query into the SAME cache the per-row path reads, so answers are identical either way and the prefetch is a pure optimisation that can be skipped or fail harmlessly. Leave ambiguous keys OUT of the bulk answer so they still fall through to the guarded single lookup and raise there. Probe existing entries THROUGH the LRU rather than with a membership test, so a key the batch is about to use is promoted rather than left at a cold rank. Finally, check the prefetch is actually wired into the batch loop — a prefetch method with no call site is a comment, not an optimisation.

**How to confirm it fires.** Precursor: find calls inside a per-row loop that reach a repository/DB object. For each, ask whether the argument varies per row. Fires when it does and no batch-level prefetch precedes the loop. If a `warm`/`prefetch` method exists, grep for its call sites — zero call sites is its own finding.

---

## `scalar-subquery-in-select-list`

**High** — A correlated scalar subquery in the SELECT list runs once per output row

**Look for.** A parenthesised `SELECT` sitting in the select list of the outer query, correlated to the outer table: `SELECT p.id, (SELECT COUNT(*) FROM CHILD_TABLE c WHERE c.parent_id = p.id) AS n FROM PARENT_TABLE p`. Worse when two or more such subqueries in one select list read the SAME child table with the same correlation.

**What goes wrong.** Oracle evaluates a correlated scalar subquery per outer row. Scalar-subquery caching helps only when the correlation value repeats often; with a high-cardinality parent key it does not repeat at all, so the child table is probed once per parent row. Two subqueries correlated to the same child table double that. Against a large fact table this is the difference between one hash join and tens of millions of index probes, and the cost is invisible in the query text — it reads like a projection, not a join.

**Fix.** Fold the subqueries into one pass: aggregate the child once in an inline view and outer-join to it (`LEFT JOIN (SELECT parent_id, COUNT(*) n, MAX(some_date) d FROM CHILD_TABLE GROUP BY parent_id) c ON c.parent_id = p.id`), or use analytic functions over a single join. Two correlated subqueries on the same table should always become one.

**How to confirm it fires.** Regex precursor: `,\s*\(\s*SELECT` or `^\s*\(\s*SELECT` inside a select list (before the FROM keyword of the enclosing block). Confirm the subquery references an outer alias — that makes it correlated and per-row. Count how many such subqueries hit the same table; two or more is an automatic finding.

---

## `split-write-and-read-caches`

**High** — Do not share one bounded cache between a high-volume write stream and a read stream

**Look for.** A single bounded store holding both freshly minted mappings (written once per inserted row, mostly never read back) and resolved parent lookups (read once per row against a large key space).

**What goes wrong.** The write stream is pure churn: it writes entries nothing reads while evicting the entries the read side depends on. With a shared cap, the effective read cache shrinks to a fraction of the cap, the store turns over every few batches, and nearly every parent resolve becomes a single-row SELECT — millions of added round trips on a run that is already slow. The bound 'works' by every memory metric while destroying throughput, so it passes the review that added it.

**Fix.** Give each stream its own store and its own cap, with the ratio written down as an assertion so it cannot drift. Size the write store from what it must hold for CORRECTNESS (a batch's worth of not-yet-durable mappings, with a floor) and give the read store the full cap. Count hits, misses and evictions per store and print a summary line, so the next large run is diagnosable from evidence rather than from a guess.

**How to confirm it fires.** in a diff adding a cache bound, check how many distinct access patterns share the store. Two or more callers where one writes per row and another reads per row is the finding. A per-store hit-rate counter in the same diff is evidence the author thought about it.

---

## `unindexed-fk-turns-a-lookup-into-a-full-scan`

**High** — Check that the join column is indexed — Oracle does not index foreign keys automatically

**Look for.** A correlated subquery or per-row lookup from a large parent into a child table on the child's FK column, especially a 'latest per parent' or 'last payment per X' expression.

**What goes wrong.** Oracle indexes primary and unique keys automatically but not foreign keys. If the child's only index is its PK, each outer row triggers a full scan of the child. At scale that is an extract that never finishes and reads as a hung migration rather than a slow query — nobody sees a timeout, they see nothing.

**Fix.** Before writing the expression, confirm an index exists on the join column (ALL_IND_COLUMNS for that table). If it does not, either restructure as a single aggregated join/window pass over the child, or propose the CREATE INDEX to whoever owns the source database — and treat creating an index on someone else's production table as a change that needs their approval, not a convenience.

**How to confirm it fires.** Regex precursor: a correlated subquery or JOIN predicate of the form `child.parent_id = parent.id`, or wording like 'latest per'/'max per'. Semantic confirm: does any index in the catalog lead with that child column? Absence fires the rule.

---

## `union-all-view-needs-branch-predicate`

**High** — A view that UNION ALLs every source must document the predicate that prunes it

**Look for.** A view whose body is a long chain of `UNION ALL` branches, each reading a different source table and selecting literal discriminator columns (`'module_x' AS module_name`, `'SOURCE_TABLE' AS source_name`). The risk shows up at the call site, not in the view: `SELECT * FROM the_view` with no predicate, or a predicate on a column that is not one of the literals.

**What goes wrong.** Every branch scans its own source table. Oracle can eliminate branches only when the caller filters on a column that each branch supplies as a constant, because only then can it prove a branch contributes nothing. Filter on anything else — a joined column, a computed column, a literal the branches do not all carry — and every branch runs, so a query that looks like a single-table lookup reads the entire in-scope estate. Callers cannot see this: the view's shape is hidden behind its name, and the query that melts the server looks identical to the one that returns in a second.

**Fix.** State the required predicate in a comment at the top of the view definition, naming the exact columns that prune (`always filter on source_name, module_name or spec_id`) and giving a worked example. Give every branch the same set of literal discriminators so pruning always has something to bite on. Where callers must be protected, expose a parameterised pipelined function or per-module views rather than one wide view.

**How to confirm it fires.** Regex precursor: count `UNION ALL` occurrences inside a single `CREATE ... VIEW` body; more than ~5 branches, each with its own FROM table, fires the rule. Confirm every branch selects the same literal-valued discriminator columns; a branch missing one silently defeats pruning for every query that filters on it.

---

## `distinct-hiding-join-fan-out`

**Medium** — DISTINCT added to silence a join that multiplies rows hides the real defect

**Look for.** `DISTINCT` or `COUNT(DISTINCT ...)` introduced in the same change that adds a join to a table with no unique constraint on the join column; a scalar subquery in the SELECT list written as `(SELECT DISTINCT some_column FROM LOOKUP_TABLE WHERE ...)`; `COUNT(DISTINCT key)` used where `COUNT(*)` would be natural.

**What goes wrong.** DISTINCT does not fix a join at the wrong grain, it only makes the symptom invisible. Row multiplication survives in every aggregate that is not itself wrapped in DISTINCT — a SUM over a fanned-out join is inflated by exactly the fan-out factor while the neighbouring COUNT(DISTINCT) looks right, so the query returns one correct column and one wrong column. A `SELECT DISTINCT` inside a scalar subquery is the sharpest version: it is there because the lookup can return more than one row, and it postpones ORA-01427 (single-row subquery returns more than one row) until the day two rows genuinely differ, at which point a report that has run for years starts failing on real data. DISTINCT also costs a full sort or hash of the intermediate result.

**Fix.** Find the grain. Either join on the complete key, or pre-aggregate the many-side in an inline view and join to that, or state the fan-out explicitly with an analytic function. Where a scalar subquery reads a lookup, join to the lookup on its primary key instead and let the constraint prove single-row. Keep a deliberate `COUNT(DISTINCT key)` only where the key genuinely repeats by design — and then say so in a comment, because the next reader cannot tell it apart from a papered-over bug.

**How to confirm it fires.** Regex precursors: `SELECT\s+DISTINCT` inside a parenthesised expression in a SELECT list; `COUNT\s*\(\s*DISTINCT`. Confirm: is there a join or subquery whose predicate does not cover a declared unique key of the joined table? If so the rule fires. In a diff, fires hardest when DISTINCT and a new join arrive in the same hunk.

---

## `dont-pay-for-an-answer-you-discard`

**Medium** — Do not issue a remote query whose answer the current mode cannot use

**Look for.** A capability/state probe called unconditionally at the top of a loop, with the flag that overrides it consulted a few lines later.

**What goes wrong.** In the mode where the override always wins, the probe's answer is thrown away — but the round trip is still made, and it is often the FIRST round trip after some earlier failure, which is precisely when it is most likely to stall. Ordering a discarded question ahead of the work is how a cheap query becomes the thing that loses a run. Note the opposite trap: such a probe usually cannot be deleted outright, because a full pass often still needs its answer afterwards (to leave a baseline for the next incremental run). The rule is ordering, not absence.

**Fix.** Consult the mode first and call the probe only where its answer changes behaviour; if a later step still needs it, move the call AFTER the main work, where a slow probe costs that one step rather than the whole table. Pin the ordering with a test that records the statement sequence.

**How to confirm it fires.** in the diff, find probe/capability calls and locate the branch that would override them. If the override is computed after the call and does not depend on it, flag. A test that asserts an ordered list of executed statements is the durable fix.

---

## `keyset-pagination-not-offset`

**Medium** — Page a resumable extract by key, not by OFFSET

**Look for.** OFFSET :n ROWS FETCH NEXT ... or a ROWNUM BETWEEN range used to batch a large extract, or a FETCH FIRST with no ORDER BY at all.

**What goes wrong.** OFFSET re-reads and discards every prior row, so restart cost is O(table) rather than O(remaining) and a long run's later batches get progressively slower. Worse, FETCH FIRST without a total order is non-deterministic: two batches can return the same row, or skip one, and nothing reports it.

**Fix.** Keyset-page on an indexed, deterministic key: WHERE key > :last_key ORDER BY key FETCH FIRST :n ROWS ONLY, storing last_key after each committed batch. For a composite key use a row-value comparison ((A > :a) OR (A = :a AND B > :b)) with a matching ORDER BY. For a table with no unique key, page on ROWID — valid only while the source is read-only and nothing reorganises the table — or better, on any existing unique index. Keep the same binary ordering in both the predicate and the ORDER BY.

**How to confirm it fires.** Regex: `OFFSET\s+`, `ROWNUM\s*(<|BETWEEN)`, or `FETCH FIRST` / `FETCH NEXT` with no `ORDER BY` in the same statement. Semantic confirm: is the statement part of a batched or resumable read?

---

## `memory-budget-must-be-run-wide`

**Medium** — A memory ceiling must be the RUN's, not multiplied by the number of objects in the run

**Look for.** A per-instance cap (self._max = N) on a structure whose owning objects are all constructed up front and held for the whole run.

**What goes wrong.** If the orchestrator builds one migration object per table and holds them all, a per-table cap of N is really a run ceiling of N x tables — the operator sets what they believe is a memory limit and gets a multiple of it. The subtlety is that there is often no per-table hook to release the allowance in: when the framework builds the extract itself, a per-table lifecycle method is never called.

**Fix.** Put the counter in a shared, run-scoped budget object that all instances reference, so the second table starts against an already-spent allowance rather than claiming a fresh one. Provide an explicit reset for tests, and pin the shared-ness with a test asserting two instances hold the SAME budget object.

**How to confirm it fires.** for a new cap in a diff, find where the owning object is constructed. If construction happens in a registry/orchestrator loop over tables and the objects outlive the table, the cap is per-table; check whether the counter is instance state or shared state.

---

## `prefetch-key-must-match-lookup-key`

**Medium** — A warm/prefetch that keys differently from the lookup it serves is dead code that still costs the round trips

**Look for.** A cache is filled with key (entity, key, source) but read with (entity, key, None) — or any diff that changes the arity of a lookup key on one side only.

**What goes wrong.** The cache never reports an error; it simply misses every time, and every call falls through to the single-row query the prefetch existed to avoid. Nothing fails, nothing logs, and the optimisation shows up in the code review as present and working. This is exactly what happened when the probe looked up under a None source while the warm filled using the real one.

**Fix.** Derive both the warm key and the lookup key from one canonicalisation function. Pin it with a test that warms N keys, performs N lookups, and asserts the repository saw exactly ONE bulk call and ZERO single-row calls.

**How to confirm it fires.** when a diff adds a key component to either the fill or the read side of a cache, check the other side in the same commit. A test that asserts only 'the answer is right' does not catch this; look for an assertion on the QUERY COUNT.

---

## `distinct-inside-union-branch`

**Low** — SELECT DISTINCT inside a branch of a UNION is dead work

**Look for.** `SELECT DISTINCT` appearing in one or more branches of a statement whose branches are combined with plain `UNION`. Related: `SELECT DISTINCT` in the same query block as a `GROUP BY` that already covers every projected column.

**What goes wrong.** `UNION` already deduplicates the combined result, so a per-branch DISTINCT dedups the same rows a second time — two sort/hash-unique operations where one suffices, on the branch that is usually the largest. The `DISTINCT` + `GROUP BY` combination is the same waste: GROUP BY already guarantees one row per group, so if every grouping key is in the select list the DISTINCT can remove nothing. Both usually mean the author was not sure the join was at the right grain and added DISTINCT defensively — which is the real finding, because it hides a fan-out instead of fixing it.

**Fix.** Drop the branch-level DISTINCT and decide deliberately between `UNION` and `UNION ALL` at the top. Drop DISTINCT that sits over a GROUP BY. If the DISTINCT was there because a join multiplies rows, fix the join grain instead — aggregate the many-side in a subquery, or join on the full key.

**How to confirm it fires.** Regex precursor: `SELECT\s+DISTINCT`. Fires if the enclosing statement contains a `UNION` (not `UNION ALL`) at the same nesting level, or if the same query block also has a `GROUP BY` whose key list covers the select list.

---

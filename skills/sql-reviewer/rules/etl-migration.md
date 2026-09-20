# ETL and migration

Defects that load the wrong data, lose rows silently, or break idempotence.

61 rules. Severity: Critical 13, High 36, Medium 12.

---

## `a-cache-in-front-of-a-guarded-lookup-bypasses-the-guard`

**Critical** — Invalidate sibling cache entries when a key is re-minted; a cache hit never reaches the ambiguity guard

**Look for.** A read-through cache in front of a repository lookup that RAISES on ambiguity, or a split read/write cache pair where a write updates one store and leaves a possibly-stale entry in the other.

**What goes wrong.** A cache hit never reaches the guard the repository has. With split stores the hazard is specific and easy to miss: the fresher (write) store is usually the SMALLER one and turns over fastest, so once its entry is evicted the fallback answers from an entry cached BEFORE the re-mint — an older value for the same key. The lookups most exposed are the unqualified ones that sit outside whatever static validation the pipeline does. In an insert-only pipeline, a foreign key written against a superseded parent cannot be repaired later.

**Fix.** On every write, POP the other store's entries for that key (both the qualified and unqualified key shapes) rather than copying the value across — copying doubles the footprint of exactly the entries the split exists to keep out of that store. Route every miss through the guarded path. Treat invalidation as the debt the split itself created, not as an optional extra safeguard: a single shared store overwrote in place and had no such problem.

**How to confirm it fires.** Precursor: find a lookup method that consults more than one in-memory store, or any cache in front of a repo method whose body contains `raise` on multiple rows. Fires when the write path inserts into one store without removing the key from the others, or when a cached hit can be returned without the guard ever running. Check BOTH key shapes if the cache keys include an optional qualifier.

---

## `advance-a-watermark-only-after-a-complete-clean-pass`

**Critical** — Gate the delta watermark on completeness, not on the run finishing without an exception

**Look for.** A `set_watermark` / high-water update that is not gated on ALL of: every batch of the table succeeding, every ROW in those batches landing, the run having read the whole table (no operator slice, no subset/gap mode, no repair mode), and the table actually being delta-capable.

**What goes wrong.** The watermark answers 'what changed in the source since', which is a different question from 'what landed in the target'. Advancing it after a partial pass tells the next incremental run that everything up to that point is done, so the unread or unloaded rows are never selected again — silent data loss dressed up as a clean run. The easiest term to miss is per-row: a batch that commits can still contain rejected, deferred or poisoned rows, so 'did not raise' is not 'loaded cleanly'.

**Fix.** AND every guard term together and keep each one individually commented with the failure it came from — they accumulate one incident at a time, and taking any one side wholesale reintroduces the others' bugs. Have the batch report ok only when every row landed, and have the table's result AND those. Log explicitly when the marker was deliberately NOT advanced and what remains to be run, so a deliberate skip is not read as a bug. Ask any probe the gate depends on LAST and lazily, so a slow probe costs a watermark rather than the whole table's work.

**How to confirm it fires.** Precursor: grep for the function that writes the progress marker (`set_watermark`, `update_high_water`, `save_checkpoint`). Read its call site's condition. Fires if the condition omits any of: per-row success, whole-table coverage, subset/slice/repair flags, delta capability. Also fires if the 'table ok' flag is computed from exception absence rather than from row outcomes.

---

## `crosswalk-lookup-must-be-scoped-by-source`

**Critical** — Scope an id-map / crosswalk lookup by the SOURCE object, not by the target entity alone

**Look for.** A lookup of the form WHERE entity = :e AND legacy_key = :k with no source-table predicate, in a pipeline where more than one source feeds the same target entity.

**What goes wrong.** Unscoped, the query asks 'has ANYTHING mapped this number under this entity' when the code means 'did THIS migration already load this source row'. Two feeders into one target have independent, overlapping key spaces: id 4413 in one source is a different row from id 4413 in the other. The already-migrated probe then matches the other feeder's row and SKIPS a row that was never loaded — no reject, no failed row, no log line, so it looks migrated and is not. Where two rows match, the lookup instead raises 'ambiguous' and fails the whole batch. It needs a key collision to show, so most rows behave correctly and the symptom reads as an ordinary skip.

**Fix.** Carry provenance (source table) as part of the crosswalk key and pass it on every resolve, warm and existence probe. Where the framework can only infer a single owner for an entity it should leave the source unpinned rather than guess — so the CALLER must name it. Test with a deliberately colliding key from two feeders and assert each sees only its own row.

**How to confirm it fires.** grep the diff for resolve/find/lookup calls against the crosswalk and check the argument list for a source/table/provenance argument. Then check whether the target entity has more than one writer (several specs naming the same target). One writer: safe. Two or more with an unscoped call: Critical.

---

## `join-must-not-fan-out-grain`

**Critical** — A join added to an extract must be unique on the join key or the grain silently multiplies

**Look for.** An extract FROM clause replaced by a join — `SOURCE_TABLE JOIN SIDE_TABLE ON ...` — where the joined relation is a bare table rather than an inline view grouped on the join key.

**What goes wrong.** A one-target-row-per-source-row contract becomes false the moment the joined side has two rows for a key: the same source row is read N times and loads N times. Nothing raises, the run reports success, and the duplicate rows are permanent if the pipeline only ever INSERTs. Unique constraints on the target do not save you when the target has none, which is common for child tables.

**Fix.** Join to an inline view that is `GROUP BY`-ed on the join key, with a deterministic aggregate for every exposed column (`MAX(x) KEEP (DENSE_RANK LAST ORDER BY ...)` where a pick is needed). If the driver supplies more than one join value, put both in the ON clause so at most one view row can match. State in a comment why the view is unique on the key.

**How to confirm it fires.** Regex precursor: `\bJOIN\b` inside an extract FROM string. Fire unless the joined relation is a parenthesised `SELECT ... GROUP BY` whose grouping columns cover the ON-clause key, or the ON key is provably the joined table's primary key. Flag any joined relation that is a bare table name.

---

## `never-evict-state-that-is-not-yet-durable`

**Critical** — A cache holding not-yet-committed state needs a floor that covers a whole batch, and a cap below it must be refused

**Look for.** A bound added to a store that also holds mappings written before their transaction commits, with the cap taken straight from configuration and no minimum.

**What goes wrong.** When a parent is loaded mid-batch its mapping exists ONLY in the cache until the post-commit flush writes it. A child in the same batch that resolves it after an eviction gets None, takes a NULL foreign key and is rejected — for some rows only, hours later, with nothing pointing back at the cache. So the cap is not merely a performance setting: below roughly one batch's worth of entries it becomes a correctness setting, and an operator lowering it to save memory would be silently corrupting the load.

**Fix.** Define a floor derived from the largest supported batch size, refuse a configured cap below it at CONSTRUCTION time with a message that explains why (naming the batch), and accept a cap exactly at the floor. Add a test that pre-fills the store PAST its cap before the parent arrives, drives a realistic batch, asserts the eviction count actually increased, and then asserts the parent still resolves from cache with the repository never queried.

**How to confirm it fires.** ask of any bounded cache in a diff: can an entry be read back before it is durable elsewhere? If yes, look for a MIN constant and a construction-time raise. Also check the test: if it never asserts that an eviction occurred, it may be passing against a store that never evicted.

---

## `never-state-an-unmeasured-row-count`

**Critical** — Never state a production row count you have not measured — emit a read-only query instead

**Look for.** A review comment, finding, or document that asserts a volume — 'this affects roughly N rows', 'the table is empty', 'this column is unused' — where N did not come from a capture, a profiling artifact or a probe result that the reviewer can point at.

**What goes wrong.** A number in a finding is acted on. It sizes the work, it decides whether a column is dropped, and it gets quoted downstream long after its provenance is forgotten. An inferred or remembered count is indistinguishable in the report from a measured one, and being wrong about it costs a re-run or, worse, deletes real data. Reviewers are especially exposed here because the database is usually not reachable from where the review happens.

**Fix.** State only what a named artifact says, and cite the artifact. Where the finding genuinely depends on a number nobody has, write a read-only script for a human to run and stop there: SELECT-only, aggregate-only (LENGTH() for free text, never its contents), one pipe-delimited section|TAG|VALUE output column so results grep cleanly, statements numbered and each standing alone so one failure does not take the rest down, no schema prefix unless it has been confirmed, and the connection ROLE named at the top rather than an instance name. Label every figure as exact, a lower bound, or sampled.

**How to confirm it fires.** Scan the reviewer's own draft output, not the diff: any numeral describing rows, records, percentages of a table, or 'empty'/'unused'/'all rows'. For each, require an adjacent citation to a named evidence file. Uncited figures must be rewritten as a probe query.

---

## `overlapping-key-spaces-need-provenance`

**Critical** — When two sources load one target, a key-only lookup returns the wrong parent

**Look for.** A child resolving a parent by key alone against a target that more than one source table feeds — no source/provenance argument on the lookup, and no distinct namespace per feeder.

**What goes wrong.** Two source tables' surrogate key ranges routinely OVERLAP (one range sitting entirely inside the other is the common case), so the same key value exists in both. A lookup on (target, key) then returns whichever feeder happened to register first. It does not fail, does not reject and does not warn: the child row loads with a populated foreign key pointing at an unrelated parent. This is the single most dangerous shape in a multi-feeder migration because the result looks perfectly healthy.

**Fix.** Carry provenance into the key map. Either (a) give each feeder its own namespace/entity, or (b) make the source table part of the map's key and require every child lookup to name it. Where the correct feeder varies PER ROW, derive the source-table name as a column on the row and pass it per row. Validate at import: a lookup against a multi-feeder target that names no source must be an error, not a run-time surprise. And when one target row is given a SECOND key alias, the target gains a second feeder — so it drops out of any auto-narrowing and every lookup against it must then name its source explicitly.

**How to confirm it fires.** Build a map of target -> feeding source tables from the spec registry. For every parent lookup, fire when the named target has >1 feeder and the call passes no source/provenance argument. Cheap precursor: more than one spec object with the same `target_table`.

---

## `polymorphic-key-and-source-must-agree`

**Critical** — A polymorphic parent key and its provenance expression must branch on the same condition

**Look for.** A pair of derived columns — one `COALESCE(a, b)` / `NVL(a, b)` picking the key, the other `CASE WHEN a IS NOT NULL THEN 'A' ELSE 'B' END` naming which table the key belongs to — used together to resolve a polymorphic foreign key.

**What goes wrong.** The two expressions encode the SAME precedence decision twice. If one prefers `a` and the other prefers `b` — an easy edit to make in one place only — then for every row that carries BOTH values the pipeline looks the key of one parent up against the catalog of the other. Where the two parents' key spaces overlap, that resolves successfully to the WRONG parent: no error, no reject, a correct-looking foreign key pointing at an unrelated record.

**Fix.** Derive both from a single condition (ideally one CASE expression producing both, or two expressions generated from one constant), state the precedence explicitly in a comment, and pin it with a test. State how many rows carry both values, because that is the blast radius. A row carrying NEITHER must yield a NULL key and reject BY NAME, not fall through to a default branch.

**How to confirm it fires.** Regex precursor: a `COALESCE(`/`NVL(` derived column in the same spec as a `CASE WHEN ... IS NOT NULL THEN '<literal>'` derived column. Compare the column order in the COALESCE against the column tested first in the CASE; fire on any mismatch, and fire when the CASE has no ELSE-NULL path for the rows carrying neither.

---

## `required-on-a-mostly-null-column`

**Critical** — A mandatory-column rule on a mostly-empty source column caps the load and reports success

**Look for.** A source column listed as mandatory (`required`, NOT NULL enforcement, or a scope filter) whose real population rate on the full source has never been measured — often a code column that a newer column has replaced.

**What goes wrong.** Every row missing the value is rejected. If the column is dead — populated on a small minority of rows — the migration loads only that minority and the run still reports SUCCESS, because rejects are an expected category. There is no error to notice; the only signal is a loaded count nobody compared against the source count. The same column may also hold values that are not in its own code table at all, so even the populated rows do not all resolve.

**Fix.** Before listing a column as mandatory, measure its NULL rate and its resolve rate on the FULL source. If it is dead, find the fact elsewhere — derive it from a well-populated column and validate the derivation against the rows that DO carry the original (agreement on the testable subset is the evidence). Always reconcile loaded-vs-source counts per table and treat a large gap as a failure, not as rejects.

**How to confirm it fires.** For each column named in a `required`/NOT NULL list, look up its NULL rate in the profiling artifacts. Fire when it exceeds a threshold (say 50%) or when no measurement exists. Secondary signal: a sibling column on the same table carries the same fact and is well populated.

---

## `schema-export-is-not-the-target`

**Critical** — A schema export is evidence about the database it came from, not the one the code writes to

**Look for.** A finding or a code change that settles a target-side question — is this column nullable, how wide is it, does this constraint exist, is this lookup seeded, does this column exist at all — by reading a committed DDL export or an introspection JSON.

**What goes wrong.** Exports are snapshots of whichever environment was exported, and pipelines routinely build against one environment and run against another. A single NOT NULL that differs between them turns an unresolved value into a failure of every row in a large table. And the newest export is not automatically the right one: a freshly-landed export can already disagree with measured values on the environment it claims to describe. Ranking exports by date is not a method.

**Fix.** Use exports for column names, types and intent — the things that rarely differ. For nullability, widths and length semantics, defaults, constraints and unique keys, seeded reference rows, and occupancy, require a probe against the actual target and cite its result file. When no probe exists, say the claim is unverified against the target and emit the probe query. Where two catalogs disagree, report the disagreement rather than picking one.

**How to confirm it fires.** Regex precursor: a path to a schema export/introspection artifact, or wording like 'the catalog says', 'per the DDL export', 'the schema shows'. Semantic confirm: is the claim about nullability, width, a constraint, a default, seeded data or occupancy on the TARGET side? If yes and no probe result is cited, the rule fires.

---

## `second-writer-on-a-shared-target-duplicates`

**Critical** — A second loader for a target another loader already fills inserts duplicates, it does not enrich

**Look for.** A new spec whose mapping instruction reads as enrichment — 'attach a note to the existing row', 'copy this value onto the record another feed created', 'set this column on the parent' — pointed at a target some other spec already populates.

**What goes wrong.** If the pipeline has no real update path, the instruction executes as an INSERT. Existence is answered from the loader's OWN key-map entity, so a second loader does not see the first's row and writes a new one. Where the target has no unique constraint over the shared key, the database accepts every duplicate without complaint — the run reports success and the target silently carries millions of duplicate records. An UPDATE-shaped rule can also fail the other way: an update filtered on a condition that matches nothing raises nothing and reports success.

**Fix.** Recognise UPDATE-shaped instructions and do not build them as loaders. Where the extra columns come from a side table at the same grain, MERGE the read into the existing spec: pre-aggregate the side table once and LEFT JOIN it, so one target row is still written per source row. Where the fact belongs to a parent row, make it an explicit post-load step and say so. Check the target for a unique constraint over the shared key before assuming a duplicate would be caught.

**How to confirm it fires.** Group specs by `target_table`; for every target with more than one writer, check whether the second's mapping reads as enrichment of the first's row (shared parent key, no independent grain). Precursor: a spec comment containing 'attach', 'update', 'set on the existing', 'enrich' or 'copy onto'.

---

## `sibling-predicates-must-partition`

**Critical** — Sibling loaders of one target need predicates that are disjoint AND total

**Look for.** Two or more specs writing the same target table from the same source table, separated by scope predicates — one `EXISTS(... = v)` and the other `EXISTS(... <> v)`, or two equality filters on a column that is nullable.

**What goes wrong.** Two failure modes, and they point in opposite directions. If the predicates OVERLAP, a row matches both arms and loads twice. If they are not TOTAL, a row matches neither and vanishes with no reject row at all — which is the worse outcome, because a duplicate is at least visible. `EXISTS(type <> v)` is not the negation of `EXISTS(type = v)`: a row with no matching side row satisfies neither, and a row whose side value is NULL satisfies neither.

**Fix.** Write one arm as the positive predicate and the other as its EXACT textual negation — `NOT EXISTS(<same predicate>)` — so totality holds by construction and the rows with no side row land in a defined arm (where they reject BY NAME on the unresolved key). Build both arms from one shared predicate string so they cannot drift apart. Say in a comment which arm the no-match and NULL cases fall into.

**How to confirm it fires.** Group specs by (source table, target table). Where more than one exists, extract the scope predicates and check: (a) is one the literal negation of the other, or are the value sets provably disjoint; (b) does their union cover NULL and no-match rows. Regex precursor: two sibling entries in a spec tuple whose filters name the same column.

---

## `the-commit-gap-between-data-and-its-load-record`

**Critical** — Treat a post-commit metadata failure as its own failure domain — keep the buffer and abort the run

**Look for.** A batch that commits target rows and THEN writes the 'what landed' record (id map, lineage, audit) to a different database, with that post-commit write inside the same `try` as the pre-commit work and handled by the same generic `except`.

**What goes wrong.** After the commit the two are separate failure domains, and the generic handler is written for the other one. It calls rollback (now a no-op) and DISCARDS the buffered mapping — so the rows are durable and the only record of them is gone. The damage compounds three ways: children cannot resolve the parent, a rollback tool cannot clean rows it has no lineage for, and the next run RE-INSERTS them because the already-migrated check reads that very record. Every later batch lands the same way and widens the gap, so a single unwritable metadata database turns into an ever-growing divergence with no error that names it.

**Fix.** Give the post-commit failure its OWN exception type and catch it BEFORE the generic handler, or the generic handler will silently undo the fix. Do not discard the buffer — it is the in-memory copy of what was just written and the cheapest route back. Log precisely which rows are unmapped, that they must NOT be re-run, and which repair path rebuilds the record. Then ABORT the run rather than continuing. Symmetrically, when a batch genuinely ROLLS back, discard its buffered diagnostic rows too: carrying them forward lands them under the next batch that commits, describing rows that do not exist.

**How to confirm it fires.** Precursor: locate `commit()` inside a try block and check what follows it in the same block. Fires when a write to a DIFFERENT datastore follows and the enclosing `except` performs rollback/discard/compensation. Also check the exception ORDER: a specific post-commit exception type must be caught before the generic one. Third: does the rollback path discard buffers that describe the rolled-back rows?

---

## `a-timeout-is-not-a-negative-answer`

**High** — Never report a question that timed out as a definitive 'no'

**Look for.** A probe whose failure path returns the same falsy value as a genuine negative, feeding a message that asserts a fact about the database ('this table has no such columns — run X').

**What goes wrong.** Defaulting to the safe value is right; printing it as a finding is not. 'No change-tracking columns here' is a claim about the source schema, and emitting it when the probe never answered sends an operator to investigate a schema that is fine, while the real fault — a stalled or timed-out catalog query — goes unmentioned. Silence is what made a multi-day stall unreadable in the first place, so the timeout itself must produce a line naming the ceiling and what it did.

**Fix.** Return a three-valued result (yes / no / did-not-answer). Take the safe branch for did-not-answer, but log it distinctly by name, and suppress any diagnostic message that asserts a schema fact. Pin both halves: an unanswered probe does not produce the claim, an answered 'no' still does.

**How to confirm it fires.** find `except Exception: return False` (or None) on a probe in the diff and trace the returned value to any operator-facing message. If the message states a fact rather than an uncertainty, flag. Look for a separate 'unknown' sentinel as the fix.

---

## `arm-precedence-for-overlapping-matches`

**High** — Give overlapping arms an explicit precedence, and make the exclusion NULL-safe

**Look for.** A set of generated specs, one per candidate slot/column, each filtering `driver.match_column = PARENT_TABLE.slot_n`, where nothing prevents one parent row from holding the same value in two slots.

**What goes wrong.** If the same value occupies two slots on one parent row, an unguarded set of arms matches the source row twice and emits TWO target rows — each pointing at a DIFFERENT resolved parent, so a unique constraint over (parent, driver) does NOT catch it and the duplicate lands silently. Adding the obvious exclusion introduces a second bug: `slot_n <> value` is UNKNOWN when the slot is NULL, which drops every row whose earlier slots happen to be empty.

**Fix.** Give the arms a fixed precedence — arm k matches its own slot and explicitly excludes every earlier slot — and write each exclusion NULL-safely: driver.match_column = PARENT.slot_k AND (PARENT.slot_j IS NULL OR driver.match_column <> PARENT.slot_j) -- for each j < k Generate the exclusions from the same ordered list the arms are generated from, so precedence and arms cannot disagree.

**How to confirm it fires.** Find generated/parameterised specs sharing a source and a target, whose predicate compares one driver column against a different parent column per arm. Fire when an arm's predicate names only its own parent column. Separately, regex `<>` inside such an exclusion without an accompanying `IS NULL OR`.

---

## `bound-a-per-run-lookup-cache-but-never-below-one-batch`

**High** — Split and bound a per-run id cache with a true LRU, and refuse a cap smaller than one batch's writes

**Look for.** A plain `dict` used as a per-RUN lookup cache in a loop over millions of rows and never cleared; or a single bounded store serving both 'keys this run just minted' and 'parents this run looks up'; or an LRU whose put does not re-promote an existing key.

**What goes wrong.** Three failures stack here. Unbounded, it is a leak: the mapper is built once per RUN, not per table, so earlier tables' entries stay resident forever even though nothing will read them again once their children are loaded — the run ends in a bare memory error with no other symptom. A single bounded store is worse in a different way: a write stream of two or more entries per inserted row owns most of the store, the parent working set is evicted continuously, and nearly every lookup degrades to a single-row SELECT — a memory fix that buys a slower failure. And an entry that has been REMEMBERED but is not yet durable must not be evicted at all: if it is, a child later in the same batch resolves None, takes a NULL foreign key and rejects — silently, and only for some rows, which is the worst shape a data defect can have.

**Fix.** Split read and write stores with separate caps; give the read store the larger share (it is the one that decides the run's round-trip count) and the write store enough to outlive one batch's mints with real headroom. Use a TRUE LRU: promote on read AND after re-writing an existing key, since writing an existing key leaves it at its old cold rank. Floor the configurable cap and REFUSE a smaller value at construction, with a message that says to lower the batch size instead. Count hits/misses/evictions always — not behind a diagnostics flag somebody forgets to set — and log one summary line per table, never per row. Derive the write-store budget from the real worst-case mint rate per source row (aliases and post-step fan-out both multiply it), and when you quote a bytes-per-entry figure, say how it was measured and quote the same number everywhere.

**How to confirm it fires.** Precursor: grep for a dict/OrderedDict attribute used as a cache in a class constructed once per run. Checks: (1) is it bounded? (2) do writes and reads share one store? (3) does the put re-promote an existing key (`move_to_end` after assignment)? (4) is there a floor on any configurable cap, and is a too-small value refused rather than accepted? (5) is there an eviction counter? Any 'no' fires.

---

## `bulk-schema-prefix-replace`

**High** — Qualify a script's schema with ALTER SESSION SET CURRENT_SCHEMA, never with a find-and-replace

**Look for.** A SQL script in which a schema name appears before every table (`FROM SCHEMA.TABLE`), applied uniformly — and in the same file, at least one `FROM SCHEMA.(` , a prefixed dictionary view (`SCHEMA.ALL_TAB_COLUMNS`), or a `JOIN some_table` with no prefix while every `FROM` has one.

**What goes wrong.** 'FROM ' is not a table-name marker. A bulk replace of `FROM ` to `FROM SCHEMA.` prefixes inline views — `FROM SCHEMA.(SELECT ...` — which is a syntax error (ORA-00903); it prefixes SYS-owned dictionary views, which then do not exist for that owner (ORA-00942); and it misses every table introduced by a `JOIN` keyword rather than `FROM`, leaving the script half-qualified. The failures are scattered and each looks like a different problem, so the script comes back with a handful of statements 'that did not work' and the run is repeated. None of it is an Oracle problem — it is the qualification method.

**Fix.** Put one `ALTER SESSION SET CURRENT_SCHEMA = <schema>;` at the top of the script and leave every application table unqualified; dictionary views then resolve correctly on their own and are filtered by an explicit `WHERE owner = '<schema>'`. Say in the header that the file must not be prefixed. If the session change is refused, hand-qualify every table reference deliberately, checking inline views and JOIN clauses — do not bulk-replace.

**How to confirm it fires.** Regex precursor: `FROM\s+\w+\.\(` (prefixed inline view — always wrong) and `\w+\.(ALL|DBA|USER|V\$)_` (prefixed dictionary view). Also compare counts of `FROM\s+\w+\.` against `JOIN\s+(?!\w+\.)` — a mismatch means a half-applied replace.

---

## `carrying-source-pk-into-identity-column`

**High** — Do not carry a source primary key into a generated identity column

**Look for.** A mapping that writes the source table's primary key into the target's identity/sequence-backed `ID` column, usually justified as 'makes reconciliation a simple join'.

**What goes wrong.** Two costs, both operational. (1) The generator is left BEHIND the data: it still starts from a low value while carried keys run into the millions, so the first row the APPLICATION creates after the load is handed an id that already exists and fails on a unique violation. Recovering needs a post-load DDL step on every affected target. (2) Where two source tables feed one target, one minting and one carrying, their ranges overlap and whichever runs second collides — which turns run ORDER into an open decision nobody owns. The reconciliation convenience it buys is recoverable another way.

**Fix.** Let the target mint every key, and publish the source key into the key map as provenance. Reconciliation becomes a lookup in that map instead of a join on the source number — the same answer, with no re-seeding step and no run-order constraint. If a carry is ever genuinely required, it must be accompanied by the generator re-seed and by a documented rule for every other feeder of the same target.

**How to confirm it fires.** Compare the mapping against the target DDL: fire when a mapped target column is `GENERATED ... AS IDENTITY` or sequence-defaulted. Cheap precursor: a source PK name appearing as a value in a column map whose key is the target's PK.

---

## `committed-is-not-loaded`

**High** — Read the per-decision counts, not the word 'committed'

**Look for.** A run report, log assertion, or test that concludes success from a commit message, a batch-committed line, or a zero exit code.

**What goes wrong.** A transaction that contains nothing still commits. Every batch of a run can report committed while writing not one row — the verb is about the transaction, not its contents. A monitoring check or a handover built on that verb reports a clean run over an empty load, and the gap is only found when someone counts the target.

**Fix.** Assert on the decision counters the run itself records — inserted, rejected, failed, skipped, deferred — and on the target's own count, not on the commit. Where a run is handed over, quote those counters. The same applies to per-row bookkeeping: a queue row is closed only when its batch actually committed, so a rolled-back batch must not mark its rows dealt with.

**How to confirm it fires.** Regex precursor: `committed`, `commit()` or exit-code checks in an assertion, log-scraping check, or report. Semantic confirm: is any row-count or decision-count assertion present alongside? If success is concluded from the commit alone, the rule fires.

---

## `composite-parent-key-resolved-on-one-column`

**High** — Resolve a parent on its full key — a single-column lookup against a composite key matches nothing, or the wrong row

**Look for.** A parent-resolution transform passing one source column, where the parent's identity is only unique within a scope (a branch number unique within a bank, a sequence unique within a year).

**What goes wrong.** Two failure shapes, both quiet. If the single column matches nothing, a NOT NULL child column rejects every row and the reject blames the child. If it matches the wrong scope, the child resolves to a real but incorrect parent and loads successfully — the worse outcome, because nothing ever errors. The same thing happens when several mappings share one identity-map key: the second silently resolves to the first's row.

**Fix.** Build the composite key explicitly and reproduce the resolver's own key format, then assert that format against the resolver's own function in a test, so a change to the separator or number rendering fails loudly instead of resolving everything to NULL. Where two mappings write the same target, give each its own identity-map entity.

**How to confirm it fires.** Regex precursor: a parent/lookup resolution call taking a single column argument. Semantic confirm: is the parent's primary or unique key composite in the source catalog? Or does another mapping already claim the same identity-map entity name? Either fires the rule.

---

## `concatenated-composite-key`

**High** — A composite key built by concatenating columns needs a separator the data cannot contain, and a NULL policy

**Look for.** A key expression of the form `TO_CHAR(col_a) || '|' || TO_CHAR(col_b)` used to join to, or write into, a single-column key store; the same expression repeated in several places (a join's ON clause, a SELECT list, a generator template).

**What goes wrong.** Three separate failures. First, Oracle treats NULL as an empty string in concatenation, so a NULL component produces a key that looks well-formed — `(NULL, 5)` and `('', 5)` collapse to the same value and two different source rows claim one identity. Second, if any component's text can contain the separator, `('a|b', 'c')` and `('a', 'b|c')` collide. Third, the concatenation is a function of the columns, so any join written against it cannot use an index on either column. And because the expression is duplicated at the write site and the read site, the two can drift: change the separator or the TO_CHAR on one side and every previously written key stops being found — silently, since a lookup miss looks exactly like 'not yet migrated'.

**Fix.** Pick a separator no component can hold and assert it (`INSTR(col_a, '|') = 0`). Reject or explicitly sentinel NULL components rather than letting concatenation swallow them. Best of all, derive the key expression from ONE definition used by both the writer and every reader rather than re-typing it. Where a number is a component, pin the conversion with an explicit format model — see the NLS rule — so the rendered key does not depend on session settings.

**How to confirm it fires.** Regex precursor: `\|\|\s*'[^']'\s*\|\|` or `TO_CHAR\([^)]*\)\s*\|\|`. Confirm: are the components nullable? Is the separator excluded from the component domains? Does the identical expression appear at more than one site, and are all copies byte-identical? Any 'no' fires.

---

## `crosswalk-keyed-on-environment-local-ids`

**High** — Key a crosswalk on a portable attribute, not on a reference table's surrogate id

**Look for.** A hard-coded mapping dict whose KEYS are surrogate ids from a source reference table — `{3: 'MUSLIM_LAW', 301: 'CHRISTIAN_LAW'}` — rather than a value that means the same thing everywhere.

**What goes wrong.** Reference tables are populated per environment, so the same concept carries different ids in test, UAT and production. A crosswalk keyed on those ids resolves perfectly in the environment it was written against and resolves NOTHING after a refresh or on a different environment — silently, because a lookup miss is just a NULL. If the target column is mandatory, every row then rejects with nothing in the message saying an id moved underneath the mapping.

**Fix.** Key the crosswalk on a stable natural attribute — the reference row's description/label/code — and read that attribute in the extract via a small correlated lookup against the source reference table. Where labels vary in spelling, normalise before matching (a deliberately lossy fold used ONLY to look a value up, never stored). Keep the id-keyed version as a comment marked superseded rather than deleting the evidence.

**How to confirm it fires.** Find dict literals whose keys are small integers and whose values are target code strings. Fire when the keys are documented as ids of a source REFERENCE table (as opposed to an application enum baked into code). Precursor: a nearby comment naming a reference or lookup table as the key's origin.

---

## `declare-not-null-columns-required`

**High** — Declare NOT NULL targets required in the pipeline so a gap is a named reject, not an anonymous ORA-01400

**Look for.** A mapping that feeds a NOT NULL target column through a transform, lookup or fallback that can produce NULL, with no validation entry listing that column as required.

**What goes wrong.** When the value is missing the row reaches the database and comes back as ORA-01400 cannot insert NULL — on a multi-column INSERT, with no indication of which column. Every such row becomes untriageable: the analyst sees a wall of identical errors and has to reconstruct the column from the mapping. The same rows expressed as named rejects are immediately actionable, and the count per column tells you which decision to chase first.

**Fix.** List every NOT NULL target column the pipeline feeds in its required/validation set, so an unresolved value is stopped before the INSERT and reported as 'unresolved required column X' with the source key. Do the same for parent resolutions, so ORA-02291 becomes a named reject too. Treat this as a reporting fix, not a data fix — the same rows still do not load, they just say why.

**How to confirm it fires.** Cross-reference: for each target column written in the diff, is it NOT NULL in the target catalog? If yes, is it listed in the mapping's required/validation set? Absence fires the rule.

---

## `extract-filter-is-a-silent-loss`

**High** — A row excluded by the extract predicate produces no reject row

**Look for.** A raw scope predicate ANDed into the extract WHERE — an `extract_where`-style clause or an `only_where` filter — used to narrow what migrates.

**What goes wrong.** There are two ways to not load a row and they are not equivalent. A row REJECTED at transform time lands in the failed queue with a named reason and is counted. A row excluded at extract time is never read: it appears nowhere, and reconciliation reads the shortfall as an unexplained gap or, worse, does not notice. Getting the predicate's arithmetic slightly wrong therefore loses rows with no trace — and the counts people reason about are routinely wrong by orders of magnitude when they come from a sampled profile or a test extract.

**Fix.** Reach for an extract filter ONLY for rows that are genuinely out of scope (loading them would be wrong), never as a shortcut for rows that fail a rule — those belong in the reject queue where they are counted. MEASURE the predicate's true row count against the real source before shipping it, state the expected extract count next to the predicate, and state that the exclusion is silent so reconciliation can be told what to expect. Note the flip side: filtering out-of-scope rows per ROW instead inflates the defect count with rows that were never defects.

**How to confirm it fires.** Regex precursor: an `extract_where=` / raw WHERE fragment on a spec. Fire when the exclusion is justified by a row count or percentage that is not traceable to a full-table measurement, or when no expected extract count is stated. Also fire when the predicate excludes rows that would otherwise FAIL a rule rather than rows that are out of scope.

---

## `filter-on-nullable-parent-key-strands-children`

**High** — A WHERE filter on the parent extract silently rejects every child of the excluded rows

**Look for.** An extract-level filter on a parent table — WHERE parent.some_id IS NOT NULL, a status filter, a date window — where child tables resolve their parent through the same pipeline.

**What goes wrong.** Filtered-out parents never enter the identity map, so every child of one fails parent resolution. The rejects appear on the child table, far from the filter, and read as a mapping or data-quality problem on the child. When the filtered column is substantially NULL in production this is not a rounding error: it can be a quarter of the module's rows, and the symptom is thousands of 'unresolved required parent' rejects that no change to the child mapping can fix.

**Fix.** Whenever a parent extract gains a filter, state what fraction of the parent it removes (measured, not assumed) and name every child that resolves through it. If the excluded parents have children that must migrate, the filter is wrong — route those parents through a second entity or a per-source fallback rather than dropping them. Where a small-sample export suggests the filter is harmless, do not believe it: child tables are routinely exported without their parents, which makes any join-rate measured on such an export meaningless.

**How to confirm it fires.** Regex precursor: a `WHERE` clause or filter declaration on a parent extract, especially `IS NOT NULL` or a status equality. Semantic confirm: does any other mapping resolve a parent through that table's identity map? If yes, the filter has downstream reach and must be justified with a measured exclusion rate.

---

## `half-of-a-split-never-built`

**High** — A discriminator filter that names a sibling must have one — check both halves exist

**Look for.** A spec carrying a scope filter on a discriminator column (`WHERE kind = 1`) whose comment refers to 'the other half', 'its sibling', or 'the money/second variant below'.

**What goes wrong.** A filter narrows the load silently: the excluded rows are simply not read, so if the sibling was never written, a large share of the source is dropped with no reject row, no count and no error. The comment reads as documentation of a complete design, which is exactly why nobody checks. The excluded half can easily be the LARGER one.

**Fix.** For every discriminator filter, assert that the complementary value(s) are claimed by an actual registered spec, and add a test that fails when a discriminator's value set is not fully covered. Where the source column is nullable, remember NULL is a third value and must be claimed too.

**How to confirm it fires.** Regex precursor: a scope filter on a column whose name reads like a type/kind discriminator, in a spec whose comments contain 'sibling', 'other half', 'below' or 'second spec'. Then enumerate the registered specs on the same source table and check the discriminator's distinct values (from the DDL/profile) are all claimed.

---

## `inner-join-silently-filters`

**High** — An inner join added for enrichment is a filter — use LEFT JOIN

**Look for.** `SOURCE_TABLE JOIN SIDE_TABLE ON ...` (no LEFT) in an extract whose purpose is to attach optional side-table columns.

**What goes wrong.** An inner join drops every source row with no match. Those rows are not read at all, so they produce no reject row, appear in no defect count, and are nowhere near the queue an analyst looks at. When the side table covers only a fraction of the driver — which is the normal case for enrichment — an inner join quietly discards the majority of the load while the run reports success.

**Fix.** Use `LEFT JOIN` for every enrichment join. A source row with no side-table match then loads with NULLs in the enriched columns, which is the honest outcome; if the enriched column is genuinely mandatory, let it reject BY NAME through the required-column check rather than through a silent join.

**How to confirm it fires.** Regex: `(?<!LEFT )\bJOIN\b` in an extract FROM string. Fire on any bare JOIN in an extract built for column enrichment. Exception: a join that is DELIBERATELY the scope filter, which must say so explicitly in a comment.

---

## `missing-target-column-fails-the-whole-batch`

**High** — Assert every written column exists in the target before the run — ORA-00904 is a batch failure, not a row reject

**Look for.** A mapping that names a target column sourced from a design document, a pending schema migration, or a workbook cell, with no check against the deployed target catalog.

**What goes wrong.** An INSERT column list is built from the mapping and is not pruned against the live target. A column that does not exist makes the whole statement ORA-00904 invalid identifier, so a large table dies with no reject row naming anything — the pipeline reports a failure with no data-level explanation. Copying a column name verbatim from a spreadsheet is exactly how this happens, and so is relying on a migration that has been written but not applied.

**Fix.** Add a test that every column any mapping writes exists in the deployed target catalog, and run it before every load. Where a column is genuinely pending DDL, make the exemption narrow, explicit and self-expiring — a second test that fails once the column appears, so the exemption is deleted rather than accumulating.

**How to confirm it fires.** Regex precursor: a new or edited target column name in a mapping dict / column list. Semantic confirm: does that identifier appear in the deployed target catalog artifact or a probe result? Absence fires the rule, at High severity because the blast radius is the whole table.

---

## `never-cache-a-negative-parent-lookup`

**High** — Cache hits only; a cached miss hardens a temporary absence into a permanent NULL FK

**Look for.** `cache[key] = repo.get(key)` written unconditionally, including when the answer is None or an empty result.

**What goes wrong.** In a pipeline with deferral, a parent that is absent now may be loaded later in the SAME run — that is exactly why deferred children exist. A cached miss turns that temporary absence into a permanent one: the child resolves None, takes a NULL foreign key and rejects, silently and only for some rows.

**Fix.** Cache only positive answers — a minted mapping is immutable once written, so it is the only thing worth remembering. Where a negative answer genuinely IS whole-run immutable (a whole-table aggregate such as 'nothing has been loaded under this entity yet'), cache it DELIBERATELY and use a distinct MISS sentinel object so the cache can distinguish 'cached None' from 'not cached' — otherwise that lookup re-queries once per row for exactly the value the cache exists to hold. An empty LIST from a set-returning lookup is a different case again: the prefetch already paid for it, so caching it costs nothing.

**How to confirm it fires.** Precursor: in any cache-put, check whether the stored value can be None/empty. Fires when a miss is stored and the pipeline has a defer/retry concept. Conversely, if a lookup legitimately caches None, check that reads use a sentinel rather than `if key in store` / `store.get(key) is None`.

---

## `nondeterministic-first-row-pick`

**High** — A 'pick one row' rule must be total-ordered or re-runs load different data

**Look for.** `... ORDER BY some_column FETCH FIRST 1 ROW ONLY`, `ROWNUM = 1` without an ORDER BY, or `MIN(x) KEEP (...)` ordered by a column that is not unique — used to choose one side-table row per driver row.

**What goes wrong.** When two rows tie on the ordering column, Oracle may return either. A re-run, a delta run, or the same query after a reorganisation can therefore load a DIFFERENT value into the same target column — so the migration is not idempotent and a reconciliation between two runs shows differences nobody introduced. It also makes the loaded value unreproducible when someone later asks 'why this one?'.

**Fix.** Make the pick deterministic: order by a tuple that ends in a unique column, or use `MIN(x) KEEP (DENSE_RANK FIRST ORDER BY priority, unique_key)`. Where an arbitrary pick is genuinely acceptable, prefer a rule that is at least STABLE (lowest key wins) and say in a comment that it is arbitrary-but-stable.

**How to confirm it fires.** Regex precursor: `FETCH FIRST`, `ROWNUM\s*=\s*1`, or `KEEP\s*\(DENSE_RANK` in a SQL string. Fire unless the ORDER BY terminates in a column that is unique on the relation being ordered (check the DDL for a PK/UK on it).

---

## `placeholder-inside-a-unique-key`

**High** — Never write a fill-in placeholder into a column that is part of a unique key

**Look for.** A mandatory target column with no source, filled with a standing placeholder value, where the column also participates in a unique constraint.

**What goes wrong.** A placeholder is by definition the SAME value on every row. In a unique key that means exactly one row can take it and every subsequent row collides — so a policy that exists to let rows load instead blocks all but the first, and the error message says 'duplicate key', not 'this column has no source'. The two mechanisms look independently reasonable and only conflict at the intersection.

**Fix.** Exclude unique-key columns from the placeholder path and report a specific reason when such a column is unresolved ('NOT NULL text, but it is part of a unique constraint, so the placeholder is withheld'). Where the row must still load, carry a value that is unique BY CONSTRUCTION instead — typically the source row's own primary key rendered as text — and say that it is an identity, not a real number. Separately: an empty string is NOT a usable placeholder in Oracle, which stores '' as NULL and would fail the very NOT NULL check the placeholder is meant to satisfy; use a single space. And never invent a NUMBER or DATE placeholder: there is no obviously-absent number, and a fabricated id or amount reads as real data forever.

**How to confirm it fires.** Intersect the set of columns the placeholder policy would fill with the column sets of the target's unique constraints and unique indexes. Fire on any overlap. Also fire on any placeholder that is an empty string, and on any non-text placeholder (0, SYSDATE, a sentinel id) written into a mandatory column with no source.

---

## `preflight-must-not-raise-and-must-not-cry-wolf`

**High** — A preflight reports unknown rather than blocked, and must account for what the run itself will fill

**Look for.** A precondition check that calls out to a service without a try/except, or that compares a prerequisite against CURRENT state only, ignoring that an earlier step of the same run populates it.

**What goes wrong.** A preflight that can break a run is worse than no preflight, so an input it cannot evaluate must be 'unknown', never 'blocked'. The subtler failure is the false alarm: prerequisites loaded earlier in the same run are of course empty when the preflight looks, so a healthy run printed BLOCKED for two parents and then wrote rows for both. A warning that is wrong on a good run trains the operator to ignore the next true one — which is the whole asset being protected. Equally, a check that stops at the first unmet prerequisite can call a dependent write OK while it still cannot write a single row.

**Fix.** Wrap every external evaluation so a failure yields OK-with-unknown. Compute the set of prerequisites this run will populate (including entities minted by side-effect steps) and pass it in; treat a prerequisite in that set as satisfied while still SAYING it was considered, so the operator sees it was not skipped. Let real counts win over the hint. Report EVERY unmet prerequisite, not the first.

**How to confirm it fires.** read the preflight body in the diff for unguarded external calls, for `break`/`return` after the first failure, and for whether ordering within the run is modelled at all. If the check only queries current state, ask which step populates that state and whether it runs first.

---

## `preflight-run-wide-preconditions`

**High** — Check run-wide preconditions before the run, not by discovering an empty output table afterwards

**Look for.** A dependent write (a link/junction table, a child needing a seeded lookup) with no start-of-run check that its parent crosswalk is populated and its required reference data seeded.

**What goes wrong.** These are not per-row facts and they fail invisibly: the main table loads perfectly, the dependent table stays empty, and nothing appears in the log. Both of the incidents behind this were knowable before a single row moved — a renamed crosswalk entity so no parent ever resolved, and a required reference category that was never seeded while the column referencing it is NOT NULL. Each cost a debugging round on the server.

**Fix.** Declare each dependent write's requirements as DATA on the spec (parent entities, required reference category, an explicit disabled reason) and evaluate them in a preflight that prints the object, the state and the fix. Where a requirement is deliberately unconfigured, short-circuit and say so instead of listing the other checks as OK beside it — 'id-map OK' next to a disabled link reads as 'nearly working'.

**How to confirm it fires.** for a diff adding a side-effect/link write, look for a matching entry in the preflight declaration structure. Absence is the finding. Also check the check's message includes a remediation string — a blocked check with no fix text is half a feature.

---

## `projection-must-select-every-referenced-name`

**High** — Every alias or column a fragment names must be in the projection — and only once

**Look for.** A transform, filter or callable side-step that reads a column or derived alias which is not visibly added to the SELECT list — or a name that is BOTH a real column and a declared alias.

**What goes wrong.** Two opposite failures. (1) A name the code reads but the projection never selects is not an error: the row lookup returns None, the rule silently does nothing, and the spec looks perfectly healthy while writing half of what it promised. This bites hardest for columns read only inside callables, which nothing can discover by inspection, and for the extra columns a multi-column matching rule consults. (2) A name emitted BOTH as a bare column and as `<expr> AS <name>` produces an invalid-identifier error that fails the whole extract.

**Fix.** Derive the projection from a single function that walks every consumer — key columns, renames, all transform source columns including fallbacks and secondary match columns, filter columns, and columns only a callable reads (which must be DECLARED, since they cannot be discovered). Drop any plain column that is also a declared alias before emitting, so a name appears exactly once. Add a test that every bare column in the projection actually exists on the source table — a joined column that is not declared as an alias is an invalid-identifier waiting to happen.

**How to confirm it fires.** Collect every column/alias name referenced by the spec's transforms, filters, defaults and declared hook-column lists; diff against the computed projection. Fire on any referenced name absent from the projection, and on any name present both as a bare column and as an alias. Precursor: a callable hook in a spec with an empty declared-columns list.

---

## `prove-a-driver-workaround-is-live-at-startup`

**High** — Self-check a version-specific workaround through the real code path before the first real row

**Look for.** A workaround for a driver- or version-specific trap with no startup self-check; or a self-check that exercises a hand-written equivalent (a literal TO_CHAR, a stubbed value) rather than the production path.

**What goes wrong.** A workaround of this kind is invisible until the first bad value arrives, which can be millions of rows into a read. When it is NOT in force the run fails exactly where it failed before the fix existed, with nothing in the log to say the fix was never live — so the obvious reading is 'the fix does not work' when the truth is usually 'the fix is not installed'. Package managers report 'already satisfied' and keep the old code whenever the version string has not changed, and an editable install keeps the CODE live while FREEZING the packaging metadata, so both directions of that confusion are common. A probe that tests something other than what the extract does is worse than no probe: the first cut can pass its own test and still die on the real path.

**Fix.** At session set-up, ask the database for the exact value that used to break — routed through the REAL production path, every step of it — and assert the expected answer. Fail with an actionable message that names the likely cause and the reinstall command. Assert the direction too: if the probe returns a VALUE where it should return the sentinel, the writer and the parser have drifted apart and every value would be silently converted to NULL, which is a worse failure than the one being guarded. Keep the probe's own results out of any end-of-run defect tally — it is your value, not the customer's data — and expose a tri-state 'verified / not verified / never checked' for the run log.

**How to confirm it fires.** Precursor: grep for comments containing 'workaround', 'thin mode', 'driver bug', or a version number next to a behavioural claim. Fires when no startup probe exercises it. If a probe exists, check it calls the same public function the production path calls, rather than re-implementing the statement inline.

---

## `reject-never-truncate-or-round`

**High** — Declare length and numeric bounds so an over-size value is a named row reject

**Look for.** A mapping from a wide source column into a narrower target column (or from an unbounded NUMBER into a bounded one) with no declared limit — and, worse, a transform that truncates or rounds to make it fit.

**What goes wrong.** Without a declared bound the value reaches the database and comes back as a value-too-large / precision error that names NEITHER the column NOR the value, and fails the whole BATCH rather than the row. With truncation or rounding it is worse: a shortened name is a different person, a rounded identifier is a different record, and nobody is aware. Silently shortening non-Latin text is especially bad because the damage is invisible to an English-reading reviewer.

**Fix.** Declare the target's real limit per column and reject over-limit rows BY NAME, quoting the actual length and a prefix of the value. Never truncate and never round — widen the column (a DDL change) or let an analyst rule on the row. Only declare bounds that are genuinely narrow: an unbounded NUMBER cannot overflow, and declaring it is noise that hides the real one.

**How to confirm it fires.** Join source and target column widths/precisions from the two catalogs. Fire on any mapping where source width > target width (or source precision > target precision) and no limit is declared for that column. Separately, regex for `[:limit]`, `.ljust(`, `round(` or `SUBSTR(` applied to a mapped VALUE — truncation in disguise.

---

## `sample-nullness-is-not-production`

**High** — Null rates from a test extract or a sampled profile are not evidence about production

**Look for.** A comment justifying a dropped column, a chosen source or a scope decision with '100% NULL', 'empty', or a percentage — where the figure comes from a test/partial extract, a single-tenant slice, or a sampled profile rather than a full-table measurement.

**What goes wrong.** A partial extract understates population and join rates (child tables are often exported without their parents), and a sampled profile cannot see a column that is populated on a tiny fraction of a huge table. Observed consequences of trusting such figures: a column written off as empty that carries real content on hundreds of thousands of production rows; a percentage quoted INVERTED (a column called 76% null that is in fact 86% populated); a per-row figure inferred from a per-parent figure and never labelled as an inference. Each one silently changes what migrates.

**Fix.** Before writing a column off, running a scope decision off a percentage, or sizing an expected loss, measure on the full source (exact GROUP BY / COUNT, sample_pct=100). Label every figure with WHERE it came from. Treat an inference ("parents are 22% money, so rows must be 22% money") as a hypothesis and mark it as such until measured — those have been wrong by three orders of magnitude.

**How to confirm it fires.** Regex precursor: a percentage or '100% NULL' / 'empty' / 'all null' in a comment next to a mapping decision. Fire when the figure has no stated full-table provenance, or when its provenance is a named test/partial extract. Cross-check against the committed full-table profile where one exists.

---

## `scope-cleanup-by-source-when-several-sources-feed-one-target`

**High** — Scope rollback and cleanup by (target, source) and by the loader's declared entity, not by target name

**Look for.** A rollback or cleanup `DELETE ... WHERE target_table = :t` with no source narrowing, or an identity-map delete keyed on the target table's name where the loader actually declares its own entity name.

**What goes wrong.** Several source tables routinely load one target. Deleting the lineage by target alone wipes every feeder's record, so a later rollback of one of THOSE finds nothing to delete and reports success while its rows are still in the database. And deleting identity rows by the wrong column value deletes nothing at all, leaving mappings that point at rows which have just been removed — the next run then thinks those legacy rows are already migrated.

**Fix.** Scope lineage deletes by (target, source) and identity deletes by the loader's declared entity; make those narrowing parameters required for any caller that means one table, and document that omitting them is correct only for a whole-module rollback. Add a count of rows recorded with NO source attribution, so a caller can tell when the scoping cannot be trusted and refuse rather than delete on the strength of the target name alone.

**How to confirm it fires.** Precursor: grep for DELETE statements in rollback/cleanup/reset code paths. For each, list the WHERE columns. Fires if the predicate is the target table alone while the schema records a source/provenance column, or if an entity-keyed table is filtered by a table name. Confirm by checking whether any two loaders in the registry share a target.

---

## `skip-decisions-can-hide-lost-rows`

**High** — Reconcile source rows against inserted + rejected + failed + skipped, and treat the residue as a defect

**Look for.** A load path with an INSERT-or-SKIP idempotency check (skip when the identity map already holds the key) and a reconciliation that compares only source count against target count.

**What goes wrong.** A skip writes nothing to the target, nothing to the reject queue and nothing to the failure queue. If the target was truncated but the identity map was not cleared, every row is judged already-migrated and vanishes — no error anywhere, and a re-run cannot repair it because the map answers 'done' for every row. This can be larger than the rejects and failures combined and it is invisible in every exported artifact.

**Fix.** Make the reconciliation explicit: for each table, source = inserted + rejected + failed + skipped + deferred, and flag any unaccounted remainder as a defect rather than rounding it away. Read the per-decision counters from the run's own metadata, on the metadata schema that run actually wrote to. Where skips are the cause, the fix is clearing the module's identity-map entries or running an update-or-insert resync — not changing any mapping.

**How to confirm it fires.** Regex precursor: a SKIP/exists-check branch in a load rule, or a reconciliation report whose columns are only source and target. Semantic confirm: does the reconciliation account for skipped and deferred decisions? If not, the rule fires.

---

## `source-code-copied-into-target-lookup-fk`

**High** — Never copy a source code straight into a target lookup foreign key

**Look for.** A plain column-to-column mapping where the source is an enum/code column and the target column name ends in `_ID` and is a foreign key to a reference table or a generic lookup table.

**What goes wrong.** The two systems number independently. Sometimes the numbers coincide for the values you happen to test — which makes a straight copy look correct on most rows and silently wrong on the rest, including for a code the target has no equivalent for at all. Sometimes they are simply offset (the source is 1-based, the target seeds from 0), so every row is shifted by one and reads as valid data. And when the target column carries NO foreign key constraint, a meaningless value like 0 is ACCEPTED silently instead of failing — so nothing ever surfaces it.

**Fix.** Resolve through a crosswalk on the target's stable CODE, never on its numeric id and never by copying the source number. Build the crosswalk from evidence (the source application's own branching logic, or an exact label match on both sides), not from the values lining up. Make an unmapped source value resolve to NOTHING so the row rejects BY NAME rather than taking a wrong code. Check whether the target column actually has an FK — if not, a wrong value will never be caught downstream.

**How to confirm it fires.** Join the mapping against both catalogs: fire when a plain copy maps a low-cardinality numeric source column into a target column that is (or is named like) a lookup FK. Cheap precursor: target column name matching `_ID$` present in a plain rename map rather than in a resolved-transform map.

---

## `source-identifier-copied-not-remapped`

**High** — An identifier from the source system must be re-pointed through the key map, never copied

**Look for.** A plain column-to-column mapping of an identifier — an audit user id, an owner id, a creator reference — from source to target, where the referenced entity is itself being migrated with newly minted keys.

**What goes wrong.** The number means a different record in the target system. A straight copy produces a populated, plausible reference to whoever happens to hold that number there — so an audit trail or ownership link points at the wrong person, with no error and nothing downstream to catch it (these columns frequently have no foreign key). The declared TYPE is a red herring in both directions: the source may declare the column as text while storing digits, and the target may declare it as text on some tables and numeric on others while the SEMANTICS are uniform.

**Fix.** Coerce the value to the right type in SQL (with a per-value conversion fallback, not a bare cast), then resolve it THROUGH the key map like any other parent. Normalise this centrally rather than per mapping, so no individual spec can forget it. Decide what an unresolvable value means from the TARGET column's own nullability: where the column is mandatory, substitute a documented 'unknown' record; where it is nullable, write NULL — stamping a substitute onto a nullable column asserts that someone did something, while NULL says the source did not record it.

**How to confirm it fires.** For every plain copy whose target column name matches an identifier pattern (`*_BY`, `*_ID`, `*_USER*`) — fire when the referenced entity has its own migration (i.e. its keys are minted) and the mapping does not go through the key map. Precursor: an identifier column appearing in a rename map rather than in a resolved-transform map.

---

## `stale-mirror-of-a-dropped-constraint`

**High** — An ETL-side mirror of a database constraint must be retired with the constraint

**Look for.** An in-pipeline uniqueness (or other) check declared to mirror a named target constraint, with no linkage to whether that constraint still exists.

**What goes wrong.** The mirror is pure in-process state — it never queries the target — so once the constraint is dropped in the database the pipeline keeps rejecting the same rows on its own authority, and no amount of DDL work makes it stop. It rejects rows the database would now accept, which reads as a data problem rather than a stale declaration. The inverse trap: dropping the constraint may not be enough. Where the constraint was created with a SEPARATELY created index (`ADD CONSTRAINT ... USING INDEX <name>`), `DROP CONSTRAINT` leaves the index in place and the database still raises a unique-constraint violation — the index must be dropped too.

**Fix.** Pair every ETL-side constraint mirror with the constraint NAME and assert that name against the committed target DDL in a test, so the declaration fails loudly when the constraint disappears. Treat 'drop the constraint' and 'remove the mirror' as one change that must move together, and include `DROP INDEX` in the DDL change when the constraint used a pre-created index.

**How to confirm it fires.** For every declared constraint mirror, look the named constraint up in the committed target DDL (`user_constraints` type 'U' or `user_indexes` uniqueness) and fire when it is absent, DISABLED, or covers a different column set. Also fire on a DDL change containing `DROP CONSTRAINT` with no matching `DROP INDEX` when the original used `USING INDEX`.

---

## `take-the-watermark-from-the-source-clock-before-the-extract`

**High** — Read the delta high-water value from the source database clock, before the first row

**Look for.** A watermark set from `datetime.now()` on the client, from the TARGET database's clock, or from a timestamp captured after the load finished.

**What goes wrong.** The value is compared next run against a change-timestamp column maintained on the SOURCE, so it has to be on the source's own clock — client and target clocks drift, and any skew silently defines a window of rows that are never re-selected. It also has to be the time taken BEFORE the extract started: a row changed while the extract was running is not in the extract's result set, so a marker set at the END of the load claims it was migrated.

**Fix.** `SELECT SYSTIMESTAMP FROM DUAL` on the SOURCE connection before the first row is read; hold the value; write it only after the table completes cleanly. If the clock read fails or times out, write no marker at all — a full reload next run is the safe direction and costs time, not data. Bound that read like any other trivial query, since a clock read takes no lock and reads no table: one still running after a minute is stuck, not busy.

**How to confirm it fires.** Precursor: grep for the value passed to the watermark writer. Trace its origin. Fires if it is `datetime.now()`/`utcnow()`, a target-side SYSTIMESTAMP, or a value captured after the extract loop. Second check: is the clock read placed before the extract begins?

---

## `target-scoped-defaults-must-not-leak-to-other-tables`

**High** — A declaration made about one target table must not apply to other tables written through the same sink

**Look for.** A per-migration mapping of column -> SQL expression (or similar target-shaped declaration) consulted inside a generic insert()/write() helper by column name alone, with no check of WHICH table is being written.

**What goes wrong.** Side-effect and link steps write additional tables through the same sink. A declaration keyed only on column name then leaks onto a table where the column may not exist (ORA-00904, which fails the whole batch) or, worse, where it exists with different semantics and silently computes the wrong value. The declaring spec described its OWN target and nothing else.

**Fix.** Gate the lookup on the table name the sink was asked to write, comparing against the migration's declared target; skip the expression for any other table. Include a test that writes a DIFFERENT table through the same sink and asserts the expression does not appear.

**How to confirm it fires.** find where the declaration dict is read inside the write helper. If the surrounding condition tests only `column in declarations`, flag; it must also test the table argument against the migration's own target.

---

## `two-queries-same-metric-different-method`

**High** — Two queries that report the same quantity by different methods will disagree — make them one

**Look for.** A pair of views or queries published together as a summary and its detail — one counting with `COUNT(DISTINCT key)`, the other returning `one row per source row` with no dedup; or a summary that filters NULL keys out via an aggregate while the detail lists them.

**What goes wrong.** `COUNT(DISTINCT key)` and `COUNT(*)` over the same population differ by exactly the duplicates, and `COUNT(DISTINCT key)` additionally drops rows whose key is NULL, while a detail query listing the same rows drops neither. So the summary reports a smaller gap than the number of rows the detail hands you to fix, and whoever reconciles them concludes one of the two is broken — usually after hours. In migration reporting this is the most common source of 'the numbers do not tie out', and it is structural, not a data problem.

**Fix.** Derive both from one definition: make the detail query the base and the summary an aggregate of it (`SELECT ..., COUNT(*) FROM detail_view GROUP BY ...`), so the two cannot drift. If they must be separate statements, use the same counting method in both, state in a comment which rows each one excludes, and add a standing check that the summary's figure equals the detail's row count.

**How to confirm it fires.** When a diff touches one of a summary/detail pair, check whether the other exists in the same file or directory and whether the counting method matches. Regex precursor: the same table name and the same WHERE predicate appearing in two statements where one contains `COUNT(DISTINCT` and the other does not.

---

## `unique-key-derived-from-one-source-column`

**High** — A composite unique key whose columns all derive from one source column is a single-column key

**Look for.** A target UNIQUE constraint over (A, B) where the migration derives BOTH A and B from the same source column — typically A is a copy of a description and B is a lookup resolved BY that same description.

**What goes wrong.** The key collapses into a function of one column, so any two source rows sharing that column ALWAYS produce the same key and the second collides. The collapse is in the RULE, not in the data: seeding more lookup values does not help, and the loss is bounded by the number of distinct values in that one column, which can be a small fraction of the source rows. Worse, the column the constraint was designed around — the real discriminator — is usually the one the derivation discarded.

**Fix.** When a target carries a composite unique key, check that the migration supplies its columns from INDEPENDENT sources. If one of them is being derived from another, find the real discriminator in the source (it is usually the column that was dropped) or escalate: the owner must decide which of each colliding group is canonical. Never dedupe silently to make the key fit.

**How to confirm it fires.** For each target unique constraint, trace every column back to its source expression. Fire when two or more columns of the key resolve to the same source column (directly, or one via a lookup keyed on the other). Cheap precursor: a lookup transform whose key column is also the plain source of another column in the same key.

---

## `unlabelled-sampled-figure`

**High** — A sampled figure must be labelled as an estimate, and some queries must never be sampled

**Look for.** `SAMPLE(n)` or `SAMPLE BLOCK(n)` in a profiling query — especially one commented in and out; a result column named `row_count` / `total` produced by a sampled scan; a figure quoted from a single-partition or single-tenant extract described as if it were the whole population.

**What goes wrong.** A sampled count and a full count land in a column with the same name and are copied into the same report cell, so an estimate becomes a fact with nobody lying. Worse, some questions are destroyed by sampling and not merely approximated: duplicate detection, uniqueness tests, MIN/MAX range checks and orphan counts all depend on seeing every row of a group — a 1% sample of a duplicate pair almost never returns both halves, so it reports near-zero duplicates with total confidence. The same applies to profiling a subset extract and generalising: a column that is entirely NULL in one slice can be well populated overall, and an ETL rule derived from the slice then routes or rejects a large population wrongly, while resolving cleanly and flagging nothing.

**Fix.** Put the sampling rate in the column alias or in an emitted marker (`rows_est_1pct`), never in a comment alone. Mark queries that must run unsampled with an explicit 'MUST NOT be sampled' comment, and never sample uniqueness, duplicate, range or orphan checks. Before deriving a rule from any subset, restate the measurement over the full population.

**How to confirm it fires.** Regex precursor: `\bSAMPLE\s*(BLOCK\s*)?\(`. Fires at High when the enclosing query contains `GROUP BY ... HAVING COUNT(*) > 1`, `COUNT(DISTINCT`, `MIN(`/`MAX(`, or a NOT EXISTS orphan test. Fires at Medium otherwise unless the sampling rate appears in a result column alias.

---

## `validation-bounds-must-come-from-the-live-catalog`

**High** — Refresh column-width and precision guards from the live catalog, not from a shipped DDL snapshot

**Look for.** Numeric precision/scale limits or text-width limits baked into shipped code from a DDL export and enforced before the INSERT; a hand-mirrored uniqueness or check constraint.

**What goes wrong.** A snapshot goes stale the moment a DBA alters the column, and it fails in the STRICT direction: the loader rejects rows the database would now accept, with a reason that reads like a data defect, and no amount of DDL work on the target can stop it. A declared constraint that no longer matches the target is worse than no constraint at all — the same failure shows up when a mirrored uniqueness rule outlives the constraint it copied.

**Fix.** Read the limits from the live catalog once per table before the first row, cache per table, and fall back to the shipped snapshot only if the read fails (it is a snapshot of the same database, so it is the right fallback). Respect the catalog's own conventions when interpreting it: a LOB reports a char length of 0, which means UNBOUNDED — drop the limit rather than enforcing zero — and a column widened to a bare numeric type has a NULL precision, which also means unbounded. Log every bound that changed, so a silently widened column is visible in the run log.

**How to confirm it fires.** Precursor: grep for a dict of column -> (precision, scale) or column -> max length declared in source. Check whether anything refreshes it from the catalog at run time. Fires when it is enforced as-is. Secondary: if a refresh exists, does it treat char_length 0 / NULL precision as unbounded rather than as a literal bound?

---

## `verify-constraint-claims-against-the-catalog`

**High** — A code declaration that asserts something about the target database must be checked against the catalog, not trusted

**Look for.** A spec/config field naming a database constraint, index, sequence or column that the code then relies on for correctness — with no test joining it to a committed schema artifact.

**What goes wrong.** The declaration is the permission to stop doing the work in code. It is only sound while the named object really exists over exactly those columns; a typo or a renamed constraint turns it into a silent licence to accept bad rows. Because the consequence appears on a production table rather than in the test suite, the claim must be checked where it is cheap. Catalog artifacts disagree with each other over time, so accept a claim backed by ANY available artifact and skip the check when none is present, rather than failing the suite on a missing file.

**Fix.** Add a test that loads the committed schema artifacts, builds {table: {frozenset(columns): [names]}}, and asserts every declaration matches. Keep a second test that inventories the declarations as an explicit literal, so adding one is a deliberate act with a review prompt attached. State plainly what the check cannot prove — an artifact cannot show the constraint is still ENABLED — and print the name at run time so an operator can verify it.

**How to confirm it fires.** grep the diff for new string literals that look like constraint/index names (UQ_/UNQ_/PK_/IDX_ prefixes, or a field named *_constraint). If the value is not referenced by any test that reads a schema artifact, flag.

---

## `any-row-fallback-always-succeeds`

**Medium** — A 'take any row' lookup fallback always yields a value — a wrong result is indistinguishable from a right one

**Look for.** A lookup transform with a catch-all fallback — 'if it returns null, set any value', 'take the first row in the category', 'attach to the first parent' — used to satisfy a mandatory target column.

**What goes wrong.** The fallback guarantees the column is never NULL, which means it also guarantees the miss is never visible. A row that matched nothing loads looking exactly like a row that matched correctly, so a broken crosswalk produces a table full of confidently wrong classifications and no rejects to show for it. The same shape applied to a parent link produces a populated foreign key that is a placeholder relationship, not the real one.

**Fix.** Make the fallback opt-in per column, never a default, and state in a comment that it is a guess and what it asserts about the rows that take it. Make the pick STABLE (lowest key wins) so re-runs do not rewrite the column. Record a post-run verification that would expose a total miss — e.g. group the target by the resolved column and check the distribution is not concentrated on a single value. Prefer rejecting BY NAME wherever the column can tolerate it.

**How to confirm it fires.** Find lookup/parent transforms carrying a catch-all fallback flag. Fire when the fallback is enabled on a column that is mandatory on the target AND no post-run distribution check is recorded. Precursor: a mapping note containing 'any value', 'first row', 'if it returns null set'.

---

## `cache-fill-must-never-fail-the-batch`

**Medium** — A prefetch or cache fill must be logged and swallowed, never allowed to fail the work it optimises

**Look for.** A warm/prefetch call added to an executor or batch loop without a try/except, or with an except clause that re-raises.

**What goes wrong.** A prefetch is a cache fill: by construction the per-row path can answer every question it answers. Letting a failure inside it abort the batch converts an optimisation into a brand new way to lose a run — the opposite of what it was added for. The failure mode is unpleasant too, because the metadata side is exactly the component whose flakiness motivated the batching.

**Fix.** Wrap the prefetch in try/except Exception, emit one operator line that names both the step and the underlying error, and continue. Also make the warm a no-op (not an AttributeError) when the repository predates the bulk method, so older deployments still run, just without the saving. Assert the warm can never change an answer: keys the bulk read did not return must still fall through to the single-row path, including ambiguous ones.

**How to confirm it fires.** locate the prefetch call site in the diff and read its enclosing block for try/except and a log call. Separately check that the bulk method is reached via getattr/hasattr or a try/except AttributeError if older backends are supported.

---

## `db-enforced-fks-are-not-the-whole-model`

**Medium** — The declared FK set is not the complete referential model

**Look for.** A dependency graph, load order, or referential-integrity rule set built only from the database's declared foreign keys — or, conversely, only from an application/ORM relationship model.

**What goes wrong.** In long-lived systems the two barely overlap: many relationships are enforced only in application code and never declared, and some declared constraints correspond to no live application path. Using either alone gives a load order that misses real parents and a validation set that misses real orphan classes. It also means a column that looks unconstrained may still be a foreign key in every practical sense.

**Fix.** Union both sources and say which is which for each edge. Where an inferred (undeclared) relationship drives a load decision, corroborate it — matching value ranges and cardinalities between the candidate child column and the parent key is cheap and settles most cases — and record the corroboration alongside the edge.

**How to confirm it fires.** Regex precursor: a query over ALL_CONSTRAINTS with constraint_type = 'R' feeding a dependency graph. Semantic confirm: is any application-layer relationship source consulted, or is the graph declared-FK-only?

---

## `deferred-constraints-for-fk-cycles`

**Medium** — Tables in a foreign-key cycle need deferred constraints, not a load order

**Look for.** A load sequence derived from a topological sort of the FK graph, with no handling for tables that are mutually dependent, or an ETL plan that assumes every table has a parent-first order.

**What goes wrong.** A cycle has no topological order. Loading it parent-first is impossible by definition, so whichever table goes first fails ORA-02291 on rows referencing the other — and the usual reaction is to disable the constraint entirely, which loses the check for the whole load rather than deferring it to the end of the transaction.

**Fix.** Identify the cycles up front from the FK graph and declare those constraints DEFERRABLE INITIALLY DEFERRED, committing the cycle's tables together so the check runs once at commit. Keep the list of cycle members in the plan — it is also the list of tables whose rollback must be handled as a unit.

**How to confirm it fires.** Regex precursor: a topological-sort or load-order construction over FK edges. Semantic confirm: does the code detect and handle cycles, or does it assume a DAG? An unhandled cycle path fires the rule.

---

## `export-zero-means-not-captured`

**Medium** — A category reported as zero in a schema export usually means it was not captured

**Look for.** A migration decision justified by an absence in a metadata export — 'this table has no indexes', 'there are no check constraints', 'nothing is unique here' — where the export's own summary shows a count of 0, or a very small count, for that whole category across the entire schema.

**What goes wrong.** Extraction tools omit categories silently: an export that never asked for indexes reports zero indexes, which is indistinguishable in the artifact from a schema that genuinely has none. Downstream this is load-bearing — whether a batched extract range-scans or full-scans, whether an orphan check is cheap or ruinous, whether a column's value set is already declared by a constraint. Deciding 'no unique key exists, so this table cannot be migrated incrementally' on the strength of an uncaptured category wastes real work. Exports also go stale: row counts and even column sets drift between the export date and the load.

**Fix.** Treat a zero total for a whole category as 'not captured' until proven otherwise, and confirm against the live dictionary before building on it. Re-measure row counts and column counts at the point of use rather than quoting the export. Record the export's date next to every figure taken from it.

**How to confirm it fires.** Not a regex rule. Fires in review when a comment or commit message justifies a design choice with the absence of a schema feature. Ask: which artifact says it is absent, what is that artifact's total for the category across the whole schema, and has it been confirmed against the live dictionary?

---

## `make-an-arbitrary-parent-pick-deterministic`

**Medium** — Pick a fallback parent deterministically and cache it; an arbitrary pick breaks idempotence

**Look for.** An 'any row of this parent will do' fallback implemented as the first row the database happens to return, or an ORDER-BY-less query feeding such a fallback; the same query issued per row.

**What goes wrong.** The value is written into a foreign key. An arbitrary-but-varying pick rewrites that column on every incremental run, so loads stop being idempotent, diffs never settle, and comparing two runs tells you nothing. Issuing it per row is separately wasteful: it is a whole-table aggregate that cannot change mid-run, so it costs one round trip per row for a constant.

**Fix.** Pick deterministically — MIN of the key, or an explicit ORDER BY then take the first — and cache it per entity for the run. Where the bookkeeping table is empty because someone else seeded the target (an earlier run whose metadata was cleared, or data the target team loaded), allow an explicit fallback that reads the target table itself, and let that fallback fail soft rather than failing the run.

**How to confirm it fires.** Precursor: grep for a method named first/any/default/fallback that returns an id from a query. Checks: does the query have MIN/ORDER BY? Is the result cached for the run, or re-queried per row? Either failure fires.

---

## `mechanical-schema-prefixing`

**Medium** — Write change and probe scripts unqualified; do not prefix a schema by string substitution

**Look for.** A script generator or edit pass that prepends SCHEMA. to every table reference, or a committed SQL file whose statements are all schema-qualified to one environment.

**What goes wrong.** Two distinct failures. Mechanical prefixing hits inline views and subqueries too, producing FROM SCHEMA.( — ORA-00903 invalid table name — on a scattering of statements. And a hard-coded schema that is wrong for the environment, or a cross-database object reached without its synonym or grant, comes back as ORA-00942 table or view does not exist, which names neither the schema nor the database, so it reads as a broken script rather than a wrong address. If the file is one transaction, statement one taking it down rolls the whole thing back.

**Fix.** Write every statement unqualified and connect as the owning user, so the same file works whatever the schema is called. Keep the one file that must name a schema — the synonym and grant setup — separate and explicitly labelled. Check required grants and synonyms before running anything that depends on them, and report the missing one by name rather than letting ORA-00942 stand as the diagnosis.

**How to confirm it fires.** Regex: an identifier of the form `[A-Z_]{3,}\.` before a table name in generated SQL, especially immediately before `(`. Semantic confirm: is the prefix a hard-coded environment name rather than a parameter? Also flag any cross-schema reference with no accompanying synonym/grant step.

---

## `null-target-hides-crosswalk-gap`

**Medium** — Distinguish 'the source was empty' from 'the crosswalk missed' on a nullable target column

**Look for.** An optional target column fed by a crosswalk, with no rule at all — so a NULL result loads quietly whatever caused it.

**What goes wrong.** A NULL in that column has two very different causes that look identical afterwards. The source genuinely had nothing (legitimate — load NULL), or the source HAD a value and the crosswalk did not cover it (a mapping gap, and writing NULL hides it forever). Making the column strictly mandatory is wrong too: it rejects rows whose source was legitimately empty. Both blunt options lose information.

**Fix.** Declare a conditional requirement: the column is mandatory only when a named SOURCE column actually carried a value. An unresolved value then rejects BY NAME with a message that says the source HAS a value and the crosswalk does not cover it, while an empty source loads as NULL. Choose the watched source column so that 'empty' means exactly 'the source did not say' — derive an explicit flag where a raw code has a meaningful zero/absent value, rather than watching the raw column.

**How to confirm it fires.** Find crosswalk/lookup transforms writing a NULLABLE target column with no conditional-requirement declaration. Fire when the source column's measured population rate is materially above zero — i.e. there is real data that could silently fail to map.

---

## `single-line-ddl-export-parsing`

**Medium** — Do not split a DDL export's column list on newlines

**Look for.** A DDL-parsing script that finds columns by iterating the lines between CREATE TABLE and its closing parenthesis.

**What goes wrong.** Export tools differ: some emit one column per line, some emit the entire column list on a single line. A newline-based parser sees exactly one column per table against the second format and reports a schema that is structurally wrong — quietly, because it still produces a plausible-looking catalog, and everything built on that catalog inherits the error.

**Fix.** Capture the whole parenthesised column list as one string and split it on top-level commas, tracking parenthesis depth so a type like NUMBER(10,2) is not split in half. Sanity-check the result against an independent column count before publishing the parse.

**How to confirm it fires.** Regex precursor: `splitlines()` / `split('\n')` in a parser near `CREATE TABLE`. Semantic confirm: does the code track parenthesis depth when splitting the column list? If it splits on lines or on bare commas, the rule fires.

---

## `start-up-probe-for-invisible-fixes`

**Medium** — A fix that only manifests deep into a run needs a start-up probe that exercises the real path

**Look for.** A correctness fix on a read/write path whose effect is only observable when a rare value arrives — added with no start-up self-check, or with a self-check that hand-builds the expected SQL instead of calling the real read function.

**What goes wrong.** A fix that is only exercised by rare data is invisible until that data arrives, which can be hours and millions of rows into a run. Two different failures then look identical in the log: a stale install (the package version was not bumped, so the upgrade reported 'already satisfied' and kept the old code) and a fix that ships but does not work. A probe that constructs its own expectation rather than calling the production read path is how a broken fix passes its own tests — the second bad build did exactly that.

**Fix.** At start-up, run one probe through the REAL read path against a value that must fail the old code, once per process rather than once per session. Distinguish three outcomes explicitly: the old exception (report a stale install by name), an unconverted/undescribed read (report the mechanism is not in force), and a value coming back at all where it should have been refused (report the writer and parser have drifted apart). Keep the probe's own value out of the end-of-run defect report.

**How to confirm it fires.** when a diff changes a low-level read/write conversion, check for a corresponding start-up verification and read its body: if it builds its own SQL string rather than calling the function the extract calls, flag it as testing something else.

---

## `tolerate-bad-values-on-the-source-side-only`

**Medium** — Defensive value-coercion belongs on the source connection, never on the target or bookkeeping ones

**Look for.** A leniency flag (tolerate bad dates, coerce unparseable values to NULL, swallow conversion errors) applied to the pool configuration of the target database or the run's own metadata/bookkeeping database.

**What goes wrong.** Values read back from the target are values this tool just wrote: they are representable by construction, so re-parsing them is pure cost — and quietly nulling one hides a bug in the tool instead of surfacing a defect in the source data. The same leniency on bookkeeping reads corrupts the run's own audit trail. Leniency is only ever correct where the data is someone else's and already known to be dirty.

**Fix.** Set the tolerance flag per connection profile: true on the source pool, false on target and metadata. Assert that split in a settings test so a later refactor that builds all three pools from one template cannot flip it.

**How to confirm it fires.** in the diff, look for a shared helper that builds several DbConfig/pool objects from one set of defaults. If a tolerance or coercion flag is hoisted into the shared default, flag; the flag must be set per profile and asserted.

---

## `two-sources-into-one-target-column`

**Medium** — Two source columns mapped onto the same target column means one is silently discarded

**Look for.** A column map, mapping sheet, or generated spec in which the same target column name appears as the destination of more than one source column.

**What goes wrong.** Only one write survives — usually whichever the dictionary or generator applies last, which is not a decision anyone made. The load succeeds and one column's data is gone. It is easy to miss because the mapping is read source-first: nobody scans the destination column for duplicates. It is frequently a symptom of a shifted row in a hand-maintained sheet, where a target name was typed into the wrong cell.

**Fix.** Validate that target column names are unique within each table's mapping and fail the build if not. When a duplicate is found, mark the mapping pending rather than picking one — the sheet's rows no longer line up with the target and the correct source is a question, not a guess. Check for cell-shift in the same rows while you are there.

**How to confirm it fires.** Mechanical: collect the destination names in each mapping dict/table in the diff and look for duplicates. Any duplicate fires, no semantic step needed.

---

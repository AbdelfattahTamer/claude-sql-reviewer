# Rule index

One line per rule. Scan this first to pick candidates, then open the category file for the
full entry only on the rules that plausibly fire. Do not load every category file.

| ID | Sev | Category | Look for |
|---|---|---|---|
| `a-cache-in-front-of-a-guarded-lookup-bypasses-the-guard` | C | etl | A read-through cache in front of a repository lookup that RAISES on ambiguity, or a split read/write cache pair where a write updates one store and le |
| `advance-a-watermark-only-after-a-complete-clean-pass` | C | etl | A `set_watermark` / high-water update that is not gated on ALL of: every batch of the table succeeding, every ROW in those batches landing, the run ha |
| `crosswalk-lookup-must-be-scoped-by-source` | C | etl | A lookup of the form WHERE entity = :e AND legacy_key = :k with no source-table predicate, in a pipeline where more than one source feeds the same tar |
| `join-must-not-fan-out-grain` | C | etl | An extract FROM clause replaced by a join — `SOURCE_TABLE JOIN SIDE_TABLE ON ...` — where the joined relation is a bare table rather than an inline vi |
| `never-evict-state-that-is-not-yet-durable` | C | etl | A bound added to a store that also holds mappings written before their transaction commits, with the cap taken straight from configuration and no mini |
| `never-state-an-unmeasured-row-count` | C | etl | A review comment, finding, or document that asserts a volume — 'this affects roughly N rows', 'the table is empty', 'this column is unused' — where N  |
| `overlapping-key-spaces-need-provenance` | C | etl | A child resolving a parent by key alone against a target that more than one source table feeds — no source/provenance argument on the lookup, and no d |
| `polymorphic-key-and-source-must-agree` | C | etl | A pair of derived columns — one `COALESCE(a, b)` / `NVL(a, b)` picking the key, the other `CASE WHEN a IS NOT NULL THEN 'A' ELSE 'B' END` naming which |
| `required-on-a-mostly-null-column` | C | etl | A source column listed as mandatory (`required`, NOT NULL enforcement, or a scope filter) whose real population rate on the full source has never been |
| `schema-export-is-not-the-target` | C | etl | A finding or a code change that settles a target-side question — is this column nullable, how wide is it, does this constraint exist, is this lookup s |
| `second-writer-on-a-shared-target-duplicates` | C | etl | A new spec whose mapping instruction reads as enrichment — 'attach a note to the existing row', 'copy this value onto the record another feed created' |
| `sibling-predicates-must-partition` | C | etl | Two or more specs writing the same target table from the same source table, separated by scope predicates — one `EXISTS(... = v)` and the other `EXIST |
| `the-commit-gap-between-data-and-its-load-record` | C | etl | A batch that commits target rows and THEN writes the 'what landed' record (id map, lineage, audit) to a different database, with that post-commit writ |
| `a-timeout-is-not-a-negative-answer` | H | etl | A probe whose failure path returns the same falsy value as a genuine negative, feeding a message that asserts a fact about the database ('this table h |
| `arm-precedence-for-overlapping-matches` | H | etl | A set of generated specs, one per candidate slot/column, each filtering `driver.match_column = PARENT_TABLE.slot_n`, where nothing prevents one parent |
| `bound-a-per-run-lookup-cache-but-never-below-one-batch` | H | etl | A plain `dict` used as a per-RUN lookup cache in a loop over millions of rows and never cleared; or a single bounded store serving both 'keys this run |
| `bulk-schema-prefix-replace` | H | etl | A SQL script in which a schema name appears before every table (`FROM SCHEMA.TABLE`), applied uniformly — and in the same file, at least one `FROM SCH |
| `carrying-source-pk-into-identity-column` | H | etl | A mapping that writes the source table's primary key into the target's identity/sequence-backed `ID` column, usually justified as 'makes reconciliatio |
| `committed-is-not-loaded` | H | etl | A run report, log assertion, or test that concludes success from a commit message, a batch-committed line, or a zero exit code. |
| `composite-parent-key-resolved-on-one-column` | H | etl | A parent-resolution transform passing one source column, where the parent's identity is only unique within a scope (a branch number unique within a ba |
| `concatenated-composite-key` | H | etl | A key expression of the form `TO_CHAR(col_a) \|\| '\|' \|\| TO_CHAR(col_b)` used to join to, or write into, a single-column key store; the same expression  |
| `crosswalk-keyed-on-environment-local-ids` | H | etl | A hard-coded mapping dict whose KEYS are surrogate ids from a source reference table — `{3: 'MUSLIM_LAW', 301: 'CHRISTIAN_LAW'}` — rather than a value |
| `declare-not-null-columns-required` | H | etl | A mapping that feeds a NOT NULL target column through a transform, lookup or fallback that can produce NULL, with no validation entry listing that col |
| `extract-filter-is-a-silent-loss` | H | etl | A raw scope predicate ANDed into the extract WHERE — an `extract_where`-style clause or an `only_where` filter — used to narrow what migrates. |
| `filter-on-nullable-parent-key-strands-children` | H | etl | An extract-level filter on a parent table — WHERE parent.some_id IS NOT NULL, a status filter, a date window — where child tables resolve their parent |
| `half-of-a-split-never-built` | H | etl | A spec carrying a scope filter on a discriminator column (`WHERE kind = 1`) whose comment refers to 'the other half', 'its sibling', or 'the money/sec |
| `inner-join-silently-filters` | H | etl | `SOURCE_TABLE JOIN SIDE_TABLE ON ...` (no LEFT) in an extract whose purpose is to attach optional side-table columns. |
| `missing-target-column-fails-the-whole-batch` | H | etl | A mapping that names a target column sourced from a design document, a pending schema migration, or a workbook cell, with no check against the deploye |
| `never-cache-a-negative-parent-lookup` | H | etl | `cache[key] = repo.get(key)` written unconditionally, including when the answer is None or an empty result. |
| `nondeterministic-first-row-pick` | H | etl | `... ORDER BY some_column FETCH FIRST 1 ROW ONLY`, `ROWNUM = 1` without an ORDER BY, or `MIN(x) KEEP (...)` ordered by a column that is not unique — u |
| `placeholder-inside-a-unique-key` | H | etl | A mandatory target column with no source, filled with a standing placeholder value, where the column also participates in a unique constraint. |
| `preflight-must-not-raise-and-must-not-cry-wolf` | H | etl | A precondition check that calls out to a service without a try/except, or that compares a prerequisite against CURRENT state only, ignoring that an ea |
| `preflight-run-wide-preconditions` | H | etl | A dependent write (a link/junction table, a child needing a seeded lookup) with no start-of-run check that its parent crosswalk is populated and its r |
| `projection-must-select-every-referenced-name` | H | etl | A transform, filter or callable side-step that reads a column or derived alias which is not visibly added to the SELECT list — or a name that is BOTH  |
| `prove-a-driver-workaround-is-live-at-startup` | H | etl | A workaround for a driver- or version-specific trap with no startup self-check; or a self-check that exercises a hand-written equivalent (a literal TO |
| `reject-never-truncate-or-round` | H | etl | A mapping from a wide source column into a narrower target column (or from an unbounded NUMBER into a bounded one) with no declared limit — and, worse |
| `sample-nullness-is-not-production` | H | etl | A comment justifying a dropped column, a chosen source or a scope decision with '100% NULL', 'empty', or a percentage — where the figure comes from a  |
| `scope-cleanup-by-source-when-several-sources-feed-one-target` | H | etl | A rollback or cleanup `DELETE ... WHERE target_table = :t` with no source narrowing, or an identity-map delete keyed on the target table's name where  |
| `skip-decisions-can-hide-lost-rows` | H | etl | A load path with an INSERT-or-SKIP idempotency check (skip when the identity map already holds the key) and a reconciliation that compares only source |
| `source-code-copied-into-target-lookup-fk` | H | etl | A plain column-to-column mapping where the source is an enum/code column and the target column name ends in `_ID` and is a foreign key to a reference  |
| `source-identifier-copied-not-remapped` | H | etl | A plain column-to-column mapping of an identifier — an audit user id, an owner id, a creator reference — from source to target, where the referenced e |
| `stale-mirror-of-a-dropped-constraint` | H | etl | An in-pipeline uniqueness (or other) check declared to mirror a named target constraint, with no linkage to whether that constraint still exists. |
| `take-the-watermark-from-the-source-clock-before-the-extract` | H | etl | A watermark set from `datetime.now()` on the client, from the TARGET database's clock, or from a timestamp captured after the load finished. |
| `target-scoped-defaults-must-not-leak-to-other-tables` | H | etl | A per-migration mapping of column -> SQL expression (or similar target-shaped declaration) consulted inside a generic insert()/write() helper by colum |
| `two-queries-same-metric-different-method` | H | etl | A pair of views or queries published together as a summary and its detail — one counting with `COUNT(DISTINCT key)`, the other returning `one row per  |
| `unique-key-derived-from-one-source-column` | H | etl | A target UNIQUE constraint over (A, B) where the migration derives BOTH A and B from the same source column — typically A is a copy of a description a |
| `unlabelled-sampled-figure` | H | etl | `SAMPLE(n)` or `SAMPLE BLOCK(n)` in a profiling query — especially one commented in and out; a result column named `row_count` / `total` produced by a |
| `validation-bounds-must-come-from-the-live-catalog` | H | etl | Numeric precision/scale limits or text-width limits baked into shipped code from a DDL export and enforced before the INSERT; a hand-mirrored uniquene |
| `verify-constraint-claims-against-the-catalog` | H | etl | A spec/config field naming a database constraint, index, sequence or column that the code then relies on for correctness — with no test joining it to  |
| `any-row-fallback-always-succeeds` | M | etl | A lookup transform with a catch-all fallback — 'if it returns null, set any value', 'take the first row in the category', 'attach to the first parent' |
| `cache-fill-must-never-fail-the-batch` | M | etl | A warm/prefetch call added to an executor or batch loop without a try/except, or with an except clause that re-raises. |
| `db-enforced-fks-are-not-the-whole-model` | M | etl | A dependency graph, load order, or referential-integrity rule set built only from the database's declared foreign keys — or, conversely, only from an  |
| `deferred-constraints-for-fk-cycles` | M | etl | A load sequence derived from a topological sort of the FK graph, with no handling for tables that are mutually dependent, or an ETL plan that assumes  |
| `export-zero-means-not-captured` | M | etl | A migration decision justified by an absence in a metadata export — 'this table has no indexes', 'there are no check constraints', 'nothing is unique  |
| `make-an-arbitrary-parent-pick-deterministic` | M | etl | An 'any row of this parent will do' fallback implemented as the first row the database happens to return, or an ORDER-BY-less query feeding such a fal |
| `mechanical-schema-prefixing` | M | etl | A script generator or edit pass that prepends SCHEMA. to every table reference, or a committed SQL file whose statements are all schema-qualified to o |
| `null-target-hides-crosswalk-gap` | M | etl | An optional target column fed by a crosswalk, with no rule at all — so a NULL result loads quietly whatever caused it. |
| `single-line-ddl-export-parsing` | M | etl | A DDL-parsing script that finds columns by iterating the lines between CREATE TABLE and its closing parenthesis. |
| `start-up-probe-for-invisible-fixes` | M | etl | A correctness fix on a read/write path whose effect is only observable when a rare value arrives — added with no start-up self-check, or with a self-c |
| `tolerate-bad-values-on-the-source-side-only` | M | etl | A leniency flag (tolerate bad dates, coerce unparseable values to NULL, swallow conversion errors) applied to the pool configuration of the target dat |
| `two-sources-into-one-target-column` | M | etl | A column map, mapping sheet, or generated spec in which the same target column name appears as the destination of more than one source column. |
| `arm-write-timeouts-not-read-timeouts` | C | oracle | A lock/call timeout applied to the source pool, or applied to the primary target while the metadata/audit/bookkeeping connection is left unbounded. |
| `concat-key-null-collapse` | C | oracle | A projection or derived column that builds a composite key or a display value by concatenating two or more columns with `\|\|` and a separator — `TO_CHA |
| `dedupe-shortcut-needs-a-real-constraint-behind-it` | C | oracle | A cap, sampling, TTL or 'saturation' added to an in-process duplicate check, applied unconditionally to every declared unique key. |
| `not-in-over-nullable-subquery` | C | oracle | A predicate of the form `x NOT IN (SELECT some_column FROM SOURCE_TABLE)` where `some_column` has no NOT NULL constraint. Extra-suspicious variants: t |
| `outer-join-plus-filter-without-plus` | C | oracle | Oracle's pre-ANSI outer-join operator `(+)` in a WHERE clause, where the same table also appears in another predicate WITHOUT `(+)` — typically a filt |
| `outer-join-predicate-in-where` | C | oracle | ANSI `LEFT JOIN CHILD_TABLE c ON ...` followed by a WHERE clause referencing `c.some_column` with anything other than `IS NULL` / `IS NOT NULL` used a |
| `partially-null-composite-unique` | C | oracle | A duplicate pre-check (in ETL code or a staging query) that skips the comparison when ANY component of a composite key is NULL — `if any(v is None for |
| `absent-fk-accepts-a-wrong-parent-silently` | H | oracle | A transform that writes a resolved parent id into a *_ID column, with a comment or assumption that a bad value would be caught by the database, and no |
| `all-null-key-cannot-collide` | H | oracle | Dedupe or uniqueness code that builds a key tuple and memoises/compares it without testing whether every component is NULL. |
| `bare-to-number-on-varchar` | H | oracle | `TO_NUMBER(col)` or `TO_DATE(col, ...)` in a projection or predicate where `col` is declared VARCHAR2/NVARCHAR2 in the source. |
| `char-vs-byte-length-semantics` | H | oracle | A VARCHAR2(n) column with no CHAR qualifier that is intended to hold non-Latin text, especially when sibling name/description columns on the same tabl |
| `clob-emptiness-test` | H | oracle | A scope predicate or content test applying `IS NOT NULL` or `TRIM(col) IS NOT NULL` to a column whose declared type is CLOB/NCLOB/BLOB. |
| `correlated-subquery-unqualified-column` | H | oracle | A subquery whose WHERE clause mixes qualified and unqualified column references — `WHERE some_flag IS NULL AND c.parent_id = p.id` — particularly when |
| `count-distinct-drops-nulls` | H | oracle | `COUNT(DISTINCT some_column)` used as a row or key count, especially in profiling, reconciliation and gap queries; a ratio built as `COUNT(*) / COUNT( |
| `db-side-default-reads-its-own-target` | H | oracle | A SQL expression injected into an INSERT's VALUES list that selects from the table being inserted into — a running counter such as `SELECT NVL(MAX(seq |
| `dormant-conditional-unique-index` | H | oracle | A change that starts populating a target column which was NULL on every migrated row, on a table carrying a unique index built over CASE expressions o |
| `empty-string-is-null-in-oracle` | H | oracle | A default, COALESCE/NVL fallback, or transform that supplies '' (or a Python "") for a NOT NULL character column that the source cannot fill. |
| `fetch-one-more-row-than-you-expect` | H | oracle | A 'resolve one id' helper ending in `cur.fetchone()` where the WHERE clause is not provably unique — for example a lookup on (entity, key) when severa |
| `identifiers-cannot-be-bound-so-validate-them` | H | oracle | An f-string putting a schema, table or column name into `ALTER SESSION`, DDL, or a FROM clause; or — the mirror image — a literal VALUE interpolated i |
| `identity-generator-behind-seeded-data` | H | oracle | A seed or data-fix script that inserts into an identity-column table with an explicit ID (often from MAX(ID)+1), with no follow-up that advances the g |
| `in-list-cap-and-never-inline-the-values` | H | oracle | `"WHERE col IN (" + ",".join(values) + ")"`, an IN list built from an unbounded key list, or a bulk close/update `WHERE id IN (...)` fed straight from |
| `inlined-literal-must-be-whitelisted-and-escaped` | H | oracle | f-string or `%`/`.format()` interpolation of a Python value into a SQL predicate or projection, with no type check on the value and no quote escaping  |
| `merge-on-clause-null-never-matches` | H | oracle | `MERGE ... ON (d.a = s.a AND d.b = s.b)` where any of those columns is nullable — typically an optional provenance/source column added by a later sche |
| `mod-with-negative-dividend` | H | oracle | `MOD(x, n)` where `x` is derived from date arithmetic (`TRUNC(some_date) - DATE '...'`), a difference of two columns, or any expression that is not pr |
| `negated-predicate-three-valued-logic` | H | oracle | A reconciliation or bucketing query written as `COUNT(CASE WHEN <p> THEN 1 END)` and `COUNT(CASE WHEN NOT (<p>) THEN 1 END)`, where `<p>` contains a f |
| `no-order-by-means-no-row-position` | H | oracle | Resume or slice logic expressed as 'restart at row N', an OFFSET, or a ROWNUM range over an extract that carries no ORDER BY. Also: an operator-suppli |
| `not-equal-drops-nulls` | H | oracle | A WHERE clause written as the complement of a value test — `some_column <> 7`, `some_column NOT IN (1,2)` — especially when a sibling query claims the |
| `scalar-subquery-multi-row-risk` | H | oracle | A scalar subquery in a SELECT list, a SET clause or a comparison whose WHERE does not fully match a declared primary or unique key of the subqueried t |
| `signed-year-and-precision-in-the-date-format-mask` | H | oracle | A `TO_CHAR(date_col, 'YYYY-MM-DD HH24:MI:SS')` or an `NLS_*_FORMAT` mask starting with `YYYY`; a TIMESTAMP rendered without `.FF`; a TIMESTAMP WITH [L |
| `structural-lint-for-hand-run-sql-scripts` | H | oracle | New or edited .sql files under the package (schema upgrades, seeds, DBA scripts) with no test that opens them. |
| `sub-select-inside-insert-must-be-schema-qualified` | H | oracle | An INSERT/UPDATE built with a schema prefix for the target but a BARE table name inside an embedded SELECT — e.g. INSERT INTO {schema}.TARGET_TABLE .. |
| `to-char-number-key-depends-on-nls` | H | oracle | `TO_CHAR(numeric_column)` with no second argument, used to build a key, a join value or anything compared against previously stored text. Also `TO_CHA |
| `unaliased-join-column-collision` | H | oracle | A joined inline view (or a second table in a FROM clause) exposing a column whose name also exists on the driver table — audit columns, `NOTES`, `STAT |
| `union-branches-align-by-position` | H | oracle | A multi-branch UNION/UNION ALL where the branches read different tables and the select lists are long. Red flags inside a branch: a bare `NULL` or emp |
| `union-where-union-all-is-meant` | H | oracle | A bare `UNION` between branches that are disjoint by construction — each branch reads a different source table, or each branch selects a distinct lite |
| `unique-key-missing-a-scoping-column` | H | oracle | A UNIQUE constraint whose column list contains the descriptive columns but not the owning parent's id — e.g. UNIQUE (type_id, from_date, to_date) on a |
| `unnamed-constraints-missing-from-exports` | H | oracle | A change script, a mapping, or an analysis that lists a table's unique keys from a committed DDL export file and then acts on that list. |
| `user-catalog-views-ignore-current-schema` | H | oracle | A catalog probe against `USER_TAB_COLUMNS`, `USER_CONSTRAINTS`, `USER_CONS_COLUMNS` or `USER_TAB_IDENTITY_COLS` in a codebase whose sessions run `ALTE |
| `varchar2-budgets-are-bytes-not-characters` | H | oracle | `str(value)[:N]` or `value[:400]` used to make a value fit a `VARCHAR2(N)` column, in a codebase that stores non-ASCII text. |
| `whitespace-only-passes-not-null` | H | oracle | A load-scope predicate of the form `some_column IS NOT NULL` used to mean 'this row has content', or a text column copied straight across with no blan |
| `aggregate-over-empty-set-returns-null` | M | oracle | `SUM(...)`, `MAX(...)` or `AVG(...)` in a query whose result is concatenated into a report line, subtracted from another number, or compared against a |
| `an-anti-join-key-expression-must-mirror-the-writer-exactly` | M | oracle | A `NOT EXISTS (... WHERE stored_key = TO_CHAR(col) ...)`, or any join to a load-record table on a stringified key, written by hand next to a client-si |
| `bind-a-key-as-the-columns-own-type` | M | oracle | A retry/replay path that reads keys back out of text storage and binds them all as strings, or blanket-`int()`s them before binding. |
| `conditional-aggregate-else-clause` | M | oracle | `COUNT(CASE WHEN cond THEN 1 ELSE 0 END)` — a CASE inside COUNT that has an ELSE branch returning a non-NULL value. The mirror image: `SUM(CASE WHEN c |
| `diagnostics-must-not-need-privileges-or-attributes-you-lack` | M | oracle | Engine code querying v$session / v$transaction (or any V$ view) to diagnose blocking, or setting optional connection attributes without tolerating fai |
| `dictionary-query-owner-and-current-schema` | M | oracle | A join between two `ALL_*` catalog views on name alone (`ON c.index_name = i.index_name`) with no owner predicate; a guarded-DDL block whose existence |
| `divide-without-nullif-guard` | M | oracle | A `/` in a select list where the denominator is an aggregate or a column rather than a literal: `COUNT(*) / COUNT(DISTINCT some_key)`, `SUM(a) / SUM(b |
| `drop-index-backing-a-constraint` | M | oracle | A DROP INDEX statement on an index whose name or columns match a unique/primary constraint, or an ALTER TABLE ... DROP CONSTRAINT / DROP UNIQUE with n |
| `filter-generated-not-null-check-constraints` | M | oracle | A query over ALL_CONSTRAINTS / USER_CONSTRAINTS with constraint_type = 'C' feeding a rule generator, a check-constraint inventory, or a count of busin |
| `implicit-number-to-char-nls` | M | oracle | A numeric source column mapped straight into a VARCHAR2 target, or compared/matched against a text lookup key, with no TO_CHAR in the SQL and no expli |
| `inline-view-rewrite-preconditions` | M | oracle | Code that wraps an arbitrary user-supplied statement as SELECT ... FROM (<original>) without first checking the cursor description for duplicate colum |
| `listagg-without-on-overflow` | M | oracle | `LISTAGG(some_column, ',') WITHIN GROUP (ORDER BY ...)` with no `ON OVERFLOW TRUNCATE` clause, especially when aggregating column names, ids or free t |
| `number-precision-caps-the-value` | M | oracle | A target column declared NUMBER(2,0) / NUMBER(3,0) / NUMBER(5,2) receiving a source value with no comparable bound, or a scale smaller than the source |
| `rownum-with-order-by-in-same-block` | M | oracle | `WHERE ROWNUM <= n` and `ORDER BY` in the same query block. Correct-but-fragile variant: `SELECT ... FROM (SELECT ... ORDER BY ...) WHERE ROWNUM <= n` |
| `scalar-subquery-beside-aggregates` | M | oracle | A single SELECT that mixes aggregate expressions (COUNT/SUM/MIN) with a bare scalar subquery or a plain column reference, and has no GROUP BY. |
| `search-condition-is-a-long-column` | M | oracle | A catalog query selecting SEARCH_CONDITION from ALL_CONSTRAINTS/USER_CONSTRAINTS, typically alongside code that works around LONG fetch semantics. |
| `varchar2-to-clob-needs-replacement` | M | oracle | A migration script containing ALTER TABLE ... MODIFY (some_column CLOB) where the column is currently VARCHAR2/NVARCHAR2. |
| `user-tab-columns-vs-user-tab-cols` | L | oracle | A catalog query against USER_TAB_COLS / ALL_TAB_COLS that does not reference HIDDEN_COLUMN or VIRTUAL_COLUMN. |
| `bound-every-control-plane-round-trip` | C | python | A catalog probe, clock read, capability check or watermark query issued on a connection pool that is deliberately configured with no call timeout (bec |
| `date-conversion-must-happen-in-the-statement` | C | python | A diff that installs `cursor.outputtypehandler` / `connection.outputtypehandler` returning `cursor.var(oracledb.DB_TYPE_VARCHAR, ...)` for a DATE/TIME |
| `every-bind-must-appear-in-the-statement` | C | python | Statement text assembled conditionally (optional columns, computed default expressions, dynamic predicates) while the parameter dict is built separate |
| `executemany-over-a-merge-must-not-contain-duplicate-keys` | C | python | `cursor.executemany(merge_sql, rows)` where two parameter sets in the array can share the statement's ON-clause key — aliases publishing a second iden |
| `lob-locator-crosses-connections` | C | python | A pipeline that SELECTs a CLOB/BLOB on one connection and binds the fetched value into an INSERT/UPDATE on a different connection, with no explicit re |
| `no-double-date-conversion-in-hand-written-sql` | C | python | A diff adds or edits an extract/join/derived-column expression containing TO_CHAR(some_column, '<format model naming a date part>') — YYYY, SYYYY, MM, |
| `number-scale-decides-the-python-type` | C | python | A cache key, dedupe set, id-map key or cross-table join built with `str(value)` where the value came from an Oracle NUMBER column. Tell-tale: two spel |
| `a-per-call-timeout-leaks-onto-a-pooled-session` | H | python | `conn.call_timeout = X` armed on a borrowed pooled connection and restored in a `finally`; a re-assert of the pool's configured ceiling guarded by `if |
| `an-all-null-bind-has-no-type-to-infer` | H | python | `executemany` with a whole batch passed as one array, or with a bind whose value is `None` in every row of the chunk — an optional provenance column,  |
| `batcherrors-hands-back-failures-nobody-reads` | H | python | `cursor.executemany(sql, rows, batcherrors=True)` with no `cursor.getbatcherrors()` call anywhere after it. |
| `number-fetched-as-float-breaks-text-match` | H | python | A value read from a NUMBER column (or from EXTRACT(YEAR FROM ...), which is also numeric) used as a dictionary key, a cache key, or a comparison again |
| `pool-acquire-and-connect-wait-forever-by-default` | H | python | `oracledb.create_pool(user=..., password=..., dsn=..., min=..., max=...)` with no `getmode`/`wait_timeout`, no `tcp_connect_timeout` and no `expire_ti |
| `restore-session-settings-after-arming-them` | H | python | Code that sets conn.call_timeout (or any per-session attribute) around one call, without a try/finally, or that restores by assigning a hard-coded 0,  |
| `roll-a-failed-array-back-before-replaying-it` | H | python | An `except` around `executemany` that immediately retries the rows one at a time, or re-raises to a caller that will, without `connection.rollback()`  |
| `timeout-error-must-abort-the-batch` | H | python | A broad per-row except that records the row as a defect and continues, with no special case for the driver's timeout error (DPY-4024) or for a lost co |
| `zero-means-forever-so-never-clamp-a-negative-timeout` | H | python | `max(0, int(os.environ[...]))`, `abs(seconds)`, or a `try/except ValueError: return 0` around a timeout read from configuration. |
| `a-pool-session-callback-runs-inside-acquire` | M | python | `create_pool(..., session_callback=fn)` where `fn` runs ALTER SESSIONs, a self-check, or any other round trip. |
| `execute-prefetches-use-parse-to-describe` | M | python | Code that runs a query purely to learn its shape — reading cursor.description, building a column list, validating that a statement is legal — using ex |
| `machine-rewriting-a-query-into-an-inline-view` | M | python | Code that wraps caller-supplied SQL as `SELECT <projection> FROM (<original>)` to add casts, conversions or a row limit — especially when the projecti |
| `returning-clause-must-survive-statement-rewrites` | M | python | A diff that rebuilds INSERT text (adding computed columns, reordering, handling the empty-column case) near code that reads a generated key back. |
| `thin-mode-requires-easy-connect` | M | python | A DSN literal or config value of the form host:port:SID, or a tnsnames alias assumed to work, in code running python-oracledb without an Instant Clien |
| `correlated-subquery-unindexed-key` | C | performance | A correlated scalar subquery in the projection of a large-table extract, correlating on a column of the side table that has no index — often a foreign |
| `unbounded-in-run-dedupe-memo` | C | performance | An in-process `set`/`dict` of seen key tuples, populated once per accepted row and never cleared, on a migration whose source is a multi-million-row t |
| `arraysize-must-follow-the-batch-size` | H | performance | A streaming read of a large table where the cursor's `arraysize`/`prefetchrows` is left at the driver default while the consumer processes in batches  |
| `bound-the-small-probes-never-the-bulk-extract` | H | performance | Either extreme: a blanket statement timeout applied to every pool including the source, or no timeout anywhere. Also a catalog probe, a clock read or  |
| `bounded-cache-must-be-true-lru` | H | performance | An OrderedDict/dict used as an LRU where the read path does `store[key]` with no move_to_end, or the write path does `store[key] = value` for a key th |
| `correlated-count-star-as-existence-test` | H | performance | A predicate of the form `(SELECT COUNT(*) FROM SOURCE_TABLE s WHERE s.key = outer.key) = 1` (or `> 0`, `<= 1`) used as a scope filter in a WHERE claus |
| `correlated-exists-against-a-large-queue-table` | H | performance | WHERE EXISTS (SELECT 1 FROM <large log/queue/mapping table> WHERE ... = outer.col) in a diagnostic or reconciliation query. |
| `function-wrapped-join-or-filter-column` | H | performance | `TO_CHAR(col)`, `NVL(col, 0)`, `TRUNC(col)`, `UPPER(col)`, `SUBSTR(col, ...)` on the COLUMN side of a join condition or a WHERE predicate. The tell in |
| `one-commit-per-row-of-bookkeeping` | H | performance | A helper that acquires a connection, executes one statement and commits, called once per processed row — audit trails, id maps, lineage, progress coun |
| `per-row-lookups-on-a-unique-key-can-never-hit-cache` | H | performance | A per-row `resolve(key)` / `exists(key)` against another database inside the row loop, where the key is the row's own natural key or a one-to-one pare |
| `scalar-subquery-in-select-list` | H | performance | A parenthesised `SELECT` sitting in the select list of the outer query, correlated to the outer table: `SELECT p.id, (SELECT COUNT(*) FROM CHILD_TABLE |
| `split-write-and-read-caches` | H | performance | A single bounded store holding both freshly minted mappings (written once per inserted row, mostly never read back) and resolved parent lookups (read  |
| `unindexed-fk-turns-a-lookup-into-a-full-scan` | H | performance | A correlated subquery or per-row lookup from a large parent into a child table on the child's FK column, especially a 'latest per parent' or 'last pay |
| `union-all-view-needs-branch-predicate` | H | performance | A view whose body is a long chain of `UNION ALL` branches, each reading a different source table and selecting literal discriminator columns (`'module |
| `distinct-hiding-join-fan-out` | M | performance | `DISTINCT` or `COUNT(DISTINCT ...)` introduced in the same change that adds a join to a table with no unique constraint on the join column; a scalar s |
| `dont-pay-for-an-answer-you-discard` | M | performance | A capability/state probe called unconditionally at the top of a loop, with the flag that overrides it consulted a few lines later. |
| `keyset-pagination-not-offset` | M | performance | OFFSET :n ROWS FETCH NEXT ... or a ROWNUM BETWEEN range used to batch a large extract, or a FETCH FIRST with no ORDER BY at all. |
| `memory-budget-must-be-run-wide` | M | performance | A per-instance cap (self._max = N) on a structure whose owning objects are all constructed up front and held for the whole run. |
| `prefetch-key-must-match-lookup-key` | M | performance | A cache is filled with key (entity, key, source) but read with (entity, key, None) — or any diff that changes the arity of a lookup key on one side on |
| `distinct-inside-union-branch` | L | performance | `SELECT DISTINCT` appearing in one or more branches of a statement whose branches are combined with plain `UNION`. Related: `SELECT DISTINCT` in the s |
| `a-check-that-can-silently-check-nothing` | H | style | A validator that iterates a collection obtained by getattr, a glob, or an attribute that might be a bound method rather than the declared data; a para |
| `check-the-framework-before-hardening-against-it` | H | style | A local workaround for a known driver or platform quirk — converting values to text, coercing types, catching a specific error — added inside one modu |
| `concatenated-sql-defeats-a-single-literal-regex` | H | style | A statement assembled from adjacent string literals or f-string parts, where each individual part is not a valid or even recognisable statement — a fr |
| `spec-hunk-unreviewable-without-its-header` | H | style | A hunk in a large declarative migration file that changes a column mapping or a SQL expression, where the hunk window contains no `source_table` / `ta |
| `split-constant-duplicated-across-modules` | H | style | A magic value re-declared in a second module — a type code, a discriminator, a threshold — that also appears in a sibling module's filter for the same |
| `sql-field-holds-a-name-not-a-string` | H | style | A SQL-carrying field assigned a bare identifier, `CONST.format(...)`, a `**SPREAD` of a shared dict, or a helper call, rather than a string literal —  |
| `sql-lint-hardcodes-its-field-list` | H | style | A repo-wide SQL guard (a test or lint) walks every migration spec and collects "all the SQL" from a hand-written list of two or three field names, whi |
| `test-the-real-path-not-an-equivalent` | H | style | A start-up probe, smoke check or regression test that reconstructs the behaviour it is verifying — writing its own version of the query, the conversio |
| `an-optimisation-hook-with-no-call-site` | M | style | A `prefetch()` / `warm()` / `preload()` method whose docstring says the caller 'may' invoke it, with no call site anywhere in the tree; or a hook docu |
| `diagnostics-must-never-cost-a-row` | M | style | A log/notice call added inside a transform or per-row function that is contractually non-raising, with no guard — or a degradation notice emitted per  |
| `diff-text-must-be-read-as-utf8` | M | style | A collector that shells out to `git diff` / `git show` and reads the result with the platform default encoding — `subprocess.run(..., text=True)` with |
| `fragment-boundary-whitespace` | M | style | A multi-line SQL fragment built from adjacent Python string literals where a line does not end in a space and the next does not begin with one — or wh |
| `generated-sql-edited-by-hand` | M | style | A diff that changes a `.sql` file whose header says it was generated by a script, or whose body is hundreds of near-identical blocks, without a matchi |
| `know-which-schema-facts-are-read-at-runtime` | M | style | A change document or commit that applies a DDL change and declares it complete, without saying whether the pipeline picks the change up on its own. |
| `malformed-configuration-must-not-silently-default` | M | style | A knob parsed with a bare try/except returning a default, or normalised with max(0, value), or accepted as a blank/whitespace string, when the value c |
| `module-level-state-needs-a-complete-reset-fixture` | M | style | An autouse fixture that clears a cache dict but leaves a companion flag, counter or shared budget object untouched — or process-global state with no r |
| `prose-field-beside-a-sql-field` | M | style | The same object declares a free-text notes/rationale/blocker field and a SQL-expression field, and the notes routinely quote SQL back at the reader (" |
| `raw-sql-fragment-unvalidated` | M | style | A hand-written WHERE predicate, FROM clause or VALUES expression passed through as a raw string, where the declarative filter mechanism could have exp |
| `received-ddl-export-reviewed-as-authored-sql` | M | style | A very large `.sql` file under a reference or documentation path, headed by a tool-generated banner, replaced wholesale in one commit (one file added, |
| `script-has-no-error-stop` | M | style | A `.sql` file meant to be run with `@script` that issues CREATE/ALTER/GRANT statements and contains no `WHENEVER SQLERROR EXIT FAILURE` and no `WHENEV |
| `sql-emitted-by-a-helper-function` | M | style | A SQL-expression field whose value is a call to a module-local helper that returns a string or a dict of SQL — `derived_columns=_audit_expressions("SO |
| `sql-lint-must-ignore-comments-and-literals` | M | style | A regex-based SQL/PL-SQL check counting BEGIN/END/IF against raw file text. |
| `sqlplus-substitution-variables` | M | style | A `.sql` script containing `&` inside a string literal, a comment or a URL, with no `SET DEFINE OFF`. Also: `&&var` used as a schema prefix, and the ` |
| `uppercase-prose-matches-uppercase-sql-regexes` | M | style | A SQL detector anchored on `FROM <UPPERCASE_IDENT>` or `JOIN <UPPERCASE_IDENT>`, applied to a codebase whose comments and notes shout in capitals for  |
| `a-closed-enumeration-in-a-comment-goes-stale` | L | style | A comment or docstring that enumerates the call sites of a method and asserts the list is complete — 'called from exactly two places', 'and nothing el |
| `config-knob-renames-must-not-fail-silently` | L | style | A new environment variable whose prefix differs from the ones already in the runbooks, or a rename that leaves the old name quietly working. |
| `implicit-comma-joins` | L | style | A FROM clause listing three or more tables separated by commas, with all join conditions mixed into one WHERE clause alongside row filters. Usually ap |
| `repeated-case-expression-in-group-by` | L | style | The same multi-branch `CASE` expression written out twice — once in the select list, once in the GROUP BY — particularly when it spans several lines,  |
| `sql-constant-name-is-not-a-type` | L | style | A collector that decides which module-level constants hold SQL from a name suffix — `_SQL`, `_QUERY`, `_WHERE`, `_STMT`. |
| `test-expectation-sql-is-a-separate-surface` | L | style | SQL substrings inside assertion strings and fixtures in test files — `assert "FROM TARGET_TABLE alias" in sql`, `("sql", "SELECT COUNT(*) FROM some_di |

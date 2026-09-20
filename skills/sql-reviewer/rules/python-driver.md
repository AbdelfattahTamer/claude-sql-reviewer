# Python driver

Traps at the python-oracledb boundary: type conversion, LOBs, binds, pooling, timeouts.

21 rules. Severity: Critical 7, High 9, Medium 5.

---

## `bound-every-control-plane-round-trip`

**Critical** — Bound the small control-plane queries, and arm the bound at acquire — not inside the caller's block

**Look for.** A catalog probe, clock read, capability check or watermark query issued on a connection pool that is deliberately configured with no call timeout (because long extracts are legitimate), with no per-call ceiling.

**What goes wrong.** A driver pool with no call_timeout, no expire_time and a default getmode that waits forever gives a stuck small query no way to end: no error is raised, no retry fires, no rows move, and the run simply stops. One such discarded probe blocked for 46 hours with nothing in the log. Fixing one call site only promotes the next one — removing the probe from the front of the path left the clock read there, and replay produced an identical multi-day stall with a different heartbeat string. The bound therefore belongs to the OPERATION class, not to one call. It must also be armed BEFORE the session-setup statements run: a ceiling armed inside the caller's `with` body covers the caller's own statement and nothing else, while the evidence from a stall cannot even tell you whether acquire, the session callback or the query was the part that stuck.

**Fix.** Let the pool take a per-acquisition ceiling and arm it immediately after acquire, before any session setup; apply it to EVERY small round trip of that class (probe, clock, catalog lookups on retry paths). Leave the extract path unbounded. Keep the ceiling in one knob and pass the knob — never a pasted constant — so the tests cannot pin a value the code no longer uses. Report a timeout in the heartbeat with a phase string that distinguishes acquiring a session from using it.

**How to confirm it fires.** grep the diff for small SELECTs against catalog views, SYSTIMESTAMP/DUAL or watermark tables on the read pool. Check whether the connection is obtained with a timeout argument. Also grep the pool config for call_timeout=0 / no expire_time — that is what makes these unbounded.

---

## `date-conversion-must-happen-in-the-statement`

**Critical** — Convert out-of-range DATE columns with TO_CHAR in the statement, not with an output type handler

**Look for.** A diff that installs `cursor.outputtypehandler` / `connection.outputtypehandler` returning `cursor.var(oracledb.DB_TYPE_VARCHAR, ...)` for a DATE/TIMESTAMP column, or that sets a session `NLS_DATE_FORMAT` and then expects fetched dates to arrive as strings. Equally: any bulk extract that selects raw DATE columns from an old system with no TO_CHAR and no tolerance for unrepresentable values.

**What goes wrong.** In python-oracledb THIN mode a fetch-type change is applied CLIENT-side: the driver decodes the DATE bytes into a Python `datetime` first and only then converts to the requested type. Oracle DATE spans 4712 BC to 9999 AD while Python `datetime` spans 1 AD to 9999 AD, so a year <= 0 raises `ValueError: year -2 is out of range` while the row is still being BUILT, inside the fetch, before any application code sees it. The session NLS format is ignored entirely on that path. One unusable value in one column of one row therefore kills the whole cursor — the table dies part-way through a multi-million-row read, and re-running re-reads the same rows and dies again at the same place.

**Fix.** Make the SERVER do the conversion, inside the statement: describe the query, then project `TO_CHAR("COL", 'SYYYY-MM-DD HH24:MI:SS') AS "COL"` over it and parse the text in Python. Map values Python cannot hold to NULL, count them per column, and report the count once at the end of the run (never once per row) so the loss is visible and the affected rows reject by name if the target column is NOT NULL. Apply it to SOURCE reads only — values this tool just wrote are representable by construction, and quietly nulling a target value would hide a bug rather than a data defect.

**How to confirm it fires.** Cheap precursor: grep the hunk for `outputtypehandler`, `DB_TYPE_VARCHAR`, `defaults.fetch_decimals`, `NLS_DATE_FORMAT`, `NLS_TIMESTAMP_FORMAT`. Confirm semantically: is a DATE/TIMESTAMP column being fetched raw (no `TO_CHAR(` in the SELECT text) while the code claims to be formatting, tolerating or string-ifying dates? If the conversion is not lexically inside the SQL, the rule fires. Also fires when a session NLS setting is the only thing standing between the run and an out-of-range date.

---

## `every-bind-must-appear-in-the-statement`

**Critical** — Never send a bind that the final statement text does not name

**Look for.** Statement text assembled conditionally (optional columns, computed default expressions, dynamic predicates) while the parameter dict is built separately — or an expression fragment that references :some_column which is only present in the bind dict when that column is non-NULL.

**What goes wrong.** python-oracledb requires every named parameter to appear as a placeholder in the statement; a missing one is DPY-4008 / ORA-01008 'not all variables bound'. Crucially this fails the STATEMENT, so with array or batched execution one malformed row kills the entire batch rather than being isolated as a single defect. The usual trigger: NULL columns are omitted from the bind set, so a default expression that reads another column's bind silently becomes unbindable exactly on the rows where that column is NULL — a minority of rows, discovered on a server.

**Fix.** Build text and binds from one pass. Before emitting an expression that names binds, verify every bind it references is present in the row's parameter set; if any is missing, DROP the expression (the row keeps today's NULL and its neighbours still load) rather than emitting an unbindable statement. Assert the invariant in the test double: scan the SQL for :NAME placeholders and fail if any sent parameter is absent.

**How to confirm it fires.** grep the diff for statement fragments containing ':' placeholders that are appended conditionally. Confirm a guard exists comparing the fragment's referenced bind names against the row's non-NULL key set. Also flag any test double whose execute() accepts params without checking them — that is where this defect hides.

---

## `executemany-over-a-merge-must-not-contain-duplicate-keys`

**Critical** — Deduplicate an array before executemany over a MERGE, keeping the value sequential calls would leave

**Look for.** `cursor.executemany(merge_sql, rows)` where two parameter sets in the array can share the statement's ON-clause key — aliases publishing a second identity for one row, a declared dedupe key, or a post-step fan-out that emits several records per source row.

**What goes wrong.** `executemany` is not a set-based statement: the driver runs the statement once per parameter set, so the second iteration's MERGE 'ought to' see the first's uncommitted row and take the MATCHED branch. 'Ought to' is an assumption about how one driver sequences one call — it is not contractual, no test double can prove it, and what it decides is which value survives. If the iteration does not see it, you get a unique-constraint error against the array instead. On a table that decides identity resolution, the wrong survivor is a wrong foreign key that nothing later notices and nothing can put back, because the pipeline only INSERTs.

**Fix.** Collapse duplicates by the ON-clause key BEFORE building the array, and pick the survivor that matches what sequential single-row calls would leave — decided by the statement's own WHEN clauses, not by taste: `keep last` when it has `WHEN MATCHED THEN UPDATE` of the value column, `keep first` when it has only `WHEN NOT MATCHED THEN INSERT`. Derive the dedupe key from the same probe that chooses the statement shape, so the two can never disagree; preserve first-appearance order so the array still reaches the database in the order the batch produced it.

**How to confirm it fires.** Precursor: grep for `executemany` and check whether the statement text contains `MERGE`. Semantic check: can the caller's row list contain two entries with the same ON-clause key? Look for aliasing, fan-out loops, or a comment enumerating call sites. If there is no explicit dedupe step between list construction and `executemany`, fire — and separately check that the chosen keep-policy matches the statement's WHEN clauses.

---

## `lob-locator-crosses-connections`

**Critical** — Never pass a LOB locator from the connection that fetched it to another connection

**Look for.** A pipeline that SELECTs a CLOB/BLOB on one connection and binds the fetched value into an INSERT/UPDATE on a different connection, with no explicit read of the LOB in between and no fetch_lobs=False anywhere.

**What goes wrong.** A LOB value fetched by default arrives as a locator, which is meaningful only inside the session that produced it. Hand it to a second session and that session reads the locator bytes as data, so the failure surfaces as something that mentions no LOB at all — ORA-01410 invalid ROWID, or ORA-00942 table or view does not exist. Nobody looks for a LOB problem when the error says the table is missing, and the bug is latent everywhere: it only shows on the first mapping that gets far enough to actually INSERT one.

**Fix.** Set oracledb.defaults.fetch_lobs = False at connection-setup time so CLOB arrives as str and BLOB as bytes at fetch time, and add a second line of defence at the write side that calls .read() on anything still a LOB object before binding it. Either one alone leaves the trap open for code paths that build their own connections.

**How to confirm it fires.** Regex precursor: `oracledb.connect` appearing more than once in a module, or a value flowing from one cursor's fetch into another cursor's execute/executemany. Semantic confirm: is any column in that flow a CLOB/BLOB/NCLOB in the source catalog, and is `fetch_lobs = False` absent and no `.read()` applied? Misleading ORA-01410 / ORA-00942 in an incident log alongside LOB columns is corroboration.

---

## `no-double-date-conversion-in-hand-written-sql`

**Critical** — Never TO_CHAR a DATE column in hand-written extract SQL when the framework converts dates itself

**Look for.** A diff adds or edits an extract/join/derived-column expression containing TO_CHAR(some_column, '<format model naming a date part>') — YYYY, SYYYY, MM, DD, HH24, MI, SS, MON, RR — in a migration spec or any layer above the connection/extract layer.

**What goes wrong.** An extract layer that tolerates out-of-range Oracle dates works by rewriting the statement to convert every DATE column to text and then parsing the text back into a datetime. It decides what to wrap and what to parse from the cursor's OWN description. A column the spec already turned into text describes as VARCHAR2, so it is never wrapped and — the fatal half — never parsed back. The raw string (with the leading blank sign slot SYYYY prints for AD years) reaches an INSERT into a DATE target column, Oracle falls back to NLS_DATE_FORMAT, and every row fails ORA-01861 'literal does not match format string'. The sibling column of the same table, mapped plainly with no TO_CHAR, round-trips correctly — so the reviewer sees one broken column beside identical working ones, and the spec author's reasoning for adding TO_CHAR was itself correct, just applied at the wrong layer.

**Fix.** Declare the expression as a DATE and let the extract layer do the conversion: the bare column, or CAST(expr AS DATE), or COALESCE over DATE expressions. If a text form is genuinely needed downstream, convert in the transform on the Python value, not in the source SQL.

**How to confirm it fires.** grep the diff for TO_CHAR in any string that ends up in source SQL, then confirm the second argument is a quoted format model containing YYYY|MM|DD|HH24|MI|SS|MON|RR. A plain TO_CHAR(numeric_col) with NO format model is a legitimate key-building idiom and must NOT fire; neither should TO_CHAR(x, '999') or any purely numeric model. Confirm the target column is a DATE/TIMESTAMP and that the framework has a date-wrapping step keyed on cursor description.

---

## `number-scale-decides-the-python-type`

**Critical** — Canonicalise NUMBER-derived values before using them as a cross-table key

**Look for.** A cache key, dedupe set, id-map key or cross-table join built with `str(value)` where the value came from an Oracle NUMBER column. Tell-tale: two spellings of the same id in stored data — '3' from one table and '3.0' from another.

**What goes wrong.** Oracle NUMBER maps to two different Python types depending on how the COLUMN was declared, and no output type handler is installed by default: `NUMBER(8,0)` (scale 0) arrives as `int` and stringifies to '3', while a bare `NUMBER` (scale -127) arrives as `float` and stringifies to '3.0'. Both hold the same id. A child whose foreign-key column is a bare NUMBER therefore cannot resolve a parent whose primary key is NUMBER(p,0): the lookup misses, the FK silently becomes NULL, and the row either loads unscoped or dies on a NOT NULL violation far from the cause. This crosses in practice wherever a schema mixes the two declarations, which large legacy schemas always do.

**Fix.** Route every scalar through one canonicaliser before it becomes a key: integral `float`/`Decimal` written without the decimal part (3.0 -> '3'), genuinely fractional values left alone (3.5 stays '3.5'), `bool` handled first because it is an `int` subclass. Use the same function for composite keys (a documented separator) and for any hand-written seed values, so an obvious integer literal matches a column that arrives as a float. When you introduce it, normalise keys already stored in the old form in one UPDATE — otherwise existing rows stop resolving.

**How to confirm it fires.** Precursor: grep for `str(` applied to a value that came from a cursor row and is then used as a dict key, set member or comparison — especially `f"{a}|{b}"`-style composite keys. Confirm: does the value originate in a NUMBER column? If the codebase has no single canonicalisation helper that both the writer and the reader call, fire.

---

## `a-per-call-timeout-leaks-onto-a-pooled-session`

**High** — Re-assert the pool's configured call timeout on every hand-out, unconditionally

**Look for.** `conn.call_timeout = X` armed on a borrowed pooled connection and restored in a `finally`; a re-assert of the pool's configured ceiling guarded by `if self._config.call_timeout_ms:`; or a restore that writes back 'whatever it held' rather than the configured value.

**What goes wrong.** A `finally` restore is best-effort by construction — the likeliest reason it fails is the lost connection it exists to survive. One failed restore hands a session back to the SHARED pool with a short probe ceiling still on it, and the next long-running statement to borrow that session dies at that ceiling having read nothing. Up to pool-max sessions get poisoned this way, one per failed restore, with nothing in the log connecting the two events. And guarding the re-assert with `if configured:` means a configured value of 0 (unbounded, the usual setting on a read pool) never clears anything — so the guard disables the only place a stale ceiling is ever removed.

**Fix.** Re-assert the pool's configured ceiling UNCONDITIONALLY as the first act of every hand-out, before any other round trip, in its own try so that a connection refusing some later nicety still gets it. Express a per-borrow override as an argument to the hand-out (so it also covers the session set-up that runs before the caller sees the connection), not as something the caller sets inside its own body. On release, restore to the pool's CONFIGURED value, never to whatever the session happened to hold — that could be another borrower's bound.

**How to confirm it fires.** Precursor: grep for `call_timeout` assignments. For each, ask: is the connection from a shared pool? Fires when (a) the restore lives only in a `finally` with no unconditional re-assert on the next acquire, (b) the re-assert is inside an `if <configured>:`, or (c) the restore writes a previously-read value rather than the pool's configured one.

---

## `an-all-null-bind-has-no-type-to-infer`

**High** — Size string binds explicitly for executemany, in bytes, and chunk the array

**Look for.** `executemany` with a whole batch passed as one array, or with a bind whose value is `None` in every row of the chunk — an optional provenance column, a parameter the caller never supplies, a column that only exists after a pending schema upgrade — and no `setinputsizes` call.

**What goes wrong.** python-oracledb decides each bind's TYPE and WIDTH by scanning the array it is handed. A bind that is None in every row gives it nothing to infer from, so the behaviour depends on a driver default nobody chose. Width matters independently: the driver sizes each buffer at the WIDEST value in the array and allocates that width for EVERY row, so passing a whole batch of thousands of rows as one array is tens of megabytes of bind buffer — on a run that already died of memory exhaustion that is not a trade worth making to save a few round trips.

**Fix.** Chunk the array (about a thousand rows per call already turns thousands of statements into single digits) and call `setinputsizes` with widths computed from the chunk: for every bind whose values are strings-or-None, the widest value, never below 1. Leave binds carrying a non-string value in any row alone — the driver types those correctly from the non-NULL entries, and forcing a string width onto a number is wrong. Size in UTF-8 BYTES, not characters: generous if the driver reads the number as characters, correct if it reads bytes, whereas a character count is a 'value too large' on the first multi-byte row in production.

**How to confirm it fires.** Precursor: grep for `executemany`; check for a nearby `setinputsizes`. Fires when absent AND any bind key can be None for all rows (trace the params builder for `or None`, `.get(...)`, a value gated on a feature probe). Second check: is the array the whole batch, or chunked? Third: if `setinputsizes` is present, is the width computed with `len(s)` rather than `len(s.encode('utf-8'))`?

---

## `batcherrors-hands-back-failures-nobody-reads`

**High** — Leave batcherrors off for writes that must not lose a row, and read getbatcherrors() where it is on

**Look for.** `cursor.executemany(sql, rows, batcherrors=True)` with no `cursor.getbatcherrors()` call anywhere after it.

**What goes wrong.** With batcherrors on, the driver applies every row it can and hands the failures back as a list the caller is free never to look at. On a write that records what has ALREADY been committed elsewhere, a quietly dropped row leaves durable data with no record of it — children cannot resolve it, cleanup cannot find it, and the next run re-inserts it as a duplicate. The failure is invisible at the call site and invisible in the logs.

**Fix.** Leave batcherrors at its default (off) wherever a dropped row is a correctness problem: the first bad row then aborts the statement and raises, and the caller decides what to do. Turn it on only for append-only diagnostic trails where losing one row is better than losing the other thousands — and even there, read `getbatcherrors()`, report the count and the offending offsets, and say in the log that the rest landed.

**How to confirm it fires.** Precursor: grep for `batcherrors=True`. Then grep the same file/class for `getbatcherrors`. Fires when the first matches and the second does not. Second pass: classify the target table — if anything downstream reads it to decide 'already processed', batcherrors should be off entirely, not merely read.

---

## `number-fetched-as-float-breaks-text-match`

**High** — Render NUMBER as text in SQL before matching it against a character column

**Look for.** A value read from a NUMBER column (or from EXTRACT(YEAR FROM ...), which is also numeric) used as a dictionary key, a cache key, or a comparison against a VARCHAR2 code/name column, with the conversion done in Python (str(x), f-string) rather than in the query.

**What goes wrong.** python-oracledb can return a NUMBER as a Python float, so str() of it yields '2023.0' and never equals the VARCHAR2 '2023' it is being matched against. Nothing raises: the lookup just misses, and if the code has a fallback, every single row silently takes the fallback. This is the quietest class of mapping bug — the load succeeds and the data is wrong.

**Fix.** Derive the text form in SQL: TRIM(TO_CHAR(some_column)) — or TRIM(TO_CHAR(EXTRACT(YEAR FROM some_date))) — so the driver only ever hands over the exact string that will be compared. Pin it with a test that asserts the rendered form, not just that the query runs.

**How to confirm it fires.** Regex precursor: `str(` or an f-string applied to something sourced from a fetch, near words like lookup/cache/match/key; or `EXTRACT(` in SQL feeding a text comparison. Semantic confirm: is the source column NUMBER and the comparison target character-typed? If so the rule fires even though nothing errors.

---

## `pool-acquire-and-connect-wait-forever-by-default`

**High** — Give a session pool a bounded acquire, a connect timeout and an idle expiry

**Look for.** `oracledb.create_pool(user=..., password=..., dsn=..., min=..., max=...)` with no `getmode`/`wait_timeout`, no `tcp_connect_timeout` and no `expire_time`.

**What goes wrong.** The driver's default getmode is WAIT, which means FOREVER. If every session is checked out — one of them wedged after an earlier failure — each later `acquire()` blocks with no timeout, no error, and nothing in the log but the phase string set before the call. No per-call timeout can cover it, because there is no connection object to arm until `acquire()` returns. Separately, a listener that accepts the socket and then never answers is indistinguishable from a slow one, and a firewall that silently drops an idle TCP connection leaves the pool handing out a socket that will never answer and never error.

**Fix.** Pass `getmode=POOL_GETMODE_TIMEDWAIT` with a `wait_timeout` far longer than anything the tool legitimately waits for but far shorter than a shift (minutes, not hours); `tcp_connect_timeout` on new physical connections; and `expire_time` so idle pooled sessions are probed and discarded instead of handed out dead. Look the getmode constant up by name (`getattr`) so an older driver — or a test double standing in for the module — still builds the pool: a bounded acquire is hardening, not a requirement.

**How to confirm it fires.** Precursor: grep for `create_pool(`. Read its kwargs. Fires on any of: no `getmode`/`wait_timeout` pair, no `tcp_connect_timeout`, no `expire_time`. Secondary: is the getmode constant referenced directly (breaks on older drivers/fakes) rather than via `getattr(oracledb, ..., None)`?

---

## `restore-session-settings-after-arming-them`

**High** — Restore a temporarily armed session setting in a finally, to its PREVIOUS value, and only if you armed it

**Look for.** Code that sets conn.call_timeout (or any per-session attribute) around one call, without a try/finally, or that restores by assigning a hard-coded 0, or that restores unconditionally on a connection where the attribute could not be read.

**What goes wrong.** The session goes straight back into a shared pool. A ceiling left behind on it will later abort the very long-running extract the pool exists to serve — the failure lands on an unrelated statement, minutes or hours later, and looks like a source problem. Restoring a hard-coded 0 is the same bug in slow motion: it silently discards whatever the pool had configured. 'Restoring' a value that was never successfully read invents a setting nobody asked for, and on older drivers or test doubles the attribute may not exist at all, so arming must be best-effort and must leave such a connection completely untouched.

**Fix.** Read the old value first; if it cannot be read, do not arm and do not restore. Arm inside try, restore the READ value in finally. Because a finally is best-effort, also clear any stale ceiling on the next acquire so a poisoned pooled session cannot outlive one caller. Pin the raising path explicitly — that is the one that matters.

**How to confirm it fires.** grep the diff for assignments to connection/session attributes or ALTER SESSION inside a helper that then runs one statement. Check for try/finally, for a saved previous value, and for a guard around the save. A context manager named like _bound_call is the right shape.

---

## `roll-a-failed-array-back-before-replaying-it`

**High** — Roll back a failed executemany before any row-by-row replay, and do not fail the batch when the replay succeeds

**Look for.** An `except` around `executemany` that immediately retries the rows one at a time, or re-raises to a caller that will, without `connection.rollback()` first. Also: a replay that re-raises the original array error even after every row was written successfully.

**What goes wrong.** A failed array may have applied part of itself. Those rows are still in the session's transaction, so a later commit on that session makes them durable — and the row-by-row replay writes them again. You get duplicates, or a constraint error on replay that reads like a new data defect. The second half is subtler: if the replay writes every row and the handler still re-raises, the caller's batch-failure path runs against data that is already committed — it rolls back a committed transaction (a no-op), discards buffers describing durable rows, reports rows as lost, and withholds the table's progress marker, so the next run re-reads the entire table.

**Fix.** In the handler: roll back first (guarded, so a dead session cannot mask the real error), then re-raise or replay. If the replay succeeds for every row, return NORMALLY and log a WARNING naming the statement and the row count — the rows ARE written, the batch is not failed, and the progress marker is not withheld. Removing the most likely trigger (duplicate keys inside the array) is what makes an unexplained array failure worth a warning rather than a routine event.

**How to confirm it fires.** Precursor: find `executemany` inside a `try`; read the `except` body. Fires if the body contains a retry/loop over the same params, or re-raises to a caller that does, and does not call `rollback()` before it. Separately: if a replay loop exists and it unconditionally re-raises the original error after completing, flag the second half.

---

## `timeout-error-must-abort-the-batch`

**High** — Classify a self-inflicted timeout as a batch failure, not as a poison row

**Look for.** A broad per-row except that records the row as a defect and continues, with no special case for the driver's timeout error (DPY-4024) or for a lost connection.

**What goes wrong.** Per-row isolation is right for a data defect and wrong for a timeout: the call was cut short by our own ceiling, so every remaining row in the batch will fail the same way. Isolating them records each as a data defect and then marks the batch committed — the run reports thousands of bogus rejects and, worse, never re-selects those rows, so real rows are lost. A data error (e.g. ORA-01400 cannot insert NULL) must keep the per-row treatment.

**Fix.** Add explicit predicates (is_call_timeout / is_connection_lost) and abort the batch on them so the rows are re-selected next run; keep isolation for genuine row-level errors. Pin with a test asserting the timeout is classified as both timeout and connection-lost, and that an ordinary constraint error is neither.

**How to confirm it fires.** grep the diff for except blocks in the row loop. Check the error-classification helpers for DPY-4024 / 'call timeout' / DPY-4011 / ORA-03113/03114. If a timeout ceiling was added in the same change without a classification update, flag.

---

## `zero-means-forever-so-never-clamp-a-negative-timeout`

**High** — Refuse a negative timeout instead of clamping it, and fall back to the default on junk

**Look for.** `max(0, int(os.environ[...]))`, `abs(seconds)`, or a `try/except ValueError: return 0` around a timeout read from configuration.

**What goes wrong.** In this API 0 means 'wait forever'. Clamping -5 to 0 does not give you five seconds the wrong way round — it DISARMS the bound entirely, which is precisely the unbounded behaviour the setting exists to prevent, arrived at by a stray minus sign. Junk falling back to 0 has the same effect: a typo in an environment variable silently removes a protection, and the two readings of the same input are hours apart in consequence.

**Fix.** Refuse a negative with a named error that reaches a human — the operator who typed it is entitled to know which reading he got — and have the caller log it and apply the DEFAULT. Fall back to the default (not to 0) on unparseable input, reject non-finite floats ('nan'/'inf' parse as floats but are not durations), and require an explicit 0 to mean unbounded. Read the value per call rather than caching it at import, so a run that is already stuck can be restarted with a different ceiling without a code change.

**How to confirm it fires.** Precursor: grep for `max(0,` / `abs(` / `except ValueError` near an env-var read whose name contains TIMEOUT/SECONDS/MS. Semantic check: in the target API, does 0 mean 'unbounded'? If yes, any clamping-to-zero or junk-to-zero path fires. Also check the default direction: a knob that is ON by default must not fall back to OFF.

---

## `a-pool-session-callback-runs-inside-acquire`

**Medium** — Do session set-up on the hand-out path, not in a pool session_callback

**Look for.** `create_pool(..., session_callback=fn)` where `fn` runs ALTER SESSIONs, a self-check, or any other round trip.

**What goes wrong.** The driver invokes the callback INSIDE `acquire()`, before the caller has a connection object — so nothing can bound that work, and any per-borrow ceiling can only be armed afterwards, i.e. after the window it exists to cover has already passed. The callback is also invoked with positional arguments only (`conn, requested_tag`), so a keyword override in the same function silently defaults back to the pool's configured (often unbounded) value. Enabling idle expiry makes fresh physical connections MORE likely mid-run, not less, so this is not a cold-start-only concern.

**Fix.** Do the set-up explicitly on every hand-out, in a wrapper that arms the bound first and then runs the ALTER SESSIONs and any self-check, and drop the callback rather than keeping a duplicate unbounded copy of the same work. Keep whatever the driver would still call harmless.

**How to confirm it fires.** Precursor: grep for `session_callback`. Read the callback body: does it execute any statement or self-check? Fires if yes and the pool also has a per-borrow timeout concept. Secondary check: does the callback take keyword parameters that the driver will never pass?

---

## `execute-prefetches-use-parse-to-describe`

**Medium** — Use cursor.parse() to describe a query; cursor.execute() already prefetches rows

**Look for.** Code that runs a query purely to learn its shape — reading cursor.description, building a column list, validating that a statement is legal — using execute() and then not fetching, or fetching zero rows.

**What goes wrong.** execute() prefetches the first batch before returning, so a query that raises while decoding a value (an out-of-range date, a bad LOB) raises during the describe, in a code path whose whole purpose was to avoid touching the data. The failure then looks like a metadata bug rather than a data bug.

**Fix.** Call cursor.parse(statement) to get the statement described and validated with no rows fetched, read cursor.description from that, then build and run the real statement.

**How to confirm it fires.** Regex precursor: `cursor.description` or `.description` within a few lines of `.execute(`. Semantic confirm: does the code fetch no rows from that execute? If it only wants the shape, it should be parse().

---

## `machine-rewriting-a-query-into-an-inline-view`

**Medium** — Describe with cursor.parse before wrapping arbitrary SQL, and refuse the rewrite on unsafe column names

**Look for.** Code that wraps caller-supplied SQL as `SELECT <projection> FROM (<original>)` to add casts, conversions or a row limit — especially when the projection is built from `cursor.description`.

**What goes wrong.** The wrap is only valid if every output name is unique and quotable. Duplicate output names make the inline view itself invalid (ORA-00918), and a name containing a double quote cannot be quoted at all — both surface as a confusing parse error on somebody else's SQL, far from the code that rewrote it. And learning the output types is itself a trap: `execute()` already prefetches rows, so describing by executing can raise on the very values the rewrite exists to survive.

**Fix.** Describe with `cursor.parse(sql)` — a describe-only round trip that fetches nothing — then inspect `cursor.description`. Refuse the rewrite and fall back to the plain statement whenever names are duplicated, empty, or contain a quote; quote every generated name and alias each projection back to its original name so the caller's column labels are unchanged. Treat the describe as an optimisation, never a gate: if it fails for any reason, run the original statement.

**How to confirm it fires.** Precursor: grep for an f-string of the shape `FROM (` wrapping a variable holding SQL, or for `cursor.description` used to build a SELECT list. Checks: (1) is the type/name discovery done with `parse` rather than `execute`? (2) is there a guard for duplicate / empty / quote-bearing names? (3) are generated names quoted and aliased? Any missing check fires.

---

## `returning-clause-must-survive-statement-rewrites`

**Medium** — Preserve RETURNING ... INTO through every statement rewrite

**Look for.** A diff that rebuilds INSERT text (adding computed columns, reordering, handling the empty-column case) near code that reads a generated key back.

**What goes wrong.** Identity/sequence-generated PKs are read back through RETURNING ... INTO, and the crosswalk that lets child rows find their parents is built from that value. A rewrite that drops or reorders the clause silently returns None, and the failure appears later as unresolved parents in child tables, far from the edit. The degenerate branch is the usual casualty: a row whose only value is a computed expression must not fall into the 'nothing to insert' path that emits VALUES (DEFAULT).

**Fix.** Route every INSERT shape through one builder that appends RETURNING <pk> INTO :out last, and cover the degenerate shapes in tests: all-NULL row, expression-only row, and normal row — each asserting the returned key.

**How to confirm it fires.** grep the diff for RETURNING and for 'VALUES (DEFAULT)'. If INSERT assembly changed without a test asserting the returned value for the expression-only and all-NULL shapes, flag.

---

## `thin-mode-requires-easy-connect`

**Medium** — Thin-mode connect strings must be service-name Easy Connect, never host:port:SID

**Look for.** A DSN literal or config value of the form host:port:SID, or a tnsnames alias assumed to work, in code running python-oracledb without an Instant Client.

**What goes wrong.** Thin mode supports only the Easy Connect service-name form host:port/service. A SID-style DSN fails at connect time with an error about the descriptor rather than about the format, and the common 'fix' — installing thick mode and Instant Client — changes the deployment shape of an offline bundle.

**Fix.** Use host:port/service_name. If the target genuinely only publishes a SID, that is a decision to run thick mode with Instant Client, and it belongs in the deployment docs, not in a quiet driver flag.

**How to confirm it fires.** Regex: a DSN string matching `[^/]+:\d+:[A-Za-z0-9_]+` (two colons, no slash). Semantic confirm: is the connection made without oracledb.init_oracle_client()? Then thin mode is in force and the DSN is invalid.

---

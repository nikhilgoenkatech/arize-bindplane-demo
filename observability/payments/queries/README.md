# Query usage

[index.json](index.json) labels the primary dashboard's 26 data queries with purpose, source and timeframe. [fallback-index.json](fallback-index.json) labels the alternate dashboard. Files exactly match their generated tiles; update `../build.py` and regenerate rather than editing individual outputs.

Run a query in its dashboard tile to use the defined variables and timeframe. In a Notebook or query editor, set a bounded timeframe and replace `$Rail`, `$PaymentType`, `$Channel`, `$CustomerSegment`, `$Scenario` with the quoted string `"ALL"`, `$TransactionId` with `""` or a quoted real ID, and `$SlowSeconds` with `"3"`. These tokens are dashboard substitution syntax, not standalone DQL variables. Never double-quote a variable token in the dashboard query.

Overview tiles inherit the dashboard timeframe (default two hours). Detail tiles override to 24 hours. The two quality tiles override to now minus two hours through now minus two minutes; they compare the same settled window. Set the equivalent editor window when executing the standalone files. All business queries require the normalized demo marker; they do not repeatedly parse raw JSON.

The trace query also scans spans within its bounded window, limits to the five service names, and joins only trace IDs from the selected demo transaction. Empty selected-ID tiles are intentional until TransactionId is supplied. No spans does not disprove a payment journey; check collector export, sampling, retention and permissions.

Reconciliation includes both record counts and unique payment coverage. Duplicate events/logs may exist even while unique counts match; KPI deduplication prevents their multiplication. The fallback's extracted-event and reconciliation tiles deliberately continue to read business events so extraction absence remains observable.

# Local API contract v1

Base `/api`; JSON. Public health/session/login; other endpoints require signed
HttpOnly cookie. GET `/session` initializes CSRF; POST `/login` uses that token,
then response replaces it. Unsafe requests require `X-CSRF-Token`. Server reads
role from users table, not request body/cookie role. Session expires after 8h.

Browser client shares concurrent GET `/session` initialization and fetches it
with `cache: no-store`. Before each login it reads the current cookie's token;
if that login receives exactly HTTP 403 `Invalid CSRF token`, it synchronizes
once more and retries login once. Wrong credentials, other 403 responses and
data mutations are never automatically retried. No CSRF token is persisted in
localStorage. Backend CSRF comparison, cookie flags, origin checks and RBAC remain
required; the client flow does not bypass them.

Money is canonical integer base-10 **string**, max 78 digits, units SIM/10^6.
Time UTC ISO 8601, `global_index` is zero-based ledger order, corresponding to
block_height/tx_index. `as_of_index` bounds ledger/assessment availability.
Unanalyzed score is null/pending, not zero or safe. Error envelope has code,
message, request_id; validation errors omit input (especially password).

| Endpoint | Notes |
| --- | --- |
| POST /simulations | Admin; config, seed; Idempotency-Key required; 202 run_id/job_id/version; max 5 queued/running local jobs |
| GET /simulations, /simulations/{run_id} | Run/config/progress/analysis jobs, safe errors |
| POST /simulations/{run_id}/replay | Admin; mode as-known/retrospective; Idempotency-Key; new version, same immutable ledger |
| POST /simulations/{run_id}/retry | Admin; failed job only; same frozen config/version, resumes/rebuilds |
| GET /transactions | Required run_id; limit<=200, cursor, wallet/min_score/as_of_index/analysis_version; items + next_cursor |
| GET /wallets/{id-or-address} | Required run_id, optional cutoff; exact balance and inflow/outflow_with_fees strings |
| GET /graph | Run/center/hops(1..5)/cutoff/alert/version; <=1.000 nodes/3.000 edges; default UI 300/600; recent 24h unless explicit evidence |
| GET /alerts, /alerts/{id} | Required run_id; version/status/detector/as_of; immutable snapshots for historical evidence/score |
| PATCH /alerts/{id}/review | Analyst/Admin; status/comment/revision; 409 stale revision; append audit/review history |
| GET /metrics | Run/version; pending until evaluation done; no direct labels returned |
| GET /feeds / POST /feeds | Read authorized roles / Admin writes append-only version with schema validation |
| GET /events | SSE authorized stream, optional run_id, Last-Event-ID; progress/job/alert; heartbeat + resync |

Cursor filter signature includes run/version/filter/cutoff, order ASC and stable
global_index tie-break. Changed filters reject stale cursor (422); out-of-scope
resource returns 404. Schema/model/rule/feed/mode are preserved per job/version.
Alert detector/anchor/quiet-time episode/version dedup; evidence union, snapshots
per observed order, review status current (review history separate from engine).
SSE keeps last 10.000 event rows; expired cursor emits resync, client reloads HTTP
snapshot. HTTP records remain authoritative. Do not put token in event URL.

Retrospective TI analysis has `sequential_with_retrospective_feed` metrics and
`analysis_mode=retrospective`; it does not publish those scores as historical
online as-known. Graph node metadata includes assessed_at/as_of_order/version.
Nginx uses Docker's runtime DNS resolver so recreating the API does not leave a
stale upstream address; frontend health checks the API proxy as well.

Config bounds: seed 0..2^31−1, wallets 80..10.000, tx 150..100.000, simulated days
1..30, interval 1..60s, max tx/block 1..100; patterns unique whitelist. Numeric
config uses JSON numbers; money does not. Invalid/negative amounts/nonces/fees
are rejected before ledger append. The current API generates data through jobs,
not arbitrary raw intent upload or real crypto transfer.

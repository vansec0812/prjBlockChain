# Bảng truy vết yêu cầu, kiểm thử và bàn giao

Nguồn: [Ke-hoach-AML-Blockchain.md](../Ke-hoach-AML-Blockchain.md).
Trạng thái khi tạo tài liệu ban đầu: chưa triển khai. Baseline local hiện được
kiểm chứng trong [báo cáo](../docs/reports/local-validation.md); các mở rộng/nghiên
cứu chưa hoàn tất được ghi riêng, không coi roadmap là đã hoàn thành.
ID Rxx là mã quản lý bổ sung; T01–T22 giữ nguyên mã kiểm thử của kế hoạch.
Tên vị trí kiểm tra là module dự kiến, không phải file/test đã tồn tại.

## Yêu cầu chính

| ID | Nguồn kế hoạch | Hành vi cần có | Rule chuyên trách / bằng chứng |
| --- | --- | --- | --- |
| R01 | Mục tiêu, phạm vi; mục 1 | Account-based/SIM; đúng giới hạn simulator, score ưu tiên | [Scope](rules/00-scope.md); UI/report không tuyên bố blockchain thật/xác suất |
| R02 | Mục 1 | Generator→ledger→DB/outbox→worker→engine→alert→API/UI; evaluator riêng | [Architecture](rules/01-architecture.md); integration luồng đầy đủ |
| R03 | Mục 2 | Integer money, balance/nonce/fee/genesis conservation, canonical hash/block | [Ledger](rules/02-ledger-simulation.md); T15/T16/T18/T21 |
| R04 | Mục 1–2, 4 | Atomic persistence, order, duplicate/restart/replay, checkpoints | [Database](rules/03-database-contracts.md); T13/T17/T21 |
| R05 | Mục 2, 5 | Nền/campaign/confounders/truth riêng, independent splits | [Ledger](rules/02-ledger-simulation.md), [Evaluation](rules/08-testing-evaluation.md); T08/T22 |
| R06 | Mục 3 | STRUCTURING/SMURFING/PEELING/MIXING/RAPID/DIRECT rules + evidence | [AML](rules/04-aml-graph-scoring.md); T01–T11 |
| R07 | Mục 3 | MultiDiGraph/temporal haircut, source portions, conservation/budgets | [AML](rules/04-aml-graph-scoring.md); T04/T12–T15/T20 |
| R08 | Mục 3–4 | TI known_at/validity/network/source/version; as-known vs retrospective | [AML](rules/04-aml-graph-scoring.md); T09/T10/T14/T21 |
| R09 | Mục 3–4 | Blend, floors, half-up, severities, versioned history/coverage | [AML](rules/04-aml-graph-scoring.md), [Database](rules/03-database-contracts.md); T02/T09/T11/T19–T21 |
| R10 | Mục 4 | Endpoints, bounded query, pagination/jobs/SSE, run scope | [API](rules/05-api-realtime.md); contract/integration/SSE tests |
| R11 | Mục 4 | Ba màn hình; timeline/evidence/score/blacklist đúng ngữ nghĩa | [Frontend](rules/06-frontend-ux.md); interaction/E2E, T13/T18/T20 |
| R12 | Mục 4 | Viewer/Analyst/Admin; validate rule/metadata, audit, SQL params | [Security](rules/07-security.md); allow/deny/scope/review/import tests |
| R13 | Mục 3, 5 | Optional ML sau B2; split/metrics/ablation thực đo; deliverables | [Evaluation](rules/08-testing-evaluation.md), [Operations](rules/09-repository-operations.md); reports/manifests |

## Test cases bắt buộc của kế hoạch

| ID | Input/trigger | Kỳ vọng phải kiểm | Phạm vi kiểm |
| --- | --- | --- | --- |
| T01 | P2P bình thường, lượng/tần suất thấp | Không motif/blacklist alert; vẫn có assessment theo contract | AML baseline |
| T02 | >=8 lệnh [0,8T;T) trong 30 phút, sum>=5T, ratio>=70% | STRUCTURING hit, final>=70, evidence đúng các lệnh | Window/rule/score |
| T03 | T−1/T/T+1 base unit, điểm đầu/cuối window | T−1 dưới T, T/T+1 không; vẫn giữ cận dưới và mọi điều kiện khác; biên [t−W,t] nhất quán | Money/window boundary |
| T04 | Fan-out qua 12 ví rồi về B | SMURFING hit; allocation hội tụ không nhân lượng bởi nhiều đường | Graph/haircut/motif |
| T05 | Peeling 6 tầng, >=2 tx/fee mỗi tầng | Hit sau đủ tầng thứ 4, evidence có nhánh lớn/nhỏ và balances/fees | Peeling/generator |
| T06 | Một tầng có nhánh lớn/nhỏ hoặc <4 tầng | Không đủ rule PEELING | Motif negative |
| T07 | Many-to-many turnover/input/output/entropy đủ | MIXING_PATTERN hit, balance vùng đúng cả fee, không cộng internal edges | Mixing/ledger |
| T08 | Payroll không hội tụ, custody giống mixing | Đo false positive, custody có thể alert; không dùng truth để suppress | Confounder/evaluator |
| T09 | Sender/recipient direct blacklist hợp lệ | Exact match, final>=95, source/list_version/direction đúng | TI/score/evidence |
| T10 | Cùng address khác network, expired feed, known_at tương lai | Không as-known match; retrospective tách assessment/version | TI/time/network |
| T11 | Đường 4 hop, dwell<=2 phút, TI severity/confidence hợp lệ | RAPID_HIGH_RISK hit, final>=80, order tăng nghiêm ngặt | Temporal tracing |
| T12 | B→C trước A→B chứa nguồn rủi ro | Không lấy B→C làm continuation của tiền nhận sau | No future/time leakage |
| T13 | Cùng block timestamp, tx_index khác | Không dùng tx phía sau ở feature/query/assessment/timeline trước order | Online pipeline/UI |
| T14 | Dust từ blacklist rồi khoản lớn nền bình thường | Direct dust alert; transfer sau không 100% exposure vì dust | Haircut/direct semantics |
| T15 | A→B→A, self-transfer, converging paths | Không loop vô hạn/tạo thêm money/exposure; self-transfer chỉ fee | Allocation/ledger |
| T16 | Thiếu balance, sai nonce/amount/fee; revert nếu adapter có | Reject intent trước append; không success edge; adapter revert là test mở rộng có điều kiện | Ledger/adapter |
| T17 | Duplicate replay, worker crash giữa ledger/analysis | Không mất work/nhân tx/edge/assessment/alert; state/checkpoint nhất quán | DB/outbox/recovery |
| T18 | Amount>2^53, khác asset, address invalid | Money chính xác qua DB/API/UI; khác asset bị từ chối trong MVP; validation rõ | Contracts/end-to-end |
| T19 | Motif P=1, E=A=0; velocity/volume cao nhưng P=E=0 | Motif final>=70; V/Q đơn độc không sinh motif/blacklist | Score/floors |
| T20 | Exhaust graph budget, history thiếu, hybrid model lỗi | Coverage partial/truncated/model unavailable đúng; không trình bày đầy đủ/an toàn | Engine/API/UI |
| T21 | Cùng seed/config/versions; reanalysis rule mới | Ledger hashes/counts tái lập; version mới không overwrite assessment cũ | Determinism/history |
| T22 | Đổi hidden labels/actor IDs, ledger+TI giữ nguyên | Predictions không đổi, cả feature/candidate/suppression không leak | Isolation/evaluator |

T01–T22 là yêu cầu test triển khai, không là thông báo đã pass. Đổi tên file/test
runner không được bỏ requirement; khi có mã nguồn, bổ sung path/test node ID
thực tế và trạng thái/bằng chứng vào task/report, tránh giữ trạng thái giả ở bảng này.

## Kiểm tra web bổ sung

| ID | Kiểm tra cần có khi triển khai |
| --- | --- |
| W01 | Auth/RBAC allow+deny, IDOR/run scope, review audit và conflict |
| W02 | Money string/BigInt/render>2^53, input SIM precision, lỗi validation |
| W03 | Cursor tie-break, đổi filter/run, as_of cache và response muộn |
| W04 | Durable job/idempotency, SSE reconnect/dedup/resync/expiry auth |
| W05 | Graph/node/edge budgets, evidence selected, loading/empty/error/partial |
| W06 | Timeline không future score/alert; online vs retrospective overlay |
| W07 | Keyboard/labels/contrast-text/responsive, memory cleanup graph/stream |
| W08 | Feed/import validation/no arbitrary expression, secret masking và export safety |
| W09 | Migration empty/existing PostgreSQL, restart/restore và writer locking |

## Mục tiêu đo và sản phẩm bàn giao

Mục tiêu không phải kết quả: direct blacklist exact fixtures 100%; campaign
recall>=85% mỗi pattern; retrospective prevalence=1% recall>=85%, FPR<=0,2%;
rule+graph D-standard>=100 tx/s (8 logical CPU/16 GB); alert p95<=2s sau indexed
evidence completion; dashboard bounded graph query p95<=1s (cache hot/cold riêng).
Metrics/splits/seed/CI theo [08-testing](rules/08-testing-evaluation.md).

Bàn giao khi triển khai xong: source+lockfiles; Compose; migrations+contracts;
config/seeds+generator; versioned rulebook+score; fixtures+test report;
raw measured metrics; demo dashboard; kiến trúc/phương pháp/kết quả/giới hạn.
Các sản phẩm này phải là artifact thật, không chỉ danh sách TODO hoặc hình mockup.

## Mở rộng sau các PR #1–#4

Ba motif Circular Flow/U-Turn/PageRank được ghi nhận tại
[ADR-0002](../docs/decisions/0002-reviewed-motifs-rules-v2.md), không thay thế các
yêu cầu T01–T22. Counterexamples độc lập tại
`backend/tests/test_pr_regressions.py`; snapshot TI và tương thích phiên bản
được kiểm qua PostgreSQL/API/worker trong `test_postgres_api_worker.py`.
Kết quả thực và giới hạn nằm ở [báo cáo review](../docs/reports/pr-review-2026-10-17.md).

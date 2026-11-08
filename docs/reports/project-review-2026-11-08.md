# Review source và củng cố baseline — 08/11/2026

## Đầu vào và phạm vi

Fetch origin và `git merge --ff-only origin/main`: đã cập nhật, main mới nhất
`49e0684f2ebfa9cb5e652060026efce26a550e79`, không có commit main mới. Working
tree ban đầu sạch. Source được review trên nhánh `improve/aml-review-20261108`.

Đối chiếu plan mục 2–5, AGENTS, ADR-0001/0002 và rules cho domain, persistence,
API/security, UI, evaluation, Docker/release. Tập trung ledger/allocation,
schema/parser, job/outbox/checkpoint, history/evidence, API/RBAC, refresh UI và
evaluator. Không thay kế hoạch, stack, dependency hoặc thresholds/floors.

## Kết quả review và thay đổi

| Mức | Vị trí trước sửa | Điều kiện / tác động | Hành vi sau sửa |
| --- | --- | --- | --- |
| P1 | `aml.Engine.process` | Nonce/fee/address/value sai bị từ chối sau khi last_order hoặc phần vốn đã thay; intent sau đó có thể đọc RAM sai | Metadata/order/ledger validation trước mọi mutation |
| P1 | `domain.Ledger.apply` | Tổng balance recipient hoặc fee_sink vượt NUMERIC(78,0), chỉ phát hiện lúc ghi DB | Tính toàn bộ effects trước apply, reject nguyên tử |
| P2 | `domain.Tx.from_payload` | Nhận timestamp naive, bool ở integer, order âm, địa chỉ/hash sai | Reject metadata; payload UTC; không đổi hash scheme |
| P1 | `main.graph` | Episode thêm giao dịch cũ vào evidence sau cutoff; lọc order cạnh vẫn làm lộ evidence chưa được phát hiện | Resolve đúng alert snapshot có hiệu lực tại cutoff |
| P1 | `worker.analyze` | Hai hit cùng tham chiếu giao dịch cũ: revision sau đọc online gốc và xóa reason trước | Tích lũy từ revision gần nhất, giữ hits/floors |
| P1 | `worker.analyze` | Motif phát hiện khi partial nhưng episode/revision vẫn complete | Hợp nhất coverage/partial reasons/truncated |
| P2 | `worker` alert snapshot | Nhiều hit cùng episode/order chỉ lưu snapshot hit đầu | Hoàn thiện một snapshot/order trong transaction hiện tại |
| P1 | `worker` evaluator | Dict theo pattern làm mất các campaign cùng loại | Truyền campaign list riêng, union chỉ cho classification |
| P1 | `evaluation.evaluate` | Matching chưa một-một; có thể dùng một episode nhận nhiều campaign | Maximum-cardinality matching deterministic, overlap>=50% |
| P2 | `evaluation.classification` | Nhãn/prediction ngoài universe tạo số đo sai | Reject dữ liệu ngoài mẫu số, mẫu số 0 giữ null |
| P2 | `frontend/App` | Timeline chỉ cắt graph; queue/table và response trễ có thể khác ngữ cảnh | Cutoff đồng nhất, sequence/abort/context guards và cursor phù hợp |

13 counterexample domain ban đầu đều fail trên artifact trước sửa. Thêm ba
ca PostgreSQL độc lập cho reasons, coverage và graph đều tái lập lỗi trước
sửa. Kiểm multi-campaign API/worker chứng minh hai campaign cùng loại không
bị collapse, một alert chỉ được ghép một campaign, không lộ identity qua metrics.
Matching còn được đối chiếu exhaustive oracle trên 512 đồ thị 3×3.

[ADR-0003](../decisions/0003-atomic-validation-and-history.md) mô tả rules-v3,
tương thích lịch sử và matching. Generator metadata mới là generator-v2,
nhưng intent generation/ledger hash không đổi. Kết quả rules-v1/v2 không bị
đổi nhãn hoặc sửa lại. Checkpoint khác version không resume bằng code mới;
run completed có thể replay sang analysis mới. Không migration schema.

## Kiểm chứng

| Lệnh | Kết quả |
| --- | --- |
| `docker compose --profile test run --rm test` | 84 passed, 20,22 s; PostgreSQL thật, không skip |
| `docker run --rm --network none aml-blockchain-backend:local ruff check --no-cache app tests` | Pass |
| `npm.cmd test` | 11 passed |
| `npm.cmd run build` | TypeScript/Vite pass |
| `npm.cmd audit --audit-level=high` | 0 vulnerabilities tại thời điểm chạy |
| `npm.cmd run test:e2e` | 6 passed; gồm cutoff thống nhất, giữ/nhả phím sau khi response đã về và response đảo thứ tự |
| `node scripts/smoke.mjs --count=5000 --all-patterns --restart-worker` | PASS, 8/8 campaigns, đủ 9 detector codes, 19 episodes |
| `python -m app.validate --transactions=5000 --wallets=500 --output=/reports/multi-seed-final.json` trong image giới hạn 2 CPU/2 GiB, network none | 5 seed 42–46 pass generation/ledger/prediction repeatability |

Một warning deprecation TestClient/httpx từ dependency còn tồn tại. Không đổi
dependency chỉ để che warning. Các báo cáo này không chứng minh toàn bộ kế
hoạch nghiên cứu đã hoàn thành.

Smoke cuối trên image hoàn tất tạo run `97029184-5106-4b8f-aa46-614c8d386c90`, analysis
`b2-rules-v3:f9f7d78db6bb:a1`, replay `b2-rules-v3:425755e14846:a2`.
Restart worker giữa ingestion. Hai analysis giữ cùng 5.000 tx, balance ví mẫu
và ledger hash `566ff35e143d1e87b3b42ecab88bb5f16331e09a07af0a844f92f170a2934def`.
Analysis đầu 10,434 s gồm persistence, ~479 tx/s; engine p95 1,819 ms;
1.124/5.000 assessments partial được thể hiện, không gán an toàn.
[Raw smoke metrics](project-review-smoke-2026-11-08.json).

Validation 5 seed × 5.000 tx: mỗi seed generation hai lần, độc lập replay ledger
và phân tích hai lần; hash/balance/supply/predictions giống nhau theo seed.
Đây là kiểm tái lập 25.000 tx phân biệt, không là test split 5 seed unseen.
[Manifest, source hash, coverage và timing](project-review-multi-seed-2026-11-08.json).
Thời gian validation bao gồm các lần lặp, không so trực tiếp với worker throughput.

## Vận hành và phần còn lại

Giữ nguyên volume và credentials, không reset `aml`; integration chỉ reset
`aml_test` sau kiểm tên DB. Trước nâng image có 6 job completed, không queued/
running. Hai run smoke mới đều completed; tổng supply của các run local được
kiểm bằng SQL với fee_sink. Compose giữ loopback 8080/8000/5433, API/worker tách tiến trình.
Không force push hoặc thay nội dung các nhánh cộng tác viên.

Kế hoạch gốc SHA256 không đổi:
`cc05f86162fe13fa53474dca104ed6147dfcdc2de75d4c42479136ee7bc86232`.
CI bổ sung validation nhỏ 150 tx/seed bên cạnh lint/PostgreSQL/smoke/browser.
DoD theo phạm vi: regression/domain/DB/history/security/build/browser và
documentation links đã kiểm; benchmark nghiên cứu/split/ML/public deployment
không áp dụng cho bản sửa này và không đánh dấu hoàn thành.

Chưa triển khai đầy đủ generator nhiều campaign/variant, merchant/payroll/
custody/treasury confounders, split 60/20/20, ablation B0–B3, backward tracing
API hoàn chỉnh, AP/bootstrap CI hoặc benchmark D-standard/D-stress. Evaluator
hỗ trợ nhiều campaign không đồng nghĩa generator/research dataset đã có chúng.
ML, EVM/UTXO/token/bridge/GNN không thuộc bản sửa. Các hạn chế này tiếp tục được
giữ trong README thay vì mô tả thành tính năng hoàn thành.

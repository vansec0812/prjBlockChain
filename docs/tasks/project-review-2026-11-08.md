# Review và củng cố baseline AML — 08/11/2026

Nguồn: kế hoạch mục 2–5, R03/R04/R09–R13, T13/T16–T22 và W03/W05/W06.
Đầu vào sạch trên main `49e0684`, đã fetch và fast-forward; origin không có
commit main mới. Không thay kế hoạch gốc, dependency hoặc mở rộng blockchain.

## Lát cắt triển khai

1. Ledger và contract: reject dữ liệu sai trước khi thay RAM state; giữ tiền
   trong NUMERIC(78,0), kiểm nonce/order/time/schema. Regression trước bản sửa.
2. Lịch sử: graph evidence dùng snapshot tại cutoff; worker giữ tích lũy các
   reason/coverage của revision; UI bỏ response cũ khi đổi ngữ cảnh.
3. Evaluator: hỗ trợ nhiều campaign cùng pattern với matching một-một; kiểm
   universe/mẫu số. Thêm lệnh validation theo nhiều seed tái lập, báo đúng phạm vi.
4. Kiểm PostgreSQL thật, lint, frontend unit/build, browser E2E, smoke restart/
   replay; ADR/contracts/report đồng bộ, commit theo nhóm, push sau kiểm tra.

Không hoàn tất toàn bộ nghiên cứu trong một lần review. Split nghiên cứu,
background confounder đa dạng, 100k stress, ML và backward investigation đầy
đủ chỉ được báo đã có khi thực sự triển khai/kiểm chứng. Tiêu chí bàn giao là
các lỗi xác nhận được bảo vệ bằng regression, khung local hoạt động và có
báo cáo lệnh/kết quả/giới hạn; không reset volume hoặc sửa lịch sử rules-v2.

## Kết quả

- [x] Domain preflight/capacity/metadata, regression chứng minh lỗi trước sửa.
- [x] Historical graph, cumulative reasons/coverage, UI cutoff/response ordering.
- [x] Multi-campaign matching, exhaustive oracle, multi-seed validation CLI.
- [x] 84 backend/PostgreSQL, 11 frontend, 6 browser tests; lint/build/audit pass.
- [x] 5.000 tx smoke/restart/replay, 5 seed × 5.000 tx validation; raw reports.
- [x] ADR/contracts/requirements-map/README và report cập nhật theo phạm vi.

Chi tiết, giới hạn và lệnh: [báo cáo](../reports/project-review-2026-11-08.md).

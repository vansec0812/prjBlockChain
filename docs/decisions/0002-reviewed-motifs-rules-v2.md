# ADR-0002 — Motif bổ sung và tương thích rules-v2

Accepted, 17/10/2026, sau review các PR #1, #2, #3 và #4 đã được chủ repo merge.
Đây là phần mở rộng so với motif S/C/M trong kế hoạch gốc, không sửa nội dung
kế hoạch. Các PR đã thêm Circular Flow, U-Turn và PageRank nhưng chưa ghi nhận
quyết định nghiệp vụ hoặc đổi rule version. ADR này khóa hành vi đã sửa qua
counterexample, bổ sung cho [ADR-0001](0001-local-architecture.md).

## Các detector

| Code | Điều kiện |
| --- | --- |
| CIRCULAR_FLOW | Chu trình 3–5 cạnh theo thứ tự ledger trong <=3.600 giây; cạnh đầu >=100 SIM; haircut phân bổ về đúng ví đầu >=70% giá trị cạnh đầu; các ví trung gian khác nhau |
| U_TURN_BOUNCE | A→B→A trong <=300 giây, kể cả cùng timestamp với tx_index tăng; cạnh đầu >=100 SIM; vốn phân bổ từ chính cạnh đầu trả về >=90%; balance B sau trả về <=5% giá trị cạnh đầu |
| TAINT_PAGERANK | Có vốn haircut từ blacklist hợp lệ, cùng network, trong <=5 hop/24 giờ; context PageRank × tỷ lệ vốn phân bổ >=0,05; evidence lấy từ đường đi nhân quả thực |

Circular/U-Turn dùng lịch sử chuyển vốn riêng, không bị xóa khi một ví trung
gian được gắn nguồn TI. Các phần vốn vẫn rời nhau; phí không trở thành cạnh
AML. Khi compact làm mất đường đi, không dựng lại bằng reachability.

PageRank tính trên các cạnh quá khứ còn trong cửa sổ 24 giờ, được duyệt từ các
blacklist seed; trọng số cạnh song song cộng bằng số nguyên. Power iteration
thuần Python, damping 0,85, tối đa 100 vòng, L1 tolerance `N × 1e-6`, dangling
restart theo personalization severity×confidence. Không gọi API nội bộ
NetworkX hoặc tự tải SciPy. Thuật toán dùng float cho tỷ lệ thống kê; số tiền
ledger/allocation giữ int. Không tuyên bố rank là lượng tiền đã truy vết.

Context chuẩn hóa bằng rank lớn nhất của seed, nhân tỷ lệ vốn blacklist thực
được phân bổ trong giao dịch, chặn 0..1. `observed.exposure_score` giữ tên để
tương thích DTO cũ, chỉ mang nghĩa context. `allocated_base` và `paths` mới là
bằng chứng lượng tiền. Không gán vốn high_risk cho seed blacklist khác chỉ vì
có đường đi trên graph. Không dùng static shortest path làm evidence.

Budget mặc định 20.000 cạnh đã duyệt/100 đường evidence. Vượt budget bỏ phần
PageRank không tính đủ và báo `graph_budget` hoặc giới hạn evidence kèm
`evidence_path_budget`; không hội tụ báo `pagerank_unavailable`. Coverage
`partial`, không báo an toàn và không làm hỏng đánh giá direct/rule khác.

## Score và version

Theo hành vi đã merge, ba detector bổ sung là motif: P=1 khi hit và floor 70.
Đây là mở rộng tập motif, không thay trọng số. Giữ đúng
`E=max(E_flow,E_direct_counterparty)`, blend 35/30/15/10/10, A=0, half-up,
floor blacklist 95 và rapid 80. PageRank không được thay hoặc nâng component E.

Toàn bộ đánh giá mới dùng `rules-v2`, kể cả RuleHit, assessment và metrics.
API đóng băng feed synthetic ngay khi enqueue nếu bật Rapid hoặc PageRank;
worker không sửa snapshot để làm đẹp kết quả sau khi đã bắt đầu.

Job ghi `config.rule_version`. Worker từ chối job thiếu version hoặc khác
version trước khi ghi ledger/assessment; retry trả 409 thay vì tiếp tục một
checkpoint bằng engine mới. Giữ nguyên ledger, outbox, assessment và review
cũ. Không đổi nhãn rules-v1 trên kết quả lịch sử.

Run cũ completed có thể replay thành analysis rules-v2 mới, không chuyển tiền
lần nữa. Run cũ chưa hoàn tất phải chạy bằng artifact cũ để hoàn tất hoặc tạo
run mới; không có cơ chế tự nâng checkpoint. Generator trong PR #4 đổi vùng ví
background từ index 64 sang 68 nên cũng không được resume bằng artifact mới.
Không yêu cầu migration schema hoặc reset volume.

## Kiểm chứng và giới hạn

[Báo cáo review](../reports/pr-review-2026-10-17.md) ghi các phản ví dụ, kết quả
test và smoke thực tế. Fixtures độc lập nhãn generator; seed campaign chỉ dùng
ở evaluator. Không suy ra chất lượng unseen-run từ dữ liệu demo. Chưa đo
D-standard/D-stress, không xác nhận tuyên bố dưới 1 ms hoặc scalability 100k
của nội dung PR. Không mở rộng sang ML/GNN/EVM/UTXO.

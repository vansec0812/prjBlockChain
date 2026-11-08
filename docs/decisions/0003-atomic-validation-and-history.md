# ADR-0003 — Validation nguyên tử, lịch sử và evaluator rules-v3

Accepted 08/11/2026. Bổ sung cho ADR-0001/0002; nguồn yêu cầu: mục 2–5 của
kế hoạch, R03/R04/R09–R13, T13/T16–T22. Không thay kế hoạch gốc hoặc threshold.

## Validation trước mutation

`Ledger.validate` tính mọi balance sau intent bằng int trước khi thay state,
kể cả self-transfer và fee_sink. Từng balance phải nằm trong [0,10^78), tương
thích NUMERIC(78,0). Tổng supply có thể lớn hơn capacity của một tài khoản;
không ép toàn bộ supply về một trường DB có cùng giới hạn.

`Engine.process` kiểm metadata/order/time và ledger validation trước khi thay
last_order, các phần vốn, graph hoặc feature. Intent sai không tiêu nonce,
không đổi state và có thể được thay bằng intent hợp lệ tại cùng order. Không
bắt lỗi invariant sau apply để giả thành score=0; lỗi nội bộ vẫn fail job và
được rebuild từ checkpoint bền vững.

Tx parser từ chối timestamp thiếu timezone, bool thay integer, order/nonce âm,
địa chỉ/hash không đúng định dạng. Payload serialize timestamp UTC. Fee vẫn
theo simulator cố định; chưa có adapter blockchain hoặc raw-intent API mới.

## Lịch sử có hiệu lực tại order

Graph evidence của alert luôn dùng snapshot gần nhất có order<=cutoff; không
lấy evidence episode hiện tại rồi chỉ lọc timestamp các cạnh. Một giao dịch cũ
cũng có thể chỉ trở thành evidence sau cutoff, nên cách lọc cũ vẫn leakage.

Revision hồi cứu bắt đầu từ assessment gần nhất, tích lũy hits/floors và hợp
nhất partial reasons, không xóa reason từ motif khác hoặc báo complete khi
phát hiện dựa trên phân tích partial. Online assessment ban đầu không đổi.
Nhiều hit của cùng episode tại một order hoàn thiện một snapshot của order đó
trong cùng database transaction; snapshot order cũ không được ghi đè.

UI gửi cutoff cho graph, alert queue và transaction cursor; metrics được ghi rõ
là toàn analysis. Refresh có thứ tự response, graph/wallet requests được hủy
hoặc bỏ qua khi ngữ cảnh đổi; không lấy response run cũ ghi vào run mới.
Trạng thái review hiện tại vẫn tách khỏi lịch sử quyết định của engine.

## Đánh giá nhiều campaign

Evaluator nhận danh sách campaign riêng; không chuyển thành dict một bản ghi
mỗi pattern làm mất các campaign cùng loại. Union nhãn chỉ phục vụ metrics
transaction-level. Campaign matching cùng pattern, overlap>=50% core; mỗi
alert/episode ghép tối đa một campaign. Chọn maximum-cardinality matching
bằng augmenting paths, thứ tự cố định theo ID/overlap để tái lập; không dùng
greedy có thể bỏ sót matching hợp lệ.

Core rỗng, ID trùng, prediction online trùng tx và nhãn/evidence ngoài universe
là lỗi dữ liệu; không sinh metrics đẹp từ mẫu số sai. Mẫu số 0 vẫn null.
Per-pattern classification chỉ có ở pattern được cung cấp campaign labels;
detector không có campaign label được liệt kê riêng, không tự coi blacklist
match đúng là false positive của một pattern chưa được gán nhãn.

API metrics chỉ chứa aggregate; danh sách identity campaign/matching chi tiết
ở evaluator riêng, không đi vào AML hoặc API phân tích. Generator hiện vẫn
một campaign mỗi pattern; hỗ trợ evaluator nhiều campaign không được mô tả
thành generator đã có nhiều campaign/variant.

## Version và vận hành

Kết quả mới dùng rules-v3 do semantics của assessment history/coverage thay đổi.
Giữ E, blend 35/30/15/10/10, floors 95/80/70, half-up và A=0. Guard job phiên
bản cũ tiếp tục áp dụng: không resume rules-v1/v2 bằng artifact mới. Run cũ
completed có thể replay sang rules-v3, không đổi ledger hoặc review cũ.

Metadata `generator-v2` phản ánh generator sau PR #4 (vùng background bắt đầu
ở ví 68); lần này không thay intent generation hoặc ledger hash. Không backfill
metadata run cũ, không migration schema/reset volume. Health trả code versions,
run DTO trả generator version đã lưu.

CLI `python -m app.validate` kiểm nhiều seed: generation lặp lại, ledger replay,
bảo toàn supply và prediction lặp lại. Report lưu config/hash/versions/coverage,
tách khỏi ground truth. Đây không phải split train/validation/test hoặc bằng
chứng chất lượng unseen-run, 100k stress hay ML. CI chạy nhỏ 150 tx/seed; report
local 5.000 tx/seed được ghi riêng trong báo cáo review.

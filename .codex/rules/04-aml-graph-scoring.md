# 04 — AML rules, temporal graph, tracing, TI và scoring

Áp dụng cho feature, detector, model, tracing, source feed hoặc cách giải thích score.
Nguồn: mục 3–4 của [kế hoạch](../../Ke-hoach-AML-Blockchain.md).
Đọc cùng [ledger](02-ledger-simulation.md), [database](03-database-contracts.md)
và [testing](08-testing-evaluation.md) khi sửa các boundary đó.

## Dữ liệu và thời gian được phép

Feature tại e chỉ dùng giao dịch có order<=order(e). Cửa sổ event time có biên
`[t-W,t]`, W gồm 5 phút, 30 phút, 60 phút, 24 giờ; với cùng timestamp vẫn cắt
theo tx_index. Forward path order tăng nghiêm ngặt; backward giảm nghiêm ngặt.
Nếu mẫu hoàn thiện muộn, lưu assessment mới với detected_at thật; không giả
vờ alert đã tồn tại ở giao dịch đầu chuỗi.

Không dùng scenario, campaign/actor IDs, role bí mật, label hoặc future review
trong feature/candidate/suppression. Đổi truth nhưng giữ ledger/TI phải không
đổi predictions. Metadata dịch vụ quan sát được cần provenance/version/thời
điểm; không lấy từ vai trò generator để che false positive.

## Rulebook khởi tạo

Threshold là config thí nghiệm có version. Thay đổi có validation report và
regression fixtures; không chọn trên test hoặc hardcode ngoại lệ cho một seed.

| Code | Điều kiện bắt buộc ban đầu | Evidence tối thiểu |
| --- | --- | --- |
| STRUCTURING | 30 phút; >=8 lệnh đi từ cùng ví trong [0,8T;T); tổng các lệnh đó>=5T; >=70% số lệnh đi | Tx IDs, count, sum, near-threshold count/total ratio, window và T |
| SMURFING | 60 phút; >=8 trung gian fan-out/fan-in; >=70% giá trị nguồn được phân bổ hội tụ tới một đích qua đường thời gian <=3 hop | Source/target, trung gian, đường/tx order, lượng phân bổ không trùng |
| PEELING | >=4 tầng liên tiếp; mỗi tầng<=10 phút; continuation 80–99% balance đầu tầng; nhánh nhỏ tổng 1–20%; số dư cuối<=2% | Balance đầu/cuối, >=2 tx mỗi tầng, nhánh lớn/nhỏ, fees và timestamps |
| MIXING_PATTERN | Vùng trung gian C có >=5 nguồn ngoài và >=5 đích ngoài trong 30 phút; các điều kiện định lượng bên dưới | Boundary flow, region, phân bổ/hold time, output distribution/entropy |
| RAPID_HIGH_RISK | Đường 3–5 hop từ TI hợp lệ severity>=80/100, confidence>=0,8; mỗi lần giữ<=2 phút; chuyển tiếp>=80% phần nhận được phân bổ từ đường đang xét | TI source/version, order, dwell, amount, ratio từng hop |
| DIRECT_BLACKLIST | Sender hoặc recipient exact match category blacklist trong feed áp dụng | Network/address, direction, source/ref, confidence, validity/known_at/list_version |

STRUCTURING/SMURFING là hai biến thể cùng nhóm S; nhiều địa chỉ không chứng minh
cùng chủ hoặc nhiều người phối hợp. T−1 là dưới T nhưng vẫn phải đạt cận dưới
0,8T và toàn bộ điều kiện count/sum/ratio. T và T+1 không thuộc near-threshold.

Peeling account-based không có transaction hai outputs; tầng gồm các giao
dịch riêng và trừ đủ fee. Nếu inflow khác/lịch sử balance thiếu khiến phân bổ
không rõ, báo coverage/ambiguity và không giả đủ điều kiện balance của rule.

## Định lượng mixing

Tạo candidate từ nhiều đối tác vào/ra, lân cận 2–3 hop và hợp nhất vùng candidate
trùng nhau theo cách xác định. Với vùng C, chỉ cộng flow qua biên, không cộng
edge nội bộ nhiều lần. I = external inflow, O = external outflow, F = fee do
ví C trả, B0/B1 = balance vùng ở đầu/cuối cửa sổ.

`B0 + I = B1 + O + F` là kiểm tra dữ liệu đúng, không phải tín hiệu AML.
Quy ước snapshot B0 phải nhất quán với cửa sổ bao gồm đầu mút; khi fee_sink là
ví hệ thống thì loại khỏi vùng C và không tính fee hai lần.

Rule ban đầu yêu cầu đồng thời:

- `I>=5T` và `O/I` trong `[0,85;1,05]`.
- >=70% giá trị nạp được phân bổ đến lần rút trong 10 phút.
- Output tới >=5 đích; entropy chuẩn hóa>=0,8.
- Các điều kiện topology/window ở bảng rulebook.

`H=-sum(p_i*ln(p_i))/ln(k)`, p_i là tỷ trọng amount theo đích, k>=2;
k<2 thì H=0. Guard I=0/tổng output=0. Ratio quyết định trên tiền có thể dùng
so sánh tích số nguyên để tránh sai boundary. Payroll/custody/treasury có thể
giống topology; không bỏ qua bằng hidden truth hoặc khẳng định mixer đã xác định.

## Graph và ngân sách

`NetworkX.MultiDiGraph`: node=(run_id,address), edge key=tx_hash trong run;
attrs gồm value_base, asset_id, timestamp, order, fee. Không dùng DiGraph làm
mất cạnh song song. Graph gộp centrality phải giữ count/value và ánh xạ edges gốc.
Degree/unique partners/flows là candidate features; betweenness chỉ phụ trợ,
chạy batch/approximate có seed, không toàn mạng sau từng tx.

Tracing mặc định max_hops=5, lookback=24h; 20.000 cạnh được xét và 100 evidence
paths mỗi truy vấn. Hết budget đặt truncated=true, coverage=partial và lý do.
Giới hạn frontend 1.000 node/3.000 edge là budget trả kết quả khác; không nhầm
với số cạnh engine đã xét. Có timeout/cancellation/candidate bound; không mở
full graph lên frontend để frontend tự chấm AML.

## Haircut và bảo toàn phân bổ

Account-based không có coin identity. BFS/DFS chỉ tìm đường/candidate, không
đủ để chứng minh lượng vốn. Replay balance/allocation trước; dựng evidence trên
state đó. Khai báo haircut như giả định phân tích, không “truy vết chính xác từng coin”.

Trước giao dịch, balance B và exposure amount L thỏa `0<=L<=B`.
Với B>0, q=L/B; value v và fee f phân bổ qv cho recipient và qf cho fee_sink;
sender giảm v+f và q(v+f). Tính bằng integer/rational hoặc Decimal đủ precision
theo ADR; không dùng float cho lượng tiền. Làm tròn base unit xác định và giữ
remainder ở sender theo quy tắc đã chốt, bảo toàn sau mỗi apply.

Theo dõi các phần vốn rời nhau: source, amount, hop/time đủ giải thích. Khi
thêm TI source chỉ gán phần chưa được gán; không gắn lại cùng lượng cho nhiều
source rồi cộng. Không cộng các đường hội tụ như lượng tiền mới; kiểm tra tổng
amount phần vốn<=balance/value liên quan. Self-transfer/vòng A→B→A chỉ thu fee,
không tự tăng lượng exposure. Có state/history để xử lý fee_sink trong conservation.

Giới hạn hop/lookback giới hạn phân tích, không xóa balance hoặc vốn ra khỏi
ledger. Lịch sử đầu kỳ thiếu phải partial. High engine score không tự tạo TI seed.
Dust từ blacklist có direct alert riêng nhưng không nhuộm 100% khoản normal về sau.
Gửi tới blacklist là counterparty risk; không đảo chiều để gán nguồn quá khứ.

## Threat intelligence

Feed records có network_id/address/category, severity, confidence, source/ref,
known_at, valid_from/valid_to, list_version và enabled. Match exact sau normalize
đúng loại network; không fuzzy/lowercase mọi network tùy tiện. `as-known` chỉ
dùng feed known_at<=as_of_time và hiệu lực tại transaction time. Chốt biên
valid_to, ngữ nghĩa as_of_time và conflict resolution trong ADR; không đoán.

Retrospective với feed mới lưu analysis_mode=retrospective, source snapshot và
assessment/version riêng. Không gộp vào online metrics hay overwrite lịch sử.
Ví badge “blacklist” chỉ do feed phù hợp, không lấy từ score/severity engine.

## Optional Isolation Forest

Chỉ thêm sau B2 baseline có kiểm chứng. Feature unit là wallet-window:
log1p(volume), count, unique counterparties, median/std amount, time gaps,
fan-in/out, near-threshold ratio, turnover, tuổi từ first_seen.
Không dùng tx_hash/address/scenario/role/nhãn/tương lai. first_seen là lúc hệ
thống quan sát, không phải lúc ví được tạo ngoài đời.

Khởi tạo theo kế hoạch: n_estimators=200, max_samples=256,
contamination='auto', random_state=42. Train chủ yếu nền, công bố contamination
thực nghiệm. score_samples càng thấp càng bất thường; không coi output là xác
suất hay auto đặt nhãn mixing. `z=-score_samples(x)`;
`A=clip((z-q50)/(q99-q50),0,1)` với q50/q99 fit trên train đã khóa.
q99=q50 là model unavailable. Scaler/threshold từ train/validation; không fit test.
Artefact phải có feature order/schema, trained-on split, random_state và version.

## Công thức điểm phải dùng chung

```text
R_blend = 100 * (0.35E + 0.30P + 0.15V + 0.10Q + 0.10A)
P = max(S, C, M)
V = min(1, max(count_out_sender_5m, count_in_recipient_5m) / 20)
Q = min(1, max(volume_out_sender_24h, volume_in_recipient_24h) / (3T))
A = max(sender_window_anomaly, recipient_window_anomaly)
E_flow = min(1, sum((a_j / value) * c_j * 0.7**(h_j-1) * exp(-delta_j/24h)))
c_j = confidence_j * severity_j/100
E = max(E_flow, E_direct_counterparty)
R = round_half_up(clip(max(R_blend, applicable_floors), 0, 100))
```

Mọi component 0..1; S=STRUCTURING/SMURFING hit, C=PEELING hit,
M=MIXING_PATTERN hit, baseline 0/1 khi đủ rule. h tính cả tx hiện tại,
delta là thời gian từ source transfer-out, a_j rời nhau và sum(a_j)<=value.
Decay thay đổi điểm, không giảm amount ledger. Chỉ tính phần nằm trong tracing
scope, phần ngoài có coverage. Công thức E_direct_counterparty cần ADR trước
implementation; không tự đồng nhất toàn bộ inbound/outbound exposure.

| Điều kiện | Floor |
| --- | --- |
| DIRECT_BLACKLIST xác thực | 95 |
| RAPID_HIGH_RISK đủ rule | 80 |
| Ít nhất một motif S/C/M đủ | 70 |
| Không đủ điều kiện trên | 0 |

Giữ nhiều floor trong explanation và lấy max. Không dùng Python `round()` mặc
định thay half-up. Ví dụ E=0,8/P=1/V=0,9/Q=0,7/A=0,8: blend=86,5→R=87;
motif đơn độc blend=30→R=70. V/Q cao đơn độc không tạo motif hoặc blacklist.

| R | Severity / hành vi |
| --- | --- |
| 0–29 | Low, lưu assessment |
| 30–49 | Medium, lưu/lọc theo dõi |
| 50–69 | Elevated, hàng đợi xem xét |
| 70–89 | High, AML alert |
| 90–100 | Critical, ưu tiên cao nhất |

Baseline chưa dùng ML: A=0, model_status thể hiện baseline, không tái phân bổ
trọng số. Hybrid lỗi model: A theo fallback được khai báo, model_status=unavailable,
coverage=partial; lưu lý do và không render như model đã chạy bình thường.
Không bỏ floor vì ML lỗi; không coi blend/final score là xác suất.

## Assessment/evidence đủ truy xét

Lưu run/trigger/evidence tx IDs, path order và amount allocations, feature
snapshot/window, rule hits với observed/threshold, source snapshots, E/P/V/Q/A,
blend/floors/final/severity, detected_at, as_of_order/time, analysis_mode/version,
rule/model/feed versions, coverage/truncated và nguyên nhân thiếu dữ liệu.
Evidence tham chiếu ledger cùng run, không dùng đoạn text suy đoán thay tx IDs.
Rule version mới tạo assessment mới; review của analyst giữ ở lịch sử riêng.

## Phần mở rộng đã được merge và review

Circular Flow, U-Turn và PageRank là mở rộng tập motif theo
[ADR-0002](../../docs/decisions/0002-reviewed-motifs-rules-v2.md), dùng rules-v2.
Giữ công thức E/blend và các floor đã khóa. PageRank chỉ cung cấp ngữ cảnh;
evidence lượng tiền phải từ haircut theo thời gian, có budget và coverage.
Không diễn giải các detector bổ sung là đã có trong kế hoạch gốc.

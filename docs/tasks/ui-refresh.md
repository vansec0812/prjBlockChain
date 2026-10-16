# Chỉnh giao diện AML Observatory

Yêu cầu: giao diện tiết chế hơn, tham khảo [Apple](https://www.apple.com/), giữ
nguyên spec và chức năng; commit và push repository hiện có.

Hướng thiết kế: nền trắng/xám trung tính, system font, hierarchy rõ, điều hướng
ngang bằng chữ, xanh dành cho thao tác. Giữ màu severity của graph, trạng thái,
coverage, online/hồi cứu và đơn vị tiền. Không sử dụng logo, ảnh hay font Apple.

- [x] Đọc reference, frontend rules và kiểm tra source/working tree.
- [x] Chỉnh shell, login, typography, form, bảng, graph inspector và mobile.
- [x] Kiểm tra build/unit/E2E, các màn hình và viewport đại diện.
- [x] Cập nhật ảnh/báo cáo, review diff và chuẩn bị commit/push theo yêu cầu.

Phạm vi liên quan R10–R12 / T13, T18, T20: API contracts, money formatter,
detector/scoring, RBAC, xử lý run/as-of và dữ liệu bền vững giữ nguyên. Các thay
đổi React nằm ở markup/presentation/accessibility; không sửa pipeline nghiệp vụ.

Bằng chứng: [báo cáo UI](../reports/ui-validation.md). Phần giao diện và phần
kiểm thử/tài liệu được chia thành hai commit; đích bàn giao là nhánh main.

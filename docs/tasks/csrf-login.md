# Sửa lỗi CSRF lúc đăng nhập

Người dùng gặp `Invalid CSRF token` khi đăng nhập. Client cũ giữ CSRF trong bộ
nhớ tab sau bootstrap; cookie lại được chia sẻ giữa các tab và có thể hết hạn
hoặc bị xóa. Khi tab khác đăng nhập, backend xoay CSRF trong cookie nhưng token
ở tab đang mở không được đồng bộ trước khi gửi login.

## Tái lập trước sửa

- Browser test: mở hai tab chung context; đăng nhập Viewer ở tab thứ hai, rồi
  đăng nhập Admin ở tab đầu → CSRF 403, vẫn ở màn hình đăng nhập.
- Browser test: tải màn hình đăng nhập, xóa cookie của context test, submit →
  CSRF 403. Cả hai regression tests fail trên web image cũ.
- Unit regression: sáu case fail trước sửa; case không tự gửi lại data mutation
  đã pass và được giữ lại làm ràng buộc an toàn.
- Đọc middleware Starlette thực trong image: phiên bản hiện tại chỉ ghi cookie
  khi session thay đổi. Không sửa middleware hoặc kết luận GET thường ghi đè cookie.

## Thay đổi

Chỉ sửa API client frontend: gộp bootstrap `/session` đang chạy để tránh hai
session ẩn danh cạnh tranh; lấy token mới trước login với `cache: no-store`;
đồng bộ và thử lại login tối đa một lần khi đúng lỗi CSRF 403. Giữ nguyên body,
credentials same-origin, signal và response token mới sau đăng nhập.

Không retry wrong password/Origin denied hoặc replay các thao tác review/create/
feed/replay. Không thay backend, không tắt CSRF/cookie/origin/RBAC, không đổi
database hoặc spec AML. Contract ở [local API](../contracts/local-api.md).

## Kiểm chứng

- `npm.cmd test`: 11 unit cases passed (7 auth/client regression + 4 money).
- `npm.cmd run build`: TypeScript/Vite passed.
- `npm.cmd run test:e2e`: 4 browser tests passed trên web image mới: hai auth
  regression và hai workspace/responsive tests.
- Docker web recreate riêng; API/worker/db giữ nguyên dữ liệu và process.
- Full GitHub CI kiểm backend/PostgreSQL, API smoke, unit/build và browser sau push.

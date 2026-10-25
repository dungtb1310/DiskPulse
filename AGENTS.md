# AGENTS.md — Quy Tắc & Kiến Trúc Dự Án Smart Disk Cleaner

## 1. Môi Trường Thực Thi Bắt Buộc
- Mọi lệnh Python/CLI trong dự án BẮT BUỘC chạy qua môi trường Conda `base`:
  ```powershell
  conda run -n base --no-capture-output <command>
  ```
- Khởi chạy Web Dashboard tại địa chỉ `http://127.0.0.1:8765`:
  ```powershell
  conda run -n base --no-capture-output python main.py
  ```

## 2. Quy Trình Git & Jira / Issue Tracking
- Nhánh chính phát triển: `develop`
- Mọi tính năng hoặc sửa lỗi đều rẽ nhánh từ `develop` theo định dạng `feature/<jira-key>-<ten-feature>` hoặc `bug/<jira-key>-<ten-bug>`.
- Gắn mã `[DISK-1]` (hoặc mã Jira tương ứng) vào tên nhánh, message Commit, và Pull Request.

## 3. Quy Tắc Kiểm Thử Thực Tế (UI/DOM & E2E)
- Sau mỗi lần cập nhật code, bắt buộc chạy server thực tế và kiểm tra giao diện (UI/DOM), các nút bấm, bộ lọc, biểu đồ ECharts và các API xem có hoạt động ổn định, đúng kỳ vọng không.

## 4. Kiến Trúc Hệ Thống
- `main.py`: FastAPI Server & REST/SSE API endpoints (`/api/drives`, `/api/scan/start`, `/api/scan/status`, `/api/clean`, `/api/open-explorer`, `/api/history`).
- `scanner.py`: Bộ máy quét đa luồng (`os.scandir` + `ThreadPoolExecutor`), kết hợp:
  1. **System & App Cache/Junk Scanner**: Quét nhanh các vị trí cố định trên ổ hệ thống (Windows Temp, Prefetch, Browser Cache, Pip/Conda/NPM/Yarn/PNPM/NuGet/HuggingFace/VSCode Cache, Recycle Bin, Crash Dumps...).
  2. **Deep Drive Scanner**: Quét sâu các ổ đĩa (`C:`, `D:`, `E:`...) với cơ chế gom cụm thông minh (`node_modules`, `.venv`, `venv`, `target`, `dist`, `build`, `__pycache__`, `.next`, `.gradle`...), kiểm tra độ tuổi sửa đổi code của project (Stale Project Check) và lọc các file lẻ dung lượng lớn (`>= 100 MB`).
- `cleaner.py`: Bộ máy dọn dẹp an toàn hỗ trợ 2 chế độ:
  - **Recycle Bin (`send2trash`)**: An toàn, có thể khôi phục.
  - **Permanent Fast Delete**: Xóa vĩnh viễn siêu tốc (dành cho `node_modules` hàng vạn file nhỏ hoặc khi ổ đĩa đầy 100%).
  - **Hard Blocklist**: Bảo vệ tuyệt đối các thư mục hệ thống trọng yếu (`C:\Windows\System32`, `Program Files`, `.git`, thư mục gốc ổ đĩa...).
- `static/index.html`, `static/styles.css`, `static/app.js`: Giao diện Web Dashboard chuẩn Operate Mode (HTML5 + CSS + Vanilla JS + Apache ECharts Treemap).

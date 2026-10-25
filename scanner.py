import os
import sys
import time
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
import psutil


def format_bytes(size: int) -> str:
    """Chuyển đổi byte sang định dạng dễ đọc (KB, MB, GB, TB)."""
    if size <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    val = float(size)
    while val >= 1024.0 and idx < len(units) - 1:
        val /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(val)} B"
    return f"{val:.2f} {units[idx]}"


def make_item_id(path: str) -> str:
    norm = os.path.normcase(os.path.normpath(path))
    return hashlib.sha1(norm.encode("utf-8", errors="ignore")).hexdigest()[:16]


@dataclass
class ScanItem:
    id: str
    name: str
    path: str
    drive: str
    size_bytes: int
    size_formatted: str
    file_count: int
    category: str
    category_label: str
    risk_level: str  # "safe" | "caution" | "review"
    risk_label: str
    reason_vi: str
    impact_vi: str
    last_modified: str
    last_modified_ts: float
    days_inactive: int
    is_dir: bool
    contents_only: bool = False
    project_name: str = ""


CATEGORY_LABELS = {
    "system_temp": "Rác & Bộ Nhớ Tạm Hệ Thống",
    "browser_app_cache": "Cache Trình Duyệt & Ứng Dụng",
    "dev_global_cache": "Cache Công Cụ Lập Trình (Global)",
    "dev_project_artifact": "Thư Mục Build & Dependencies Dự Án",
    "downloads_installer": "Bộ Cài & File Nén (Downloads)",
    "log_dump": "File Log, Crash Dump & Rác Rải Rác",
    "large_file": "File Lẻ Dung Lượng Lớn",
}

RISK_LABELS = {
    "safe": "An toàn tuyệt đối",
    "caution": "Cần lưu ý",
    "review": "Xem xét thủ công",
}

# Các thư mục hệ thống hoặc phần mềm cài đặt tuyệt đối bỏ qua khi quét sâu
SKIP_DEEP_DIRS_LOWER = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "appdata",
    "miniconda3",
    "anaconda3",
    "miniconda",
    "anaconda",
    "$recycle.bin",
    "system volume information",
    "recovery",
    "perflogs",
    "msocache",
    "$winreagent",
    "config.msi",
    "documents and settings",
    ".git",
    ".vscode",
    ".cursor",
    ".antigravity",
    ".gemini",
    "site-packages",
    "steamapps",
    "riot games",
}

# Các thư mục con trong प्रोजेक्ट là artifact có thể gom cụm
ALWAYS_SAFE_ARTIFACT_DIRS = {
    "__pycache__": (
        "Bytecode cache của Python",
        "Python tự động tạo lại ngay khi chạy chương trình, không mất bất kỳ dữ liệu nào.",
    ),
    ".pytest_cache": (
        "Bộ nhớ đệm kết quả chạy Pytest",
        "Pytest tự tạo lại khi chạy test lần tiếp theo.",
    ),
    ".mypy_cache": (
        "Bộ nhớ đệm kiểm tra kiểu dữ liệu Mypy",
        "Mypy tự tạo lại khi kiểm tra code lần tiếp theo.",
    ),
    ".ruff_cache": (
        "Bộ nhớ đệm công cụ kiểm tra code Ruff",
        "Ruff tự tạo lại trong tích tắc khi chạy lint.",
    ),
    ".parcel-cache": (
        "Cache đóng gói của Parcel bundler",
        "Tự động tạo lại khi chạy dev/build.",
    ),
    ".turbo": (
        "Cache build của Turborepo",
        "Tự động tạo lại khi chạy lệnh turbo build/dev.",
    ),
}

PROJECT_DEPENDENCY_DIRS = {
    "node_modules": (
        "Thư viện phụ thuộc Node.js (node_modules)",
        "Có thể khôi phục bất cứ lúc nào bằng lệnh `npm install`, `pnpm install` hoặc `yarn`.",
        {"package.json"},
    ),
    ".venv": (
        "Môi trường ảo Python (.venv)",
        "Có thể tạo lại và cài đặt từ `requirements.txt` hoặc `pyproject.toml`.",
        {"requirements.txt", "pyproject.toml", "setup.py", "pipfile"},
    ),
    "venv": (
        "Môi trường ảo Python (venv)",
        "Có thể tạo lại và cài đặt từ `requirements.txt` hoặc `pyproject.toml`.",
        {"requirements.txt", "pyproject.toml", "setup.py", "pipfile"},
    ),
    ".next": (
        "Thư mục build/cache của Next.js (.next)",
        "Tự động tạo lại khi chạy `npm run dev` hoặc `npm run build`.",
        {"package.json", "next.config.js", "next.config.mjs", "next.config.ts"},
    ),
    ".nuxt": (
        "Thư mục build/cache của Nuxt.js (.nuxt)",
        "Tự động tạo lại khi chạy dự án Nuxt.",
        {"package.json", "nuxt.config.ts", "nuxt.config.js"},
    ),
    ".svelte-kit": (
        "Thư mục build tự sinh của SvelteKit",
        "Tự động tạo lại khi chạy dev hoặc build.",
        {"package.json", "svelte.config.js"},
    ),
    "target": (
        "Thư mục kết quả biên dịch Rust / Maven (target)",
        "Thường chiếm hàng GB. Tự tạo lại khi chạy `cargo build` hoặc `mvn package`.",
        {"cargo.toml", "pom.xml"},
    ),
    ".gradle": (
        "Bộ nhớ đệm cục bộ của Gradle trong dự án",
        "Gradle sẽ tự tạo lại khi build project.",
        {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"},
    ),
    "dist": (
        "Thư mục đóng gói bản phân phối (dist)",
        "Tạo lại dễ dàng bằng lệnh build của dự án.",
        {"package.json", "pyproject.toml", "setup.py", "tsconfig.json", "vite.config.ts", "vite.config.js"},
    ),
    "build": (
        "Thư mục xuất kết quả biên dịch (build)",
        "Tạo lại khi chạy lệnh build của dự án.",
        {"package.json", "pyproject.toml", "setup.py", "cmakelists.txt", "build.gradle", "build.gradle.kts"},
    ),
    "obj": (
        "Thư mục đối tượng trung gian khi biên dịch .NET / C++ (obj)",
        "Visual Studio / `dotnet build` sẽ tự động tạo lại.",
        {"*.csproj", "*.sln", "*.vcxproj", "*.fsproj"},
    ),
}


def get_drives_info() -> List[Dict]:
    """Lấy danh sách các ổ đĩa hiện có trên Windows kèm dung lượng."""
    drives = []
    seen = set()
    for part in psutil.disk_partitions(all=False):
        if not part.mountpoint or part.mountpoint in seen:
            continue
        if "cdrom" in part.opts.lower() or part.fstype == "":
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            seen.add(part.mountpoint)
            drive_code = part.mountpoint.rstrip("\\/")
            drives.append({
                "drive": drive_code,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "total_formatted": format_bytes(usage.total),
                "used_formatted": format_bytes(usage.used),
                "free_formatted": format_bytes(usage.free),
                "percent_used": round(usage.percent, 1),
            })
        except (PermissionError, OSError):
            continue
    return drives


def compute_dir_stats(path: str, cancel_event: Optional[threading.Event] = None, max_files_cap: int = 250000) -> Tuple[int, int, float]:
    """Tính nhanh tổng dung lượng (bytes), số lượng file, và thời gian sửa đổi gần nhất của thư mục."""
    total_size = 0
    file_count = 0
    latest_mtime = 0.0
    stack = [path]

    try:
        latest_mtime = os.path.getmtime(path)
    except OSError:
        pass

    while stack:
        if cancel_event and cancel_event.is_set():
            break
        curr = stack.pop()
        try:
            with os.scandir(curr) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            st = entry.stat(follow_symlinks=False)
                            total_size += st.st_size
                            file_count += 1
                            if st.st_mtime > latest_mtime:
                                latest_mtime = st.st_mtime
                            if file_count >= max_files_cap:
                                return total_size, file_count, latest_mtime
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError):
            continue

    return total_size, file_count, latest_mtime


def _has_project_marker(parent_dir: str, markers: Set[str]) -> bool:
    """Kiểm tra thư mục cha có chứa các file dấu hiệu của một project (package.json, Cargo.toml, *.csproj...) không."""
    try:
        with os.scandir(parent_dir) as it:
            for entry in it:
                if not entry.is_file(follow_symlinks=False):
                    continue
                name_lower = entry.name.lower()
                if name_lower in markers:
                    return True
                for m in markers:
                    if m.startswith("*.") and name_lower.endswith(m[1:]):
                        return True
    except (PermissionError, OSError):
        pass
    return False


def _get_project_code_last_mtime(project_dir: str, ignore_dir_name: str) -> float:
    """Lấy thời điểm sửa đổi gần nhất của các file mã nguồn trong thư mục gốc project."""
    latest = 0.0
    skip_names = {
        ignore_dir_name.lower(),
        ".git",
        ".idea",
        ".vscode",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "target",
        "dist",
        "build",
        ".next",
    }
    try:
        latest = os.path.getmtime(project_dir)
    except OSError:
        pass

    try:
        with os.scandir(project_dir) as it:
            for entry in it:
                name_l = entry.name.lower()
                if name_l in skip_names:
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                    if st.st_mtime > latest:
                        latest = st.st_mtime
                    # Quét thêm 1 tầng thư mục con (VD: src/, app/, lib/) để chính xác hơn
                    if entry.is_dir(follow_symlinks=False):
                        with os.scandir(entry.path) as sub_it:
                            for sub_e in sub_it:
                                if sub_e.name.lower() in skip_names:
                                    continue
                                try:
                                    sub_st = sub_e.stat(follow_symlinks=False)
                                    if sub_st.st_mtime > latest:
                                        latest = sub_st.st_mtime
                                except (PermissionError, OSError):
                                    continue
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass

    return latest


def get_known_cache_targets() -> List[Dict]:
    """Danh sách các vị trí Cache & Temp cố định trên Windows."""
    user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    local_app = os.environ.get("LOCALAPPDATA", os.path.join(user_profile, "AppData", "Local"))
    roaming_app = os.environ.get("APPDATA", os.path.join(user_profile, "AppData", "Roaming"))
    win_dir = os.environ.get("WINDIR", r"C:\Windows")
    temp_dir = os.environ.get("TEMP", os.path.join(local_app, "Temp"))

    targets = [
        # 1. System Temp & Dump
        {
            "name": "Windows User Temp (%TEMP%)",
            "path": temp_dir,
            "category": "system_temp",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Thư mục lưu file tạm của các ứng dụng Windows trong quá trình chạy.",
            "impact_vi": "Xóa an toàn 100%. Các file đang được phần mềm mở sẽ tự động được bỏ qua.",
        },
        {
            "name": "Windows System Temp (C:\\Windows\\Temp)",
            "path": os.path.join(win_dir, "Temp"),
            "category": "system_temp",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Thư mục file tạm cấp hệ thống của Windows.",
            "impact_vi": "Xóa an toàn 100%, không ảnh hưởng tới hệ điều hành.",
        },
        {
            "name": "Windows Update Download Cache",
            "path": os.path.join(win_dir, "SoftwareDistribution", "Download"),
            "category": "system_temp",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Các gói cài đặt bản cập nhật Windows đã tải về máy.",
            "impact_vi": "Xóa an toàn, giải phóng các gói update cũ đã cài xong.",
        },
        {
            "name": "Application Crash Dumps (LocalAppData)",
            "path": os.path.join(local_app, "CrashDumps"),
            "category": "log_dump",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Chứa các bản ghi bộ nhớ (.dmp) khi ứng dụng bị văng/crash.",
            "impact_vi": "Xóa an toàn 100%, chỉ dùng nếu bạn cần debug lỗi crash cũ.",
        },
        {
            "name": "Windows Error Reporting (WER)",
            "path": os.path.join(local_app, "Microsoft", "Windows", "WER"),
            "category": "log_dump",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Báo cáo lỗi ứng dụng lưu tự động của Windows.",
            "impact_vi": "Xóa an toàn 100%.",
        },
        {
            "name": "DirectX Shader Cache (NVIDIA DXCache)",
            "path": os.path.join(local_app, "NVIDIA", "DXCache"),
            "category": "system_temp",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm shader đồ họa của card rời NVIDIA.",
            "impact_vi": "Xóa an toàn, card đồ họa sẽ tự biên dịch lại shader mới khi cần.",
        },
        {
            "name": "NVIDIA OpenGL Shader Cache (GLCache)",
            "path": os.path.join(local_app, "NVIDIA", "GLCache"),
            "category": "system_temp",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm OpenGL shader của card đồ họa NVIDIA.",
            "impact_vi": "Xóa an toàn 100%.",
        },
        {
            "name": "Direct3D Shader Cache (D3DSCache)",
            "path": os.path.join(local_app, "D3DSCache"),
            "category": "system_temp",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm Direct3D của Windows.",
            "impact_vi": "Xóa an toàn 100%, tự tạo lại khi cần.",
        },
        {
            "name": "Windows Package Cache (LocalAppData\\Package Cache)",
            "path": os.path.join(local_app, "Package Cache"),
            "category": "system_temp",
            "risk_level": "caution",
            "contents_only": True,
            "reason_vi": "Lưu trữ các bộ cài đặt phụ trợ Visual C++ Redistributable / MSI cũ.",
            "impact_vi": "Xóa giúp giải phóng ổ C; tuy nhiên nếu gỡ/sửa phần mềm cũ có thể cần tải lại trình cài đặt.",
        },
        {
            "name": "Windows Explorer Thumbnail Cache",
            "path": os.path.join(local_app, "Microsoft", "Windows", "Explorer"),
            "category": "system_temp",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm ảnh thu nhỏ (thumbcache) của File Explorer.",
            "impact_vi": "Xóa an toàn, Windows tự tạo lại ảnh thu nhỏ khi bạn mở thư mục.",
        },

        # 2. Browser & App Cache
        {
            "name": "Google Chrome — Web Cache",
            "path": os.path.join(local_app, "Google", "Chrome", "User Data", "Default", "Cache"),
            "category": "browser_app_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm hình ảnh, script của trình duyệt Chrome (Không chứa mật khẩu hay lịch sử).",
            "impact_vi": "Xóa an toàn 100%, không mất đăng nhập hay tab đang lưu.",
        },
        {
            "name": "Google Chrome — Code Cache",
            "path": os.path.join(local_app, "Google", "Chrome", "User Data", "Default", "Code Cache"),
            "category": "browser_app_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm mã JavaScript/WASM đã biên dịch của Chrome.",
            "impact_vi": "Xóa an toàn 100%, trình duyệt tự tạo lại khi lướt web.",
        },
        {
            "name": "Microsoft Edge — Web Cache",
            "path": os.path.join(local_app, "Microsoft", "Edge", "User Data", "Default", "Cache"),
            "category": "browser_app_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm duyệt web của Microsoft Edge.",
            "impact_vi": "Xóa an toàn 100%, không mất mật khẩu hay cookie đăng nhập.",
        },
        {
            "name": "Microsoft Edge — Code Cache",
            "path": os.path.join(local_app, "Microsoft", "Edge", "User Data", "Default", "Code Cache"),
            "category": "browser_app_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm JavaScript của Microsoft Edge.",
            "impact_vi": "Xóa an toàn 100%.",
        },
        {
            "name": "VS Code — CachedData & VSIX Cache",
            "path": os.path.join(roaming_app, "Code", "CachedData"),
            "category": "browser_app_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm biên dịch của các phiên bản VS Code cũ và hiện tại.",
            "impact_vi": "Xóa an toàn 100%, giữ nguyên toàn bộ cài đặt và extension của VS Code.",
        },
        {
            "name": "VS Code — CachedExtensionVSIXs",
            "path": os.path.join(roaming_app, "Code", "CachedExtensionVSIXs"),
            "category": "browser_app_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Các file bộ cài đặt extension (.vsix) đã tải xong của VS Code.",
            "impact_vi": "Xóa an toàn 100%, các extension đã cài vẫn hoạt động bình thường.",
        },
        {
            "name": "VS Code — Web/GPU Cache",
            "path": os.path.join(roaming_app, "Code", "Cache"),
            "category": "browser_app_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Cache giao diện Electron của VS Code.",
            "impact_vi": "Xóa an toàn 100%.",
        },
        {
            "name": "Discord — App Cache",
            "path": os.path.join(roaming_app, "discord", "Cache"),
            "category": "browser_app_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm ảnh, video, sticker đã xem trên Discord.",
            "impact_vi": "Xóa an toàn 100%, không mất tin nhắn hay tài khoản.",
        },
        {
            "name": "Zalo PC — Temp & Cache",
            "path": os.path.join(local_app, "ZaloPC"),
            "category": "browser_app_cache",
            "risk_level": "caution",
            "contents_only": False,
            "reason_vi": "Dữ liệu cục bộ và bộ nhớ đệm của ứng dụng Zalo trên máy tính.",
            "impact_vi": "Lưu ý: Xóa toàn bộ thư mục ZaloPC có thể làm mất file/ảnh cũ chưa đồng bộ Cloud.",
        },

        # 3. Developer Global Caches
        {
            "name": "Pip Global Cache (Python)",
            "path": os.path.join(local_app, "pip", "cache"),
            "category": "dev_global_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Lưu các file `.whl` / `.tar.gz` mà `pip install` đã tải về từ trước.",
            "impact_vi": "Xóa an toàn 100%! Không ảnh hưởng các thư viện Python đã cài; chỉ tải lại khi cài mới.",
        },
        {
            "name": "UV Package Manager Cache (Python)",
            "path": os.path.join(local_app, "uv", "cache"),
            "category": "dev_global_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm gói cài đặt của trình quản lý thư viện Python `uv`.",
            "impact_vi": "Xóa an toàn 100%, các môi trường ảo hiện tại vẫn chạy bình thường.",
        },
        {
            "name": "Conda Package Tarballs & Cache (Miniconda3/pkgs)",
            "path": os.path.join(user_profile, "miniconda3", "pkgs"),
            "category": "dev_global_cache",
            "risk_level": "caution",
            "contents_only": False,
            "reason_vi": "Kho lưu trữ các gói thư viện Conda đã tải về (thường chiếm nhiều GB).",
            "impact_vi": "Nên dùng chế độ dọn chuẩn hoặc kiểm tra kỹ vì Conda dùng hardlink từ pkgs sang các env.",
        },
        {
            "name": "Anaconda Package Cache (Anaconda3/pkgs)",
            "path": os.path.join(user_profile, "anaconda3", "pkgs"),
            "category": "dev_global_cache",
            "risk_level": "caution",
            "contents_only": False,
            "reason_vi": "Kho lưu trữ các gói thư viện Anaconda đã tải về.",
            "impact_vi": "Lưu ý: Conda liên kết các gói từ thư mục này vào môi trường đang chạy.",
        },
        {
            "name": "NPM Global Cache (npm-cache)",
            "path": os.path.join(local_app, "npm-cache"),
            "category": "dev_global_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm tải gói của Node.js NPM (`LocalAppData\\npm-cache`).",
            "impact_vi": "Xóa an toàn 100%, tương đương lệnh `npm cache clean --force`.",
        },
        {
            "name": "NPM Roaming Cache",
            "path": os.path.join(roaming_app, "npm-cache"),
            "category": "dev_global_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm tải gói của Node.js NPM (`AppData\\Roaming\\npm-cache`).",
            "impact_vi": "Xóa an toàn 100%, không ảnh hưởng tới bất kỳ project Node.js nào.",
        },
        {
            "name": "Yarn Global Cache",
            "path": os.path.join(local_app, "Yarn", "Cache"),
            "category": "dev_global_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm các gói thư viện đã tải của Yarn.",
            "impact_vi": "Xóa an toàn 100%, không ảnh hưởng tới các thư mục `node_modules` hiện có.",
        },
        {
            "name": "PNPM Store Cache",
            "path": os.path.join(local_app, "pnpm-store"),
            "category": "dev_global_cache",
            "risk_level": "caution",
            "contents_only": True,
            "reason_vi": "Kho lưu trữ tập trung của PNPM.",
            "impact_vi": "Lưu ý: PNPM dùng hardlink từ store vào các project; nếu xóa sẽ cần chạy `pnpm install` lại.",
        },
        {
            "name": "NuGet Global Packages Cache (.NET)",
            "path": os.path.join(user_profile, ".nuget", "packages"),
            "category": "dev_global_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm các thư viện .NET / C# tải qua NuGet.",
            "impact_vi": "Xóa an toàn, Visual Studio / `dotnet restore` sẽ tự tải lại các gói đang dùng.",
        },
        {
            "name": "Gradle Global Caches (Java/Android)",
            "path": os.path.join(user_profile, ".gradle", "caches"),
            "category": "dev_global_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm các bản build và thư viện tải về của Gradle.",
            "impact_vi": "Xóa an toàn 100%, Gradle tự tải lại thư viện cần thiết khi build project.",
        },
        {
            "name": "Maven Local Repository (.m2/repository)",
            "path": os.path.join(user_profile, ".m2", "repository"),
            "category": "dev_global_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Kho lưu trữ thư viện `.jar` đã tải của Maven.",
            "impact_vi": "Xóa an toàn, Maven sẽ tự động tải lại các dependency khi bạn build dự án.",
        },
        {
            "name": "Cargo Registry Cache (Rust)",
            "path": os.path.join(user_profile, ".cargo", "registry", "cache"),
            "category": "dev_global_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Các file nén `.crate` đã tải về của trình quản lý gói Rust Cargo.",
            "impact_vi": "Xóa an toàn 100%.",
        },
        {
            "name": "HuggingFace Models & Datasets Cache",
            "path": os.path.join(user_profile, ".cache", "huggingface", "hub"),
            "category": "dev_global_cache",
            "risk_level": "caution",
            "contents_only": True,
            "reason_vi": "Chứa các trọng số mô hình AI (LLM, Diffusion...) tải từ HuggingFace Hub.",
            "impact_vi": "Giải phóng rất nhiều GB, nhưng bạn sẽ phải tải lại model nếu chạy lại code AI đó.",
        },
        {
            "name": "PyTorch Hub & Torch Cache",
            "path": os.path.join(user_profile, ".cache", "torch"),
            "category": "dev_global_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm model pretrained và kernel của PyTorch.",
            "impact_vi": "Xóa an toàn, PyTorch sẽ tự tải lại model khi code gọi tới.",
        },
        {
            "name": "Google Chrome — Service Worker CacheStorage",
            "path": os.path.join(local_app, "Google", "Chrome", "User Data", "Default", "Service Worker", "CacheStorage"),
            "category": "browser_app_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm offline của các trang web PWA trên Chrome (thường phình rất to).",
            "impact_vi": "Xóa an toàn 100%, không mất tài khoản đăng nhập hay mật khẩu.",
        },
        {
            "name": "Microsoft Edge — Service Worker CacheStorage",
            "path": os.path.join(local_app, "Microsoft", "Edge", "User Data", "Default", "Service Worker", "CacheStorage"),
            "category": "browser_app_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm offline Service Worker của Microsoft Edge.",
            "impact_vi": "Xóa an toàn 100%, không mất đăng nhập.",
        },
        {
            "name": "Cốc Cốc Browser — Web Cache",
            "path": os.path.join(local_app, "CocCoc", "Browser", "User Data", "Default", "Cache"),
            "category": "browser_app_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm duyệt web của trình duyệt Cốc Cốc.",
            "impact_vi": "Xóa an toàn 100%.",
        },
        {
            "name": "Brave Browser — Web Cache",
            "path": os.path.join(local_app, "BraveSoftware", "Brave-Browser", "User Data", "Default", "Cache"),
            "category": "browser_app_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm duyệt web của trình duyệt Brave.",
            "impact_vi": "Xóa an toàn 100%.",
        },
        {
            "name": "CapCut PC — Media & Render Cache",
            "path": os.path.join(local_app, "CapCut", "User Data", "Cache"),
            "category": "browser_app_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm render proxy và hiệu ứng của CapCut PC.",
            "impact_vi": "Xóa an toàn, CapCut sẽ tự render lại preview nếu mở lại project cũ.",
        },
        {
            "name": "Go Build Cache (go-build)",
            "path": os.path.join(local_app, "go-build"),
            "category": "dev_global_cache",
            "risk_level": "safe",
            "contents_only": True,
            "reason_vi": "Bộ nhớ đệm biên dịch của ngôn ngữ Go.",
            "impact_vi": "Xóa an toàn 100%, tương đương `go clean -cache`.",
        },
        {
            "name": "Puppeteer Browser Binaries Cache",
            "path": os.path.join(user_profile, ".cache", "puppeteer"),
            "category": "dev_global_cache",
            "risk_level": "caution",
            "contents_only": True,
            "reason_vi": "Các bản trình duyệt Chromium tải tự động bởi Puppeteer.",
            "impact_vi": "Xóa giúp giải phóng hàng trăm MB; tự tải lại nếu dùng Puppeteer.",
        },
    ]
    return targets


class ScanManager:
    """Quản lý tiến trình quét ổ đĩa đa luồng và cập nhật kết quả theo thời gian thực."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.status: str = "idle"  # idle | running | completed | stopped | error
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.current_path: str = ""
        self.scanned_dirs: int = 0
        self.scanned_files: int = 0
        self.items: Dict[str, ScanItem] = {}
        self.known_paths_norm: Set[str] = set()
        self.error_message: str = ""
        self.config: Dict = {
            "drives": [],
            "include_known_caches": True,
            "include_deep_scan": True,
            "large_file_mb": 100,
            "stale_project_days": 14,
        }

    def start_scan(
        self,
        drives: Optional[List[str]] = None,
        include_known_caches: bool = True,
        include_deep_scan: bool = True,
        large_file_mb: int = 100,
        stale_project_days: int = 14,
    ) -> Dict:
        if self.status == "running":
            self.stop_scan()

        available_drives = [d["mountpoint"] for d in get_drives_info()]
        if not drives:
            selected_drives = available_drives
        else:
            selected_drives = []
            for d in drives:
                norm_d = d.upper().rstrip("\\/") + "\\"
                if norm_d in available_drives or os.path.exists(norm_d):
                    selected_drives.append(norm_d)
            if not selected_drives:
                selected_drives = available_drives

        with self._lock:
            self._cancel_event.clear()
            self.status = "running"
            self.started_at = time.time()
            self.finished_at = None
            self.current_path = "Đang khởi tạo bộ máy quét..."
            self.scanned_dirs = 0
            self.scanned_files = 0
            self.items.clear()
            self.known_paths_norm.clear()
            self.error_message = ""
            self.config = {
                "drives": selected_drives,
                "include_known_caches": include_known_caches,
                "include_deep_scan": include_deep_scan,
                "large_file_mb": max(10, int(large_file_mb)),
                "stale_project_days": max(1, int(stale_project_days)),
            }

        self._thread = threading.Thread(target=self._run_scan_worker, daemon=True)
        self._thread.start()
        return self.get_snapshot()

    def stop_scan(self) -> Dict:
        self._cancel_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.5)
        with self._lock:
            if self.status == "running":
                self.status = "stopped"
                self.finished_at = time.time()
                self.current_path = "Đã dừng quét theo yêu cầu."
        return self.get_snapshot()

    def remove_item_ids(self, item_ids: List[str]):
        with self._lock:
            for iid in item_ids:
                self.items.pop(iid, None)

    def _add_item(self, item: ScanItem):
        if item.size_bytes < 512 * 1024:  # Bỏ qua các mục quá nhỏ (< 512 KB) để tránh nhiễu
            return
        with self._lock:
            self.items[item.id] = item

    def _run_scan_worker(self):
        try:
            cfg = self.config
            now_ts = time.time()

            # BƯỚC 1: Quét nhanh các vị trí Cache & Temp cố định
            if cfg["include_known_caches"] and not self._cancel_event.is_set():
                self._scan_known_locations(now_ts)

            # BƯỚC 2: Quét thư mục Downloads của User để tìm bộ cài đặt / file nén cũ
            if cfg["include_known_caches"] and not self._cancel_event.is_set():
                self._scan_downloads_installers(now_ts)

            # BƯỚC 3: Quét sâu các ổ đĩa đã chọn (Multi-threaded)
            if cfg["include_deep_scan"] and not self._cancel_event.is_set():
                self._scan_drives_deep(
                    drives=cfg["drives"],
                    large_file_bytes=cfg["large_file_mb"] * 1024 * 1024,
                    stale_days=cfg["stale_project_days"],
                    now_ts=now_ts,
                )

            with self._lock:
                if self._cancel_event.is_set():
                    self.status = "stopped"
                    self.current_path = "Đã dừng quét."
                else:
                    self.status = "completed"
                    self.current_path = "Hoàn tất quét toàn bộ các ổ đĩa."
                self.finished_at = time.time()
        except Exception as exc:
            with self._lock:
                self.status = "error"
                self.error_message = str(exc)
                self.finished_at = time.time()

    def _scan_known_locations(self, now_ts: float):
        targets = get_known_cache_targets()
        for t in targets:
            if self._cancel_event.is_set():
                return
            path = t["path"]
            norm_path = os.path.normcase(os.path.normpath(path))
            with self._lock:
                self.known_paths_norm.add(norm_path)
                self.current_path = f"Đang kiểm tra Cache: {t['name']}"

            if not os.path.exists(path):
                continue

            size_bytes, file_count, latest_mtime = compute_dir_stats(path, self._cancel_event)
            if size_bytes < 1024 * 1024:  # Chỉ hiển thị nếu >= 1 MB
                continue

            drive = os.path.splitdrive(path)[0].upper() or "C:"
            days_inactive = max(0, int((now_ts - latest_mtime) / 86400)) if latest_mtime > 0 else 0
            mtime_str = datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d %H:%M") if latest_mtime > 0 else "N/A"

            cat = t["category"]
            risk = t["risk_level"]
            item = ScanItem(
                id=make_item_id(path),
                name=t["name"],
                path=path,
                drive=drive,
                size_bytes=size_bytes,
                size_formatted=format_bytes(size_bytes),
                file_count=file_count,
                category=cat,
                category_label=CATEGORY_LABELS.get(cat, cat),
                risk_level=risk,
                risk_label=RISK_LABELS.get(risk, risk),
                reason_vi=t["reason_vi"],
                impact_vi=t["impact_vi"],
                last_modified=mtime_str,
                last_modified_ts=latest_mtime,
                days_inactive=days_inactive,
                is_dir=True,
                contents_only=t.get("contents_only", True),
            )
            self._add_item(item)

    def _scan_downloads_installers(self, now_ts: float):
        user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
        downloads_dir = os.path.join(user_profile, "Downloads")
        if not os.path.isdir(downloads_dir):
            return

        installer_exts = {".exe", ".msi", ".iso", ".msix", ".vsix"}
        archive_exts = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"}

        with self._lock:
            self.current_path = f"Đang kiểm tra thư mục Downloads: {downloads_dir}"

        try:
            with os.scandir(downloads_dir) as it:
                for entry in it:
                    if self._cancel_event.is_set():
                        return
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        ext = os.path.splitext(entry.name)[1].lower()
                        if ext not in installer_exts and ext not in archive_exts:
                            continue

                        st = entry.stat(follow_symlinks=False)
                        if st.st_size < 10 * 1024 * 1024:  # Chỉ báo bộ cài/file nén >= 10 MB
                            continue

                        norm_p = os.path.normcase(os.path.normpath(entry.path))
                        with self._lock:
                            self.known_paths_norm.add(norm_p)

                        days_old = max(0, int((now_ts - st.st_mtime) / 86400))
                        mtime_str = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                        drive = os.path.splitdrive(entry.path)[0].upper() or "C:"

                        if ext in installer_exts:
                            risk = "safe" if days_old >= 7 else "caution"
                            reason = f"Bộ cài đặt phần mềm ({ext}) trong Downloads đã tải về {days_old} ngày trước."
                            impact = "Sau khi đã cài phần mềm xong, file bộ cài `.exe`/`.msi`/`.iso` không còn cần thiết nữa."
                        else:
                            risk = "caution"
                            reason = f"File nén ({ext}) dung lượng lớn trong thư mục Downloads ({days_old} ngày trước)."
                            impact = "Nếu bạn đã giải nén dữ liệu ra nơi khác, có thể xóa file nén gốc này để tiết kiệm chỗ."

                        item = ScanItem(
                            id=make_item_id(entry.path),
                            name=f"Downloads: {entry.name}",
                            path=entry.path,
                            drive=drive,
                            size_bytes=st.st_size,
                            size_formatted=format_bytes(st.st_size),
                            file_count=1,
                            category="downloads_installer",
                            category_label=CATEGORY_LABELS["downloads_installer"],
                            risk_level=risk,
                            risk_label=RISK_LABELS[risk],
                            reason_vi=reason,
                            impact_vi=impact,
                            last_modified=mtime_str,
                            last_modified_ts=st.st_mtime,
                            days_inactive=days_old,
                            is_dir=False,
                            contents_only=False,
                        )
                        self._add_item(item)
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError):
            pass

    def _scan_drives_deep(self, drives: List[str], large_file_bytes: int, stale_days: int, now_ts: float):
        """Quét sâu đa luồng trên các ổ đĩa để phát hiện project artifacts, log/dump, và file siêu nặng."""
        top_dirs: List[str] = []

        for drive_root in drives:
            if self._cancel_event.is_set():
                return
            drive_code = os.path.splitdrive(drive_root)[0].upper() or drive_root.rstrip("\\/")
            try:
                with os.scandir(drive_root) as it:
                    for entry in it:
                        if self._cancel_event.is_set():
                            return
                        try:
                            if entry.is_symlink():
                                continue
                            name_l = entry.name.lower()
                            if entry.is_dir(follow_symlinks=False):
                                if name_l in SKIP_DEEP_DIRS_LOWER:
                                    continue
                                top_dirs.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                self._inspect_single_file(entry, drive_code, large_file_bytes, now_ts)
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError):
                continue

        # Sử dụng ThreadPoolExecutor để quét song song các nhánh thư mục lớn
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [
                executor.submit(self._walk_subtree, d, large_file_bytes, stale_days, now_ts)
                for d in top_dirs
            ]
            for f in futures:
                if self._cancel_event.is_set():
                    break
                try:
                    f.result()
                except Exception:
                    continue

    def _walk_subtree(self, root_dir: str, large_file_bytes: int, stale_days: int, now_ts: float):
        stack = [root_dir]
        drive_code = os.path.splitdrive(root_dir)[0].upper() or "C:"

        while stack and not self._cancel_event.is_set():
            curr_dir = stack.pop()
            norm_curr = os.path.normcase(os.path.normpath(curr_dir))

            with self._lock:
                if norm_curr in self.known_paths_norm:
                    continue
                self.scanned_dirs += 1
                if self.scanned_dirs % 25 == 0:
                    self.current_path = curr_dir

            dir_name = os.path.basename(curr_dir)
            dir_name_l = dir_name.lower()

            # Bỏ qua thư mục .git nội bộ hoặc gói cài đặt ứng dụng Electron (resources\app*)
            if dir_name_l == ".git" or "\\resources\\app" in norm_curr:
                continue

            # 1. Kiểm tra xem đây có phải thư mục cache luôn an toàn (__pycache__, .pytest_cache...) không
            if dir_name_l in ALWAYS_SAFE_ARTIFACT_DIRS:
                reason_title, impact_text = ALWAYS_SAFE_ARTIFACT_DIRS[dir_name_l]
                size_b, f_cnt, mtime = compute_dir_stats(curr_dir, self._cancel_event)
                if size_b >= 1 * 1024 * 1024:  # >= 1 MB
                    parent_name = os.path.basename(os.path.dirname(curr_dir))
                    days_old = max(0, int((now_ts - mtime) / 86400)) if mtime > 0 else 0
                    mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime > 0 else "N/A"
                    self._add_item(
                        ScanItem(
                            id=make_item_id(curr_dir),
                            name=f"{dir_name} ({parent_name})",
                            path=curr_dir,
                            drive=drive_code,
                            size_bytes=size_b,
                            size_formatted=format_bytes(size_b),
                            file_count=f_cnt,
                            category="dev_project_artifact",
                            category_label=CATEGORY_LABELS["dev_project_artifact"],
                            risk_level="safe",
                            risk_label=RISK_LABELS["safe"],
                            reason_vi=f"{reason_title} trong thư mục `{parent_name}`.",
                            impact_vi=impact_text,
                            last_modified=mtime_str,
                            last_modified_ts=mtime,
                            days_inactive=days_old,
                            is_dir=True,
                            contents_only=False,
                            project_name=parent_name,
                        )
                    )
                continue

            # 2. Kiểm tra xem đây có phải thư mục build / dependency của project (node_modules, .venv, target...) không
            if dir_name_l in PROJECT_DEPENDENCY_DIRS:
                title_vi, restore_vi, markers = PROJECT_DEPENDENCY_DIRS[dir_name_l]
                parent_dir = os.path.dirname(curr_dir)
                norm_parent = os.path.normcase(parent_dir)
                # Bỏ qua node_modules nằm trong gói đóng gói của ứng dụng Electron (resources\app)
                if norm_parent.endswith("\\resources\\app") or "\\resources\\app\\" in norm_parent:
                    continue
                if _has_project_marker(parent_dir, markers):
                    size_b, f_cnt, mtime = compute_dir_stats(curr_dir, self._cancel_event)
                    if size_b >= 5 * 1024 * 1024:  # Chỉ báo cáo nếu >= 5 MB
                        proj_name = os.path.basename(parent_dir) or parent_dir
                        code_mtime = _get_project_code_last_mtime(parent_dir, dir_name)
                        effective_mtime = code_mtime if code_mtime > 0 else mtime
                        days_inactive = max(0, int((now_ts - effective_mtime) / 86400)) if effective_mtime > 0 else 0
                        mtime_str = (
                            datetime.fromtimestamp(effective_mtime).strftime("%Y-%m-%d %H:%M")
                            if effective_mtime > 0
                            else "N/A"
                        )

                        if days_inactive >= stale_days:
                            risk = "safe"
                            reason = (
                                f"{title_vi} của dự án `{proj_name}` — "
                                f"Dự án đã {days_inactive} ngày không chỉnh sửa code (ngưỡng >= {stale_days} ngày)."
                            )
                        else:
                            risk = "caution"
                            reason = (
                                f"{title_vi} của dự án `{proj_name}` — "
                                f"Dự án mới làm việc {days_inactive} ngày trước (đang hoạt động)."
                            )

                        self._add_item(
                            ScanItem(
                                id=make_item_id(curr_dir),
                                name=f"{dir_name} — {proj_name}",
                                path=curr_dir,
                                drive=drive_code,
                                size_bytes=size_b,
                                size_formatted=format_bytes(size_b),
                                file_count=f_cnt,
                                category="dev_project_artifact",
                                category_label=CATEGORY_LABELS["dev_project_artifact"],
                                risk_level=risk,
                                risk_label=RISK_LABELS[risk],
                                reason_vi=reason,
                                impact_vi=restore_vi,
                                last_modified=mtime_str,
                                last_modified_ts=effective_mtime,
                                days_inactive=days_inactive,
                                is_dir=True,
                                contents_only=False,
                                project_name=proj_name,
                            )
                        )
                    # Không đi sâu vào bên trong node_modules / .venv nữa!
                    continue

            # 3. Duyệt các phần tử bên trong thư mục hiện tại
            try:
                with os.scandir(curr_dir) as it:
                    for entry in it:
                        if self._cancel_event.is_set():
                            return
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                sub_l = entry.name.lower()
                                if sub_l in SKIP_DEEP_DIRS_LOWER:
                                    continue
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                self._inspect_single_file(entry, drive_code, large_file_bytes, now_ts)
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError):
                continue

    def _inspect_single_file(self, entry: os.DirEntry, drive_code: str, large_file_bytes: int, now_ts: float):
        with self._lock:
            self.scanned_files += 1
            norm_p = os.path.normcase(os.path.normpath(entry.path))
            if norm_p in self.known_paths_norm:
                return

        try:
            st = entry.stat(follow_symlinks=False)
        except (PermissionError, OSError):
            return

        size_b = st.st_size
        if size_b < 5 * 1024 * 1024:  # Bỏ qua mọi file lẻ < 5 MB
            return

        name_l = entry.name.lower()
        ext = os.path.splitext(name_l)[1]
        days_old = max(0, int((now_ts - st.st_mtime) / 86400))
        mtime_str = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")

        # Kiểm tra file ổ cứng ảo WSL2 / Docker (.vhdx)
        if ext == ".vhdx" and size_b >= 500 * 1024 * 1024:
            self._add_item(
                ScanItem(
                    id=make_item_id(entry.path),
                    name=f"Ổ đĩa ảo WSL2/Docker: {entry.name}",
                    path=entry.path,
                    drive=drive_code,
                    size_bytes=size_b,
                    size_formatted=format_bytes(size_b),
                    file_count=1,
                    category="large_file",
                    category_label=CATEGORY_LABELS["large_file"],
                    risk_level="review",
                    risk_label=RISK_LABELS["review"],
                    reason_vi="File ổ đĩa ảo (.vhdx) của WSL2 hoặc Docker Desktop thường tự phình to và không tự co lại.",
                    impact_vi="KHÔNG xóa trực tiếp nếu bạn cần dữ liệu Linux/Docker bên trong; hãy dùng `docker system prune` hoặc thu gọn VHDX.",
                    last_modified=mtime_str,
                    last_modified_ts=st.st_mtime,
                    days_inactive=days_old,
                    is_dir=False,
                    contents_only=False,
                )
            )
            return

        # Kiểm tra các file tạm, crash dump, log nặng (>= 5 MB)
        if ext in {".tmp", ".temp", ".dmp", ".hdmp", ".mdmp", ".log", ".old", ".bak"}:
            is_safe_ext = ext in {".tmp", ".temp", ".dmp", ".hdmp", ".mdmp"}
            risk = "safe" if is_safe_ext else "caution"
            reason = (
                f"File tạm / bản ghi lỗi (`{ext}`) dung lượng {format_bytes(size_b)}, tạo cách đây {days_old} ngày."
                if is_safe_ext
                else f"File nhật ký (`.log`) hoặc bản sao lưu cũ (`{ext}`) nặng {format_bytes(size_b)} ({days_old} ngày trước)."
            )
            impact = (
                "Xóa an toàn 100%, không ảnh hưởng tới chương trình."
                if is_safe_ext
                else "Có thể xóa nếu bạn không còn nhu cầu tra cứu log cũ hoặc bản backup tạm này."
            )
            self._add_item(
                ScanItem(
                    id=make_item_id(entry.path),
                    name=entry.name,
                    path=entry.path,
                    drive=drive_code,
                    size_bytes=size_b,
                    size_formatted=format_bytes(size_b),
                    file_count=1,
                    category="log_dump",
                    category_label=CATEGORY_LABELS["log_dump"],
                    risk_level=risk,
                    risk_label=RISK_LABELS[risk],
                    reason_vi=reason,
                    impact_vi=impact,
                    last_modified=mtime_str,
                    last_modified_ts=st.st_mtime,
                    days_inactive=days_old,
                    is_dir=False,
                    contents_only=False,
                )
            )
            return

        # Kiểm tra các file lẻ dung lượng lớn (>= ngưỡng large_file_bytes, mặc định 100 MB)
        if size_b >= large_file_bytes:
            # Bỏ qua các file hệ thống Windows pagefile/hiberfil/swapfile ở根目录 ổ C
            if name_l in {"pagefile.sys", "hiberfil.sys", "swapfile.sys"}:
                return

            self._add_item(
                ScanItem(
                    id=make_item_id(entry.path),
                    name=entry.name,
                    path=entry.path,
                    drive=drive_code,
                    size_bytes=size_b,
                    size_formatted=format_bytes(size_b),
                    file_count=1,
                    category="large_file",
                    category_label=CATEGORY_LABELS["large_file"],
                    risk_level="review",
                    risk_label=RISK_LABELS["review"],
                    reason_vi=f"File dung lượng lớn ({format_bytes(size_b)}), lần sửa cuối cách đây {days_old} ngày.",
                    impact_vi="Hãy mở vị trí file bằng nút Explorer để kiểm tra trước khi xóa vì đây là file dữ liệu đơn lẻ.",
                    last_modified=mtime_str,
                    last_modified_ts=st.st_mtime,
                    days_inactive=days_old,
                    is_dir=False,
                    contents_only=False,
                )
            )

    def get_snapshot(self) -> Dict:
        with self._lock:
            items_list = sorted(self.items.values(), key=lambda x: x.size_bytes, reverse=True)
            total_reclaimable = sum(i.size_bytes for i in items_list)
            safe_reclaimable = sum(i.size_bytes for i in items_list if i.risk_level == "safe")
            caution_reclaimable = sum(i.size_bytes for i in items_list if i.risk_level == "caution")
            review_reclaimable = sum(i.size_bytes for i in items_list if i.risk_level == "review")

            by_category: Dict[str, Dict] = {}
            for i in items_list:
                cat = i.category
                if cat not in by_category:
                    by_category[cat] = {
                        "category": cat,
                        "label": i.category_label,
                        "size_bytes": 0,
                        "size_formatted": "0 B",
                        "count": 0,
                    }
                by_category[cat]["size_bytes"] += i.size_bytes
                by_category[cat]["count"] += 1

            for cat_obj in by_category.values():
                cat_obj["size_formatted"] = format_bytes(cat_obj["size_bytes"])

            elapsed = 0.0
            if self.started_at:
                end_t = self.finished_at or time.time()
                elapsed = round(end_t - self.started_at, 1)

            return {
                "status": self.status,
                "elapsed_seconds": elapsed,
                "current_path": self.current_path,
                "scanned_dirs": self.scanned_dirs,
                "scanned_files": self.scanned_files,
                "error_message": self.error_message,
                "config": self.config,
                "summary": {
                    "total_items": len(items_list),
                    "total_bytes": total_reclaimable,
                    "total_formatted": format_bytes(total_reclaimable),
                    "safe_bytes": safe_reclaimable,
                    "safe_formatted": format_bytes(safe_reclaimable),
                    "caution_bytes": caution_reclaimable,
                    "caution_formatted": format_bytes(caution_reclaimable),
                    "review_bytes": review_reclaimable,
                    "review_formatted": format_bytes(review_reclaimable),
                    "by_category": list(by_category.values()),
                },
                "items": [asdict(i) for i in items_list],
            }


scan_manager = ScanManager()

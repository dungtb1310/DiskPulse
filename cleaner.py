import os
import stat
import json
import time
import shutil
import subprocess
from datetime import datetime
from typing import Dict, List, Tuple
from send2trash import send2trash
from scanner import format_bytes, compute_dir_stats

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clean_history.json")

# Danh sách các đường dẫn hệ thống trọng yếu bị khóa cứng không bao giờ cho phép xóa trực tiếp
PROTECTED_EXACT_PATHS = {
    os.path.normcase(os.path.normpath("C:\\")),
    os.path.normcase(os.path.normpath("D:\\")),
    os.path.normcase(os.path.normpath("E:\\")),
    os.path.normcase(os.path.normpath("F:\\")),
    os.path.normcase(os.path.normpath(os.environ.get("WINDIR", r"C:\Windows"))),
    os.path.normcase(os.path.normpath(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32"))),
    os.path.normcase(os.path.normpath(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "SysWOW64"))),
    os.path.normcase(os.path.normpath(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "WinSxS"))),
    os.path.normcase(os.path.normpath(os.environ.get("ProgramFiles", r"C:\Program Files"))),
    os.path.normcase(os.path.normpath(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))),
    os.path.normcase(os.path.normpath(os.environ.get("USERPROFILE", os.path.expanduser("~")))),
    os.path.normcase(os.path.normpath(os.path.dirname(os.path.abspath(__file__)))),
}


def is_protected_path(path: str) -> Tuple[bool, str]:
    """Kiểm tra xem đường dẫn có nằm trong vùng cấm xóa để bảo vệ hệ điều hành và code không."""
    if not path:
        return True, "Đường dẫn rỗng."

    norm_p = os.path.normcase(os.path.normpath(path))

    # Không bao giờ xóa gốc ổ đĩa (VD: C:\, D:\)
    drive, tail = os.path.splitdrive(norm_p)
    if tail in ("", "\\", "/"):
        return True, f"Không được phép xóa thư mục gốc của ổ đĩa ({drive})."

    if norm_p in PROTECTED_EXACT_PATHS:
        return True, "Đường dẫn hệ thống hoặc thư mục gốc trọng yếu được bảo vệ."

    # Không xóa thư mục .git hoặc thư mục nội bộ của ứng dụng Electron (resources\app)
    base = os.path.basename(norm_p)
    if base == ".git" or "\\.git\\" in norm_p:
        return True, "Bảo vệ lịch sử kho mã nguồn (.git)."
    if "\\resources\\app\\" in norm_p or norm_p.endswith("\\resources\\app"):
        return True, "Bảo vệ thư mục mã nguồn ứng dụng đã cài đặt (resources\\app)."

    # Không xóa bất kỳ thứ gì bên trong System32, SysWOW64, WinSxS
    win_dir = os.path.normcase(os.path.normpath(os.environ.get("WINDIR", r"C:\Windows")))
    for critical_sub in ("system32", "syswow64", "winsxs", "servicing", "boot"):
        crit_prefix = os.path.join(win_dir, critical_sub)
        if norm_p == crit_prefix or norm_p.startswith(crit_prefix + os.sep):
            return True, f"Tệp tin hệ thống Windows trọng yếu ({critical_sub}) được bảo vệ tuyệt đối."

    return False, ""


def _remove_readonly(func, path, _excinfo):
    """Gỡ bỏ thuộc tính Read-Only trên Windows khi xóa vĩnh viễn."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def _delete_single_target(path: str, mode: str) -> Tuple[bool, str]:
    """Thực hiện xóa 1 file hoặc thư mục theo chế độ 'recycle' hoặc 'permanent'."""
    try:
        if mode == "recycle":
            send2trash(os.path.normpath(path))
            return True, ""
        else:
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path, onerror=_remove_readonly)
            else:
                try:
                    os.chmod(path, stat.S_IWRITE)
                except OSError:
                    pass
                os.remove(path)
            return True, ""
    except Exception as exc:
        return False, str(exc)


def clean_item(path: str, mode: str = "recycle", contents_only: bool = False, estimated_bytes: int = 0) -> Dict:
    """
    Xóa một mục được chọn:
    - Nếu contents_only=True (VD: %TEMP%), giữ lại thư mục gốc và xóa từng phần tử con bên trong (bỏ qua file đang bị khóa).
    - Nếu contents_only=False (VD: node_modules, .venv, file lẻ), xóa toàn bộ thư mục/file đó.
    """
    blocked, reason = is_protected_path(path)
    if blocked:
        return {
            "path": path,
            "success": False,
            "reclaimed_bytes": 0,
            "reclaimed_formatted": "0 B",
            "skipped_locked": 0,
            "message": f"Đã chặn: {reason}",
        }

    if not os.path.exists(path):
        return {
            "path": path,
            "success": False,
            "reclaimed_bytes": 0,
            "reclaimed_formatted": "0 B",
            "skipped_locked": 0,
            "message": "Đường dẫn không còn tồn tại trên ổ đĩa.",
        }

    if contents_only and os.path.isdir(path):
        before_bytes, _, _ = compute_dir_stats(path)
        skipped_locked = 0
        deleted_entries = 0

        try:
            with os.scandir(path) as it:
                entries = list(it)
        except (PermissionError, OSError) as exc:
            return {
                "path": path,
                "success": False,
                "reclaimed_bytes": 0,
                "reclaimed_formatted": "0 B",
                "skipped_locked": 0,
                "message": f"Không có quyền truy cập thư mục: {exc}",
            }

        for entry in entries:
            ok, _err = _delete_single_target(entry.path, mode)
            if ok:
                deleted_entries += 1
            else:
                skipped_locked += 1

        after_bytes, _, _ = compute_dir_stats(path)
        reclaimed = max(0, before_bytes - after_bytes)
        if reclaimed == 0 and deleted_entries > 0 and estimated_bytes > 0:
            reclaimed = estimated_bytes

        msg = f"Đã dọn {deleted_entries} mục con ({format_bytes(reclaimed)})."
        if skipped_locked > 0:
            msg += f" Bỏ qua {skipped_locked} file đang được ứng dụng mở."

        return {
            "path": path,
            "success": deleted_entries > 0 or skipped_locked == 0,
            "reclaimed_bytes": reclaimed,
            "reclaimed_formatted": format_bytes(reclaimed),
            "skipped_locked": skipped_locked,
            "message": msg,
        }

    # Xóa toàn bộ thư mục hoặc file đơn lẻ
    actual_bytes = estimated_bytes
    if actual_bytes <= 0:
        try:
            if os.path.isdir(path):
                actual_bytes, _, _ = compute_dir_stats(path)
            else:
                actual_bytes = os.path.getsize(path)
        except OSError:
            actual_bytes = 0

    ok, err = _delete_single_target(path, mode)
    if ok:
        return {
            "path": path,
            "success": True,
            "reclaimed_bytes": actual_bytes,
            "reclaimed_formatted": format_bytes(actual_bytes),
            "skipped_locked": 0,
            "message": (
                f"Đã chuyển vào Recycle Bin ({format_bytes(actual_bytes)})."
                if mode == "recycle"
                else f"Đã xóa vĩnh viễn ({format_bytes(actual_bytes)})."
            ),
        }
    else:
        return {
            "path": path,
            "success": False,
            "reclaimed_bytes": 0,
            "reclaimed_formatted": "0 B",
            "skipped_locked": 1,
            "message": f"Không thể xóa (có thể file đang mở): {err}",
        }


def load_clean_history() -> List[Dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def append_clean_history(record: Dict):
    history = load_clean_history()
    history.insert(0, record)
    history = history[:50]  # Giữ tối đa 50 lần dọn gần nhất
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def open_in_explorer(path: str) -> Tuple[bool, str]:
    """Mở Windows File Explorer và trỏ trực tiếp tới file hoặc thư mục."""
    norm_p = os.path.normpath(path)
    if not os.path.exists(norm_p):
        parent = os.path.dirname(norm_p)
        if os.path.exists(parent):
            norm_p = parent
        else:
            return False, "Đường dẫn không tồn tại trên hệ thống."

    try:
        if os.path.isfile(norm_p):
            subprocess.Popen(["explorer.exe", f"/select,{norm_p}"])
        else:
            subprocess.Popen(["explorer.exe", norm_p])
        return True, f"Đã mở trong Windows Explorer: {norm_p}"
    except Exception as exc:
        return False, f"Lỗi khi mở Explorer: {exc}"

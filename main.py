import os
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn

from scanner import get_drives_info, scan_manager, format_bytes
from cleaner import clean_item, load_clean_history, append_clean_history, open_in_explorer

app = FastAPI(
    title="DiskPulse — Smart Disk Analyzer & Cleaner",
    description="Công cụ phân tích dung lượng ổ đĩa và dọn rác thông minh cho Developer trên Windows",
    version="1.0.0",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)


class ScanStartRequest(BaseModel):
    drives: Optional[List[str]] = None
    include_known_caches: bool = True
    include_deep_scan: bool = True
    large_file_mb: int = 100
    stale_project_days: int = 14


class CleanRequest(BaseModel):
    item_ids: List[str]
    mode: str = "recycle"  # "recycle" | "permanent"


class ExplorerRequest(BaseModel):
    path: str


@app.get("/api/drives")
def api_get_drives():
    return {"drives": get_drives_info()}


@app.post("/api/scan/start")
def api_start_scan(req: ScanStartRequest):
    snapshot = scan_manager.start_scan(
        drives=req.drives,
        include_known_caches=req.include_known_caches,
        include_deep_scan=req.include_deep_scan,
        large_file_mb=req.large_file_mb,
        stale_project_days=req.stale_project_days,
    )
    return snapshot


@app.post("/api/scan/stop")
def api_stop_scan():
    return scan_manager.stop_scan()


@app.get("/api/scan/status")
def api_scan_status():
    return scan_manager.get_snapshot()


@app.post("/api/clean")
def api_clean_items(req: CleanRequest):
    if req.mode not in ("recycle", "permanent"):
        raise HTTPException(status_code=400, detail="Chế độ xóa không hợp lệ (chỉ hỗ trợ 'recycle' hoặc 'permanent').")

    if not req.item_ids:
        raise HTTPException(status_code=400, detail="Chưa chọn mục nào để dọn dẹp.")

    snapshot = scan_manager.get_snapshot()
    item_map = {item["id"]: item for item in snapshot["items"]}

    results = []
    cleaned_ids = []
    total_reclaimed = 0

    for iid in req.item_ids:
        target = item_map.get(iid)
        if not target:
            continue

        res = clean_item(
            path=target["path"],
            mode=req.mode,
            contents_only=target.get("contents_only", False),
            estimated_bytes=target.get("size_bytes", 0),
        )
        res["id"] = iid
        res["name"] = target["name"]
        res["category_label"] = target["category_label"]
        results.append(res)

        if res["success"]:
            cleaned_ids.append(iid)
            total_reclaimed += res["reclaimed_bytes"]

    if cleaned_ids:
        scan_manager.remove_item_ids(cleaned_ids)
        history_entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": req.mode,
            "mode_label": "Thùng rác (Recycle Bin)" if req.mode == "recycle" else "Xóa vĩnh viễn",
            "cleaned_count": len(cleaned_ids),
            "reclaimed_bytes": total_reclaimed,
            "reclaimed_formatted": format_bytes(total_reclaimed),
            "items": [
                {
                    "name": r["name"],
                    "path": r["path"],
                    "reclaimed_formatted": r["reclaimed_formatted"],
                }
                for r in results
                if r["success"]
            ][:25],
        }
        append_clean_history(history_entry)

    return {
        "cleaned_count": len(cleaned_ids),
        "failed_count": len(results) - len(cleaned_ids),
        "total_reclaimed_bytes": total_reclaimed,
        "total_reclaimed_formatted": format_bytes(total_reclaimed),
        "results": results,
        "drives": get_drives_info(),
        "snapshot": scan_manager.get_snapshot(),
    }


@app.post("/api/open-explorer")
def api_open_explorer(req: ExplorerRequest):
    ok, msg = open_in_explorer(req.path)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "message": msg}


@app.get("/api/history")
def api_get_history():
    return {"history": load_clean_history()}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, reload=False)

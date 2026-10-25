const state = {
  drives: [],
  selectedDrivesForScan: new Set(),
  snapshot: null,
  selectedItemIds: new Set(),
  pollTimer: null,
  chartInstance: null,
  confirmingClean: false,
};

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let idx = 0;
  let val = Number(bytes);
  while (val >= 1024 && idx < units.length - 1) {
    val /= 1024;
    idx++;
  }
  return idx === 0 ? `${Math.round(val)} B` : `${val.toFixed(2)} ${units[idx]}`;
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function showToast(message, type = "info") {
  const stack = document.getElementById("toastStack");
  if (!stack) return;
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  stack.appendChild(el);
  setTimeout(() => {
    el.remove();
  }, 4500);
}

async function fetchDrives() {
  try {
    const res = await fetch("/api/drives");
    const data = await res.json();
    state.drives = data.drives || [];
    if (state.selectedDrivesForScan.size === 0) {
      state.drives.forEach((d) => state.selectedDrivesForScan.add(d.mountpoint));
    }
    renderDrives();
    populateDriveFilterDropdown();
  } catch (err) {
    showToast("Không thể tải thông tin ổ đĩa: " + err.message, "error");
  }
}

function populateDriveFilterDropdown() {
  const sel = document.getElementById("selDriveFilter");
  if (!sel) return;
  const currentVal = sel.value;
  sel.innerHTML = `<option value="ALL">Tất cả ổ đĩa</option>`;
  state.drives.forEach((d) => {
    const opt = document.createElement("option");
    opt.value = d.drive;
    opt.textContent = `Ổ đĩa ${d.drive} (Trống ${d.free_formatted})`;
    sel.appendChild(opt);
  });
  if ([...sel.options].some((o) => o.value === currentVal)) {
    sel.value = currentVal;
  }
}

function renderDrives() {
  const container = document.getElementById("driveStrip");
  if (!container) return;

  // Tính dung lượng có thể giải phóng theo từng ổ đĩa
  const reclaimByDrive = {};
  if (state.snapshot && state.snapshot.items) {
    for (const item of state.snapshot.items) {
      reclaimByDrive[item.drive] = (reclaimByDrive[item.drive] || 0) + item.size_bytes;
    }
  }

  container.innerHTML = state.drives
    .map((d) => {
      const isChecked = state.selectedDrivesForScan.has(d.mountpoint);
      const fillClass =
        d.percent_used >= 90 ? "critical" : d.percent_used >= 78 ? "high" : "";
      const reclaimBytes = reclaimByDrive[d.drive] || 0;

      return `
        <div class="drive-tile ${isChecked ? "selected" : ""}" data-mount="${escapeHtml(d.mountpoint)}">
          <div class="drive-header">
            <label class="drive-title" style="cursor: pointer;">
              <input
                type="checkbox"
                class="chk-drive-scan"
                data-mount="${escapeHtml(d.mountpoint)}"
                ${isChecked ? "checked" : ""}
              />
              <span>Ổ đĩa ${escapeHtml(d.drive)}</span>
              <span style="font-size: 0.75rem; font-weight: 400; color: var(--text-secondary);">(${escapeHtml(d.fstype)})</span>
            </label>
            <span class="mono" style="font-weight: 600; font-size: 0.8125rem; color: ${
              d.percent_used >= 90 ? "var(--review-fg)" : "var(--text-primary)"
            };">
              ${d.percent_used}% đã dùng
            </span>
          </div>

          <div class="drive-bar-track">
            <div class="drive-bar-fill ${fillClass}" style="width: ${Math.min(100, d.percent_used)}%;"></div>
          </div>

          <div class="drive-meta tabular">
            <span>Trống: <strong style="color: var(--text-primary);">${escapeHtml(d.free_formatted)}</strong> / ${escapeHtml(d.total_formatted)}</span>
            <span style="color: var(--safe-fg); font-weight: 600;">
              Có thể dọn: ${formatBytes(reclaimBytes)}
            </span>
          </div>
        </div>
      `;
    })
    .join("");

  container.querySelectorAll(".chk-drive-scan").forEach((chk) => {
    chk.addEventListener("change", (e) => {
      const mount = e.target.getAttribute("data-mount");
      if (e.target.checked) {
        state.selectedDrivesForScan.add(mount);
      } else {
        if (state.selectedDrivesForScan.size <= 1) {
          e.target.checked = true;
          showToast("Cần chọn ít nhất 1 ổ đĩa để quét.", "info");
          return;
        }
        state.selectedDrivesForScan.delete(mount);
      }
      renderDrives();
    });
  });
}

async function startScan() {
  const includeKnown = document.getElementById("chkKnownCaches").checked;
  const includeDeep = document.getElementById("chkDeepScan").checked;
  const largeFileMb = parseInt(document.getElementById("inpLargeFileMb").value || "100", 10);
  const staleDays = parseInt(document.getElementById("inpStaleDays").value || "14", 10);

  if (!includeKnown && !includeDeep) {
    showToast("Vui lòng bật ít nhất 1 chế độ quét (Cache cố định hoặc Quét sâu).", "error");
    return;
  }

  try {
    const res = await fetch("/api/scan/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        drives: Array.from(state.selectedDrivesForScan),
        include_known_caches: includeKnown,
        include_deep_scan: includeDeep,
        large_file_mb: largeFileMb,
        stale_project_days: staleDays,
      }),
    });
    const snapshot = await res.json();
    state.selectedItemIds.clear();
    updateSnapshotUI(snapshot);
    startPolling();
  } catch (err) {
    showToast("Lỗi khi bắt đầu quét: " + err.message, "error");
  }
}

async function stopScan() {
  try {
    const res = await fetch("/api/scan/stop", { method: "POST" });
    const snapshot = await res.json();
    updateSnapshotUI(snapshot);
  } catch (err) {
    showToast("Không thể dừng quét: " + err.message, "error");
  }
}

function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    try {
      const res = await fetch("/api/scan/status");
      const snapshot = await res.json();
      updateSnapshotUI(snapshot);
      if (snapshot.status !== "running") {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
    } catch (_err) {
      // ignore transient error
    }
  }, 450);
}

function updateSnapshotUI(snapshot) {
  state.snapshot = snapshot;

  // Toggle buttons & scan banner
  const btnStart = document.getElementById("btnStartScan");
  const btnStop = document.getElementById("btnStopScan");
  const banner = document.getElementById("scanBanner");
  const pulseDot = document.getElementById("scanPulseDot");
  const statusLabel = document.getElementById("scanStatusLabel");

  if (snapshot.status === "running") {
    btnStart.style.display = "none";
    btnStop.style.display = "inline-flex";
    banner.classList.remove("hidden");
    pulseDot.style.display = "inline-block";
    statusLabel.textContent = "Đang quét:";
  } else {
    btnStart.style.display = "inline-flex";
    btnStop.style.display = "none";
    if (snapshot.status === "completed" || snapshot.status === "stopped") {
      banner.classList.remove("hidden");
      pulseDot.style.display = "none";
      statusLabel.textContent = snapshot.status === "completed" ? "Hoàn tất:" : "Đã dừng:";
    }
  }

  document.getElementById("scanCurrentPath").textContent = snapshot.current_path || "";
  document.getElementById("statScannedDirs").textContent = (snapshot.scanned_dirs || 0).toLocaleString("vi-VN");
  document.getElementById("statScannedFiles").textContent = (snapshot.scanned_files || 0).toLocaleString("vi-VN");
  document.getElementById("statElapsed").textContent = `${snapshot.elapsed_seconds || 0}s`;

  // Update summary totals
  const sum = snapshot.summary || {};
  document.getElementById("valTotalReclaim").textContent = sum.total_formatted || "0 B";
  document.getElementById("valTotalItems").textContent = (sum.total_items || 0).toLocaleString("vi-VN");
  document.getElementById("valSafeBytes").textContent = sum.safe_formatted || "0 B";
  document.getElementById("valCautionBytes").textContent = sum.caution_formatted || "0 B";
  document.getElementById("valReviewBytes").textContent = sum.review_formatted || "0 B";

  renderDrives();
  renderTreemap(sum.by_category || []);
  renderTable();
  updateSelectionSummary();
}

function renderTreemap(categories) {
  const container = document.getElementById("treemapChart");
  if (!container) return;

  if (!categories || categories.length === 0) {
    if (state.chartInstance) {
      state.chartInstance.clear();
    }
    container.innerHTML = `<div class="empty-state" style="padding: 32px 12px;">Chưa có dữ liệu phân bổ dung lượng.</div>`;
    return;
  }

  const palette = {
    system_temp: "#10b981",
    browser_app_cache: "#0ea5e9",
    dev_global_cache: "#6366f1",
    dev_project_artifact: "#f59e0b",
    downloads_installer: "#ec4899",
    log_dump: "#14b8a6",
    large_file: "#f43f5e",
  };

  if (typeof window.echarts !== "undefined") {
    if (!state.chartInstance || state.chartInstance.isDisposed()) {
      container.innerHTML = "";
      state.chartInstance = window.echarts.init(container);
      state.chartInstance.on("click", (params) => {
        if (params.data && params.data.categoryKey) {
          const selCat = document.getElementById("selCategoryFilter");
          selCat.value = selCat.value === params.data.categoryKey ? "ALL" : params.data.categoryKey;
          renderTable();
        }
      });
    }

    const treeData = categories.map((c) => ({
      name: `${c.label}\n(${c.size_formatted})`,
      value: c.size_bytes,
      categoryKey: c.category,
      formatted: c.size_formatted,
      count: c.count,
      itemStyle: {
        color: palette[c.category] || "#38bdf8",
      },
    }));

    state.chartInstance.setOption({
      tooltip: {
        backgroundColor: "#111827",
        borderColor: "#334155",
        textStyle: { color: "#f1f5f9", fontSize: 12 },
        formatter: (info) => {
          const d = info.data;
          if (!d) return "";
          return `<strong>${escapeHtml(d.name.split("\n")[0])}</strong><br/>Dung lượng: ${escapeHtml(d.formatted)}<br/>Số mục: ${d.count}`;
        },
      },
      series: [
        {
          type: "treemap",
          width: "100%",
          height: "100%",
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          label: {
            show: true,
            color: "#ffffff",
            fontSize: 11,
            fontWeight: 600,
          },
          itemStyle: {
            borderColor: "#0b0f17",
            borderWidth: 2,
            gapWidth: 2,
          },
          data: treeData,
        },
      ],
    });
  } else {
    // Fallback bar list if CDN is unreachable
    const maxBytes = Math.max(...categories.map((c) => c.size_bytes), 1);
    container.innerHTML = `<div style="display:flex;flex-direction:column;gap:8px;padding:8px;">
      ${categories
        .map(
          (c) => `
        <div style="cursor:pointer;" data-cat="${escapeHtml(c.category)}">
          <div style="display:flex;justify-content:space-between;font-size:0.78rem;margin-bottom:3px;">
            <span>${escapeHtml(c.label)} (${c.count})</span>
            <strong class="mono">${escapeHtml(c.size_formatted)}</strong>
          </div>
          <div class="drive-bar-track">
            <div style="height:100%;width:${Math.max(3, Math.round((c.size_bytes / maxBytes) * 100))}%;background:${
            palette[c.category] || "#38bdf8"
          };"></div>
          </div>
        </div>`
        )
        .join("")}
    </div>`;
  }
}

function getFilteredItems() {
  if (!state.snapshot || !state.snapshot.items) return [];

  const q = (document.getElementById("inpSearch").value || "").trim().toLowerCase();
  const driveFilter = document.getElementById("selDriveFilter").value;
  const riskFilter = document.getElementById("selRiskFilter").value;
  const catFilter = document.getElementById("selCategoryFilter").value;
  const sortBy = document.getElementById("selSortBy").value;

  let list = state.snapshot.items.filter((item) => {
    if (driveFilter !== "ALL" && item.drive !== driveFilter) return false;
    if (riskFilter !== "ALL" && item.risk_level !== riskFilter) return false;
    if (catFilter !== "ALL" && item.category !== catFilter) return false;
    if (q) {
      const hay = `${item.name} ${item.path} ${item.project_name || ""} ${item.reason_vi}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  list.sort((a, b) => {
    if (sortBy === "size_desc") return b.size_bytes - a.size_bytes;
    if (sortBy === "size_asc") return a.size_bytes - b.size_bytes;
    if (sortBy === "age_desc") return b.days_inactive - a.days_inactive;
    if (sortBy === "age_asc") return a.days_inactive - b.days_inactive;
    return b.size_bytes - a.size_bytes;
  });

  return list;
}

function renderTable() {
  const tbody = document.getElementById("itemsTableBody");
  const lblCount = document.getElementById("lblFilteredCount");
  if (!tbody) return;

  const filtered = getFilteredItems();
  lblCount.textContent = filtered.length.toLocaleString("vi-VN");

  // Highlight active risk tier row
  const currentRisk = document.getElementById("selRiskFilter").value;
  document.querySelectorAll(".risk-tier-row").forEach((row) => {
    row.classList.toggle("active", row.getAttribute("data-risk-filter") === currentRisk);
  });

  if (filtered.length === 0) {
    const isRunning = state.snapshot && state.snapshot.status === "running";
    tbody.innerHTML = `
      <tr>
        <td colspan="7">
          <div class="empty-state">
            <p>${
              isRunning
                ? "Đang quét ổ đĩa... Các mục phát hiện sẽ xuất hiện trực tiếp tại đây."
                : "Không có mục nào khớp với bộ lọc hiện tại."
            }</p>
          </div>
        </td>
      </tr>
    `;
    document.getElementById("chkSelectAllVisible").checked = false;
    return;
  }

  const allVisibleSelected =
    filtered.length > 0 && filtered.every((item) => state.selectedItemIds.has(item.id));
  document.getElementById("chkSelectAllVisible").checked = allVisibleSelected;

  tbody.innerHTML = filtered
    .map((item) => {
      const isSelected = state.selectedItemIds.has(item.id);
      const badgeClass =
        item.risk_level === "safe"
          ? "badge-safe"
          : item.risk_level === "caution"
          ? "badge-caution"
          : "badge-review";

      return `
        <tr class="${isSelected ? "row-selected" : ""}" data-id="${escapeHtml(item.id)}">
          <td style="text-align: center;">
            <input
              type="checkbox"
              class="chk-row-item"
              data-id="${escapeHtml(item.id)}"
              ${isSelected ? "checked" : ""}
              aria-label="Chọn ${escapeHtml(item.name)}"
            />
          </td>
          <td>
            <div class="item-name-cell">
              <span class="item-title">${escapeHtml(item.name)}</span>
              <span class="item-path mono">${escapeHtml(item.path)}</span>
            </div>
          </td>
          <td>
            <div style="display: flex; flex-direction: column; align-items: flex-start; gap: 4px;">
              <span class="badge ${badgeClass}">${escapeHtml(item.risk_label)}</span>
              <span style="font-size: 0.75rem; color: var(--text-secondary);">${escapeHtml(item.category_label)}</span>
            </div>
          </td>
          <td>
            <div class="reason-cell">
              <div class="reason-primary">${escapeHtml(item.reason_vi)}</div>
              <div class="reason-impact">&rarr; ${escapeHtml(item.impact_vi)}</div>
            </div>
          </td>
          <td class="size-cell mono">
            ${escapeHtml(item.size_formatted)}
            <span class="size-sub">${item.file_count.toLocaleString("vi-VN")} tệp</span>
          </td>
          <td style="text-align: right; white-space: nowrap;" class="tabular">
            <div style="font-size: 0.8125rem;">${item.days_inactive} ngày trước</div>
            <div class="mono" style="font-size: 0.72rem; color: var(--text-secondary);">${escapeHtml(item.last_modified)}</div>
          </td>
          <td style="text-align: center;">
            <button
              type="button"
              class="btn btn-ghost btn-open-explorer"
              data-path="${escapeHtml(item.path)}"
              title="Mở vị trí trong Windows File Explorer"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
              </svg>
              <span>Explorer</span>
            </button>
          </td>
        </tr>
      `;
    })
    .join("");

  tbody.querySelectorAll(".chk-row-item").forEach((chk) => {
    chk.addEventListener("change", (e) => {
      const id = e.target.getAttribute("data-id");
      if (e.target.checked) {
        state.selectedItemIds.add(id);
      } else {
        state.selectedItemIds.delete(id);
      }
      resetCleanConfirmState();
      renderTable();
      updateSelectionSummary();
    });
  });

  tbody.querySelectorAll(".btn-open-explorer").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const path = e.currentTarget.getAttribute("data-path");
      await openInExplorer(path);
    });
  });
}

function updateSelectionSummary() {
  const lblCount = document.getElementById("lblSelectedCount");
  const lblBytes = document.getElementById("lblSelectedBytes");
  const btnClean = document.getElementById("btnExecuteClean");

  let totalBytes = 0;
  let validCount = 0;
  if (state.snapshot && state.snapshot.items) {
    for (const item of state.snapshot.items) {
      if (state.selectedItemIds.has(item.id)) {
        totalBytes += item.size_bytes;
        validCount++;
      }
    }
  }

  lblCount.textContent = validCount.toLocaleString("vi-VN");
  lblBytes.textContent = formatBytes(totalBytes);
  btnClean.disabled = validCount === 0;
}

function resetCleanConfirmState() {
  state.confirmingClean = false;
  const btnClean = document.getElementById("btnExecuteClean");
  const btnText = document.getElementById("btnExecuteCleanText");
  if (btnClean && btnText) {
    btnClean.classList.remove("btn-danger");
    btnClean.classList.add("btn-primary");
    btnText.textContent = "Dọn Dẹp Các Mục Đã Chọn";
  }
}

async function executeClean() {
  const ids = Array.from(state.selectedItemIds);
  if (ids.length === 0) return;

  const modeEl = document.querySelector('input[name="cleanMode"]:checked');
  const mode = modeEl ? modeEl.value : "recycle";

  // Inline 2-step confirmation (no disruptive modal needed)
  if (!state.confirmingClean) {
    state.confirmingClean = true;
    const btnClean = document.getElementById("btnExecuteClean");
    const btnText = document.getElementById("btnExecuteCleanText");
    btnClean.classList.remove("btn-primary");
    btnClean.classList.add("btn-danger");
    const modeName = mode === "recycle" ? "Đưa vào Thùng Rác" : "XÓA VĨNH VIỄN";
    btnText.textContent = `Xác nhận ${modeName} (${ids.length} mục)? Bấm lần nữa để thực thi`;
    setTimeout(() => {
      if (state.confirmingClean) resetCleanConfirmState();
    }, 5000);
    return;
  }

  resetCleanConfirmState();
  const btnClean = document.getElementById("btnExecuteClean");
  const btnText = document.getElementById("btnExecuteCleanText");
  btnClean.disabled = true;
  btnText.textContent = "Đang dọn dẹp...";

  try {
    const res = await fetch("/api/clean", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_ids: ids,
        mode: mode,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Lỗi khi dọn dẹp");
    }

    state.selectedItemIds.clear();
    if (data.drives) {
      state.drives = data.drives;
    }
    if (data.snapshot) {
      updateSnapshotUI(data.snapshot);
    }

    showToast(
      `Đã dọn thành công ${data.cleaned_count} mục, giải phóng ${data.total_reclaimed_formatted}!`,
      "success"
    );
    await fetchHistory();
  } catch (err) {
    showToast("Lỗi khi xóa: " + err.message, "error");
  } finally {
    resetCleanConfirmState();
    updateSelectionSummary();
  }
}

async function openInExplorer(path) {
  try {
    const res = await fetch("/api/open-explorer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Không thể mở Explorer");
    }
    showToast(data.message, "info");
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function fetchHistory() {
  try {
    const res = await fetch("/api/history");
    const data = await res.json();
    const list = data.history || [];
    const badge = document.getElementById("historyCountBadge");
    const container = document.getElementById("historyList");
    if (badge) badge.textContent = `${list.length} phiên`;
    if (!container) return;

    if (list.length === 0) {
      container.innerHTML = `<div style="color: var(--text-secondary); font-size: 0.8125rem; padding: 8px 0;">Chưa có lịch sử dọn dẹp nào.</div>`;
      return;
    }

    container.innerHTML = list
      .map(
        (h) => `
        <div class="history-item">
          <div>
            <div style="font-weight: 600; color: var(--text-primary);">
              Đã dọn ${h.cleaned_count} mục (${escapeHtml(h.mode_label)})
            </div>
            <div class="mono" style="font-size: 0.72rem; color: var(--text-secondary);">
              ${escapeHtml(h.timestamp)}
            </div>
          </div>
          <span class="badge badge-safe mono">+${escapeHtml(h.reclaimed_formatted)}</span>
        </div>
      `
      )
      .join("");
  } catch (_err) {
    // ignore
  }
}

function bindEvents() {
  document.getElementById("btnStartScan").addEventListener("click", startScan);
  document.getElementById("btnStopScan").addEventListener("click", stopScan);

  document.getElementById("btnSelectAllSafe").addEventListener("click", () => {
    if (!state.snapshot || !state.snapshot.items) return;
    let added = 0;
    for (const item of state.snapshot.items) {
      if (item.risk_level === "safe") {
        state.selectedItemIds.add(item.id);
        added++;
      }
    }
    resetCleanConfirmState();
    renderTable();
    updateSelectionSummary();
    showToast(`Đã chọn ${added} mục An toàn tuyệt đối.`, "info");
  });

  document.getElementById("btnClearSelection").addEventListener("click", () => {
    state.selectedItemIds.clear();
    resetCleanConfirmState();
    renderTable();
    updateSelectionSummary();
  });

  document.getElementById("chkSelectAllVisible").addEventListener("change", (e) => {
    const visible = getFilteredItems();
    if (e.target.checked) {
      visible.forEach((i) => state.selectedItemIds.add(i.id));
    } else {
      visible.forEach((i) => state.selectedItemIds.delete(i.id));
    }
    resetCleanConfirmState();
    renderTable();
    updateSelectionSummary();
  });

  document.getElementById("inpSearch").addEventListener("input", renderTable);
  document.getElementById("selDriveFilter").addEventListener("change", renderTable);
  document.getElementById("selRiskFilter").addEventListener("change", renderTable);
  document.getElementById("selCategoryFilter").addEventListener("change", renderTable);
  document.getElementById("selSortBy").addEventListener("change", renderTable);

  document.querySelectorAll(".risk-tier-row").forEach((row) => {
    row.addEventListener("click", () => {
      const risk = row.getAttribute("data-risk-filter");
      const sel = document.getElementById("selRiskFilter");
      sel.value = sel.value === risk ? "ALL" : risk;
      renderTable();
    });
  });

  document.getElementById("btnExecuteClean").addEventListener("click", executeClean);

  window.addEventListener("resize", () => {
    if (state.chartInstance && !state.chartInstance.isDisposed()) {
      state.chartInstance.resize();
    }
  });
}

async function initApp() {
  bindEvents();
  await fetchDrives();
  await fetchHistory();

  // Kiểm tra trạng thái quét hiện tại, nếu chưa quét lần nào thì tự động chạy quét ngay
  try {
    const res = await fetch("/api/scan/status");
    const snapshot = await res.json();
    if (snapshot.status === "idle") {
      await startScan();
    } else {
      updateSnapshotUI(snapshot);
      if (snapshot.status === "running") {
        startPolling();
      }
    }
  } catch (_err) {
    await startScan();
  }
}

document.addEventListener("DOMContentLoaded", initApp);

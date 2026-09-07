/** FestIn Monitoring Dashboard — SPA Frontend */

(function () {
    "use strict";

    const API_BASE = "/api/v1";
    let refreshInterval;

     // -- Utility functions --

    async function apiFetch(path, options = {}) {
         const headers = { "Accept": "application/json", ...options.headers };
        try {
             const resp = await fetch(API_BASE + path, { ...options, headers });
            if (!resp.ok) {
                let msg = "HTTP " + resp.status;
                try {
                     const body = await resp.json();
                    msg = body.error || msg;
                 } catch (_) {}
                throw new Error(msg);
            }
             return resp.json();
        } catch (err) {
            console.warn("API Error:", path, err.message);
            throw err;
         }
    }

    function severityClass(sev) {
        return "severity-" + (sev || "low");
    }

    function formatDate(ts) {
         if (!ts) return "—";
        const d = new Date(ts);
        return d.toLocaleString();
    }

    // -- Rendering functions --

     async function renderStats() {
        try {
            const data = await apiFetch("/stats");
             document.getElementById("stat-scans").textContent = data.scan_count || 0;
            document.getElementById("stat-findings").textContent = data.findings?.total || 0;
            document.getElementById("stat-critical").textContent = data.findings?.critical || 0;
            document.getElementById("stat-high").textContent = data.findings?.high || 0;
         } catch (_) {}
    }

     async function renderScanList() {
        try {
             const data = await apiFetch("/scans?limit=20");
            const el = document.getElementById("scan-list");
             document.getElementById("queue-count").textContent = data.total || 0;
            if (!data.scans || data.scans.length === 0) {
                el.innerHTML = '<div class="empty-state">No scans yet</div>';
                return;
            }
             el.innerHTML = data.scans.map((s) => `
                 <div class="list-item">
                     <div>
                         <strong>${s.scan_id || s.id}</strong>
                         <span style="color:#a0aec0;font-size:0.8rem;margin-left:0.5rem">${formatDate(s.started_at)}</span>
                     </div>
                     <button class="btn btn-sm btn-danger" onclick="deleteScan('${s.scan_id || s.id}')">Delete</button>
                 </div>
             `).join("");
         } catch (_) {
            el.innerHTML = '<div class="empty-state">Failed to load scans</div>';
        }
    }

    async function deleteScan(scanId) {
        if (!confirm("Delete scan " + scanId + "?")) return;
        try {
             await apiFetch("/scans/" + encodeURIComponent(scanId), { method: "DELETE" });
            await renderScanList();
             alert("Scan deleted");
         } catch (err) {
            alert("Error: " + err.message);
         }
    }

    async function renderFindings() {
        try {
             const sevFilter = document.getElementById("severity-filter").value;
            const params = new URLSearchParams({ limit: "50" });
            if (sevFilter) params.set("severity", sevFilter);
            const data = await apiFetch("/findings?" + params.toString());
            const el = document.getElementById("findings-list");
             if (!data.findings || data.findings.length === 0) {
                el.innerHTML = '<div class="empty-state">No findings</div>';
                return;
            }
             el.innerHTML = data.findings.map((f) => `
                 <div class="list-item">
                     <div>
                         <span class="severity-badge ${severityClass(f.severity)}">${f.severity}</span>
                         <strong style="margin-left:0.5rem">${escapeHtml(f.rule_name || f.rule_id)}</strong>
                         <span style="color:#718096;font-size:0.8rem;margin-left:0.5rem">@${escapeHtml(f.bucket_name || "?")}</span>
                     </div>
                     <div style="font-size:0.8rem;color:#a0aec0">${escapeHtml((f.match_redacted || f.match || "").substring(0, 40))}</div>
                 </div>
             `).join("");
         } catch (_) {
             el.innerHTML = '<div class="empty-state">Failed to load findings</div>';
        }
    }

     async function renderBuckets() {
        try {
             const data = await apiFetch("/buckets?limit=30");
            const el = document.getElementById("buckets-list");
            if (!data.buckets || data.buckets.length === 0) {
                 el.innerHTML = '<div class="empty-state">No buckets found</div>';
                return;
            }
             el.innerHTML = data.buckets.map((b) => `
                 <div class="list-item">
                     <span><strong>${escapeHtml(b.bucket_name)}</strong> @ ${escapeHtml(b.domain)}</span>
                     <span style="font-size:0.8rem;color:#a0aec0">${b.scan_id || ""}</span>
                 </div>
             `).join("");
         } catch (_) {
             el.innerHTML = '<div class="empty-state">Failed to load buckets</div>';
        }
    }

     async function renderScheduled() {
        try {
            const data = await apiFetch("/queues/schedule");
             const el = document.getElementById("scheduled-list");
            if (!data.scheduled || data.scheduled.length === 0) {
                 el.innerHTML = '<div class="empty-state">No scheduled scans</div>';
                return;
            }
             el.innerHTML = data.scheduled.map((s) => `
                 <div class="list-item">
                     <div>
                         <strong>${escapeHtml(s.domain)}</strong>
                         <span style="color:#718096;font-size:0.8rem;margin-left:0.5rem">every ${s.interval_minutes}m</span>
                     </div>
                     <button class="btn btn-sm btn-danger" onclick="removeScheduled(${s.id})">Remove</button>
                 </div>
             `).join("");
         } catch (_) {
            el.innerHTML = '<div class="empty-state">Failed to load schedules</div>';
        }
    }

    async function removeScheduled(id) {
        try {
             await apiFetch("/queues/schedule/" + id, { method: "DELETE" });
            await renderScheduled();
         } catch (err) {
            alert("Error: " + err.message);
        }
    }

     async function renderHealth() {
         try {
             const data = await apiFetch("/health");
             const el = document.getElementById("health-status");
            el.className = "health-box";
             el.textContent = "Database: " + (data.db_path || "not configured") +
                 " | Queues pending: " + (data.queues_pending ?? "?");
         } catch (err) {
             const el = document.getElementById("health-status");
             el.className = "health-box error";
            el.textContent = "Service unavailable: " + err.message;
        }
    }

     // -- Event handlers --

    function escapeHtml(str) {
         return String(str || "").replace(/[&<>"']/g, (m) => ({
             "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
          })[m]);
    }

    async function handleScanSubmit(e) {
         e.preventDefault();
        const input = document.getElementById("domains-input");
        const domains = input.value.split(",").map((d) => d.trim()).filter(Boolean);
        if (domains.length === 0) return;
        try {
             await apiFetch("/scans/run-scan", {
                 method: "POST",
                headers: { "Content-Type": "application/json" },
                 body: JSON.stringify({ domains }),
             });
            alert("Scan started for: " + domains.join(", "));
            input.value = "";
            await renderScanList();
        } catch (err) {
            alert("Error starting scan: " + err.message);
         }
    }

    async function handleScheduleSubmit(e) {
        e.preventDefault();
        const domain = document.getElementById("schedule-domain").value.trim();
         const interval = parseInt(document.getElementById("schedule-interval").value, 10) || 60;
        if (!domain) return;
        try {
             await apiFetch("/queues/schedule", {
                method: "POST",
                 headers: { "Content-Type": "application/json" },
                 body: JSON.stringify({ domain, interval_minutes: interval }),
             });
            document.getElementById("schedule-domain").value = "";
             await renderScheduled();
        } catch (err) {
            alert("Error scheduling: " + err.message);
        }
    }

     // -- Init --

    async function refreshAll() {
         await Promise.all([
            renderStats(),
            renderScanList(),
            renderFindings(),
            renderBuckets(),
            renderScheduled(),
             renderHealth(),
         ]);
    }

    function init() {
         // Bind form handlers
        document.getElementById("scan-form").addEventListener("submit", handleScanSubmit);
        document.getElementById("schedule-form").addEventListener("submit", handleScheduleSubmit);

        // Severity filter change
        document.getElementById("severity-filter").addEventListener("change", renderFindings);

         // Initial render
         refreshAll();

         // Auto-refresh every 30 seconds
         refreshInterval = setInterval(refreshAll, 30000);
    }

     // Boot on DOM ready
     if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
         init();
    }

     // Expose for inline handlers
     window.deleteScan = deleteScan;
    window.removeScheduled = removeScheduled;

})();

/** FestIn Monitoring Dashboard — SPA Frontend */

(function () {
    "use strict";

    const API_BASE = "/api/v1";
    const TOKEN_KEY = "festin_token";
    let refreshInterval;

     // -- Utility functions --

    function getToken() {
        return localStorage.getItem(TOKEN_KEY);
    }

    function setToken(token) {
        if (token) {
            localStorage.setItem(TOKEN_KEY, token);
        } else {
            localStorage.removeItem(TOKEN_KEY);
        }
    }

    async function apiFetch(path, options = {}) {
        const headers = { "Accept": "application/json", ...options.headers };
        const token = getToken();
        if (token) {
            headers["Authorization"] = "Bearer " + token;
        }
        const resp = await fetch(API_BASE + path, { ...options, headers });
        if (resp.status === 401) {
            setToken(null);
            showLogin();
            throw new Error("Session expired — please sign in again");
        }
        if (!resp.ok) {
            let msg = "HTTP " + resp.status;
            try {
                const body = await resp.json();
                msg = body.error || msg;
            } catch (_) {}
            throw new Error(msg);
        }
        return resp.json();
    }

    function severityClass(sev) {
        return "severity-" + (sev || "low");
    }

    let flashTimer;
    function showFlash(message, isError) {
        let el = document.getElementById("flash-toast");
        if (!el) {
            el = document.createElement("div");
            el.id = "flash-toast";
            document.body.appendChild(el);
        }
        el.textContent = message;
        el.className = "flash-toast" + (isError ? " flash-error" : "");
        clearTimeout(flashTimer);
        flashTimer = setTimeout(() => { el.className += " flash-hidden"; }, 3500);
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
            showFlash("Scan deleted");
        } catch (err) {
            showFlash("Error: " + escapeHtml(err.message), true);
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
            showFlash("Error: " + escapeHtml(err.message), true);
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
            showFlash("Scan started for: " + escapeHtml(domains.join(", ")));
            input.value = "";
            await renderScanList();
        } catch (err) {
            showFlash("Error starting scan: " + escapeHtml(err.message), true);
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
            showFlash("Error scheduling: " + escapeHtml(err.message), true);
        }
    }

    // -- Auth --

    function showLogin() {
        // Stop polling while logged out (body does more than clear: resets handle)
        if (refreshInterval) {
            clearInterval(refreshInterval);
            refreshInterval = undefined;
        }
        document.getElementById("dashboard").hidden = true;
        document.getElementById("user-area").hidden = true;
        document.getElementById("login-section").hidden = false;
        document.getElementById("login-password").value = "";
        const first = document.getElementById("login-username");
        if (!first.value) first.focus();
    }

    function showDashboard(username) {
        document.getElementById("login-section").hidden = true;
        document.getElementById("dashboard").hidden = false;
        document.getElementById("user-name").textContent = username;
        document.getElementById("user-area").hidden = false;
    }

    function showLoginError(msg) {
        const el = document.getElementById("login-error");
        el.textContent = msg;
        el.hidden = false;
    }

    function clearLoginError() {
        const el = document.getElementById("login-error");
        el.textContent = "";
        el.hidden = true;
    }

    async function login(username, password) {
        const resp = await fetch(API_BASE + "/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json", "Accept": "application/json" },
            body: JSON.stringify({ username, password }),
        });
        if (!resp.ok) {
            let msg = "HTTP " + resp.status;
            try {
                const body = await resp.json();
                msg = body.error || msg;
            } catch (_) {}
            throw new Error(msg);
        }
        const data = await resp.json();
        setToken(data.access_token);
        return data;
    }

    async function register(username, password) {
        const resp = await fetch(API_BASE + "/auth/register", {
            method: "POST",
            headers: { "Content-Type": "application/json", "Accept": "application/json" },
            body: JSON.stringify({ username, password }),
        });
        if (!resp.ok) {
            let msg = "HTTP " + resp.status;
            try {
                const body = await resp.json();
                msg = body.error || msg;
            } catch (_) {}
            if (resp.status === 409 || resp.status === 403) {
                throw new Error("Registration closed");
            }
            throw new Error(msg);
        }
        return resp.json();
    }

    function usernameFromToken(token) {
        try {
            const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
            return payload.sub || payload.username || "";
        } catch (_) {
            return "";
        }
    }

    function logout() {
        setToken(null);
        showLogin();
    }

    let registerMode = false;

    async function handleLoginSubmit(e) {
        e.preventDefault();
        clearLoginError();
        const username = document.getElementById("login-username").value.trim();
        const password = document.getElementById("login-password").value;
        if (!username || !password) {
            showLoginError("Username and password are required");
            return;
        }
        if (registerMode) {
            await doRegister(username, password);
        } else {
            try {
                await login(username, password);
                startSession(username);
            } catch (err) {
                showLoginError("Sign in failed: " + err.message);
            }
        }
    }

    async function doRegister(username, password) {
        try {
            await register(username, password);
            // First user registered: try to sign in immediately
            try {
                await login(username, password);
                startSession(username);
            } catch (_) {
                showLoginError("Account created — please sign in");
            }
            registerMode = false;
            resetAuthForm();
        } catch (err) {
            showLoginError(err.message);
        }
    }

    function toggleRegisterMode() {
        registerMode = !registerMode;
        resetAuthForm();
    }

    function resetAuthForm() {
        const btn = document.getElementById("login-submit");
        const link = document.getElementById("register-toggle");
        const title = document.getElementById("login-title");
        if (btn) btn.textContent = registerMode ? "Create account" : "Sign in";
        if (link) link.textContent = registerMode ? "Back to sign in" : "Create admin account";
        if (title) title.textContent = registerMode ? "Create Admin Account" : "Sign in to Festin";
    }

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

    function startSession(username) {
        clearLoginError();
        showDashboard(username);
        refreshAll();
        clearInterval(refreshInterval);
        refreshInterval = setInterval(refreshAll, 30000);
    }

    function init() {
        // Bind form handlers
        document.getElementById("scan-form").addEventListener("submit", handleScanSubmit);
        document.getElementById("schedule-form").addEventListener("submit", handleScheduleSubmit);

        // Auth bindings
        document.getElementById("login-form").addEventListener("submit", handleLoginSubmit);
        document.getElementById("logout-btn").addEventListener("click", logout);
        document.getElementById("register-toggle").addEventListener("click", (e) => {
            e.preventDefault();
            toggleRegisterMode();
        });

        // Severity filter change
        document.getElementById("severity-filter").addEventListener("change", renderFindings);

        // Route to login or dashboard based on stored token
        const token = getToken();
        if (token) {
            showDashboard(usernameFromToken(token));
            refreshAll();
            refreshInterval = setInterval(refreshAll, 30000);
        } else {
            showLogin();
        }
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

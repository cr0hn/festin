/** FestIn — S3 Exposure Monitor SPA (vanilla JS, hash routing) */

(function () {
    "use strict";

    const API = "/api/v1";
    const TOKEN_KEY = "festin_token";
    const ROLE_KEY = "festin_role";
    const POLL_MS = 15000;
    const ARM_MS = 3000;

    let pollTimer = null;
    let pollRoute = null;
    let registerMode = false;
    let flashTimer = null;
    let scanAutoTimer = null;

    /* ---------- utilities ---------- */

    function escapeHtml(str) {
        return String(str == null ? "" : str).replace(/[&<>"']/g, (m) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
        })[m]);
    }

    function $(id) { return document.getElementById(id); }

    function showFlash(message, isError) {
        const el = $("flash-toast");
        if (!el) return;
        el.textContent = message;
        el.hidden = false;
        el.className = "flash" + (isError ? " flash-error" : " flash-ok");
        clearTimeout(flashTimer);
        flashTimer = setTimeout(() => { el.className += " flash-hidden"; }, 3500);
    }

    function getToken() { return localStorage.getItem(TOKEN_KEY); }

    function setToken(token) {
        if (token) localStorage.setItem(TOKEN_KEY, token);
        else localStorage.removeItem(TOKEN_KEY);
    }

    function getRole() { return localStorage.getItem(ROLE_KEY) || "viewer"; }

    function setRole(role) {
        if (role) localStorage.setItem(ROLE_KEY, role);
        else localStorage.removeItem(ROLE_KEY);
    }

    function isAdmin() { return getRole() === "admin"; }

    function fmtDate(ts) {
        if (!ts) return "\u2014";
        const d = new Date(ts);
        return isNaN(d.getTime()) ? String(ts) : d.toISOString().replace("T", " ").slice(0, 19);
    }

    function sevToken(sev) {
        const s = String(sev || "low").toLowerCase();
        const cls = { critical: "sev-critical", high: "sev-high", medium: "sev-medium", low: "sev-low", info: "sev-info" }[s] || "sev-low";
        const label = { critical: "CRIT", high: "HIGH", medium: "MED", low: "LOW", info: "INFO" }[s] || s.toUpperCase();
        return '<span class="sev ' + cls + '">' + escapeHtml(label) + "</span>";
    }

    function statusToken(status) {
        const s = String(status || "pending").toLowerCase();
        const map = { pending: ["QUEUED", "tok-pending"], running: ["RUNNING", "tok-running"], completed: ["DONE", "tok-completed"], failed: ["FAIL", "tok-failed"] };
        const m = map[s] || [s.toUpperCase(), "tok-pending"];
        return '<span class="token ' + m[1] + '">[' + escapeHtml(m[0]) + "]</span>";
    }

    function emptyLine(msg, isError) {
        return '<div class="' + (isError ? "empty-err" : "empty") + '">// ' + escapeHtml(msg) + "</div>";
    }

    /* ---------- api ---------- */

    async function apiFetch(path, options) {
        const opts = options || {};
        const headers = Object.assign({ "Accept": "application/json" }, opts.headers || {});
        const token = getToken();
        if (token) headers["Authorization"] = "Bearer " + token;
        let resp;
        try {
            resp = await fetch(API + path, Object.assign({}, opts, { headers }));
        } catch (_) {
            throw new Error("network error \u2014 service unreachable");
        }
        if (resp.status === 401) {
            clearSession();
            route();
            throw new Error("session expired");
        }
        if (!resp.ok) {
            let msg = "HTTP " + resp.status;
            try {
                const body = await resp.json();
                if (body && body.error) msg = body.error;
                else if (body && body.detail) msg = body.detail;
            } catch (_) {}
            throw new Error(msg);
        }
        if (resp.status === 204) return null;
        return resp.json();
    }

    /* ---------- session / auth ---------- */

    function clearSession() {
        setToken(null);
        setRole(null);
        stopPolling();
        stopScanAuto();
    }

    function doLogout() {
        clearSession();
        location.hash = "#/login";
        route();
        showFlash("Signed out");
    }

    async function requestLogin(username, password) {
        const data = await apiFetch("/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username: username, password: password }),
        });
        setToken(data.access_token);
        setRole(data.role || "viewer");
        return data;
    }

    async function requestRegister(username, password) {
        try {
            return await apiFetch("/auth/register", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username: username, password: password }),
            });
        } catch (err) {
            const m = String(err.message || "");
            if (err.status === 401 || err.status === 403 || err.status === 409 || /40[139]/.test(m)) {
                throw new Error("Registration closed");
            }
            throw err;
        }
    }

    async function handleLoginSubmit(e) {
        e.preventDefault();
        const errEl = $("login-error");
        errEl.hidden = true;
        const username = ($("login-username").value || "").trim();
        const password = $("login-password").value || "";
        if (!username || !password) {
            errEl.textContent = "// username and password required";
            errEl.hidden = false;
            return;
        }
        try {
            if (registerMode) {
                await requestRegister(username, password);
                registerMode = false;
                setRegisterMode();
                await requestLogin(username, password);
            } else {
                await requestLogin(username, password);
            }
            enterShell();
            location.hash = "#/home";
            route();
            showFlash("signed in as " + username);
        } catch (err) {
            errEl.textContent = "// " + err.message;
            errEl.hidden = false;
        }
    }

    function setRegisterMode() {
        const submit = $("login-submit");
        const toggle = $("register-toggle");
        const mode = $("login-mode");
        if (!submit || !toggle || !mode) return;
        if (registerMode) {
            submit.textContent = "CREATE ACCOUNT";
            toggle.textContent = "BACK TO SIGN IN";
            mode.textContent = "// first account becomes ADMIN \u00b7 later registration is admin-only";
        } else {
            submit.textContent = "SIGN IN";
            toggle.textContent = "CREATE ADMIN ACCOUNT";
            mode.textContent = "// first account becomes ADMIN";
        }
        $("login-error").hidden = true;
    }

    function toggleRegister() {
        registerMode = !registerMode;
        setRegisterMode();
    }

    /* ---------- shell / nav ---------- */

    function enterShell() {
        $("view-login").hidden = true;
        $("shell").hidden = false;
        $("chip-user").textContent = usernameFromToken(getToken()) || "user";
        const roleEl = $("chip-role");
        roleEl.textContent = " \u00b7 " + getRole().toUpperCase();
        const usersLink = document.querySelector('[data-nav="users"]');
        if (usersLink) usersLink.hidden = !isAdmin();
        refreshHealth();
    }

    function usernameFromToken(token) {
        try {
            const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
            return payload.sub || payload.username || "";
        } catch (_) {
            return "";
        }
    }

    function showLogin() {
        $("shell").hidden = true;
        $("view-login").hidden = false;
    }

    function setActiveNav(name) {
        document.querySelectorAll(".nav-link").forEach((a) => {
            a.classList.toggle("active", a.getAttribute("data-nav") === name);
        });
    }

    function setTitle(t) { $("view-title").textContent = t; }

    function refreshHealth() {
        apiFetch("/health").then((h) => {
            const pending = typeof h.pending === "number" ? h.pending : "\u2014";
            const cls = typeof h.pending === "number" && h.pending > 0 ? "dot-busy" : "dot-ok";
            $("health-strip").innerHTML =
                '<span class="' + cls + '">\u25cf</span> SCHED OK \u00b7 PENDING ' + escapeHtml(String(pending));
        }).catch(() => {
            $("health-strip").innerHTML = '<span class="dot-err">\u25cf</span> SCHED ERR';
        });
    }

    /* ---------- polling ---------- */

    function stopPolling() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        pollRoute = null;
    }

    function armPolling(fn) {
        stopPolling();
        pollRoute = location.hash;
        pollTimer = setInterval(fn, POLL_MS);
    }

    function stopScanAuto() {
        if (scanAutoTimer) { clearInterval(scanAutoTimer); scanAutoTimer = null; }
    }

    /* ---------- two-step destructive arm ---------- */

    function armButton(btn, onConfirm) {
        if (btn.getAttribute("data-armed") === "1") {
            btn.removeAttribute("data-armed");
            btn.classList.remove("armed");
            btn.textContent = btn.getAttribute("data-label") || "delete";
            onConfirm();
            return;
        }
        btn.setAttribute("data-armed", "1");
        btn.classList.add("armed");
        btn.setAttribute("data-label", btn.textContent);
        btn.textContent = "confirm?";
        setTimeout(() => {
            if (btn.getAttribute("data-armed") === "1") {
                btn.removeAttribute("data-armed");
                btn.classList.remove("armed");
                btn.textContent = btn.getAttribute("data-label") || "delete";
            }
        }, ARM_MS);
    }

    /* ---------- rendering helpers ---------- */

    function renderInto(id, html) { $(id).innerHTML = html; }

    function escNum(v) { return escapeHtml(String(v == null ? 0 : v)); }

    function tableHtml(headers, rowsHtml) {
        return '<div class="tbl-wrap"><table><thead><tr>' +
            headers.map((h) => {
                const num = h.charAt(0) === "#" ? ' class="num"' : "";
                return "<th" + num + ">" + escapeHtml(h.replace(/^#/, "")) + "</th>";
            }).join("") +
            "</tr></thead><tbody>" + (rowsHtml || "") + "</tbody></table></div>";
    }

    /* ---------- views: home ---------- */

    function sparkline(values, width) {
        /* ASCII block sparkline: ▁▂▃▄▅▆▇█ over normalized values */
        const blocks = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588";
        const vals = values.length ? values : [0];
        const max = Math.max.apply(null, vals);
        if (max === 0) return new Array(width).fill("\u2581").join("");
        const out = [];
        for (let i = 0; i < width; i++) {
            const v = i < vals.length ? vals[vals.length - width + i] : 0;
            const idx = Math.round((v / max) * (blocks.length - 1));
            out.push(blocks[Math.max(0, idx)]);
        }
        return out.join("");
    }

    function barRow(label, value, max, colorCls) {
        const width = 24;
        const filled = max > 0 ? Math.round((value / max) * width) : 0;
        const bar = "\u2588".repeat(filled) + "\u00b7".repeat(width - filled);
        return '<div class="chart-row">' +
            '<span class="chart-label ' + (colorCls || "") + '">' + escapeHtml(label) + "</span>" +
            '<span class="chart-bar">' + bar + "</span>" +
            '<span class="num chart-value">' + escNum(value) + "</span></div>";
    }

    function exposureHeadline(timeline) {
        /* Single-metric story: trend of total findings/day across the window. */
        const days = timeline.filter((d) => d.findings > 0 || d.scans > 0);
        if (timeline.length < 2) {
            return { title: "NOT ENOUGH HISTORY YET", sub: "run more scans to build the 14-day trend", foot: "" };
        }
        const first = timeline[0], last = timeline[timeline.length - 1];
        const peak = timeline.reduce((a, b) => (b.findings > a.findings ? b : a), timeline[0]);
        const total = timeline.reduce((acc, d) => acc + d.findings, 0);
        const critTotal = timeline.reduce((acc, d) => acc + (d.critical || 0), 0);
        const hiTotal = timeline.reduce((acc, d) => acc + (d.high || 0), 0);

        let title, sub;
        if (total === 0) {
            title = "NO FINDINGS IN THE LAST 14 DAYS";
            sub = "every scan came back clean across all projects";
        } else if (last.findings < first.findings && first.findings > 0) {
            const drop = Math.round(((first.findings - last.findings) / first.findings) * 100);
            title = "EXPOSURE TRENDING DOWN " + drop + "% SINCE " + escapeHtml(first.day.slice(5));
            sub = "findings fell from " + first.findings + " to " + last.findings + " per scan-day";
        } else if (last.findings > first.findings) {
            const rise = Math.round(((last.findings - first.findings) / Math.max(1, first.findings)) * 100);
            title = "EXPOSURE TRENDING UP " + rise + "% SINCE " + escapeHtml(first.day.slice(5));
            sub = "findings grew from " + first.findings + " to " + last.findings + " per scan-day";
        } else {
            title = "EXPOSURE FLAT OVER 14 DAYS";
            sub = total + " findings across " + timeline.length + " active scan-days";
        }
        const foot = critTotal || hiTotal
            ? "of " + total + " findings, " + critTotal + " critical and " + hiTotal + " high \u2014 peak day " + escapeHtml(peak.day)
            : "no critical or high findings in the window";
        return { title: title, sub: sub, foot: foot };
    }

    function renderExposureHero(timeline) {
        const w = timeline.length ? 14 : 0;
        if (!w) {
            return '<div class="panel hero-card"><div class="panel-label">EXPOSURE TREND \u2014 14 DAYS</div>' +
                emptyLine("no scan data yet \u2014 run a scan to build the trend") + "</div>";
        }
        const head = exposureHeadline(timeline);
        const maxVal = Math.max(1, Math.max.apply(null, timeline.map((d) => Math.max(d.findings, d.scans))));
        const rows = timeline.map((d) => {
            const scansH = Math.round((d.scans / maxVal) * 40);
            const findsH = Math.round((d.findings / maxVal) * 40);
            const critH = Math.round(((d.critical || 0) / maxVal) * 40);
            const highH = Math.round(((d.high || 0) / maxVal) * 40);
            return "<tr>" +
                '<td class="axis-day">' + escapeHtml(d.day.slice(5)) + "</td>" +
                '<td class="axis-cells">' +
                    '<span class="hbar hbar-scans" style="height:' + scansH + 'px" title="scans: ' + d.scans + '"></span>' +
                    '<span class="hbar hbar-finds" style="height:' + findsH + 'px" title="findings: ' + d.findings + '"></span>' +
                    '<span class="hbar hbar-crit" style="height:' + critH + 'px" title="critical: ' + (d.critical || 0) + '"></span>' +
                "</td></tr>";
        }).join("");
        return (
            '<div class="panel hero-card">' +
            '<div class="hero-headline">' + head.title + "</div>" +
            '<div class="hero-sub">' + head.sub + "</div>" +
            '<div class="hero-chart">' +
            "<table><tbody>" + rows + "</tbody></table>" +
            "</div>" +
            '<div class="hero-foot"><span class="legend-scan">\u25a0</span> scans' +
            '<span class="legend-find">\u25a0</span> findings' +
            '<span class="legend-crit">\u25a0</span> critical (stacked)' +
            '<span class="hero-foot-right">' + head.foot + "</span></div>" +
            "</div>"
        );
    }
    function renderHomeShell(stats) {
        const f = stats.findings || {};
        const timeline = (stats.recent_scans || []).slice().reverse();
        const scanSpark = sparkline(timeline.map((d) => d.scans), 28);
        const findSpark = sparkline(timeline.map((d) => d.findings), 28);
        const bucketSpark = sparkline(timeline.map((d) => d.buckets), 28);
        const sevMax = Math.max(f.critical || 0, f.high || 0, f.medium || 0, f.low || 0, 1);
        const totalBuckets = timeline.reduce((acc, d) => acc + (d.buckets || 0), 0);
        const lastDay = timeline.length ? timeline[timeline.length - 1] : null;

        return (
            '<div class="count-strip">' +
            '<div class="count-item"><span class="micro">SCANS</span><span class="count-value">' + escNum(stats.scan_count) + "</span></div>" +
            '<div class="count-item"><span class="micro">FINDINGS</span><span class="count-value">' + escNum(f.total) + "</span></div>" +
            '<div class="count-item"><span class="micro">CRITICAL</span><span class="count-value sev-critical">' + escNum(f.critical) + "</span></div>" +
            '<div class="count-item"><span class="micro">HIGH</span><span class="count-value sev-high">' + escNum(f.high) + "</span></div>" +
            '<div class="count-item"><span class="micro">BUCKETS (14d)</span><span class="count-value">' + escNum(totalBuckets) + "</span></div>" +
            "</div>" +

            renderExposureHero(timeline) +
            '<div class="home-grid">' +
            '<div class="panel"><div class="panel-label">ACTIVITY \u2014 SCANS / DAY (14d)</div>' +
            '<div class="spark">' + scanSpark + "</div>" +
            '<div class="spark-meta micro">' + (timeline.length ? escapeHtml(timeline[0].day) + " \u2192 " + escapeHtml(timeline[timeline.length - 1].day) : "no data yet") + "</div></div>" +

            '<div class="panel"><div class="panel-label">EXPOSURE \u2014 FINDINGS / DAY</div>' +
            '<div class="spark spark-accent">' + findSpark + "</div>" +
            '<div class="spark-meta micro">peak ' + escNum(Math.max.apply(null, timeline.length ? timeline.map((d) => d.findings) : [0])) + "</div></div>" +

            '<div class="panel"><div class="panel-label">DISCOVERY \u2014 BUCKETS / DAY</div>' +
            '<div class="spark spark-low">' + bucketSpark + "</div>" +
            '<div class="spark-meta micro">total ' + escNum(totalBuckets) + (lastDay ? " \u00b7 today " + escNum(lastDay.buckets) : "") + "</div></div>" +

            '<div class="panel"><div class="panel-label">FINDINGS BY SEVERITY</div>' +
            barRow("CRIT", f.critical || 0, sevMax, "sev-critical") +
            barRow("HIGH", f.high || 0, sevMax, "sev-high") +
            barRow("MED", f.medium || 0, sevMax, "sev-medium") +
            barRow("LOW", f.low || 0, sevMax, "sev-low") +
            "</div>" +

            '<div class="panel"><div class="panel-label">SCAN OUTCOMES (LAST 100)</div>' +
            '<div id="home-status">' + emptyLine("loading\u2026") + "</div></div>" +

            '<div class="panel"><div class="panel-label">TOP DOMAINS \u2014 FINDINGS</div>' +
            '<div id="home-domains">' + emptyLine("loading\u2026") + "</div></div>" +

            '<div class="panel"><div class="panel-label">PROJECT RANKING \u2014 FINDINGS</div>' +
            '<div id="home-projects">' + emptyLine("loading\u2026") + "</div></div>" +
            "</div>"
        );
    }

    async function loadHomeStatus() {
        try {
            const data = await apiFetch("/scans?limit=100");
            const scans = data.scans || [];
            if (!scans.length) { renderInto("home-status", emptyLine("no scans yet")); return; }
            const order = ["completed", "failed", "running", "pending"];
            const counts = {};
            scans.forEach((s) => { const k = String(s.status || "pending").toLowerCase(); counts[k] = (counts[k] || 0) + 1; });
            const total = scans.length || 1;
            renderInto("home-status",
                '<div class="stack-bar">' +
                order.map((st) => {
                    const n = counts[st] || 0;
                    const pct = Math.round((n / total) * 100);
                    return n ? '<span class="stack-seg tok-' + st + '" style="width:' + pct + '%" title="' + st + ': ' + n + '"></span>' : "";
                }).join("") +
                "</div>" +
                '<div class="stack-legend micro">' +
                order.map((st) => counts[st] ? '<span class="tok-' + st + '">' + st.toUpperCase() + " " + counts[st] + "</span>" : "").join(" \u00b7 ") +
                "</div>");
        } catch (err) {
            renderInto("home-status", emptyLine(err.message, true));
        }
    }

    async function loadHomeDomains() {
        try {
            const data = await apiFetch("/scans?limit=100");
            const byDomain = {};
            (data.scans || []).forEach((s) => {
                const d = s.domain_name || "?";
                byDomain[d] = (byDomain[d] || 0) + (s.findings_count || 0);
            });
            const list = Object.keys(byDomain).map((d) => ({ domain: d, n: byDomain[d] }))
                .filter((x) => x.n > 0).sort((a, b) => b.n - a.n).slice(0, 6);
            if (!list.length) { renderInto("home-domains", emptyLine("no domain with findings yet")); return; }
            const max = list[0].n || 1;
            renderInto("home-domains", list.map((x) =>
                '<div class="chart-row"><span class="chart-label">' + escapeHtml(x.domain) + "</span>" +
                '<span class="chart-bar">' + "\u2588".repeat(Math.max(1, Math.round((x.n / max) * 18))) + "</span>" +
                '<span class="num chart-value">' + escNum(x.n) + "</span></div>"
            ).join(""));
        } catch (err) {
            renderInto("home-domains", emptyLine(err.message, true));
        }
    }

    async function loadHomeProjects() {
        try {
            const data = await apiFetch("/projects");
            const list = (data.projects || []).filter((p) => p.findings_count > 0)
                .sort((a, b) => b.findings_count - a.findings_count).slice(0, 5);
            if (!list.length) { renderInto("home-projects", emptyLine("no findings across projects")); return; }
            const max = list[0].findings_count || 1;
            renderInto("home-projects", list.map((p) =>
                '<div class="chart-row"><span class="chart-label"><a href="#/project/' + p.id + '">' +
                escapeHtml(p.name) + "</a></span>" +
                '<span class="chart-bar">' + "\u2588".repeat(Math.max(1, Math.round((p.findings_count / max) * 18))) + "</span>" +
                '<span class="num chart-value">' + escNum(p.findings_count) + "</span></div>"
            ).join(""));
        } catch (err) {
            renderInto("home-projects", emptyLine(err.message, true));
        }
    }

    async function loadHome() {
        setTitle("HOME");
        setActiveNav("home");
        const view = $("view-home");
        view.hidden = false;
        view.innerHTML = emptyLine("loading\u2026");
        let stats;
        try {
            stats = await apiFetch("/stats");
        } catch (err) {
            view.innerHTML = emptyLine(err.message, true);
            return;
        }
        view.innerHTML = renderHomeShell(stats);
        loadHomeProjects();
        loadHomeStatus();
        loadHomeDomains();
    }


    async function loadHomeTable() { await loadHome(); }

    /* ---------- views: projects ---------- */

    function renderProjectsShell() {
        const view = $("view-projects");
        view.hidden = false;
        const admin = isAdmin();
        view.innerHTML =
            '<div class="section-head"><h2 class="panel-label">PROJECTS</h2></div>' +
            (admin
                ? '<form id="project-create-form" class="form-row" data-form="project-create">' +
                  '<input id="np-name" placeholder="project name" required>' +
                  '<input id="np-desc" placeholder="description (optional)">' +
                  '<button class="btn btn-primary" type="submit">+ NEW PROJECT</button></form>'
                : "") +
            '<div id="projects-body">' + emptyLine("loading\u2026") + "</div>";
        bindViewForms(view);
    }

    async function loadProjects() {
        setTitle("PROJECTS");
        setActiveNav("projects");
        renderProjectsShell();
        await loadProjectsTable();
    }

    async function loadProjectsTable() {
        const admin = isAdmin();
        try {
            const data = await apiFetch("/projects");
            const list = data.projects || [];
            if (!list.length) {
                renderInto("projects-body", emptyLine("no projects yet"));
                return;
            }
            const rows = list.map((p) => {
                const del = admin && p.id !== 1
                    ? '<button class="btn btn-danger btn-sm" data-action="del-project" data-id="' + p.id + '" data-name="' + escapeHtml(p.name) + '">DELETE</button>'
                    : "";
                return "<tr class=\"clickable\" data-action=\"open-project\" data-id=\"" + p.id + "\">" +
                    "<td>" + escapeHtml(p.name) + "</td>" +
                    "<td>" + escapeHtml(p.description || "") + "</td>" +
                    '<td class="num">' + escNum(p.domain_count) + "</td>" +
                    '<td class="num">' + escNum(p.scan_count) + "</td>" +
                    '<td class="num">' + escNum(p.findings_count) + "</td>" +
                    "<td>" + escapeHtml(fmtDate(p.last_scan_at)) + "</td>" +
                    "<td>" + del + "</td></tr>";
            }).join("");
            renderInto("projects-body", tableHtml(["NAME", "DESCRIPTION", "#DOMAINS", "#SCANS", "#FINDINGS", "LAST SCAN", ""], rows));
        } catch (err) {
            renderInto("projects-body", emptyLine(err.message, true));
        }
    }

    async function submitProjectCreate() {
        const name = ($("np-name").value || "").trim();
        const description = ($("np-desc").value || "").trim();
        if (!name) return;
        try {
            await apiFetch("/projects", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: name, description: description }),
            });
            showFlash("project created: " + name);
            $("np-name").value = "";
            $("np-desc").value = "";
            await loadProjectsTable();
        } catch (err) {
            showFlash(err.message, true);
        }
    }

    async function deleteProject(id) {
        try {
            await apiFetch("/projects/" + id, { method: "DELETE" });
            showFlash("project " + id + " deleted");
            await loadProjectsTable();
        } catch (err) {
            showFlash(err.message, true);
        }
    }

    async function deleteProject(id) {
        try {
            await apiFetch("/projects/" + id, { method: "DELETE" });
            showFlash("project " + id + " deleted");
            await loadProjectsTable();
        } catch (err) {
            showFlash(err.message, true);
        }
    }

    /* ---------- views: project detail ---------- */

    async function loadProjectDetail(id) {
        setTitle("PROJECT " + id);
        setActiveNav("projects");
        stopScanAuto();
        const view = $("view-project");
        view.hidden = false;
        const admin = isAdmin();
        view.innerHTML = emptyLine("loading\u2026");
        let data;
        try {
            data = await apiFetch("/projects/" + id);
        } catch (err) {
            view.innerHTML = emptyLine(err.message, true);
            return;
        }
        const p = data.project || {};
        const domains = data.domains || [];
        const counts =
            '<div class="count-strip">' +
            '<div class="count-item"><span class="micro">DOMAINS</span><span class="count-value">' + escNum(p.domain_count) + "</span></div>" +
            '<div class="count-item"><span class="micro">SCANS</span><span class="count-value">' + escNum(p.scan_count) + "</span></div>" +
            '<div class="count-item"><span class="micro">FINDINGS</span><span class="count-value">' + escNum(p.findings_count) + "</span></div>" +
            '<div class="count-item"><span class="micro">LAST SCAN</span><span class="count-value">' + escapeHtml(fmtDate(p.last_scan_at)) + "</span></div>" +
            "</div>";

        const domainRows = domains.length
            ? domains.map((d) => {
                const del = admin
                    ? ' <button class="btn btn-danger btn-sm" data-action="del-domain" data-id="' + d.id + '">DEL</button>'
                    : "";
                return "<tr><td>" + escapeHtml(d.domain_name) + "</td>" +
                    "<td>" + (d.enabled ? "yes" : "no") + "</td>" +
                    "<td>" + escapeHtml(fmtDate(d.created_at)) + "</td>" +
                    "<td>" + del + "</td></tr>";
            }).join("")
            : "";
        const domainsHtml =
            '<div class="section-head"><h2 class="panel-label">DOMAINS</h2>' +
            (admin
                ? '<button class="btn btn-primary" data-action="run-project-scan" data-id="' + id + '">RUN SCAN (ALL DOMAINS)</button>'
                : "") +
            "</div>" +
            (admin
                ? '<form class="form-row" data-form="add-domain" data-pid="' + id + '">' +
                  '<input class="ad-domain" placeholder="example.com" required>' +
                  '<button class="btn btn-primary" type="submit">+ ADD DOMAIN</button></form>'
                : "") +
            (domains.length ? tableHtml(["DOMAIN", "ENABLED", "CREATED", ""], domainRows) : emptyLine("no domains yet \u2014 add one, then run a scan"));

        let schedHtml = '<div class="section-head"><h2 class="panel-label">SCHEDULED SCANS</h2></div>';
        if (admin) {
            const opts = domains.map((d) => '<option value="' + escapeHtml(d.domain_name) + '">' + escapeHtml(d.domain_name) + "</option>").join("");
            schedHtml += '<form class="form-row" data-form="add-schedule" data-pid="' + id + '">' +
                '<select class="sched-domain">' + (opts || '<option value="">\u2014 no domains \u2014</option>') + "</select>" +
                '<input class="sched-interval" type="number" min="5" value="60" title="interval minutes"> <span class="mono-note">min</span>' +
                '<button class="btn btn-primary" type="submit">+ SCHEDULE</button></form>';
        }
        schedHtml += '<div id="sched-list">' + emptyLine("loading\u2026") + "</div>";

        view.innerHTML =
            '<div class="section-head"><h2 class="view-title">' + escapeHtml(p.name || "PROJECT " + id) + "</h2>" +
            (admin && p.id !== 1
                ? '<button class="btn btn-danger" data-action="del-project" data-id="' + id + '" data-name="' + escapeHtml(p.name || "") + '">DELETE PROJECT</button>'
                : "") +
            "</div>" +
            '<div class="section-head"><h2 class="panel-label">RECENT SCANS</h2></div>' +
            '<div id="project-scans-body">' + emptyLine("loading\u2026") + "</div>" +
            counts + domainsHtml + schedHtml;
        bindViewForms(view);
        loadProjectScansBody(id);
        loadSchedulesFor(id);
    }

    async function loadProjectScansBody(pid) {
        const el = $("project-scans-body");
        if (!el) return;
        try {
            const data = await apiFetch("/scans?project_id=" + encodeURIComponent(pid) + "&limit=20");
            const list = data.scans || [];
            if (!list.length) { el.innerHTML = emptyLine("no scans for this project yet"); return; }
            const rows = list.map((s) =>
                '<tr class="clickable" data-action="open-scan" data-id="' + s.scan_id + '">' +
                "<td>" + statusToken(s.status) + "</td>" +
                "<td>" + escapeHtml(s.domain_name || "\u2014") + "</td>" +
                '<td class="num">' + escNum(s.buckets_found) + "</td>" +
                '<td class="num">' + escNum(s.findings_count) + "</td>" +
                "<td>" + escapeHtml(fmtDate(s.started_at)) + "</td></tr>").join("");
            el.innerHTML = tableHtml(["STATUS", "DOMAIN", "#BUCKETS", "#FINDINGS", "STARTED"], rows);
        } catch (err) {
            el.innerHTML = emptyLine(err.message, true);
        }
    }

    async function submitAddDomain(form) {
        const pid = form.getAttribute("data-pid");
        const input = form.querySelector(".ad-domain");
        const domainName = (input.value || "").trim();
        if (!domainName) return;
        try {
            await apiFetch("/projects/" + pid + "/domains", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ domain_name: domainName }),
            });
            showFlash("domain added: " + domainName);
            loadProjectDetail(pid);
        } catch (err) {
            showFlash(err.message, true);
        }
    }

    async function deleteDomain(id) {
        try {
            await apiFetch("/domains/" + id, { method: "DELETE" });
            showFlash("domain deleted");
            route();
        } catch (err) {
            showFlash(err.message, true);
        }
    }

    async function runProjectScan(pid) {
        let data;
        try {
            data = await apiFetch("/projects/" + pid);
        } catch (err) {
            showFlash(err.message, true);
            return;
        }
        const domainNames = (data.domains || []).map((d) => d.domain_name);
        if (!domainNames.length) {
            showFlash("no domains in project \u2014 add one first", true);
            return;
        }
        try {
            await apiFetch("/scans/run-scan", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ domains: domainNames, project_id: Number(pid) }),
            });
            showFlash("scan accepted for " + domainNames.length + " domain(s)");
            loadProjectDetail(pid);
        } catch (err) {
            showFlash(err.message, true);
        }
    }

    /* ---------- schedules (per project + global queue) ---------- */

    async function submitSchedule(form) {
        const domain = form.querySelector(".sched-domain").value;
        const interval = parseInt(form.querySelector(".sched-interval").value, 10) || 60;
        if (!domain) return;
        try {
            await apiFetch("/queues/schedule", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ domain: domain, interval_minutes: interval }),
            });
            showFlash("scheduled: " + domain + " every " + interval + "m");
            route();
        } catch (err) {
            showFlash(err.message, true);
        }
    }

    async function loadSchedulesFor(pid) {
        try {
            const data = await apiFetch("/queues/schedule");
            const list = data.scheduled || [];
            const domainNames = new Set();
            try {
                const pd = await apiFetch("/projects/" + pid);
                (pd.domains || []).forEach((d) => domainNames.add(d.domain_name));
            } catch (_) {}
            const mine = list.filter((s) => domainNames.has(s.domain));
            const el = $("sched-list");
            if (!el) return;
            if (!mine.length) { el.innerHTML = emptyLine("no scheduled scans for this project"); return; }
            el.innerHTML = tableHtml(["DOMAIN", "INTERVAL", "CREATED", ""], mine.map((s) =>
                "<tr><td>" + escapeHtml(s.domain) + "</td>" +
                '<td class="num">' + escNum(s.interval_minutes) + " min</td>" +
                "<td>" + escapeHtml(fmtDate(s.created_at)) + "</td>" +
                '<td><button class="btn btn-danger btn-sm" data-action="del-schedule" data-id="' + s.id + '">DEL</button></td></tr>'
            ).join(""));
        } catch (err) {
            const el = $("sched-list");
            if (el) el.innerHTML = emptyLine(err.message, true);
        }
    }

    async function deleteSchedule(id) {
        try {
            await apiFetch("/queues/schedule/" + id, { method: "DELETE" });
            showFlash("schedule removed");
            route();
        } catch (err) {
            showFlash(err.message, true);
        }
    }

    /* ---------- views: scans ---------- */

    function renderScansShell() {
        const view = $("view-scans");
        view.hidden = false;
        view.innerHTML =
            '<div class="section-head"><h2 class="panel-label">SCAN QUEUE</h2></div>' +
            '<form id="run-scan-form" class="form-row" data-form="run-scan">' +
            '<input id="rs-domains" placeholder="domains, comma separated" required>' +
            '<button class="btn btn-primary" type="submit">RUN SCAN</button></form>' +
            '<div class="form-row">' +
            '<select id="scan-f-project"><option value="">project: all</option></select>' +
            '<select id="scan-f-status"><option value="">status: all</option>' +
            '<option value="pending">pending</option><option value="running">running</option>' +
            '<option value="completed">completed</option><option value="failed">failed</option></select>' +
            "</div>" +
            '<div id="scans-body">' + emptyLine("loading\u2026") + "</div>";
        bindViewForms(view);
        $("scan-f-status").addEventListener("change", loadScanTable);
        apiFetch("/projects").then((projs) => {
            const sel = $("scan-f-project");
            if (!sel) return;
            (projs.projects || []).forEach((p) => {
                const opt = document.createElement("option");
                opt.value = String(p.id);
                opt.textContent = p.name;
                sel.appendChild(opt);
            });
            sel.addEventListener("change", loadScanTable);
        }).catch(() => {});
    }

    async function loadScans() {
        setTitle("SCANS");
        setActiveNav("scans");
        renderScansShell();
        await loadScanTable();
    }

    async function loadScanTable() {
        const params = new URLSearchParams();
        params.set("limit", "100");
        const fproj = $("scan-f-project");
        const fstat = $("scan-f-status");
        if (fproj && fproj.value) params.set("project_id", fproj.value);
        if (fstat && fstat.value) params.set("status", fstat.value);
        try {
            const data = await apiFetch("/scans?" + params.toString());
            const list = data.scans || [];
            if (!list.length) { renderInto("scans-body", emptyLine("no scans yet")); return; }
            const rows = list.map((s) => {
                const del = isAdmin()
                    ? ' <button class="btn btn-danger btn-sm" data-action="del-scan" data-id="' + s.scan_id + '">DEL</button>'
                    : "";
                return '<tr class="clickable" data-action="open-scan" data-id="' + s.scan_id + '">' +
                    "<td>" + statusToken(s.status) + "</td>" +
                    "<td>" + escapeHtml(s.domain_name || "\u2014") + "</td>" +
                    "<td>" + escapeHtml(s.project_name || "\u2014") + "</td>" +
                    '<td class="num">' + escNum(s.buckets_found) + "</td>" +
                    '<td class="num">' + escNum(s.findings_count) + "</td>" +
                    "<td>" + escapeHtml(fmtDate(s.started_at)) + "</td>" +
                    "<td>" + del + "</td></tr>";
            }).join("");
            renderInto("scans-body", tableHtml(["STATUS", "DOMAIN", "PROJECT", "#BUCKETS", "#FINDINGS", "STARTED", ""], rows));
        } catch (err) {
            renderInto("scans-body", emptyLine(err.message, true));
        }
    }

    async function submitRunScan(e) {
        e.preventDefault();
        const raw = ($("rs-domains").value || "");
        const domains = raw.split(",").map((d) => d.trim()).filter(Boolean);
        if (!domains.length) return;
        try {
            await apiFetch("/scans/run-scan", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ domains: domains }),
            });
            showFlash("scan accepted: " + domains.join(", "));
            $("rs-domains").value = "";
            loadScanTable();
        } catch (err) {
            showFlash(err.message, true);
        }
    }

    async function deleteScan(id) {
        try {
            await apiFetch("/scans/" + id, { method: "DELETE" });
            showFlash("scan " + id + " deleted");
            route();
        } catch (err) {
            showFlash(err.message, true);
        }
    }

    /* ---------- views: scan detail ---------- */

    async function loadScanDetail(id) {
        setTitle("SCAN " + id);
        setActiveNav("scans");
        stopScanAuto();
        const view = $("view-scan");
        view.hidden = false;
        view.innerHTML = emptyLine("loading\u2026");
        let data;
        try {
            data = await apiFetch("/scans/" + id);
        } catch (err) {
            view.innerHTML = emptyLine(err.message, true);
            return;
        }
        const s = data.scan || {};
        const findings = data.findings || [];
        const buckets = data.buckets || [];

        const bucketRows = buckets.length
            ? buckets.map((b) =>
                "<tr><td>" + escapeHtml(b.name) + "</td>" +
                '<td class="num">' + escNum(b.objects_count) + "</td></tr>").join("")
            : "";
        const bucketsHtml = buckets.length
            ? '<div class="section-head"><h2 class="panel-label">BUCKETS (' + buckets.length + ')</h2></div>' +
              tableHtml(["BUCKET", "#OBJECTS"], bucketRows)
            : '<div class="section-head"><h2 class="panel-label">BUCKETS</h2></div>' + emptyLine("no buckets found");

        const findingRows = findings.length
            ? findings.map((f) =>
                "<tr><td>" + sevToken(f.severity) + "</td>" +
                "<td>" + escapeHtml(f.rule || "\u2014") + "</td>" +
                "<td>" + escapeHtml(f.bucket || "\u2014") + "</td>" +
                '<td class="snippet">' + escapeHtml(f.object || "\u2014") + "</td>" +
                '<td class="num">' + (f.line == null ? "\u2014" : escNum(f.line)) + "</td>" +
                '<td class="snippet">' + escapeHtml(f.match || "") + "</td></tr>").join("")
            : "";
        const findingsHtml = findings.length
            ? '<div class="section-head"><h2 class="panel-label">FINDINGS (' + findings.length + ')</h2></div>' +
              tableHtml(["SEVERITY", "RULE", "BUCKET", "OBJECT", "#LINE", "MATCH"], findingRows)
            : '<div class="section-head"><h2 class="panel-label">FINDINGS</h2></div>' + emptyLine("no findings");

        view.innerHTML =
            '<div class="section-head"><h2 class="view-title">' + statusToken(s.status) +
            ' <span class="mono-note">SCAN #' + escapeHtml(String(s.scan_id != null ? s.scan_id : id)) + "</span></h2>" +
            '<button class="btn btn-ghost" data-action="back-scans">&larr; BACK</button></div>' +
            '<div class="count-strip">' +
            '<div class="count-item"><span class="micro">DOMAIN</span><span class="count-value">' + escapeHtml(s.domain_name || "\u2014") + "</span></div>" +
            '<div class="count-item"><span class="micro">PROJECT</span><span class="count-value">' + escapeHtml(s.project_name || "\u2014") + "</span></div>" +
            '<div class="count-item"><span class="micro">STARTED</span><span class="count-value">' + escapeHtml(fmtDate(s.started_at)) + "</span></div>" +
            '<div class="count-item"><span class="micro">FINISHED</span><span class="count-value">' + escapeHtml(fmtDate(s.finished_at)) + "</span></div>" +
            '<div class="count-item"><span class="micro">BUCKETS</span><span class="count-value">' + escNum(s.buckets_found) + "</span></div>" +
            '<div class="count-item"><span class="micro">FINDINGS</span><span class="count-value">' + escNum(s.findings_count) + "</span></div>" +
            "</div>" +
            bucketsHtml + findingsHtml;

        const st = String(s.status || "").toLowerCase();
        if (st === "pending" || st === "running") {
            scanAutoTimer = setTimeout(() => {
                if (location.hash === "#/scan/" + id) loadScanDetail(id);
            }, 4000);
        }
    }

    /* ---------- views: findings ---------- */

    async function loadFindings() {
        setTitle("FINDINGS");
        setActiveNav("findings");
        const view = $("view-findings");
        view.hidden = false;
        view.innerHTML =
            '<div class="section-head"><h2 class="panel-label">FINDINGS</h2></div>' +
            '<div class="form-row"><select id="sev-filter">' +
            '<option value="">severity: all</option>' +
            '<option value="critical">critical</option><option value="high">high</option>' +
            '<option value="medium">medium</option><option value="low">low</option>' +
            "</select>" +
            '<span class="mono-note">// keys: bucket, rule \u2014 redaction server-side</span></div>' +
            '<div id="findings-body">' + emptyLine("loading\u2026") + "</div>";
        $("sev-filter").addEventListener("change", loadFindingsTable);
        await loadFindingsTable();
    }

    async function loadFindingsTable() {
        const params = new URLSearchParams();
        params.set("limit", "100");
        const sel = $("sev-filter");
        if (sel && sel.value) params.set("severity", sel.value);
        try {
            const data = await apiFetch("/findings?" + params.toString());
            const list = data.findings || [];
            if (!list.length) { renderInto("findings-body", emptyLine("no findings")); return; }
            const rows = list.map((f) =>
                "<tr><td>" + sevToken(f.severity) + "</td>" +
                "<td>" + escapeHtml(f.rule || "\u2014") + "</td>" +
                "<td>" + escapeHtml(f.bucket || "\u2014") + "</td>" +
                '<td class="snippet">' + escapeHtml(f.object || "\u2014") + "</td>" +
                '<td class="num">' + (f.line == null ? "\u2014" : escNum(f.line)) + "</td>" +
                '<td class="snippet">' + escapeHtml(f.match || "") + "</td></tr>").join("");
            renderInto("findings-body", tableHtml(["SEVERITY", "RULE", "BUCKET", "OBJECT", "#LINE", "MATCH"], rows));
        } catch (err) {
            renderInto("findings-body", emptyLine(err.message, true));
        }
    }

    /* ---------- views: users ---------- */

    function renderUsersShell() {
        const view = $("view-users");
        view.hidden = false;
        view.innerHTML =
            '<div class="section-head"><h2 class="panel-label">USERS</h2></div>' +
            '<form class="form-row" data-form="create-user">' +
            '<input class="cu-username" placeholder="username" required>' +
            '<input class="cu-password" type="password" placeholder="password" required>' +
            '<select class="cu-role"><option value="viewer">viewer</option><option value="admin">admin</option></select>' +
            '<button class="btn btn-primary" type="submit">+ CREATE USER</button></form>' +
            '<div id="users-body">' + emptyLine("loading\u2026") + "</div>";
        bindViewForms(view);
    }

    async function loadUsers() {
        setTitle("USERS");
        setActiveNav("users");
        renderUsersShell();
        await loadUsersTable();
    }

    async function loadUsersTable() {
        try {
            const data = await apiFetch("/users");
            const list = data.users || [];
            const me = usernameFromToken(getToken());
            if (!list.length) { renderInto("users-body", emptyLine("no users")); return; }
            const rows = list.map((u) => {
                const isSelf = u.username === me;
                const roleSel = '<select data-role-for="' + u.id + '"' + (isSelf ? " disabled" : "") + ">" +
                    '<option value="viewer"' + (u.role === "viewer" ? " selected" : "") + '>viewer</option>' +
                    '<option value="admin"' + (u.role === "admin" ? " selected" : "") + '>admin</option></select>';
                const del = !isSelf
                    ? '<button class="btn btn-danger btn-sm" data-action="del-user" data-id="' + u.id + '">DEL</button>'
                    : '<span class="mono-note">(you)</span>';
                return "<tr><td>" + escapeHtml(u.username) + "</td><td>" + roleSel + "</td><td>" + del + "</td></tr>";
            }).join("");
            renderInto("users-body", tableHtml(["USERNAME", "ROLE", ""], rows));
        } catch (err) {
            renderInto("users-body", emptyLine(err.message, true));
        }
    }

    async function submitCreateUser(form) {
        const username = (form.querySelector(".cu-username").value || "").trim();
        const password = form.querySelector(".cu-password").value || "";
        const role = form.querySelector(".cu-role").value || "viewer";
        if (!username || !password) return;
        try {
            await apiFetch("/users", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ username: username, password: password, role: role }),
            });
            showFlash("user created: " + username);
            form.querySelector(".cu-username").value = "";
            form.querySelector(".cu-password").value = "";
            await loadUsersTable();
        } catch (err) {
            showFlash(err.message, true);
        }
    }

    async function setUserRole(id, role) {
        try {
            await apiFetch("/users/" + id, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ role: role }),
            });
            showFlash("role updated: " + role);
        } catch (err) {
            showFlash(err.message, true);
            loadUsersTable();
        }
    }

    async function deleteUser(id) {
        try {
            await apiFetch("/users/" + id, { method: "DELETE" });
            showFlash("user deleted");
            await loadUsersTable();
        } catch (err) {
            showFlash(err.message, true);
        }
    }
    /* ---------- forms binding ---------- */

    function bindViewForms(root) {
        root.querySelectorAll("form[data-form]").forEach((form) => {
            if (form.getAttribute("data-bound") === "1") return;
            form.setAttribute("data-bound", "1");
            form.addEventListener("submit", (e) => {
                e.preventDefault();
                const kind = form.getAttribute("data-form");
                if (kind === "project-create") submitProjectCreate();
                else if (kind === "add-domain") submitAddDomain(form);
                else if (kind === "add-schedule") submitSchedule(form);
                else if (kind === "run-scan") submitRunScan(e);
                else if (kind === "create-user") submitCreateUser(form);
            });
        });
    }

    /* ---------- delegated clicks ---------- */

    async function handleDelegatedClick(e) {
        const target = e.target.closest("[data-action]");
        if (!target) return;
        const action = target.getAttribute("data-action");
        const id = target.getAttribute("data-id");

        if (action === "open-project") { location.hash = "#/project/" + id; return; }
        if (action === "open-scan") { location.hash = "#/scan/" + id; return; }
        if (action === "back-scans") { location.hash = "#/scans"; return; }

        if (action === "logout") { doLogout(); return; }
        if (action === "toggle-register") { toggleRegister(); return; }

        if (action === "run-project-scan") { runProjectScan(id); return; }


        const confirmActions = {
            "del-project": () => deleteProject(id),
            "del-domain": () => deleteDomain(id),
            "del-scan": () => deleteScan(id),
            "del-schedule": () => deleteSchedule(id),
            "del-user": () => deleteUser(id),
        };
        if (confirmActions[action]) {
            e.stopPropagation();
            armButton(target, confirmActions[action]);
        }
    }

    /* ---------- router ---------- */

    function route() {
        stopScanAuto();
        const hash = location.hash || "";
        ["view-home", "view-projects", "view-project", "view-scans", "view-scan", "view-findings", "view-users"].forEach((id) => { $(id).hidden = true; });

        const logged = !!getToken();
        if (!logged || hash.startsWith("#/login")) { showLogin(); stopPolling(); return; }
        enterShell();

        let m;
        if (hash.startsWith("#/home")) { loadHome(); return; }
        if ((m = hash.match(/^#\/project\/(\d+)$/))) { loadProjectDetail(m[1]); return; }
        if ((m = hash.match(/^#\/scan\/(\d+)$/))) { loadScanDetail(m[1]); return; }
        if (hash.startsWith("#/scans")) { loadScans(); return; }
        if (hash.startsWith("#/findings")) { loadFindings(); return; }
        if (hash.startsWith("#/users")) {
            if (isAdmin()) loadUsers();
            else { location.hash = "#/projects"; }
            return;
        }
        loadProjects();
    }

    function startPollingForCurrentView() {
        const hash = location.hash || "";
        const map = {
            "#/home": loadHomeTable,
            "#/projects": loadProjectsTable,
            "#/scans": loadScanTable,
            "#/findings": loadFindingsTable,
            "#/users": loadUsersTable,
        };
        if (map[hash]) armPolling(map[hash]);
        else stopPolling();
    }

    /* ---------- init ---------- */

    function init() {
        document.body.addEventListener("click", handleDelegatedClick);
        document.body.addEventListener("change", handleDelegatedChange);
        $("login-form").addEventListener("submit", handleLoginSubmit);
        window.addEventListener("hashchange", () => { route(); startPollingForCurrentView(); });

        if (getToken()) {
            location.hash = location.hash || "#/home";
        } else {
            location.hash = "#/login";
        }
        route();
        startPollingForCurrentView();
    }

    function handleDelegatedChange(e) {
        const sel = e.target.closest("[data-role-for]");
        if (!sel) return;
        setUserRole(sel.getAttribute("data-role-for"), sel.value);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
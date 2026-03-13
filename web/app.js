/* ═══════════════════════════════════════════════════════════════════════════
   Auto Studio Web — App Logic
   ═══════════════════════════════════════════════════════════════════════════ */

const S = {
    config: {},
    styles: { content: [], video: [] },
    segments: [],
    videoPrompts: [],
    script: "",
    scriptCommitted: "",
    scriptDraft: "",
    scriptDirty: false,
    scriptOriginal: "",
    scriptTranslated: "",
    scriptViewMode: "original",
    projectId: "",
    currentTab: "script",
    running: false,
    paused: false,
    // Splitter state
    splSegments: [],
    // Queue state
    queue: [],
    queueRunning: false,
    queueCurrent: 0,
    queueTotal: 0,
    queueCurrentTopic: "",
    queueEditIdx: -1,
    p2p: {
        shares: [],
        pickedFiles: [],
        selectedToken: "",
        editToken: "",
        lastToken: "",
    },
    // Modal translation state
    modalType: "",
    modalIndex: -1,
    modalOriginalText: "",
    modalDraftText: "",
    modalTranslatedText: "",
    modalViewMode: "original",
    modalDirty: false,
    styleOriginalPrompt: "",
    styleTranslatedPrompt: "",
    styleViewMode: "original",
    guideContent: "",
    guideUpdatedAt: "",
    guideLoaded: false,
    guideLoading: false,
    confirmState: null,
    scriptSyncLock: false,
};
const SUPPORTED_LANGUAGES = ["English", "Tiếng Việt", "日本語", "한국어"];
const DEFAULT_LANGUAGE = "English";
const TRANSLATE_ONLY_MODEL = "gpt-5-codex-mini";
const FAST_TRANSLATE_MODEL = TRANSLATE_ONLY_MODEL;
let evtSource = null;
let sseReconnectTimer = null;

function normalizeLanguage(value) {
    return SUPPORTED_LANGUAGES.includes(value) ? value : DEFAULT_LANGUAGE;
}

function getUserSelectableModels() {
    const models = Array.isArray(S.config.available_models) ? S.config.available_models.slice() : [];
    const filtered = models.filter(m => String(m || "").trim() && m !== TRANSLATE_ONLY_MODEL);
    return filtered.length ? filtered : models;
}

function setSelectValueOrFallback(el, value) {
    if (!el) return;
    if (value && Array.from(el.options).some(o => o.value === value)) {
        el.value = value;
        return;
    }
    if (el.options.length) el.selectedIndex = 0;
}

function normalizeScriptLines(text) {
    return String(text || "")
        .replace(/\r/g, "")
        .split("\n")
        .map(x => x.trim())
        .filter(Boolean);
}

function countWords(text) {
    const t = String(text || "").trim();
    if (!t) return 0;
    return t.split(/\s+/).length;
}

function estimateSegmentDuration(words) {
    const wpm = Number(S.config.wpm) || 130;
    if (!words || wpm <= 0) return 0;
    return Math.max(0, +(words * 60 / wpm).toFixed(2));
}

function computeInRange(duration) {
    const target = Number(S.config.target_seconds) || 8.0;
    const tol = target * 0.2;
    return duration >= (target - tol) && duration <= (target + tol);
}

function makeSegment(text, index) {
    const words = countWords(text);
    const duration = estimateSegmentDuration(words);
    return {
        index,
        text,
        words,
        duration,
        in_range: computeInRange(duration),
    };
}

function rebuildSegmentsFromScript(scriptText) {
    const lines = normalizeScriptLines(scriptText);
    return lines.map((line, i) => makeSegment(line, i + 1));
}

function normalizeText(text) {
    return String(text ?? "").replace(/\r/g, "");
}

function toNumberedLines(text) {
    const src = normalizeText(text);
    const lineCount = Math.max(1, src.split("\n").length);
    return Array.from({ length: lineCount }, (_, i) => String(i + 1)).join("\n");
}

function updateScriptLineNumbers() {
    const scriptEl = $("script-output");
    const lineEl = $("script-lines");
    if (!scriptEl || !lineEl) return;
    lineEl.textContent = toNumberedLines(scriptEl.value || "");
    lineEl.scrollTop = scriptEl.scrollTop;
}

function updateContentEditButtons() {
    const btnApply = $("btn-content-apply");
    const btnReset = $("btn-content-reset");
    if (!btnApply || !btnReset) return;
    const editable = S.scriptViewMode === "original";
    const hasProject = !!S.projectId;
    btnApply.disabled = !(S.scriptDirty && editable && hasProject);
    btnReset.disabled = !(S.scriptDirty && editable);
}

function updateScriptTranslateButton() {
    const btn = $("btn-script-translate");
    const scriptEl = $("script-output");
    if (!btn || !scriptEl) return;
    if (S.scriptViewMode === "translated") {
        btn.textContent = "Ngôn Ngữ Gốc";
        btn.title = "Hiển thị nội dung gốc trước khi dịch";
        scriptEl.readOnly = true;
    } else {
        btn.textContent = "🌐 Dịch VI";
        btn.title = "Dịch nội dung Content sang tiếng Việt";
        scriptEl.readOnly = false;
    }
    updateContentEditButtons();
}

function setScriptOutputValue(value) {
    const scriptEl = $("script-output");
    if (!scriptEl) return;
    S.scriptSyncLock = true;
    scriptEl.value = normalizeText(value || "");
    S.scriptSyncLock = false;
    updateScriptLineNumbers();
}

function setContentSnapshot(contentText) {
    const normalized = normalizeText(contentText || "");
    S.script = normalized;
    S.scriptOriginal = normalized;
    S.scriptCommitted = normalized;
    S.scriptDraft = normalized;
    S.scriptTranslated = "";
    S.scriptViewMode = "original";
    S.scriptDirty = false;
    setScriptOutputValue(normalized);
    updateScriptTranslateButton();
    updateContentEditButtons();
}

function refreshContentDirtyState() {
    S.scriptDirty = normalizeText(S.scriptDraft || "") !== normalizeText(S.scriptCommitted || "");
    updateContentEditButtons();
}

function syncScriptFromSegments() {
    const nextScript = S.segments.map(s => String(s?.text || "").trim()).filter(Boolean).join("\n");
    setContentSnapshot(nextScript);
}

function onScriptScroll() {
    const scriptEl = $("script-output");
    const lineEl = $("script-lines");
    if (!scriptEl || !lineEl) return;
    lineEl.scrollTop = scriptEl.scrollTop;
}

function onScriptInputChanged() {
    if (S.scriptSyncLock) return;
    if (S.scriptViewMode !== "original") return;
    const scriptEl = $("script-output");
    if (!scriptEl) return;
    S.scriptDraft = normalizeText(scriptEl.value || "");
    S.script = S.scriptDraft;
    S.scriptOriginal = S.scriptDraft;
    S.scriptTranslated = "";
    updateScriptLineNumbers();
    refreshContentDirtyState();
}

// ── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    loadConfig();
    loadStyles();
    loadVersion();
    connectSSE();
});

function onPagesLoaded() {
    reorderSidebarNav();
    setupNav();
    const scriptEl = $("script-output");
    if (scriptEl) {
        scriptEl.addEventListener("input", onScriptInputChanged);
        scriptEl.addEventListener("scroll", onScriptScroll);
        setContentSnapshot(scriptEl.value || "");
    }
    const modalText = $("modal-text");
    if (modalText) {
        modalText.addEventListener("input", onModalTextInput);
    }
    const stylePromptEl = $("sd-prompt");
    if (stylePromptEl) {
        stylePromptEl.addEventListener("input", onStylePromptInput);
    }
    initP2PDropzone();
    renderP2PPickedFiles();
    updateP2PComposeMode();
    updateScriptTranslateButton();
    updateScriptLineNumbers();
    updateModalControls();
    updateStyleTranslateButton();
    updateWriterTabActionsVisibility();
    updateContinueButtonVisibility();
    updateRegenerateSelectedVisibility();
    // Re-fill dropdown selectors after pages loaded
    fillModels();
    fillStyles();
}

function reorderSidebarNav() {
    const navPrompts = $("nav-prompts");
    const navP2P = $("nav-p2p");
    if (!navPrompts || !navP2P) return;
    if (navPrompts.parentElement !== navP2P.parentElement) return;
    navPrompts.insertAdjacentElement("afterend", navP2P);
}

// ═══════════════════════════════════════════════════════════════════════════
// Navigation
// ═══════════════════════════════════════════════════════════════════════════
function setupNav() {
    document.querySelectorAll(".nav-btn").forEach(btn => {
        btn.addEventListener("click", async () => {
            const p = btn.dataset.page;
            const writerPage = $("page-writer");
            const isLeavingWriter = writerPage && writerPage.classList.contains("active") && p !== "writer";
            if (isLeavingWriter) {
                const proceed = await ensureContentDraftResolvedBeforeLeave("page");
                if (!proceed) return;
            }
            document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            document.querySelectorAll(".page").forEach(pg => pg.classList.remove("active"));
            document.getElementById(`page-${p}`).classList.add("active");
            if (p === "projects") loadProjectList();
            if (p === "prompts") renderAllStyles();
            if (p === "queue") { loadQueue(); fillQueueSelects(); }
            if (p === "p2p") loadP2PShares();
            if (p === "guide") loadGuide(false);
        });
    });
}

function formatGuideUpdatedAt(raw) {
    if (!raw) return "-";
    const dt = new Date(raw);
    if (Number.isNaN(dt.getTime())) return raw;
    return dt.toLocaleString("vi-VN", { hour12: false });
}

function renderGuideInline(text) {
    let html = esc(text || "");
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
        const safe = String(href || "");
        const ok = safe.startsWith("http://") || safe.startsWith("https://") || safe.startsWith("/");
        const target = ok ? safe : "#";
        return `<a href="${target}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });
    return html;
}

function renderGuideMarkdown(markdown) {
    const src = normalizeText(markdown || "");
    if (!src.trim()) return '<div class="tbl-empty">Nội dung hướng dẫn đang trống</div>';

    const lines = src.split("\n");
    const out = [];
    let inCode = false;
    let codeLang = "";
    let codeBuffer = [];
    let listType = "";

    const closeList = () => {
        if (listType === "ul") out.push("</ul>");
        if (listType === "ol") out.push("</ol>");
        listType = "";
    };

    const flushCode = () => {
        const cls = codeLang ? ` class="lang-${esc(codeLang)}"` : "";
        out.push(`<pre><code${cls}>${esc(codeBuffer.join("\n"))}</code></pre>`);
        codeBuffer = [];
        codeLang = "";
    };

    for (const line of lines) {
        const raw = line || "";
        const trimmed = raw.trim();

        if (trimmed.startsWith("```")) {
            closeList();
            if (!inCode) {
                inCode = true;
                codeLang = trimmed.slice(3).trim();
                codeBuffer = [];
            } else {
                inCode = false;
                flushCode();
            }
            continue;
        }

        if (inCode) {
            codeBuffer.push(raw);
            continue;
        }

        if (!trimmed) {
            closeList();
            continue;
        }

        if (trimmed === "---" || trimmed === "***") {
            closeList();
            out.push("<hr>");
            continue;
        }

        const h3 = trimmed.match(/^###\s+(.+)$/);
        if (h3) {
            closeList();
            out.push(`<h3>${renderGuideInline(h3[1])}</h3>`);
            continue;
        }
        const h2 = trimmed.match(/^##\s+(.+)$/);
        if (h2) {
            closeList();
            out.push(`<h2>${renderGuideInline(h2[1])}</h2>`);
            continue;
        }
        const h1 = trimmed.match(/^#\s+(.+)$/);
        if (h1) {
            closeList();
            out.push(`<h1>${renderGuideInline(h1[1])}</h1>`);
            continue;
        }

        const quote = trimmed.match(/^>\s+(.+)$/);
        if (quote) {
            closeList();
            out.push(`<blockquote>${renderGuideInline(quote[1])}</blockquote>`);
            continue;
        }

        const ol = trimmed.match(/^\d+\.\s+(.+)$/);
        if (ol) {
            if (listType !== "ol") {
                closeList();
                out.push("<ol>");
                listType = "ol";
            }
            out.push(`<li>${renderGuideInline(ol[1])}</li>`);
            continue;
        }

        const ul = trimmed.match(/^[-*]\s+(.+)$/);
        if (ul) {
            if (listType !== "ul") {
                closeList();
                out.push("<ul>");
                listType = "ul";
            }
            out.push(`<li>${renderGuideInline(ul[1])}</li>`);
            continue;
        }

        closeList();
        out.push(`<p>${renderGuideInline(trimmed)}</p>`);
    }

    if (inCode) flushCode();
    closeList();
    return out.join("\n");
}

function renderGuideContent() {
    const metaEl = $("guide-meta");
    const bodyEl = $("guide-content");
    if (!metaEl || !bodyEl) return;
    metaEl.textContent = `Tệp: guild.md | Cập nhật: ${formatGuideUpdatedAt(S.guideUpdatedAt)}`;
    bodyEl.innerHTML = renderGuideMarkdown(S.guideContent);
}

async function loadGuide(force = false) {
    if (S.guideLoading) return;
    if (S.guideLoaded && !force) {
        renderGuideContent();
        return;
    }
    const metaEl = $("guide-meta");
    const bodyEl = $("guide-content");
    if (metaEl) metaEl.textContent = "Đang tải tài liệu hướng dẫn...";
    if (bodyEl) bodyEl.innerHTML = '<div class="tbl-empty">Đang tải...</div>';

    S.guideLoading = true;
    try {
        const r = await fetch("/api/guide");
        const d = await r.json();
        if (!r.ok || d.error) {
            const msg = d.error || `HTTP ${r.status}`;
            if (metaEl) metaEl.textContent = "Không thể tải tài liệu hướng dẫn";
            if (bodyEl) bodyEl.innerHTML = `<div class="tbl-empty">${esc(msg)}</div>`;
            log(`[guide] Không thể tải guild.md: ${msg}`, "err");
            return;
        }
        S.guideContent = String(d.content || "");
        S.guideUpdatedAt = d.updated_at || "";
        S.guideLoaded = true;
        renderGuideContent();
    } catch (e) {
        if (metaEl) metaEl.textContent = "Lỗi kết nối khi tải tài liệu hướng dẫn";
        if (bodyEl) bodyEl.innerHTML = `<div class="tbl-empty">${esc(e.message || String(e))}</div>`;
        log(`[guide] Lỗi khi tải guild.md: ${e.message || e}`, "err");
    } finally {
        S.guideLoading = false;
    }
}

function refreshGuide() {
    loadGuide(true);
}

// ═══════════════════════════════════════════════════════════════════════════
// Config
// ═══════════════════════════════════════════════════════════════════════════
async function loadVersion() {
    try {
        const r = await fetch("/api/version");
        const d = await r.json();
        const el = $("sidebar-version");
        if (el && d.version) el.textContent = `v${d.version} @hvbinh73`;
    } catch (_) { }
}

async function loadConfig() {
    try {
        const r = await fetch("/api/config");
        S.config = await r.json();
        fillModels();
        $("cfg-endpoint").value = S.config.endpoint || "";
        $("cfg-apikey").value = "";
        $("cfg-apikey").placeholder = S.config.has_api_key
            ? "Saved API key (enter to replace)"
            : "API key...";
        // Output dir
        loadOutputDir();
        loadP2PDownloadDir();
    } catch (e) { log(`[config] Không thể tải cấu hình ứng dụng: ${e.message || e}`, "err"); }
}

function fillModels() {
    const models = getUserSelectableModels();
    ["sel-model", "sel-model-video", "sel-remix-model", "sel-remix-model-analyze", "sel-remix-model-video"].forEach(id => {
        const el = $(id);
        if (!el) return;
        el.innerHTML = models.map(m => `<option value="${m}">${m}</option>`).join("")
            || '<option value="">— chưa có model —</option>';
    });
    setSelectValueOrFallback($("sel-model"), S.config.model);
    setSelectValueOrFallback($("sel-model-video"), S.config.model_video);
    setSelectValueOrFallback($("sel-remix-model"), S.config.model);
    setSelectValueOrFallback($("sel-remix-model-analyze"), S.config.model);
    setSelectValueOrFallback($("sel-remix-model-video"), S.config.model_video);
}

async function saveSettings() {
    const ep = $("cfg-endpoint").value.trim();
    const key = $("cfg-apikey").value.trim();
    const st = $("settings-status");
    const ml = $("model-list");

    if (!ep) { st.textContent = "Vui lòng nhập endpoint"; st.className = "status-msg err"; return; }

    st.textContent = "Đang kết nối..."; st.className = "status-msg";
    ml.innerHTML = '<div class="muted">Checking models...</div>';

    const body = { endpoint: ep };
    if (key) body.api_key = key;
    await fetch("/api/config", { method: "POST", headers: CT_JSON, body: JSON.stringify(body) });

    try {
        const r = await fetch("/api/models");
        const d = await r.json();
        if (d.error) { st.textContent = d.error; st.className = "status-msg err"; return; }

        // Keep full model list selectable; readiness is informational.
        S.config.available_models = Array.isArray(d.models) ? d.models : [];
        fillModels();

        ml.innerHTML = d.models.map(m => {
            const ok = d.ready.includes(m);
            return `<div class="model-item"><span class="mi-st">${ok ? "✔" : "✖"}</span><span class="mi-name">${m}</span><span class="muted">${ok ? "ready" : "unavailable"}</span></div>`;
        }).join("");

        st.textContent = `Connected — ${d.ready.length}/${d.models.length} models ready`;
        st.className = "status-msg ok";
        await loadConfig();
    } catch (e) { st.textContent = "Connection failed"; st.className = "status-msg err"; }
}

function toggleApiKeyVisibility() {
    const el = $("cfg-apikey");
    el.type = el.type === "password" ? "text" : "password";
}

// ═══════════════════════════════════════════════════════════════════════════
// Styles
// ═══════════════════════════════════════════════════════════════════════════
async function loadStyles() {
    try {
        const r = await fetch("/api/styles");
        S.styles = await r.json();
        fillStyles();
    } catch (e) { log(`[styles] Không thể tải danh sách Content/Video Style: ${e.message || e}`, "err"); }
}

function fillStyles() {
    const contentOpts = S.styles.content.map(s => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join("");
    const videoOpts = S.styles.video.map(s => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join("");
    const ss = $("sel-style"); if (ss) ss.innerHTML = contentOpts;
    const sv = $("sel-vstyle"); if (sv) sv.innerHTML = videoOpts;
    const rs = $("sel-remix-style"); if (rs) rs.innerHTML = contentOpts;
    const rv = $("sel-remix-vstyle"); if (rv) rv.innerHTML = videoOpts;
}

// ═══════════════════════════════════════════════════════════════════════════
// SSE
// ═══════════════════════════════════════════════════════════════════════════
function connectSSE() {
    if (evtSource) { evtSource.close(); evtSource = null; }
    evtSource = new EventSource("/api/events");

    evtSource.onopen = () => {
        if (sseReconnectTimer) {
            clearTimeout(sseReconnectTimer);
            sseReconnectTimer = null;
        }
        const statusDot = document.querySelector(".dot");
        const connText = $("conn-text");
        if (statusDot) statusDot.className = "dot ok";
        if (connText) connText.textContent = "Connected";
    };
    evtSource.onmessage = (e) => {
        try {
            const d = JSON.parse(e.data);
            if (d.type === "log") log(d.message, logCls(d.message), d.time);
            else if (d.type === "state") onState(d);
            else if (d.type === "script_chunk") onScriptChunk(d.chunk, d.source);
            else if (d.type === "queue_state") onQueueState(d);
        } catch (_) { }
    };
    evtSource.onerror = () => {
        const statusDot = document.querySelector(".dot");
        const connText = $("conn-text");
        if (statusDot) statusDot.className = "dot err";
        if (connText) connText.textContent = "Disconnected";
        if (evtSource) {
            evtSource.close();
            evtSource = null;
        }
        if (!sseReconnectTimer) {
            sseReconnectTimer = setTimeout(() => {
                sseReconnectTimer = null;
                connectSSE();
            }, 3000);
        }
    };
}

function logCls(m) {
    const text = String(m || "");
    const lower = text.toLowerCase();
    if (text.includes("✔") || text.includes("✓") || lower.includes("[ok]")) return "ok";
    if (
        text.includes("✖") || text.includes("✗")
        || lower.includes("[fail]")
        || lower.includes("error")
        || lower.includes("timeout")
    ) return "err";
    if (text.includes("⚡") || text.includes("⏳") || text.includes("⚠") || lower.includes("warn")) return "warn";
    return "";
}

function onScriptChunk(chunk, source) {
    const isRemix = source === "remix";
    if (isRemix) {
        // Route to Remix Content tab
        const el = $("remix-script-output");
        if (el) {
            el.value = (el.value || "") + (chunk || "");
            el.scrollTop = el.scrollHeight;
            // Update line numbers
            const lineEl = $("remix-script-lines");
            if (lineEl) lineEl.textContent = toNumberedLines(el.value);
        }
        switchRemixTab("remix-script");
        return;
    }
    // Default: Writer page
    const el = $("script-output");
    if (S.scriptViewMode !== "original") {
        S.scriptViewMode = "original";
        S.scriptTranslated = "";
        updateScriptTranslateButton();
        setScriptOutputValue(S.scriptDraft || S.scriptCommitted || S.scriptOriginal || S.script || "");
    }
    const next = normalizeText((el.value || "") + (chunk || ""));
    setScriptOutputValue(next);
    S.script = next;
    S.scriptOriginal = next;
    S.scriptCommitted = next;
    S.scriptDraft = next;
    S.scriptTranslated = "";
    S.scriptDirty = false;
    updateContentEditButtons();
    el.scrollTop = el.scrollHeight;
}

function onState(d) {
    S.running = d.running;
    S.paused = d.paused;
    if (d.project_id) S.projectId = d.project_id;

    const isDone = d.step === "done" || d.step === "error" || d.step === "stopped";
    const isActive = d.running && !isDone;
    const isRemix = d.source === "remix";

    if (isRemix) {
        // Route to Remix page UI
        const btnStart = $("btn-remix-start");
        const btnPause = $("btn-remix-pause");
        const btnStop = $("btn-remix-stop");
        if (btnStart) btnStart.disabled = isActive;
        if (btnPause) { btnPause.disabled = !isActive; btnPause.textContent = d.paused ? "▶ Tiếp Tục" : "⏸ Tạm Dừng"; }
        if (btnStop) { btnStop.disabled = !isActive || !d.paused; }

        const pa = $("remix-progress-area");
        if (pa && (d.running || isDone)) {
            pa.classList.remove("hidden");
            const pct = d.total > 0 ? (d.progress / d.total * 100) : 0;
            const pfill = $("remix-pfill"); if (pfill) pfill.style.width = pct + "%";
            const names = { extract: "Trích xuất...", count: "Đếm ký tự...", analyze: "Phân tích...", rewrite: "Viết lại...", split: "Tách đoạn...", video: "Tạo video prompt...", done: "✓ Hoàn thành!", error: "✗ Lỗi", stopped: "⏹ Đã dừng", cancelled: "⏹ Đã huỷ" };
            const ptext = $("remix-ptext"); if (ptext) ptext.textContent = d.paused ? "⏸ Đã tạm dừng" : (names[d.step] || d.step);
        }

        // YouTube info → populate Content Gốc tab
        if (d.youtube_info) {
            const yi = d.youtube_info;
            _ytData = Object.assign({ok: true}, yi);
            const emptyEl = $("remix-original-empty");
            const contentEl = $("remix-original-content");
            if (emptyEl) emptyEl.classList.add("hidden");
            if (contentEl) contentEl.classList.remove("hidden");

            const titleEl = $("remix-og-title"); if (titleEl) titleEl.textContent = yi.title || "";
            const dur = yi.duration ? Math.floor(yi.duration / 60) + ":" + String(yi.duration % 60).padStart(2, "0") : "?";
            const dateEl = $("remix-og-date"); if (dateEl) dateEl.textContent = _formatYTDate(yi.upload_date);
            const viewsEl = $("remix-og-views"); if (viewsEl) viewsEl.textContent = (yi.view_count || 0).toLocaleString() + " views";
            const chanEl = $("remix-og-channel"); if (chanEl) chanEl.textContent = yi.channel || "—";
            const durEl = $("remix-og-duration"); if (durEl) durEl.textContent = dur;
            const tagsEl = $("remix-og-tags");
            if (tagsEl) { const tags = Array.isArray(yi.tags) ? yi.tags : []; tagsEl.textContent = tags.length ? tags.join(", ") : "Không có từ khoá"; }
            const descEl = $("remix-og-desc"); if (descEl) descEl.textContent = yi.description || "";
            const subEl = $("remix-og-subtitles"); if (subEl) subEl.textContent = yi.subtitles_text || "";
        }

        // Analysis
        if (d.analysis) {
            const el = $("remix-og-analysis");
            if (el) el.textContent = d.analysis;
        }

        // Script (full state, not chunk)
        if (Object.prototype.hasOwnProperty.call(d, "script") && d.script && d.step !== "rewrite") {
            const el = $("remix-script-output");
            if (el) {
                el.value = d.script;
                const lineEl = $("remix-script-lines");
                if (lineEl) lineEl.textContent = toNumberedLines(d.script);
            }
        }

        // Segments & prompts for Remix video tab
        if (Array.isArray(d.segments) && d.segments.length > 0) {
            const container = $("remix-tbl-body");
            if (container) {
                const prompts = Array.isArray(d.video_prompts) ? d.video_prompts : [];
                container.innerHTML = d.segments.map((seg, i) => {
                    const prompt = prompts[i] || "";
                    return `<div class="tr"><div class="td c-chk"><input type="checkbox"></div><div class="td c-idx">${i + 1}</div><div class="td c-text">${esc(seg.text || "")}</div><div class="td c-text">${esc(prompt)}</div></div>`;
                }).join("");
            }
        }
        return;
    }

    // Default: Writer page
    autoSwitchTabForStep(d.step, isActive);

    $("btn-start").disabled = isActive;
    $("btn-pause").disabled = !isActive;
    $("btn-stop").disabled = !isActive || !d.paused;
    $("btn-pause").textContent = d.paused ? "▶ Tiếp Tục" : "⏸ Tạm Dừng";

    // Disable step buttons while running
    document.querySelectorAll(".step-actions .btn").forEach(b => b.disabled = isActive);

    // Progress
    const pa = $("progress-area");
    if (d.running || isDone) {
        pa.classList.remove("hidden");
        const pct = d.total > 0 ? (d.progress / d.total * 100) : 0;
        $("pfill").style.width = pct + "%";
        const names = { write: "Viết nội dung...", split: "Tách đoạn...", video: "Tạo video prompt...", continue_prompts: "Tạo video prompt thiếu...", done: "✓ Hoàn thành!", error: "✗ Lỗi", stopped: "⏹ Đã dừng" };
        $("ptext").textContent = d.paused ? "⏸ Đã tạm dừng" : (names[d.step] || d.step);
    }

    // Script (full state update, not chunk)
    if (Object.prototype.hasOwnProperty.call(d, "script") && d.step !== "write") {
        setContentSnapshot(d.script || "");
    }

    // Segments & prompts
    if (Array.isArray(d.segments)) S.segments = d.segments;
    if (Array.isArray(d.video_prompts)) S.videoPrompts = d.video_prompts;
    if (S.segments.length > 0) {
        renderTable();
    } else {
        $("tbl-body").innerHTML = '<div class="tbl-empty">No data yet</div>';
        updateRegenerateSelectedVisibility();
        updateContinueButtonVisibility();
    }
}

function splitConfirmLines(text) {
    const src = normalizeText(text || "");
    return src ? src.split("\n") : [];
}

function buildArrayDiff(leftItems, rightItems) {
    const left = Array.isArray(leftItems) ? leftItems : [];
    const right = Array.isArray(rightItems) ? rightItems : [];
    const dp = Array.from({ length: left.length + 1 }, () => Array(right.length + 1).fill(0));

    for (let i = left.length - 1; i >= 0; i -= 1) {
        for (let j = right.length - 1; j >= 0; j -= 1) {
            dp[i][j] = left[i] === right[j]
                ? dp[i + 1][j + 1] + 1
                : Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
    }

    const ops = [];
    let i = 0;
    let j = 0;
    while (i < left.length && j < right.length) {
        if (left[i] === right[j]) {
            ops.push({ type: "equal", value: left[i] });
            i += 1;
            j += 1;
        } else if (dp[i + 1][j] >= dp[i][j + 1]) {
            ops.push({ type: "remove", value: left[i] });
            i += 1;
        } else {
            ops.push({ type: "add", value: right[j] });
            j += 1;
        }
    }
    while (i < left.length) {
        ops.push({ type: "remove", value: left[i] });
        i += 1;
    }
    while (j < right.length) {
        ops.push({ type: "add", value: right[j] });
        j += 1;
    }
    return ops;
}

function appendDiffSegment(segments, type, text) {
    if (!text) return;
    const last = segments[segments.length - 1];
    if (last && last.type === type) {
        last.text += text;
        return;
    }
    segments.push({ type, text });
}

function buildInlineDiffSegments(beforeText, afterText) {
    const ops = buildArrayDiff(Array.from(String(beforeText || "")), Array.from(String(afterText || "")));
    const beforeSegments = [];
    const afterSegments = [];
    ops.forEach((op) => {
        if (op.type === "equal") {
            appendDiffSegment(beforeSegments, "same", op.value);
            appendDiffSegment(afterSegments, "same", op.value);
            return;
        }
        if (op.type === "remove") {
            appendDiffSegment(beforeSegments, "remove", op.value);
            return;
        }
        appendDiffSegment(afterSegments, "add", op.value);
    });
    return { beforeSegments, afterSegments };
}

function buildLineChangeEntries(beforeText, afterText) {
    const beforeLines = splitConfirmLines(beforeText);
    const afterLines = splitConfirmLines(afterText);
    const ops = buildArrayDiff(beforeLines, afterLines);
    const changes = [];
    let beforeLineNo = 1;
    let afterLineNo = 1;
    let pendingRemoved = [];
    let pendingAdded = [];

    const flushPending = () => {
        const count = Math.max(pendingRemoved.length, pendingAdded.length);
        for (let idx = 0; idx < count; idx += 1) {
            changes.push({
                before: pendingRemoved[idx] || null,
                after: pendingAdded[idx] || null,
            });
        }
        pendingRemoved = [];
        pendingAdded = [];
    };

    ops.forEach((op) => {
        if (op.type === "equal") {
            flushPending();
            beforeLineNo += 1;
            afterLineNo += 1;
            return;
        }
        if (op.type === "remove") {
            pendingRemoved.push({ lineNo: beforeLineNo, text: op.value });
            beforeLineNo += 1;
            return;
        }
        pendingAdded.push({ lineNo: afterLineNo, text: op.value });
        afterLineNo += 1;
    });
    flushPending();
    return changes;
}

function renderDiffSegments(segments) {
    if (!segments.length) {
        return '<span class="confirm-inline-empty">(trống)</span>';
    }
    return segments.map((seg) => {
        if (seg.type === "same") return esc(seg.text);
        const cls = seg.type === "add" ? "confirm-inline-add" : "confirm-inline-remove";
        return `<span class="${cls}">${esc(seg.text)}</span>`;
    }).join("");
}

function renderConfirmDiffRow(kind, html) {
    const isBefore = kind === "before";
    const rowClass = isBefore ? "confirm-diff-row-before" : "confirm-diff-row-after";
    return `
        <div class="confirm-diff-row ${rowClass}">
            <div class="confirm-diff-text">${html}</div>
        </div>
    `;
}

function renderEditConfirmDiff(beforeText, afterText) {
    const changes = buildLineChangeEntries(beforeText, afterText);
    if (!changes.length) {
        return {
            summary: "Không phát hiện thay đổi nào để xác nhận.",
            html: '<div class="tbl-empty">Không có nội dung thay đổi</div>',
        };
    }

    const rowCount = changes.reduce((acc, item) => acc + (item.before ? 1 : 0) + (item.after ? 1 : 0), 0);
    const html = changes.map((item) => {
        const beforeTextValue = item.before?.text || "";
        const afterTextValue = item.after?.text || "";
        const { beforeSegments, afterSegments } = buildInlineDiffSegments(beforeTextValue, afterTextValue);
        const lineNo = item.before?.lineNo || item.after?.lineNo || 0;
        return `
            <div class="confirm-diff-item">
                <div class="confirm-diff-head">Dòng thay đổi: #${lineNo}</div>
                ${renderConfirmDiffRow("before", renderDiffSegments(beforeSegments))}
                ${renderConfirmDiffRow("after", renderDiffSegments(afterSegments))}
            </div>
        `;
    }).join("");

    return {
        summary: `${changes.length} cụm thay đổi • ${rowCount} dòng hiển thị`,
        html,
    };
}

function openEditConfirmDialog(opts) {
    const overlay = $("edit-confirm-overlay");
    if (!overlay) return;
    const summaryEl = $("edit-confirm-summary");
    const diffEl = $("edit-confirm-diff-list");
    const diff = renderEditConfirmDiff(opts?.beforeText || "", opts?.afterText || "");
    $("edit-confirm-title").textContent = opts?.title || "Xác nhận cập nhật";
    if (summaryEl) summaryEl.textContent = diff.summary;
    if (diffEl) diffEl.innerHTML = diff.html;
    $("btn-edit-confirm-ok").textContent = opts?.confirmText || "Xác nhận";
    $("btn-edit-confirm-cancel").textContent = opts?.cancelText || "Huỷ";
    $("btn-edit-confirm-ok").disabled = false;
    $("btn-edit-confirm-cancel").disabled = false;
    S.confirmState = {
        onConfirm: typeof opts?.onConfirm === "function" ? opts.onConfirm : null,
        onCancel: typeof opts?.onCancel === "function" ? opts.onCancel : null,
        pending: false,
    };
    overlay.classList.add("open");
}

function closeEditConfirmDialog() {
    const overlay = $("edit-confirm-overlay");
    if (overlay) overlay.classList.remove("open");
    S.confirmState = null;
}

async function confirmEditConfirm() {
    if (!S.confirmState) return;
    if (S.confirmState.pending) return;
    const okBtn = $("btn-edit-confirm-ok");
    const cancelBtn = $("btn-edit-confirm-cancel");
    S.confirmState.pending = true;
    if (okBtn) okBtn.disabled = true;
    if (cancelBtn) cancelBtn.disabled = true;
    try {
        if (S.confirmState.onConfirm) {
            await S.confirmState.onConfirm();
        }
    } finally {
        closeEditConfirmDialog();
    }
}

async function cancelEditConfirm() {
    if (!S.confirmState) {
        closeEditConfirmDialog();
        return;
    }
    if (S.confirmState.pending) return;
    const okBtn = $("btn-edit-confirm-ok");
    const cancelBtn = $("btn-edit-confirm-cancel");
    S.confirmState.pending = true;
    if (okBtn) okBtn.disabled = true;
    if (cancelBtn) cancelBtn.disabled = true;
    try {
        if (S.confirmState.onCancel) {
            await S.confirmState.onCancel();
        }
    } finally {
        closeEditConfirmDialog();
    }
}

async function saveProjectEdits(changes, source = "update") {
    if (!S.projectId) {
        log(`[${source}] Không thể lưu vì chưa có project_id`, "err");
        return false;
    }
    const payload = {
        topic: $("inp-topic")?.value || "",
        style_name: $("sel-style")?.value || "",
        video_style_name: $("sel-vstyle")?.value || "",
        language: normalizeLanguage($("sel-lang")?.value || DEFAULT_LANGUAGE),
        model: $("sel-model")?.value || "",
        model_video: $("sel-model-video")?.value || "",
        ...changes,
    };
    try {
        const r = await fetch(`/api/projects/${encodeURIComponent(S.projectId)}/update`, {
            method: "POST",
            headers: CT_JSON,
            body: JSON.stringify(payload),
        });
        const d = await r.json();
        if (!r.ok || d.error) {
            log(`[${source}] Không thể lưu project ${S.projectId}: ${d.error || `HTTP ${r.status}`}`, "err");
            return false;
        }
        if (d.project_id) S.projectId = d.project_id;
        return true;
    } catch (e) {
        log(`[${source}] Lỗi kết nối khi lưu project ${S.projectId}: ${e.message || e}`, "err");
        return false;
    }
}

async function commitContentDraft() {
    const nextScript = normalizeText(S.scriptDraft || "");
    const nextSegments = rebuildSegmentsFromScript(nextScript);
    const nextPrompts = Array.isArray(S.videoPrompts) ? S.videoPrompts.slice(0, nextSegments.length) : [];
    const ok = await saveProjectEdits(
        { script: nextScript, segments: nextSegments, video_prompts: nextPrompts },
        "content",
    );
    if (!ok) return false;

    S.segments = nextSegments;
    S.videoPrompts = nextPrompts;
    S.script = nextScript;
    S.scriptOriginal = nextScript;
    S.scriptCommitted = nextScript;
    S.scriptDraft = nextScript;
    S.scriptTranslated = "";
    S.scriptViewMode = "original";
    S.scriptDirty = false;
    setScriptOutputValue(nextScript);
    updateScriptTranslateButton();
    updateContentEditButtons();
    renderTable();
    updateContinueButtonVisibility();
    updateRegenerateSelectedVisibility();
    log(`[content] Đã cập nhật Content và lưu project ${S.projectId}`, "ok");
    return true;
}

function revertContentDraft(fromAutoFlow = false) {
    const base = normalizeText(S.scriptCommitted || "");
    S.scriptDraft = base;
    S.script = base;
    S.scriptOriginal = base;
    S.scriptTranslated = "";
    S.scriptViewMode = "original";
    S.scriptDirty = false;
    setScriptOutputValue(base);
    updateScriptTranslateButton();
    updateContentEditButtons();
    if (fromAutoFlow) {
        log("[content] Đã huỷ chỉnh sửa chưa lưu và quay về nội dung gốc", "warn");
    }
}

async function requestUpdateContent() {
    if (!S.scriptDirty) return;
    openEditConfirmDialog({
        title: "Xác nhận lưu chỉnh sửa Content",
        beforeText: S.scriptCommitted,
        afterText: S.scriptDraft,
        confirmText: "Xác nhận lưu",
        cancelText: "Huỷ",
        onConfirm: async () => {
            await commitContentDraft();
        },
        onCancel: () => { },
    });
}

async function ensureContentDraftResolvedBeforeLeave(source = "") {
    if (!S.scriptDirty) return true;
    return new Promise((resolve) => {
        openEditConfirmDialog({
            title: "Content có thay đổi chưa lưu",
            beforeText: S.scriptCommitted,
            afterText: S.scriptDraft,
            confirmText: "Xác nhận lưu",
            cancelText: "Huỷ chỉnh sửa",
            onConfirm: async () => {
                const ok = await commitContentDraft();
                resolve(!!ok);
            },
            onCancel: () => {
                revertContentDraft(true);
                resolve(true);
            },
        });
    });
}

// ═══════════════════════════════════════════════════════════════════════════
// Pipeline
// ═══════════════════════════════════════════════════════════════════════════
async function startPipeline() {
    const topic = $("inp-topic").value.trim();
    if (!topic) { alert("Vui lòng nhập chủ đề!"); return; }
    if (!(await ensureContentDraftResolvedBeforeLeave("start_pipeline"))) return;
    const keepProjectId = !!S.projectId;
    clearPipelineOutputs(keepProjectId);

    const params = {
        topic,
        style_name: $("sel-style").value,
        video_style_name: $("sel-vstyle").value,
        language: normalizeLanguage($("sel-lang").value),
        model: $("sel-model").value,
        model_video: $("sel-model-video").value,
    };
    if (S.projectId) params.project_id = S.projectId;

    try {
        const r = await fetch("/api/pipeline/start", { method: "POST", headers: CT_JSON, body: JSON.stringify(params) });
        const d = await r.json();
        if (d.error) {
            log(`[pipeline][start] Không thể khởi chạy: ${d.error}`, "err");
            return;
        }
        if (d.project_id) S.projectId = d.project_id;
        log(
            `[pipeline][start] Đã gửi yêu cầu | project_id=${S.projectId || "(new)"} | style=${params.style_name || "-"} | video_style=${params.video_style_name || "-"} | language=${params.language || "-"} | model_content=${params.model || "-"} | model_video=${params.model_video || "-"}`,
            "ok",
        );
    } catch (e) { log(`[pipeline][start] Lỗi kết nối: ${e.message || e}`, "err"); }
}

async function togglePause() {
    // Optimistic UI update
    const prevPaused = S.paused;
    S.paused = !S.paused;
    $("btn-pause").textContent = S.paused ? "▶ Tiếp Tục" : "⏸ Tạm Dừng";
    $("btn-stop").disabled = !S.paused;
    try {
        const r = await fetch("/api/pipeline/pause", { method: "POST" });
        const d = await r.json();
        if (d.error) {
            log(`[pipeline][pause] Không thể đổi trạng thái tạm dừng: ${d.error}`, "err");
            S.paused = prevPaused;
            $("btn-pause").textContent = S.paused ? "▶ Tiếp Tục" : "⏸ Tạm Dừng";
            $("btn-stop").disabled = !S.paused;
        }
    } catch (e) {
        log(`[pipeline][pause] Lỗi kết nối: ${e.message || e}`, "err");
        S.paused = prevPaused;
        $("btn-pause").textContent = S.paused ? "▶ Tiếp Tục" : "⏸ Tạm Dừng";
        $("btn-stop").disabled = !S.paused;
    }
}

async function stopPipeline() {
    if (!S.paused) return;
    $("btn-stop").disabled = true;
    $("btn-pause").disabled = true;
    try {
        await fetch("/api/pipeline/stop", { method: "POST" });
        S.paused = false;
        log("[pipeline][stop] Đã gửi yêu cầu hủy pipeline hiện tại", "ok");
    } catch (e) { log(`[pipeline][stop] Lỗi kết nối: ${e.message || e}`, "err"); }
}

// ═══════════════════════════════════════════════════════════════════════════
// Table
// ═══════════════════════════════════════════════════════════════════════════
function renderTable() {
    const el = $("tbl-body");
    const segs = S.segments;
    const prm = S.videoPrompts;
    if (!segs.length) {
        el.innerHTML = '<div class="tbl-empty">No data yet</div>';
        updateRegenerateSelectedVisibility();
        updateContinueButtonVisibility();
        return;
    }

    let h = "";
    for (let i = 0; i < segs.length; i++) {
        const s = segs[i], idx = s.index || i + 1;
        const txt = esc(s.text || "");
        const p = i < prm.length ? esc(prm[i]) : "";
        const er = p.startsWith("[ERROR]");
        h += `<div class="tr"><div class="td c-chk"><input type="checkbox" class="rchk" onchange="updateRegenerateSelectedVisibility()"></div><div class="td c-idx">${idx}</div><div class="td c-text" onclick="popup('Segment #${idx}',${i},'seg')">${txt}</div><div class="td c-text${er ? ' c-err' : ''}" onclick="popup('Video Prompt #${idx}',${i},'prm')">${p}</div></div>`;
    }
    el.innerHTML = h;
    updateRegenerateSelectedVisibility();
    updateContinueButtonVisibility();
}

// ═══════════════════════════════════════════════════════════════════════════
// Tabs
// ═══════════════════════════════════════════════════════════════════════════
async function switchTab(t, opts = {}) {
    const force = !!opts.force;
    if (!force && S.currentTab === "script" && t !== "script") {
        const proceed = await ensureContentDraftResolvedBeforeLeave("tab");
        if (!proceed) return;
    }
    S.currentTab = t;
    document.querySelectorAll(".tab").forEach(b => b.classList.toggle("active", b.dataset.tab === t));
    document.querySelectorAll(".tab-pane").forEach(c => c.classList.remove("active"));
    $(`tab-${t}`).classList.add("active");
    updateWriterTabActionsVisibility();
    updateRegenerateSelectedVisibility();
}

function autoSwitchTabForStep(step, isActive) {
    if (!isActive) return;
    const tabByStep = {
        write: "script",
        split: "video",
        video: "video",
        continue_prompts: "video",
    };
    const nextTab = tabByStep[step];
    if (nextTab && S.currentTab !== nextTab) {
        switchTab(nextTab, { force: true });
    }
}

async function toggleScriptTranslate() {
    const scriptEl = $("script-output");
    const btn = $("btn-script-translate");
    if (!scriptEl || !btn) return;

    if (S.scriptViewMode === "translated") {
        S.scriptViewMode = "original";
        setScriptOutputValue(S.scriptDraft || S.scriptCommitted || S.scriptOriginal || S.script || "");
        updateScriptTranslateButton();
        return;
    }

    const sourceText = normalizeText(S.scriptDraft || scriptEl.value || S.scriptCommitted || S.script || "").trim();
    if (!sourceText) {
        log("[translate][content] Không có nội dung Content để dịch", "warn");
        return;
    }

    btn.disabled = true;
    btn.textContent = "⏳ Đang dịch...";
    try {
        const r = await fetch("/api/translate/vi", {
            method: "POST",
            headers: CT_JSON,
            body: JSON.stringify({
                text: sourceText,
                source_type: "content_script",
                model: FAST_TRANSLATE_MODEL,
                mode: "fixed",
            }),
        });
        const d = await r.json();
        if (d.error) {
            log(`[translate][content] Không thể dịch Content: ${d.error}`, "err");
            return;
        }

        const translated = String(d.translated_text || "").trim();
        if (!translated) {
            log("[translate][content] Kết quả dịch rỗng", "err");
            return;
        }

        S.scriptOriginal = sourceText;
        S.scriptTranslated = translated;
        S.scriptViewMode = "translated";
        setScriptOutputValue(translated);
        updateScriptTranslateButton();
        log(
            `[translate][content] Đã dịch Content sang tiếng Việt | chars_in=${sourceText.length} | chars_out=${translated.length} | model=${d.model || FAST_TRANSLATE_MODEL} | ${d.cached ? "cache_hit" : "api_call"}${d.elapsed_ms ? ` | elapsed_ms=${d.elapsed_ms}` : ""}`,
            "ok",
        );
    } catch (e) {
        log(`[translate][content] Lỗi kết nối khi dịch Content: ${e.message || e}`, "err");
    } finally {
        btn.disabled = false;
        updateScriptTranslateButton();
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Modal
// ═══════════════════════════════════════════════════════════════════════════
function popup(title, idx, type) {
    let text = "";
    let exists = false;
    if (type === "seg" && idx < S.segments.length) {
        text = S.segments[idx].text || "";
        exists = true;
    } else if (type === "prm" && idx < S.segments.length) {
        text = idx < S.videoPrompts.length ? (S.videoPrompts[idx] || "") : "";
        exists = true;
    }
    if (!exists) return;
    S.modalType = type;
    S.modalIndex = idx;
    S.modalOriginalText = text;
    S.modalDraftText = text;
    S.modalTranslatedText = "";
    S.modalViewMode = "original";
    S.modalDirty = false;
    $("modal-title").textContent = title;
    const modalText = $("modal-text");
    modalText.value = text;
    updateModalControls();
    $("modal-overlay").classList.add("open");
}

function normalizeModalEditText(type, value) {
    const raw = String(value ?? "").replace(/\r/g, "");
    if (type === "seg") {
        return raw
            .split("\n")
            .map(x => x.trim())
            .filter(Boolean)
            .join(" ")
            .replace(/\s+/g, " ")
            .trim();
    }
    return raw;
}

function updateModalControls() {
    const textArea = $("modal-text");
    const translateBtn = $("btn-modal-translate");
    const saveBtn = $("btn-modal-save");
    const editable = (S.modalType === "seg" || S.modalType === "prm") && S.modalViewMode === "original";
    if (textArea) textArea.readOnly = !editable;

    if (translateBtn) {
        if (S.modalViewMode === "translated") {
            translateBtn.textContent = "Ngôn Ngữ Gốc";
            translateBtn.title = "Hiển thị nội dung gốc trước khi dịch";
        } else {
            translateBtn.textContent = "🌐 Dịch VI";
            translateBtn.title = S.modalType === "seg"
                ? "Dịch Segment sang tiếng Việt"
                : "Dịch Video Prompt sang tiếng Việt";
        }
    }
    if (saveBtn) {
        const label = S.modalType === "seg" ? "Cập Nhật Segments" : "Cập Nhật Video Prompt";
        saveBtn.textContent = label;
        saveBtn.disabled = !(editable && S.modalDirty);
    }
}

function onModalTextInput() {
    if (!(S.modalType === "seg" || S.modalType === "prm")) return;
    if (S.modalViewMode !== "original") return;
    const textArea = $("modal-text");
    if (!textArea) return;
    S.modalDraftText = normalizeText(textArea.value || "");
    const current = normalizeModalEditText(S.modalType, S.modalDraftText);
    const base = normalizeModalEditText(S.modalType, S.modalOriginalText);
    S.modalDirty = current !== base;
    updateModalControls();
}

function closeModal() {
    $("modal-overlay").classList.remove("open");
    S.modalType = "";
    S.modalIndex = -1;
    S.modalOriginalText = "";
    S.modalDraftText = "";
    S.modalTranslatedText = "";
    S.modalViewMode = "original";
    S.modalDirty = false;
    updateModalControls();
}

function copyModal() {
    navigator.clipboard.writeText($("modal-text").value).then(() => log("[clipboard] Đã copy nội dung đang xem", "ok"));
}

async function updateModalContent() {
    if (!(S.modalType === "seg" || S.modalType === "prm")) return;
    if (S.modalViewMode !== "original") return;
    if (!S.modalDirty) return;
    if (S.modalIndex < 0) return;

    const nextSegments = S.segments.map(s => ({ ...s }));
    const nextPrompts = Array.isArray(S.videoPrompts) ? S.videoPrompts.slice() : [];
    const textValue = normalizeModalEditText(S.modalType, S.modalDraftText);
    if (S.modalType === "seg") {
        const currentSeg = nextSegments[S.modalIndex];
        if (!currentSeg || !textValue) {
            log("[modal][update] Segment không hợp lệ để cập nhật", "err");
            return;
        }
        const merged = makeSegment(textValue, currentSeg.index || S.modalIndex + 1);
        nextSegments[S.modalIndex] = { ...currentSeg, ...merged };
    } else {
        while (nextPrompts.length <= S.modalIndex) nextPrompts.push("");
        nextPrompts[S.modalIndex] = textValue;
    }
    const nextScript = nextSegments.map(s => String(s?.text || "").trim()).filter(Boolean).join("\n");
    const ok = await saveProjectEdits(
        { script: nextScript, segments: nextSegments, video_prompts: nextPrompts },
        "modal",
    );
    if (!ok) return;

    S.segments = nextSegments;
    S.videoPrompts = nextPrompts;
    setContentSnapshot(nextScript);
    renderTable();
    updateContinueButtonVisibility();
    updateRegenerateSelectedVisibility();
    const id = S.modalIndex + 1;
    const label = S.modalType === "seg" ? "segment" : "video prompt";
    log(`[modal][update] Đã cập nhật ${label} #${id} và lưu project ${S.projectId}`, "ok");
    closeModal();
}

async function translateModalToVietnamese() {
    const textArea = $("modal-text");
    const btn = $("btn-modal-translate");
    if (!textArea || !btn) return;

    if (S.modalViewMode === "translated") {
        S.modalViewMode = "original";
        textArea.value = S.modalDraftText || S.modalOriginalText || "";
        updateModalControls();
        return;
    }

    const sourceText = normalizeText(S.modalDraftText || S.modalOriginalText || textArea.value || "").trim();
    if (!sourceText) {
        log("[translate][vi] Không có nội dung để dịch", "warn");
        return;
    }

    const sourceType = S.modalType === "seg"
        ? "segment"
        : (S.modalType === "prm" ? "video_prompt" : "text");
    const modelHint = FAST_TRANSLATE_MODEL;

    btn.disabled = true;
    btn.textContent = "⏳ Đang dịch...";
    try {
        const r = await fetch("/api/translate/vi", {
            method: "POST",
            headers: CT_JSON,
            body: JSON.stringify({
                text: sourceText,
                source_type: sourceType,
                model: modelHint,
                mode: "fixed",
            }),
        });
        const d = await r.json();
        if (d.error) {
            log(`[translate][vi] Không thể dịch ${sourceType}: ${d.error}`, "err");
            return;
        }
        const translated = String(d.translated_text || "").trim();
        if (!translated) {
            log("[translate][vi] Kết quả dịch rỗng", "err");
            return;
        }
        S.modalTranslatedText = translated;
        S.modalViewMode = "translated";
        textArea.value = translated;
        updateModalControls();
        log(
            `[translate][vi] Đã dịch ${sourceType} sang tiếng Việt | chars_in=${sourceText.length} | chars_out=${translated.length} | model=${d.model || FAST_TRANSLATE_MODEL} | ${d.cached ? "cache_hit" : "api_call"}${d.elapsed_ms ? ` | elapsed_ms=${d.elapsed_ms}` : ""}`,
            "ok",
        );
    } catch (e) {
        log(`[translate][vi] Lỗi kết nối khi dịch: ${e.message || e}`, "err");
    } finally {
        btn.disabled = false;
        updateModalControls();
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Projects
// ═══════════════════════════════════════════════════════════════════════════
function capitalizeFirst(text) {
    if (!text) return "";
    return text.charAt(0).toUpperCase() + text.slice(1);
}

function formatTopicPreview(text, maxWords = 10) {
    const clean = String(text || "").replace(/\s+/g, " ").trim();
    if (!clean) return "";
    const words = clean.split(" ");
    const clipped = words.slice(0, maxWords).join(" ");
    return words.length > maxWords ? `${capitalizeFirst(clipped)}...` : capitalizeFirst(clipped);
}

function projectStatusLabel(status) {
    const s = String(status || "").toLowerCase();
    if (s === "done") return "Hoàn tất";
    if (s === "error") return "Lỗi";
    if (s === "stopped") return "Đã dừng";
    return "Đang xử lý";
}

function projectStatusClass(status) {
    const s = String(status || "").toLowerCase();
    if (s === "done") return "done";
    if (s === "error") return "error";
    if (s === "stopped") return "stopped";
    return "in-progress";
}

async function loadProjectList() {
    const el = $("project-list");
    el.innerHTML = '<div class="tbl-empty">Đang tải...</div>';
    try {
        const r = await fetch("/api/projects");
        const projects = await r.json();
        if (!projects.length) {
            el.innerHTML = '<div class="tbl-empty">Chưa có project nào</div>';
            return;
        }

        el.innerHTML = projects.map(p => {
            const pid = esc(p.project_id || "");
            const folderName = String(p.name || p.project_id || "Project").trim();
            const topicPreview = formatTopicPreview(p.topic || "", 10);
            const line1 = topicPreview ? `${folderName} - ${topicPreview}` : folderName;

            const date = String(p.updated_at || p.created_at || "").replace("T", " ").slice(0, 16);
            const segs = Number(p.segments_count || 0);
            const prms = Number(p.video_prompts_count || 0);
            const lang = String(p.language || "-");
            const contentStyle = String(p.style_name || "-");
            const promptStyle = String(p.video_style_name || "-");
            const status = projectStatusLabel(p.status);
            const statusClass = projectStatusClass(p.status);
            const line2 = `${date} | ${segs} segments | ${prms} prompts | ${lang} | ${contentStyle} | ${promptStyle}`;

            return `<div class="proj-card">
                <div class="proj-top" onclick="openProject('${pid}')">
                    <div class="proj-head">
                        <div class="proj-name">${esc(line1)}</div>
                        <span class="proj-status proj-status-${statusClass}">${esc(status)}</span>
                    </div>
                </div>
                <div class="proj-bottom">
                    <div class="proj-meta-line">${esc(line2)}</div>
                    <div class="proj-actions">
                        <button class="btn sm" onclick="openProjectFolder('${pid}')" title="Mở thư mục">📂 Mở Thư Mục</button>
                        <button class="btn sm danger" onclick="deleteProject('${pid}')" title="Xoá project">Xoá</button>
                    </div>
                </div>
            </div>`;
        }).join("");
    } catch (e) {
        el.innerHTML = '<div class="tbl-empty">Lỗi tải projects</div>';
    }
}

async function openProject(pid) {
    if (!pid) return;
    if (!(await ensureContentDraftResolvedBeforeLeave("open_project"))) return;
    try {
        const r = await fetch(`/api/projects/${pid}`);
        const d = await r.json();
        if (d.error) {
            log(`[project][load] Không thể mở project ${pid}: ${d.error}`, "err");
            return;
        }

        setContentSnapshot(d.script || "");
        S.segments = d.segments || [];
        S.videoPrompts = d.video_prompts || [];
        S.projectId = d.project_id || pid;

        updateScriptTranslateButton();
        $("inp-topic").value = d.topic || "";
        if (d.style_name) $("sel-style").value = d.style_name;
        if (d.video_style_name) $("sel-vstyle").value = d.video_style_name;
        if (d.language) $("sel-lang").value = normalizeLanguage(d.language);
        setSelectValueOrFallback($("sel-model"), d.model);
        setSelectValueOrFallback($("sel-model-video"), d.model_video);
        renderTable();

        // Check for missing prompts
        const missing = S.segments.length - S.videoPrompts.length;
        if (missing > 0) {
            log(
                `[project][load] Project ${S.projectId || pid}: còn ${missing}/${S.segments.length} video prompt thiếu (đã có ${S.videoPrompts.length}). Nhấn "Tạo Video Prompt Thiếu" để tạo phần còn lại.`,
                "warn",
            );
        } else {
            log(
                `[project][load] Project ${S.projectId || pid}: video prompt đã đủ ${S.videoPrompts.length}/${S.segments.length}.`,
                "ok",
            );
        }

        // Switch to writer page
        document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
        $("nav-writer").classList.add("active");
        document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
        $("page-writer").classList.add("active");

        log(
            `[project][load] Đã nạp project ${S.projectId || pid} | chủ đề="${d.topic || d.name || "-"}" | segments=${S.segments.length} | video_prompts=${S.videoPrompts.length} | language=${d.language || "-"} | content_style=${d.style_name || "-"} | video_style=${d.video_style_name || "-"}`,
            "ok",
        );
    } catch (e) { log(`[project][load] Lỗi kết nối khi mở project ${pid}: ${e.message || e}`, "err"); }
}

function exportProject(pid) {
    window.open(`/api/projects/${pid}/export`, "_blank");
}

async function openProjectFolder(pid) {
    if (!pid) return;
    try {
        const r = await fetch(`/api/projects/${pid}/open-folder`, { method: "POST" });
        const d = await r.json();
        if (d.error) {
            log(`[project][folder] Không thể mở thư mục project ${pid}: ${d.error}`, "err");
            return;
        }
        log(`[project][folder] Đã mở thư mục project ${pid}: ${d.path || "(không có đường dẫn trả về)"}`, "ok");
    } catch (e) {
        log(`[project][folder] Lỗi kết nối khi mở thư mục project ${pid}: ${e.message || e}`, "err");
    }
}

async function deleteProject(pid) {
    if (!pid) return;
    if (!confirm("Xóa project này?")) return;
    try {
        const r = await fetch(`/api/projects/${pid}`, { method: "DELETE" });
        const d = await r.json();
        if (d.error) { log(`[project][delete] Không thể xóa project ${pid}: ${d.error}`, "err"); return; }
        if (S.projectId === pid) clearPipelineOutputs();
        await loadProjectList();
        log(`[project][delete] Đã xóa project ${pid}`, "ok");
    } catch (e) { log(`[project][delete] Lỗi kết nối khi xóa project ${pid}: ${e.message || e}`, "err"); }
}

// ═══════════════════════════════════════════════════════════════════════════
// Splitter
// ═══════════════════════════════════════════════════════════════════════════
async function manualSplit() {
    const text = $("spl-input").value.trim();
    if (!text) { alert("Nhập nội dung cần tách!"); return; }
    try {
        const r = await fetch("/api/split/manual", {
            method: "POST", headers: CT_JSON,
            body: JSON.stringify({ text, wpm: +$("spl-wpm").value, target_seconds: +$("spl-target").value }),
        });
        const d = await r.json();
        if (d.error) { log(`[split][manual] Không thể tách đoạn: ${d.error}`, "err"); return; }
        S.splSegments = d.segments;
        renderSplitResults(d.segments, d.summary);
        log(`[split][manual] Hoàn tất tách đoạn | segments=${d.summary?.count ?? S.splSegments.length} | total_duration=${d.summary?.total_duration ?? "-"}s`, "ok");
    } catch (e) { log(`[split][manual] Lỗi kết nối khi tách đoạn: ${e.message || e}`, "err"); }
}

async function aiSplit() {
    const text = $("spl-input").value.trim();
    if (!text) { alert("Nhập nội dung cần tách!"); return; }
    const stats = $("spl-stats");
    stats.classList.remove("hidden");
    stats.innerHTML = '<span class="muted">🤖 Đang tách bằng AI...</span>';
    try {
        const r = await fetch("/api/split/ai", {
            method: "POST", headers: CT_JSON,
            body: JSON.stringify({ text, wpm: +$("spl-wpm").value, target_seconds: +$("spl-target").value }),
        });
        const d = await r.json();
        if (d.error) { log(`[split][ai] Không thể tách đoạn bằng AI: ${d.error}`, "err"); stats.innerHTML = `<span class="status-msg err">${esc(d.error)}</span>`; return; }
        S.splSegments = d.segments;
        renderSplitResults(d.segments, d.summary);
        log(`[split][ai] Hoàn tất tách đoạn bằng AI | segments=${d.summary?.count ?? S.splSegments.length} | total_duration=${d.summary?.total_duration ?? "-"}s`, "ok");
    } catch (e) { log(`[split][ai] Lỗi kết nối: ${e.message || e}`, "err"); stats.innerHTML = '<span class="status-msg err">AI Split failed</span>'; }
}

function renderSplitResults(segments, summary) {
    const el = $("spl-results");
    const stats = $("spl-stats");
    if (!segments.length) { el.innerHTML = '<div class="tbl-empty">Không có kết quả</div>'; return; }

    stats.classList.remove("hidden");
    stats.innerHTML = `<span class="spl-stat-item">${summary.count} đoạn</span>
        <span class="spl-stat-item">${summary.total_words} từ</span>
        <span class="spl-stat-item">${summary.total_duration}s tổng</span>
        <span class="spl-stat-item">~${summary.avg_duration}s/đoạn</span>`;

    el.innerHTML = segments.map((s, i) => `
        <div class="seg-card">
            <div class="seg-head">
                <span class="seg-badge">#${s.index}</span>
                <span class="seg-meta">${s.words} từ · ${s.duration}s</span>
                <button class="btn sm" onclick="copySeg(${i})" title="Copy">📋</button>
            </div>
            <div class="seg-text">${esc(s.text)}</div>
        </div>
    `).join("");
}

function copySeg(i) {
    if (i < S.splSegments.length) {
        navigator.clipboard.writeText(S.splSegments[i].text).then(() => log(`[split][copy] Đã copy segment #${i + 1}`, "ok"));
    }
}

function copyAllSegments() {
    if (!S.splSegments.length) return;
    const text = S.splSegments.map(s => s.text).join("\n\n");
    navigator.clipboard.writeText(text).then(() => log(`[split][copy] Đã copy ${S.splSegments.length} segments`, "ok"));
}

function saveSegments() {
    if (!S.splSegments.length) return;
    const blob = new Blob([JSON.stringify(S.splSegments, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `segments_${Date.now()}.json`;
    a.click();
    log(`[split][export] Đã lưu file JSON segments (${S.splSegments.length} dòng)`, "ok");
}

function clearSplitResults() {
    S.splSegments = [];
    $("spl-results").innerHTML = '<div class="tbl-empty">Chưa có kết quả tách đoạn</div>';
    $("spl-stats").classList.add("hidden");
}

// ═══════════════════════════════════════════════════════════════════════════
// Prompt Manager
// ═══════════════════════════════════════════════════════════════════════════
let _styleDialogState = { section: "content", editIdx: -1 };

function renderAllStyles() {
    renderStyleSection("content", S.styles.content, $("content-styles-list"));
    renderStyleSection("video", S.styles.video, $("video-styles-list"));
}

function renderStyleSection(section, items, container) {
    if (!items || !items.length) {
        container.innerHTML = '<div class="tbl-empty">Chưa có style nào</div>';
        return;
    }
    container.innerHTML = items.map((s, i) => {
        const dur = s.duration_minutes ? ` · ${s.duration_minutes} phút` : "";
        return `<div class="style-card">
            <div class="style-info">
                <div class="style-name">${esc(s.name)}</div>
                <div class="style-prompt">${esc(s.prompt || "")}</div>
                ${dur ? `<div class="style-meta">${dur}</div>` : ""}
            </div>
            <div class="style-actions">
                <button class="btn sm" onclick="openStyleDialog('${section}',${i})">✏ Sửa</button>
                <button class="btn sm danger" onclick="deleteStyle('${section}',${i})">🗑</button>
            </div>
        </div>`;
    }).join("");
}

function openStyleDialog(section, editIdx = -1) {
    _styleDialogState = { section, editIdx };
    const isEdit = editIdx >= 0;
    $("sd-title").textContent = isEdit ? "Sửa Style" : "Thêm Style";
    $("sd-duration-group").style.display = section === "content" ? "" : "none";

    if (isEdit) {
        const items = section === "content" ? S.styles.content : S.styles.video;
        const item = items[editIdx];
        $("sd-name").value = item.name || "";
        $("sd-prompt").value = item.prompt || "";
        $("sd-duration").value = item.duration_minutes || 0;
    } else {
        $("sd-name").value = "";
        $("sd-prompt").value = "";
        $("sd-duration").value = 0;
    }
    S.styleOriginalPrompt = $("sd-prompt").value || "";
    S.styleTranslatedPrompt = "";
    S.styleViewMode = "original";
    updateStyleTranslateButton();
    $("style-dialog-overlay").classList.add("open");
}

function closeStyleDialog() {
    $("style-dialog-overlay").classList.remove("open");
    S.styleOriginalPrompt = "";
    S.styleTranslatedPrompt = "";
    S.styleViewMode = "original";
    updateStyleTranslateButton();
}

function onStylePromptInput() {
    if (S.styleViewMode !== "original") return;
    const promptEl = $("sd-prompt");
    if (!promptEl) return;
    S.styleOriginalPrompt = normalizeText(promptEl.value || "");
    S.styleTranslatedPrompt = "";
}

function updateStyleTranslateButton() {
    const promptEl = $("sd-prompt");
    const btn = $("btn-sd-translate");
    if (!promptEl || !btn) return;
    if (S.styleViewMode === "translated") {
        btn.textContent = "Ngôn Ngữ Gốc";
        btn.title = "Hiển thị prompt gốc trước khi dịch";
        promptEl.readOnly = true;
    } else {
        btn.textContent = "🌐 Dịch VI";
        btn.title = "Dịch nội dung prompt sang tiếng Việt";
        promptEl.readOnly = false;
    }
}

async function translateStylePromptToVietnamese() {
    const promptEl = $("sd-prompt");
    const btn = $("btn-sd-translate");
    if (!promptEl || !btn) return;

    if (S.styleViewMode === "translated") {
        S.styleViewMode = "original";
        promptEl.value = S.styleOriginalPrompt || "";
        updateStyleTranslateButton();
        return;
    }

    const sourceText = normalizeText(S.styleOriginalPrompt || promptEl.value || "").trim();
    if (!sourceText) {
        log("[styles][translate][vi] Không có nội dung prompt để dịch", "warn");
        return;
    }

    btn.disabled = true;
    btn.textContent = "⏳ Đang dịch...";
    try {
        const r = await fetch("/api/translate/vi", {
            method: "POST",
            headers: CT_JSON,
            body: JSON.stringify({
                text: sourceText,
                source_type: "style_prompt",
                model: FAST_TRANSLATE_MODEL,
                mode: "fixed",
            }),
        });
        const d = await r.json();
        if (d.error) {
            log(`[styles][translate][vi] Không thể dịch prompt style: ${d.error}`, "err");
            return;
        }

        const translated = String(d.translated_text || "").trim();
        if (!translated) {
            log("[styles][translate][vi] Kết quả dịch rỗng", "err");
            return;
        }

        S.styleTranslatedPrompt = translated;
        S.styleViewMode = "translated";
        promptEl.value = translated;
        updateStyleTranslateButton();
        log(
            `[styles][translate][vi] Đã dịch prompt style sang tiếng Việt | chars_in=${sourceText.length} | chars_out=${translated.length} | model=${d.model || FAST_TRANSLATE_MODEL} | ${d.cached ? "cache_hit" : "api_call"}${d.elapsed_ms ? ` | elapsed_ms=${d.elapsed_ms}` : ""}`,
            "ok",
        );
    } catch (e) {
        log(`[styles][translate][vi] Lỗi kết nối khi dịch prompt style: ${e.message || e}`, "err");
    } finally {
        btn.disabled = false;
        updateStyleTranslateButton();
    }
}

async function saveStyleDialog() {
    const name = $("sd-name").value.trim();
    const prompt = (S.styleViewMode === "translated"
        ? S.styleOriginalPrompt
        : $("sd-prompt").value).trim();
    if (!name) { alert("Nhập tên style!"); return; }

    const { section, editIdx } = _styleDialogState;
    const item = { name, prompt };
    if (section === "content") item.duration_minutes = +$("sd-duration").value || 0;

    const action = editIdx >= 0 ? "edit" : "add";
    const body = { action, item };
    if (editIdx >= 0) body.index = editIdx;

    try {
        const r = await fetch(`/api/styles/${section}`, { method: "POST", headers: CT_JSON, body: JSON.stringify(body) });
        const updated = await r.json();
        if (!r.ok || updated.error) {
            log(`[styles][save] Không thể lưu ${section} style "${name}": ${updated.error || `HTTP ${r.status}`}`, "err");
            return;
        }
        if (!Array.isArray(updated)) {
            log(`[styles][save] Phản hồi không hợp lệ khi lưu ${section} style`, "err");
            return;
        }
        if (section === "content") S.styles.content = updated;
        else S.styles.video = updated;
        fillStyles();
        renderAllStyles();
        closeStyleDialog();
        log(`[styles][save] Đã ${action === "edit" ? "cập nhật" : "thêm mới"} ${section} style "${name}"`, "ok");
    } catch (e) { log(`[styles][save] Lỗi kết nối khi lưu style "${name}": ${e.message || e}`, "err"); }
}

async function deleteStyle(section, idx) {
    if (!confirm("Xóa style này?")) return;
    try {
        const r = await fetch(`/api/styles/${section}`, {
            method: "POST", headers: CT_JSON,
            body: JSON.stringify({ action: "delete", index: idx }),
        });
        const updated = await r.json();
        if (!r.ok || updated.error) {
            log(`[styles][delete] Không thể xóa ${section} style #${idx + 1}: ${updated.error || `HTTP ${r.status}`}`, "err");
            return;
        }
        if (!Array.isArray(updated)) {
            log(`[styles][delete] Phản hồi không hợp lệ khi xóa ${section} style`, "err");
            return;
        }
        if (section === "content") S.styles.content = updated;
        else S.styles.video = updated;
        fillStyles();
        renderAllStyles();
        log(`[styles][delete] Đã xóa ${section} style #${idx + 1}`, "ok");
    } catch (e) { log(`[styles][delete] Lỗi kết nối khi xóa style #${idx + 1}: ${e.message || e}`, "err"); }
}

// ═══════════════════════════════════════════════════════════════════════════
// Queue Manager
// ═══════════════════════════════════════════════════════════════════════════
async function loadQueue() {
    try {
        const r = await fetch("/api/queue");
        const d = await r.json();
        S.queue = d.queue || [];
        S.queueRunning = d.running || false;
        S.queueCurrent = d.current || 0;
        S.queueTotal = d.total || 0;
        S.queueCurrentTopic = d.current_topic || "";
        renderQueue();
    } catch (e) { log(`[queue][load] Không thể tải hàng chờ: ${e.message || e}`, "err"); }
}

function fillQueueSelects() {
    const models = getUserSelectableModels();
    ["q-model", "q-model-video"].forEach(id => {
        const el = $(id);
        el.innerHTML = models.map(m => `<option value="${m}">${m}</option>`).join("")
            || '<option value="">— chưa có model —</option>';
    });
    setSelectValueOrFallback($("q-model"), S.config.model);
    setSelectValueOrFallback($("q-model-video"), S.config.model_video);

    // Styles
    $("q-style").innerHTML = S.styles.content.map(s => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join("");
    $("q-vstyle").innerHTML = S.styles.video.map(s => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join("");
}

function renderQueue() {
    const el = $("q-list");
    $("q-count").textContent = S.queue.length;

    const qp = $("q-progress");
    if (S.queueRunning) {
        qp.textContent = `- Running ${S.queueCurrent}/${S.queueTotal}`;
        if (S.queueCurrentTopic) qp.textContent += `: ${S.queueCurrentTopic.substring(0, 28)}`;
    } else {
        qp.textContent = "";
    }

    $("q-start-btn").disabled = S.queueRunning || !S.queue.length;
    $("q-clear-btn").disabled = S.queueRunning || !S.queue.length;
    $("q-add-btn").disabled = S.queueRunning;
    $("q-cancel-edit").disabled = S.queueRunning;

    if (!S.queue.length) {
        el.innerHTML = '<div class="tbl-empty">Chưa có project trong hàng chờ</div>';
        return;
    }

    el.innerHTML = S.queue.map((q, i) => `
        <div class="queue-card">
            <div class="q-badge">${i + 1}</div>
            <div class="q-info">
                <div class="q-topic">${esc(q.topic || "").substring(0, 60)}</div>
                <div class="q-meta">${esc(`Ngôn ngữ: ${q.language || "-"} | Content Style: ${q.style_name || "-"} | Video Style: ${q.video_style_name || "-"} | Model Content: ${q.model || "-"} | Model Video: ${q.model_video || q.model || "-"}`)}</div>
            </div>
            <div class="q-actions">
                <button class="btn sm" onclick="editQueueItem(${i})" ${S.queueRunning ? "disabled" : ""}>Edit</button>
                <button class="btn sm danger" onclick="deleteQueueItem(${i})" ${S.queueRunning ? "disabled" : ""}>Delete</button>
            </div>
        </div>
    `).join("");
}

async function addQueueItem() {
    if (S.queueRunning) return;
    const topic = $("q-topic").value.trim();
    if (!topic) { alert("Nhập chủ đề!"); return; }

    const item = {
        topic,
        language: normalizeLanguage($("q-lang").value),
        style_name: $("q-style").value,
        video_style_name: $("q-vstyle").value,
        model: $("q-model").value,
        model_video: $("q-model-video").value,
    };

    try {
        if (S.queueEditIdx >= 0) {
            // Editing existing item
            await fetch(`/api/queue/${S.queueEditIdx}`, { method: "PUT", headers: CT_JSON, body: JSON.stringify(item) });
            log(`[queue][update] Đã cập nhật item #${S.queueEditIdx + 1} | topic="${topic.slice(0, 50)}"`, "ok");
            S.queueEditIdx = -1;
            $("q-add-btn").textContent = "➕ Thêm Vào Hàng Chờ";
            $("q-cancel-edit").classList.add("hidden");
        } else {
            await fetch("/api/queue", { method: "POST", headers: CT_JSON, body: JSON.stringify(item) });
            log(`[queue][add] Đã thêm vào hàng chờ | topic="${topic.slice(0, 50)}" | style=${item.style_name || "-"} | video_style=${item.video_style_name || "-"} | language=${item.language || "-"}`, "ok");
        }
        $("q-topic").value = "";
        await loadQueue();
    } catch (e) { log(`[queue][save] Không thể lưu item hàng chờ: ${e.message || e}`, "err"); }
}

function editQueueItem(idx) {
    if (S.queueRunning) return;
    const item = S.queue[idx];
    if (!item) return;
    S.queueEditIdx = idx;
    $("q-topic").value = item.topic || "";
    $("q-lang").value = normalizeLanguage(item.language);
    if (item.style_name) $("q-style").value = item.style_name;
    if (item.video_style_name) $("q-vstyle").value = item.video_style_name;
    setSelectValueOrFallback($("q-model"), item.model);
    setSelectValueOrFallback($("q-model-video"), item.model_video);
    $("q-add-btn").textContent = "💾 Lưu Thay Đổi";
    $("q-cancel-edit").classList.remove("hidden");
}

function cancelQueueEdit() {
    S.queueEditIdx = -1;
    $("q-topic").value = "";
    $("q-add-btn").textContent = "➕ Thêm Vào Hàng Chờ";
    $("q-cancel-edit").classList.add("hidden");
}

async function deleteQueueItem(idx) {
    if (S.queueRunning) return;
    try {
        await fetch(`/api/queue/${idx}`, { method: "DELETE" });
        log(`[queue][delete] Đã xóa item #${idx + 1} khỏi hàng chờ`, "ok");
        await loadQueue();
    } catch (e) { log(`[queue][delete] Không thể xóa item #${idx + 1}: ${e.message || e}`, "err"); }
}

async function clearQueue() {
    if (S.queueRunning) return;
    if (!S.queue.length) return;
    if (!confirm("Xóa tất cả hàng chờ?")) return;
    try {
        await fetch("/api/queue/clear", { method: "POST" });
        log("[queue][clear] Đã xóa toàn bộ hàng chờ", "ok");
        await loadQueue();
    } catch (e) { log(`[queue][clear] Không thể xóa hàng chờ: ${e.message || e}`, "err"); }
}

async function startQueue() {
    if (S.queueRunning) return;
    if (!S.queue.length) { alert("Hàng chờ trống!"); return; }
    try {
        const r = await fetch("/api/queue/start", { method: "POST" });
        const d = await r.json();
        if (d.error) log(`[queue][start] Không thể chạy hàng chờ: ${d.error}`, "err");
        else {
            log(`[queue][start] Đã bắt đầu chạy hàng chờ | tổng project=${d.count}`, "ok");
            await loadQueue();
        }
    } catch (e) { log(`[queue][start] Lỗi kết nối khi chạy hàng chờ: ${e.message || e}`, "err"); }
}

function onQueueState(d) {
    S.queue = d.queue || [];
    S.queueRunning = !!d.running;
    S.queueCurrent = d.current || 0;
    S.queueTotal = d.total || 0;
    S.queueCurrentTopic = d.current_topic || "";
    renderQueue();
}

// ═══════════════════════════════════════════════════════════════════════════
// P2P Share
// ═══════════════════════════════════════════════════════════════════════════
function sanitizeP2PToken(value) {
    return String(value || "").toUpperCase().replace(/[^A-Z]/g, "").slice(0, 6);
}

function formatP2PSize(bytes) {
    const n = Number(bytes) || 0;
    if (n <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let v = n;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) {
        v /= 1024;
        i += 1;
    }
    return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatP2PDate(raw) {
    const s = String(raw || "").trim();
    if (!s) return "-";
    const dt = new Date(s);
    if (Number.isNaN(dt.getTime())) return s;
    return dt.toLocaleString("vi-VN", { hour12: false });
}

function makeP2PDefaultName() {
    const dt = new Date();
    const pad = (x) => String(x).padStart(2, "0");
    return `Share_${dt.getFullYear()}${pad(dt.getMonth() + 1)}${pad(dt.getDate())}_${pad(dt.getHours())}${pad(dt.getMinutes())}${pad(dt.getSeconds())}`;
}

function summarizeP2PFiles(files) {
    const list = Array.isArray(files) ? files : [];
    const total = list.reduce((acc, f) => acc + (Number(f?.size) || 0), 0);
    return { count: list.length, total };
}

function renderP2PPickedFiles() {
    const box = $("p2p-picked-files");
    const summary = $("p2p-picked-summary");
    const btnCreate = $("btn-p2p-create");
    if (!box || !summary || !btnCreate) return;
    const files = Array.isArray(S.p2p.pickedFiles) ? S.p2p.pickedFiles : [];
    if (!files.length) {
        box.innerHTML = '<div class="tbl-empty">Chưa có file nào được chọn</div>';
        summary.textContent = "Chưa chọn file nào.";
        btnCreate.disabled = true;
        return;
    }
    const s = summarizeP2PFiles(files);
    summary.textContent = `${s.count} file | ${formatP2PSize(s.total)}`;
    box.innerHTML = files.map((f, idx) => `
        <div class="p2p-file-item">
            <div class="p2p-file-main">
                <div class="p2p-file-name">${esc(f.rel_path || f.name || `File ${idx + 1}`)}</div>
                <div class="p2p-file-size">${formatP2PSize(f.size || 0)}</div>
            </div>
            <button class="btn sm danger" onclick="removePickedP2PFile(${idx})">X</button>
        </div>
    `).join("");
    btnCreate.disabled = false;
}

function removePickedP2PFile(idx) {
    if (!Array.isArray(S.p2p.pickedFiles)) return;
    if (!(idx >= 0 && idx < S.p2p.pickedFiles.length)) return;
    S.p2p.pickedFiles.splice(idx, 1);
    renderP2PPickedFiles();
}

function mergeP2PFiles(existing, incoming) {
    const out = [];
    const seen = new Set();
    for (const item of [...(existing || []), ...(incoming || [])]) {
        if (!item || !item.path) continue;
        const key = `${item.path}`.toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        out.push(item);
    }
    return out;
}

function getP2PPickerInitialDir() {
    const files = Array.isArray(S.p2p.pickedFiles) ? S.p2p.pickedFiles : [];
    const firstPath = String(files[0]?.path || "").trim();
    if (!firstPath) return "";
    const normalized = firstPath.replace(/\\/g, "/");
    const slashIdx = normalized.lastIndexOf("/");
    if (slashIdx <= 0) return "";
    return normalized.slice(0, slashIdx).replace(/\//g, "\\");
}

async function pickP2PFilesByOS(type = "files") {
    const isFolder = type === "folder";
    const url = isFolder ? "/api/p2p/pick-folder" : "/api/p2p/pick-files";
    const r = await fetch(url, {
        method: "POST",
        headers: CT_JSON,
        body: JSON.stringify({ initial_dir: getP2PPickerInitialDir() }),
    });
    const d = await r.json();
    if (!r.ok || d.error) {
        throw new Error(d.error || `HTTP ${r.status}`);
    }
    return {
        cancelled: !!d.cancelled,
        files: Array.isArray(d.files) ? d.files : [],
        suggestedName: String(d.suggested_name || "").trim(),
        fileCount: Number(d.file_count || 0),
    };
}

async function p2pPickFiles() {
    try {
        const picked = await pickP2PFilesByOS("files");
        if (picked.cancelled) return;
        if (!picked.files.length) {
            log("[p2p] Không có file nào được chọn", "warn");
            return;
        }
        S.p2p.pickedFiles = mergeP2PFiles(S.p2p.pickedFiles, picked.files);
        const nameEl = $("p2p-share-name");
        if (nameEl && !nameEl.value.trim()) {
            nameEl.value = picked.suggestedName || makeP2PDefaultName();
        }
        renderP2PPickedFiles();
        log(`[p2p] Đã thêm ${picked.fileCount || picked.files.length} file (giữ nguyên file gốc)`, "ok");
    } catch (e) {
        log(`[p2p] Không thể chọn file: ${e.message || e}`, "err");
    }
}

async function p2pPickFolder() {
    try {
        const picked = await pickP2PFilesByOS("folder");
        if (picked.cancelled) return;
        if (!picked.files.length) {
            log("[p2p] Không có file nào trong folder được chọn (hoặc đã hủy chọn)", "warn");
            return;
        }
        S.p2p.pickedFiles = mergeP2PFiles(S.p2p.pickedFiles, picked.files);
        const nameEl = $("p2p-share-name");
        if (nameEl && !nameEl.value.trim()) {
            nameEl.value = picked.suggestedName || makeP2PDefaultName();
        }
        renderP2PPickedFiles();
        log(`[p2p] Đã thêm folder | files=${picked.fileCount || picked.files.length} (giữ nguyên file gốc)`, "ok");
    } catch (e) {
        log(`[p2p] Không thể chọn folder: ${e.message || e}`, "err");
    }
}

function initP2PDropzone() {
    const zone = $("p2p-dropzone");
    if (!zone) return;
    zone.addEventListener("click", () => p2pPickFiles());
    const stop = (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
    };
    ["dragenter", "dragover", "dragleave", "drop"].forEach((evt) => {
        zone.addEventListener(evt, stop);
    });
    ["dragenter", "dragover"].forEach((evt) => {
        zone.addEventListener(evt, () => zone.classList.add("active"));
    });
    ["dragleave", "drop"].forEach((evt) => {
        zone.addEventListener(evt, () => zone.classList.remove("active"));
    });
    zone.addEventListener("drop", () => {
        log("[p2p] Đã tắt kéo-thả để tránh sao chép file vào app. Hãy dùng nút Chọn 1/N File hoặc Chọn Folder.", "warn");
    });
}

async function p2pCreateShare() {
    const files = Array.isArray(S.p2p.pickedFiles) ? S.p2p.pickedFiles : [];
    if (!files.length) {
        log("[p2p] Vui lòng chọn file hoặc folder trước khi tạo token", "warn");
        return;
    }
    const name = ($("p2p-share-name")?.value || "").trim();
    const editToken = sanitizeP2PToken(S.p2p.editToken || "");
    try {
        const url = editToken.length === 6
            ? `/api/p2p/shares/${encodeURIComponent(editToken)}`
            : "/api/p2p/shares";
        const method = editToken.length === 6 ? "PUT" : "POST";
        const r = await fetch(url, {
            method,
            headers: CT_JSON,
            body: JSON.stringify({ name, files }),
        });
        const d = await r.json();
        if (!r.ok || d.error) {
            const actionName = editToken.length === 6 ? "cập nhật token" : "tạo token";
            log(`[p2p] Không thể ${actionName}: ${d.error || `HTTP ${r.status}`}`, "err");
            return;
        }
        const token = d.share?.token || "";
        if (editToken.length === 6) {
            S.p2p.editToken = token || editToken;
            S.p2p.selectedToken = S.p2p.editToken;
            await loadP2PShares(S.p2p.editToken);
            const refreshed = getP2PShareByToken(S.p2p.editToken);
            if (refreshed) {
                S.p2p.pickedFiles = Array.isArray(refreshed.files) ? refreshed.files.slice() : [];
                const nameEl = $("p2p-share-name");
                if (nameEl) nameEl.value = refreshed.name || name;
                renderP2PPickedFiles();
            }
            updateP2PComposeMode();
            log(`[p2p] Đã cập nhật token ${S.p2p.editToken}`, "ok");
            return;
        }
        $("p2p-new-token").textContent = token || "------";
        $("p2p-new-token-wrap")?.classList.remove("hidden");
        S.p2p.lastToken = token;
        S.p2p.pickedFiles = [];
        const nameEl = $("p2p-share-name");
        if (nameEl) nameEl.value = "";
        renderP2PPickedFiles();
        await loadP2PShares(token);
        log(`[p2p] Đã tạo token ${token}`, "ok");
    } catch (e) {
        const actionName = editToken.length === 6 ? "cập nhật token" : "tạo token";
        log(`[p2p] Lỗi ${actionName}: ${e.message || e}`, "err");
    }
}

function copyP2PToken() {
    const token = String($("p2p-new-token")?.textContent || "").trim();
    if (!token || token === "------") return;
    navigator.clipboard.writeText(token).then(() => log(`[p2p] Đã copy token ${token}`, "ok"));
}

function copyP2PShareToken(token) {
    const t = sanitizeP2PToken(token);
    if (!t) return;
    navigator.clipboard.writeText(t).then(() => log(`[p2p] Đã copy token ${t}`, "ok"));
}

function getP2PShareByToken(token) {
    const t = sanitizeP2PToken(token);
    return (S.p2p.shares || []).find(s => sanitizeP2PToken(s.token) === t) || null;
}

function updateP2PComposeMode() {
    const isEdit = sanitizeP2PToken(S.p2p.editToken).length === 6;
    const head = $("p2p-compose-head");
    const badge = $("p2p-edit-badge");
    const badgeText = $("p2p-edit-badge-text");
    const submitBtn = $("btn-p2p-create");
    if (head) head.textContent = isEdit ? "Chỉnh Sửa Token Gửi File" : "Tạo Token Gửi File";
    if (submitBtn) submitBtn.textContent = isEdit ? "Lưu Chỉnh Sửa" : "Tạo Token";
    if (badge) badge.classList.toggle("hidden", !isEdit);
    if (badgeText) badgeText.textContent = isEdit ? `Đang chỉnh sửa token ${S.p2p.editToken}` : "";
}

function startP2PEdit(token) {
    const t = sanitizeP2PToken(token);
    const share = getP2PShareByToken(t);
    if (!share) {
        log(`[p2p] Không tìm thấy token ${t} để chỉnh sửa`, "warn");
        return;
    }
    S.p2p.editToken = t;
    S.p2p.selectedToken = t;
    S.p2p.pickedFiles = Array.isArray(share.files) ? share.files.slice() : [];
    const nameEl = $("p2p-share-name");
    if (nameEl) nameEl.value = share.name || "";
    $("p2p-new-token-wrap")?.classList.add("hidden");
    renderP2PPickedFiles();
    updateP2PComposeMode();
    renderP2PShares();
    log(`[p2p] Đã nạp token ${t} vào form Tạo Token để chỉnh sửa`, "ok");
}

function cancelP2PEdit(keepForm = false) {
    S.p2p.editToken = "";
    S.p2p.selectedToken = "";
    if (!keepForm) {
        S.p2p.pickedFiles = [];
        const nameEl = $("p2p-share-name");
        if (nameEl) nameEl.value = "";
        renderP2PPickedFiles();
    }
    updateP2PComposeMode();
    renderP2PShares();
}

function renderP2PShares() {
    const createdEl = $("p2p-share-list");
    const downloadedEl = $("p2p-downloaded-list");
    if (!createdEl || !downloadedEl) return;
    const shares = Array.isArray(S.p2p.shares) ? S.p2p.shares : [];
    const createdShares = shares.filter(s => s.type !== "download");
    const downloadedShares = shares
        .filter(s => s.type === "download" || String(s.last_download_dir || "").trim())
        .sort((a, b) => String(b.last_download_at || "").localeCompare(String(a.last_download_at || "")));

    createdEl.innerHTML = createdShares.length ? createdShares.map(s => {
        const token = sanitizeP2PToken(s.token || "");
        const active = S.p2p.selectedToken === token;
        const createdAt = formatP2PDate(s.created_at);
        const updatedAt = formatP2PDate(s.updated_at);
        const fCount = Number(s.file_count || (s.files || []).length || 0);
        return `
            <div class="p2p-share-card${active ? " active" : ""}">
                <div class="p2p-share-top">
                    <div class="p2p-share-name">${esc(s.name || "(No name)")}</div>
                    <div class="p2p-share-top-actions">
                        <button class="btn sm" onclick="startP2PEdit('${token}')">Chỉnh sửa</button>
                        <button class="btn sm danger" onclick="p2pDeleteShare('${token}')">Xóa</button>
                    </div>
                </div>
                <div class="p2p-share-meta">
                    ${fCount} file | ${formatP2PSize(s.total_size || 0)} | tạo: ${esc(createdAt)} | cập nhật: ${esc(updatedAt)}
                </div>
            </div>
        `;
    }).join("") : '<div class="tbl-empty">Chưa có share nào</div>';

    downloadedEl.innerHTML = downloadedShares.length ? downloadedShares.map(s => {
        const downloadPath = String(s.last_download_dir || "").trim();
        const lastDownloadAt = formatP2PDate(s.last_download_at);
        const fCount = Number(s.file_count || (s.files || []).length || 0);
        const safePath = encodeURIComponent(downloadPath);
        return `
            <div class="p2p-share-card">
                <div class="p2p-share-top">
                    <div class="p2p-share-name">${esc(s.name || "(No name)")}</div>
                </div>
                <div class="p2p-share-meta">
                    ${fCount} file | ${formatP2PSize(s.total_size || 0)} | tải lần cuối: ${esc(lastDownloadAt)}
                </div>
                <div class="p2p-share-path">Đã lưu tại${downloadPath ? `<br>${esc(downloadPath)}` : ""}</div>
                <div class="p2p-share-actions">
                    <button class="btn sm primary" onclick="openP2PFolder(decodeURIComponent('${safePath}'))">Mở thư mục</button>
                </div>
            </div>
        `;
    }).join("") : '<div class="tbl-empty">Chưa tải file nào</div>';
}

async function loadP2PShares(preferToken = "") {
    try {
        const r = await fetch("/api/p2p/shares");
        const d = await r.json();
        if (!r.ok || d.error) {
            log(`[p2p] Không thể tải danh sách token: ${d.error || `HTTP ${r.status}`}`, "err");
            return;
        }
        S.p2p.shares = Array.isArray(d.shares) ? d.shares : [];
        const preferred = sanitizeP2PToken(preferToken || S.p2p.selectedToken || "");
        if (preferred && S.p2p.shares.some(s => s.token === preferred)) S.p2p.selectedToken = preferred;
        else if (!S.p2p.shares.some(s => s.token === S.p2p.selectedToken)) S.p2p.selectedToken = "";
        if (S.p2p.editToken && !S.p2p.shares.some(s => s.token === S.p2p.editToken)) {
            cancelP2PEdit();
        }
        renderP2PShares();
    } catch (e) {
        log(`[p2p] Lỗi tải danh sách token: ${e.message || e}`, "err");
    }
}

async function p2pDeleteShare(token) {
    const t = sanitizeP2PToken(token);
    if (!t) return;
    if (!confirm(`Xóa token ${t}?`)) return;
    try {
        const r = await fetch(`/api/p2p/shares/${encodeURIComponent(t)}`, { method: "DELETE" });
        const d = await r.json();
        if (!r.ok || d.error) {
            log(`[p2p] Không thể xoá token: ${d.error || `HTTP ${r.status}`}`, "err");
            return;
        }
        if (S.p2p.editToken === t) cancelP2PEdit();
        else S.p2p.selectedToken = "";
        await loadP2PShares();
        log(`[p2p] Đã xoá token ${t}`, "ok");
    } catch (e) {
        log(`[p2p] Lỗi xoá token: ${e.message || e}`, "err");
    }
}

async function openP2PFolder(folderPath) {
    if (!folderPath) return;
    try {
        await fetch("/api/open-folder", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: folderPath }) });
    } catch (e) {
        log("[p2p] Lỗi mở thư mục: " + e.message, "err");
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// WebRTC P2P File Transfer — High-Speed Binary Protocol
// ═══════════════════════════════════════════════════════════════════════════
const _webrtc = { peer: null, conn: null, token: "", sessionId: "", paused: false, cancelled: false };
const CHUNK_SIZE = 256 * 1024; // 256KB chunks for speed
const BUFFER_HIGH = 8 * 1024 * 1024; // 8MB DataChannel buffer threshold
const SAVE_BATCH = 1024 * 1024; // 1MB save batch to Flask

function _genSessionId() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 8); }
function _fmtSize(b) {
    if (b < 1024) return b + " B";
    if (b < 1024 * 1024) return (b / 1024).toFixed(1) + " KB";
    if (b < 1024 * 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + " MB";
    return (b / 1024 / 1024 / 1024).toFixed(2) + " GB";
}
function _fmtSpeed(bps) {
    if (bps < 1024) return bps.toFixed(0) + " B/s";
    if (bps < 1024 * 1024) return (bps / 1024).toFixed(1) + " KB/s";
    return (bps / 1024 / 1024).toFixed(1) + " MB/s";
}
function _makePeerConfig() {
    return {
        config: {
            iceServers: [
                { urls: "stun:stun.l.google.com:19302" },
                { urls: "stun:stun1.l.google.com:19302" },
            ]
        }
    };
}

// ── Combined share + connect (sender) ──
async function p2pShareAndConnect() {
    const nameEl = $("p2p-share-name");
    const shareName = nameEl ? nameEl.value.trim() : "";
    if (!S.p2p.pickedFiles.length) { log("[p2p] Chưa chọn file", "warn"); return; }

    const btn = $("btn-p2p-create");
    if (btn) { btn.disabled = true; btn.textContent = "Đang tạo..."; }
    try {
        const body = { name: shareName || undefined, files: S.p2p.pickedFiles.map(f => f.path) };
        const r = await fetch("/api/p2p/shares", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const d = await r.json();
        if (!r.ok || d.error) { log("[p2p] Lỗi tạo share: " + (d.error || ""), "err"); if (btn) { btn.disabled = false; btn.textContent = "Chia sẻ"; } return; }
        _webrtc.token = d.share?.token || d.token || "";
        log(`[p2p] Đã tạo share: ${_webrtc.token}`);
        await loadP2PShares(_webrtc.token);
    } catch (e) {
        log("[p2p] Lỗi: " + e.message, "err");
        if (btn) { btn.disabled = false; btn.textContent = "Chia sẻ"; }
        return;
    }

    _webrtc.cancelled = false;
    const peerWrap = $("p2p-peer-id-wrap");
    const peerIdEl = $("p2p-peer-id");
    const statusEl = $("p2p-peer-status");
    if (peerWrap) peerWrap.classList.remove("hidden");
    if (statusEl) statusEl.textContent = "Đang kết nối...";
    if (btn) btn.textContent = "Đang chia sẻ...";

    try {
        if (_webrtc.peer) _webrtc.peer.destroy();
        const peerId = "AS-" + Math.random().toString(36).slice(2, 8).toUpperCase();
        _webrtc.peer = new Peer(peerId, _makePeerConfig());

        _webrtc.peer.on("open", (id) => {
            if (peerIdEl) peerIdEl.textContent = id;
            if (statusEl) statusEl.textContent = "Sẵn sàng. Gửi Peer ID cho người nhận.";
            log(`[p2p][webrtc] Peer ID: ${id} — sẵn sàng`, "ok");
        });

        _webrtc.peer.on("connection", (conn) => {
            log(`[p2p][webrtc] Có người kết nối: ${conn.peer}`);
            _webrtc.conn = conn;
            if (statusEl) statusEl.textContent = "Đang gửi file...";
            let senderFilesDone = 0, senderTotalFiles = 0;
            conn.on("open", () => {
                conn.on("data", async (msg) => {
                    if (typeof msg === "object" && msg.type === "request-meta") {
                        try {
                            const r2 = await fetch(`/api/p2p/share-meta/${encodeURIComponent(_webrtc.token)}`);
                            const meta = await r2.json();
                            senderTotalFiles = (meta.files || []).length;
                            conn.send({ type: "meta", data: meta });
                        } catch (e2) {
                            conn.send({ type: "error", message: "Không thể đọc metadata" });
                        }
                    } else if (typeof msg === "object" && msg.type === "request-file") {
                        if (statusEl) statusEl.textContent = `Đang gửi ${++senderFilesDone}/${senderTotalFiles}: ${msg.rel_path}`;
                        await _webrtcSendFile(conn, _webrtc.token, msg.rel_path, msg.size || 0);
                        if (senderFilesDone >= senderTotalFiles && statusEl) statusEl.textContent = `Hoàn tất! Đã gửi ${senderTotalFiles} file.`;
                    }
                });
            });
            conn.on("close", () => {
                if (statusEl) statusEl.textContent = senderFilesDone >= senderTotalFiles ? `Hoàn tất! Đã gửi ${senderTotalFiles} file.` : "Người nhận đã ngắt kết nối.";
                if (btn) { btn.disabled = false; btn.textContent = "Chia sẻ"; }
            });
        });

        _webrtc.peer.on("error", (err) => {
            if (statusEl) statusEl.textContent = "Lỗi: " + err.type;
            log(`[p2p][webrtc] Lỗi: ${err.type}`, "err");
            if (btn) { btn.disabled = false; btn.textContent = "Chia sẻ"; }
        });
    } catch (e) {
        log(`[p2p][webrtc] Lỗi khởi tạo: ${e.message}`, "err");
        if (btn) { btn.disabled = false; btn.textContent = "Chia sẻ"; }
    }
}

async function _webrtcSendFile(conn, token, relPath, totalSize) {
    log(`[p2p][webrtc] Đang gửi: ${relPath} (${_fmtSize(totalSize)})`);
    try {
        const url = `/api/p2p/stream-file?token=${encodeURIComponent(token)}&rel_path=${encodeURIComponent(relPath)}`;
        const response = await fetch(url);
        if (!response.ok) { conn.send({ type: "file-error", rel_path: relPath, error: `HTTP ${response.status}` }); return; }

        conn.send({ type: "file-start", rel_path: relPath, size: totalSize });
        const reader = response.body.getReader();

        while (true) {
            if (_webrtc.cancelled) { conn.send({ type: "file-error", rel_path: relPath, error: "Cancelled" }); return; }
            while (_webrtc.paused && !_webrtc.cancelled) await new Promise(r => setTimeout(r, 200));
            const { done, value } = await reader.read();
            if (done) break;

            for (let i = 0; i < value.length; i += CHUNK_SIZE) {
                if (_webrtc.cancelled) return;
                while (_webrtc.paused && !_webrtc.cancelled) await new Promise(r => setTimeout(r, 200));
                const slice = value.slice(i, Math.min(i + CHUNK_SIZE, value.length));
                while (conn.dataChannel && conn.dataChannel.bufferedAmount > BUFFER_HIGH) {
                    await new Promise(r => setTimeout(r, 20));
                }
                conn.send(slice.buffer.byteLength === slice.length ? slice.buffer : slice.buffer.slice(slice.byteOffset, slice.byteOffset + slice.byteLength));
            }
        }
        conn.send({ type: "file-end", rel_path: relPath, total: totalSize });
        log(`[p2p][webrtc] Đã gửi xong: ${relPath} (${_fmtSize(totalSize)})`, "ok");
    } catch (e) {
        conn.send({ type: "file-error", rel_path: relPath, error: e.message });
        log(`[p2p][webrtc] Lỗi gửi ${relPath}: ${e.message}`, "err");
    }
}

function copyP2PPeerId() {
    const el = $("p2p-peer-id");
    if (el) { navigator.clipboard.writeText(el.textContent); log("[p2p] Đã copy Peer ID"); }
}

// ── Receiver ──
async function webrtcConnect() {
    const input = $("p2p-peer-input");
    const statusEl = $("p2p-webrtc-status");
    if (!input) return;
    const peerId = input.value.trim();
    if (!peerId) { if (statusEl) statusEl.textContent = "Vui lòng nhập Peer ID."; return; }

    _webrtc.paused = false;
    _webrtc.cancelled = false;
    _webrtcFileState.receivedTotal = 0;
    if (statusEl) statusEl.textContent = "Đang kết nối...";
    log(`[p2p][webrtc] Đang kết nối tới ${peerId}...`);

    const ctrlWrap = $("p2p-webrtc-controls");
    if (ctrlWrap) { ctrlWrap.classList.remove("hidden"); ctrlWrap.style.display = "flex"; }

    try {
        if (_webrtc.peer) _webrtc.peer.destroy();
        _webrtc.sessionId = _genSessionId();
        _webrtc.peer = new Peer(undefined, _makePeerConfig());

        _webrtc.peer.on("open", () => {
            const conn = _webrtc.peer.connect(peerId, { reliable: true, serialization: "binary" });
            _webrtc.conn = conn;

            conn.on("open", () => {
                if (statusEl) statusEl.textContent = "Đã kết nối! Đang lấy danh sách file...";
                conn.send({ type: "request-meta" });
            });

            conn.on("data", async (msg) => {
                const isBinary = msg instanceof ArrayBuffer || (ArrayBuffer.isView(msg) && !msg.type);
                if (isBinary) {
                    const data = msg instanceof ArrayBuffer ? new Uint8Array(msg) : (msg instanceof Uint8Array ? msg : new Uint8Array(msg.buffer, msg.byteOffset, msg.byteLength));
                    _webrtcReceiveRawChunk(data);
                } else if (typeof msg === "object") {
                    if (msg.type === "meta") {
                        await _webrtcReceiveMeta(conn, msg.data);
                    } else if (msg.type === "file-start") {
                        _webrtcFileState.current = msg.rel_path;
                        _webrtcFileState.size = msg.size;
                        _webrtcFileState.received = 0;
                        _chunkBuffer = { relPath: msg.rel_path, chunks: [], totalBytes: 0, offset: 0 };
                    } else if (msg.type === "file-end") {
                        await _flushChunkBuffer();
                        _webrtcFileState.completedFiles++;
                        _webrtcUpdateProgress();
                        if (_webrtcFileState.completedFiles >= _webrtcFileState.totalFiles) {
                            await _drainChunkQueue();
                            await _webrtcFinalize();
                        }
                    } else if (msg.type === "file-error") {
                        log(`[p2p][webrtc] Lỗi nhận ${msg.rel_path}: ${msg.error}`, "err");
                        _webrtcFileState.completedFiles++;
                    } else if (msg.type === "error") {
                        if (statusEl) statusEl.textContent = "Lỗi: " + msg.message;
                    }
                }
            });

            conn.on("close", () => {
                if (statusEl && _webrtcFileState.completedFiles < _webrtcFileState.totalFiles)
                    statusEl.textContent = "Kết nối bị đóng.";
            });
        });

        _webrtc.peer.on("error", (err) => {
            if (statusEl) statusEl.textContent = "Lỗi: " + err.type;
        });
    } catch (e) {
        if (statusEl) statusEl.textContent = "Lỗi: " + e.message;
    }
}

// ── Controls ──
function webrtcPause() {
    _webrtc.paused = true;
    const btn = $("btn-webrtc-pause");
    if (btn) { btn.textContent = "▶ Tiếp tục"; btn.onclick = webrtcResume; }
    const st = $("p2p-webrtc-status"); if (st) st.textContent = "Đã tạm dừng.";
}
function webrtcResume() {
    _webrtc.paused = false;
    const btn = $("btn-webrtc-pause");
    if (btn) { btn.textContent = "⏸ Tạm dừng"; btn.onclick = webrtcPause; }
}
function webrtcCancel() {
    _webrtc.cancelled = true; _webrtc.paused = false;
    if (_webrtc.conn) try { _webrtc.conn.close(); } catch (_) { }
    if (_webrtc.peer) { _webrtc.peer.destroy(); _webrtc.peer = null; }
    const st = $("p2p-webrtc-status"); if (st) st.textContent = "Đã huỷ.";
    const cw = $("p2p-webrtc-controls");
    if (cw) { cw.classList.add("hidden"); cw.style.display = "none"; }
}

// ── File state + buffered receive ──
const _webrtcFileState = { totalFiles: 0, completedFiles: 0, totalSize: 0, receivedTotal: 0, current: "", size: 0, received: 0, startTime: 0, shareName: "" };
let _chunkBuffer = { relPath: "", chunks: [], totalBytes: 0, offset: 0 };

async function _webrtcReceiveMeta(conn, meta) {
    const statusEl = $("p2p-webrtc-status");
    const files = meta.files || [];
    if (!files.length) { if (statusEl) statusEl.textContent = "Không có file."; return; }
    Object.assign(_webrtcFileState, { totalFiles: files.length, completedFiles: 0, totalSize: meta.total_size || 0, receivedTotal: 0, startTime: Date.now(), shareName: meta.name || "download" });
    if (statusEl) statusEl.textContent = `${files.length} file (${_fmtSize(meta.total_size)}) — đang tải...`;
    const pw = $("p2p-webrtc-progress"); if (pw) pw.classList.remove("hidden");
    log(`[p2p][webrtc] Bắt đầu tải ${files.length} file (${_fmtSize(meta.total_size)})`);

    for (const f of files) {
        if (_webrtc.cancelled) return;
        while (_webrtc.paused && !_webrtc.cancelled) await new Promise(r => setTimeout(r, 200));
        Object.assign(_webrtcFileState, { current: f.rel_path, received: 0, size: f.size });
        _chunkBuffer = { relPath: f.rel_path, chunks: [], totalBytes: 0, offset: 0 };
        conn.send({ type: "request-file", rel_path: f.rel_path, size: f.size });
        await new Promise(resolve => {
            const iv = setInterval(() => {
                if (_webrtc.cancelled || _webrtcFileState.completedFiles > (_webrtcFileState.totalFiles - files.length + files.indexOf(f))) { clearInterval(iv); resolve(); }
            }, 200);
        });
    }
}

function _webrtcReceiveRawChunk(data) {
    _webrtcFileState.received += data.length;
    _webrtcFileState.receivedTotal += data.length;
    _webrtcUpdateProgress();
    _chunkBuffer.chunks.push(data);
    _chunkBuffer.totalBytes += data.length;
    if (_chunkBuffer.totalBytes >= SAVE_BATCH) _flushChunkBuffer();
}

async function _flushChunkBuffer() {
    if (!_chunkBuffer.chunks.length) return;
    const totalLen = _chunkBuffer.chunks.reduce((s, c) => s + c.length, 0);
    const merged = new Uint8Array(totalLen);
    let pos = 0;
    for (const c of _chunkBuffer.chunks) { merged.set(c, pos); pos += c.length; }
    const item = { rel_path: _chunkBuffer.relPath, offset: _chunkBuffer.offset, data: merged };
    _chunkBuffer.offset += totalLen;
    _chunkBuffer.chunks = [];
    _chunkBuffer.totalBytes = 0;
    _chunkQueue.push(item);
    _processChunkQueue();
}

const _chunkQueue = [];
let _chunkWriting = false;

async function _processChunkQueue() {
    if (_chunkWriting || !_chunkQueue.length) return;
    _chunkWriting = true;
    while (_chunkQueue.length) {
        if (_webrtc.cancelled) { _chunkQueue.length = 0; break; }
        const item = _chunkQueue.shift();
        for (let a = 0; a < 3; a++) {
            try {
                await fetch(`/api/p2p/save-chunk?session=${encodeURIComponent(_webrtc.sessionId)}&rel_path=${encodeURIComponent(item.rel_path)}&offset=${item.offset}`, { method: "POST", body: item.data });
                break;
            } catch (e) {
                if (a === 2) log(`[p2p][webrtc] Lỗi lưu: ${e.message}`, "err");
                else await new Promise(r => setTimeout(r, 300 * (a + 1)));
            }
        }
    }
    _chunkWriting = false;
}

async function _drainChunkQueue() {
    while (_chunkQueue.length || _chunkWriting) {
        await new Promise(r => setTimeout(r, 100));
        if (!_chunkWriting && _chunkQueue.length) _processChunkQueue();
    }
}

function _webrtcUpdateProgress() {
    const pfill = $("p2p-webrtc-pfill"), ptext = $("p2p-webrtc-ptext"), statusEl = $("p2p-webrtc-status");
    const pct = _webrtcFileState.totalSize > 0 ? Math.min(100, _webrtcFileState.receivedTotal / _webrtcFileState.totalSize * 100) : 0;
    if (pfill) pfill.style.width = pct.toFixed(1) + "%";
    const elapsed = (Date.now() - _webrtcFileState.startTime) / 1000;
    const speed = elapsed > 0 ? _webrtcFileState.receivedTotal / elapsed : 0;
    if (ptext) ptext.textContent = `${pct.toFixed(1)}% | ${_fmtSize(_webrtcFileState.receivedTotal)} / ${_fmtSize(_webrtcFileState.totalSize)} | ${_fmtSpeed(speed)}`;
    if (statusEl && !_webrtc.paused) statusEl.textContent = `File ${_webrtcFileState.completedFiles + 1}/${_webrtcFileState.totalFiles}: ${_webrtcFileState.current || "..."} | ${_fmtSpeed(speed)}`;
}

async function _webrtcFinalize() {
    const statusEl = $("p2p-webrtc-status");
    try {
        const r = await fetch("/api/p2p/save-done", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session: _webrtc.sessionId, name: _webrtcFileState.shareName }) });
        const d = await r.json();
        if (d.ok) {
            if (statusEl) statusEl.textContent = `Hoàn tất! ${d.file_count} file → ${d.saved_dir}`;
            log(`[p2p][webrtc] Hoàn tất! ${d.file_count} file → ${d.saved_dir}`, "ok");
        } else { if (statusEl) statusEl.textContent = "Lỗi: " + (d.error || ""); }
    } catch (e) { if (statusEl) statusEl.textContent = "Lỗi: " + e.message; }
    const cw = $("p2p-webrtc-controls");
    if (cw) { cw.classList.add("hidden"); cw.style.display = "none"; }
    if (_webrtc.peer) { _webrtc.peer.destroy(); _webrtc.peer = null; }
    // Refresh download list
    try { await loadP2PShares(); } catch (_) { }
}





// ═══════════════════════════════════════════════════════════════════════════
// Utils
// ═══════════════════════════════════════════════════════════════════════════
const CT_JSON = { "Content-Type": "application/json; charset=utf-8" };
function $(id) { return document.getElementById(id); }

function esc(s) {
    if (!s) return "";
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function countMissingPrompts() {
    if (!S.segments.length) return 0;
    return Math.max(0, S.segments.length - S.videoPrompts.length);
}

function updateContinueButtonVisibility() {
    const btn = $("btn-step-continue");
    if (!btn) return;
    const missing = countMissingPrompts();
    btn.classList.toggle("hidden", missing <= 0);
    btn.title = missing > 0 ? `Tạo ${missing} video prompt thiếu` : "Không có video prompt thiếu";
}

function updateWriterTabActionsVisibility() {
    const wrap = $("writer-tab-actions");
    if (!wrap) return;
    const isScriptTab = S.currentTab === "script";
    const isVideoTab = S.currentTab === "video";
    wrap.querySelectorAll(".tab-action-script").forEach(btn => {
        btn.classList.toggle("hidden", !isScriptTab);
    });
    wrap.querySelectorAll(".tab-action-video").forEach(btn => {
        btn.classList.toggle("hidden", !isVideoTab);
    });
}

function updateRegenerateSelectedVisibility() {
    const btn = $("btn-regen-selected");
    if (!btn) return;
    const anyChecked = Array.from(document.querySelectorAll(".rchk")).some(x => x.checked);
    const show = S.currentTab === "video" && anyChecked;
    btn.classList.toggle("hidden", !show);
    btn.disabled = !anyChecked;
    btn.title = anyChecked ? "Tạo lại các Video Prompt đã chọn" : "Chọn ít nhất 1 Video Prompt để tạo lại";
}
async function clearResults() {
    if (!(await ensureContentDraftResolvedBeforeLeave("clear_results"))) return;
    clearPipelineOutputs();
    // Reset left panel
    $("inp-topic").value = "";
    $("sel-lang").selectedIndex = 0;
    $("sel-style").selectedIndex = 0;
    $("sel-vstyle").selectedIndex = 0;
    setSelectValueOrFallback($("sel-model"), S.config.model);
    setSelectValueOrFallback($("sel-model-video"), S.config.model_video);
}

function clearPipelineOutputs(keepProjectId = false) {
    setContentSnapshot("");
    S.segments = [];
    S.videoPrompts = [];
    if (!keepProjectId) S.projectId = "";

    updateScriptTranslateButton();
    $("tbl-body").innerHTML = '<div class="tbl-empty">No data yet</div>';
    $("progress-area").classList.add("hidden");
    $("pfill").style.width = "0%";
    $("ptext").textContent = "Waiting...";
    $("chk-all").checked = false;
    $("btn-stop").disabled = true;
    updateRegenerateSelectedVisibility();
    updateContinueButtonVisibility();
}

function copyContent() {
    let t = S.currentTab === "script" ? $("script-output").value : S.videoPrompts.join("\n");
    if (t) navigator.clipboard.writeText(t).then(() => log(`[clipboard] Đã copy ${S.currentTab === "script" ? "content script" : "video prompts"}`, "ok"));
}

function toggleAll() {
    const c = $("chk-all").checked;
    document.querySelectorAll(".rchk").forEach(x => x.checked = c);
    updateRegenerateSelectedVisibility();
}

async function importProject() {
    if (!(await ensureContentDraftResolvedBeforeLeave("import_project"))) return;
    $("file-import").click();
}

async function handleImport(ev) {
    const f = ev.target.files[0]; if (!f) return;
    try {
        const d = JSON.parse(await f.text());
        setContentSnapshot(d.script || "");
        S.segments = d.segments || [];
        S.videoPrompts = d.video_prompts || [];
        S.projectId = d.project_id || "";
        updateScriptTranslateButton();
        $("inp-topic").value = d.topic || "";
        if (d.language) $("sel-lang").value = normalizeLanguage(d.language);
        renderTable();
        updateContinueButtonVisibility();
        updateRegenerateSelectedVisibility();
        log(`[project][import] Đã import dữ liệu: ${d.name || f.name} | segments=${S.segments.length} | video_prompts=${S.videoPrompts.length}`, "ok");
    } catch (e) { log(`[project][import] Import thất bại: ${e.message || e}`, "err"); }
    ev.target.value = "";
}

// ═══════════════════════════════════════════════════════════════════════════
// Per-step Pipeline
// ═══════════════════════════════════════════════════════════════════════════
async function runStep(step) {
    const stepLabelMap = {
        write: "Viết Content",
        split: "Tách Đoạn",
        video: "Tạo Video Prompt",
        continue_prompts: "Tạo Video Prompt Thiếu",
    };
    const stepLabel = stepLabelMap[step] || step;

    if (!(await ensureContentDraftResolvedBeforeLeave("run_step"))) return;
    if (S.running) { log(`[step][run] Không thể chạy "${stepLabel}" vì pipeline đang chạy`, "err"); return; }
    autoSwitchTabForStep(step, true);

    const body = {
        step,
        topic: $("inp-topic").value,
        style_name: $("sel-style").value,
        video_style_name: $("sel-vstyle").value,
        model: $("sel-model").value,
        model_video: $("sel-model-video").value,
        language: normalizeLanguage($("sel-lang").value),
        project_id: S.projectId,
        script: S.scriptCommitted,
        segments: S.segments,
        video_prompts: S.videoPrompts,
    };

    try {
        const r = await fetch("/api/pipeline/step", {
            method: "POST", headers: CT_JSON, body: JSON.stringify(body),
        });
        const d = await r.json();
        if (d.error) { log(`[step][run] Không thể chạy "${stepLabel}": ${d.error}`, "err"); return; }
        if (d.project_id) S.projectId = d.project_id;
        log(
            `[step][run] Đã gửi yêu cầu chạy "${stepLabel}" | project_id=${S.projectId || "(new)"} | segments=${S.segments.length} | prompts=${S.videoPrompts.length}`,
            "ok",
        );
    } catch (e) { log(`[step][run] Lỗi kết nối khi chạy "${stepLabel}": ${e.message || e}`, "err"); }
}

// ═══════════════════════════════════════════════════════════════════════════
// Regenerate Single Prompt
// ═══════════════════════════════════════════════════════════════════════════
async function regeneratePrompt(idx) {
    if (!S.segments[idx]) { log(`[regen][single] Segment #${idx + 1} không tồn tại`, "err"); return; }

    try {
        const r = await fetch("/api/pipeline/regenerate-prompt", {
            method: "POST", headers: CT_JSON,
            body: JSON.stringify({
                index: idx,
                segment_id: S.segments[idx].index || idx + 1,
                text: S.segments[idx].text,
                video_style_name: $("sel-vstyle").value,
                model_video: $("sel-model-video").value,
                project_id: S.projectId,
            }),
        });
        const d = await r.json();
        if (d.error) { log(`[regen][single] Không thể tạo lại prompt cho segment #${idx + 1}: ${d.error}`, "err"); return; }

        while (S.videoPrompts.length <= idx) S.videoPrompts.push("");
        S.videoPrompts[idx] = d.prompt;
        renderTable();
        log(`[regen][single] Đã tạo lại video prompt cho segment #${idx + 1}`, "ok");
    } catch (e) { log(`[regen][single] Lỗi kết nối khi tạo lại segment #${idx + 1}: ${e.message || e}`, "err"); }
}

async function regenerateSelectedPrompts() {
    if (S.running) { log("[regen][batch] Không thể chạy vì pipeline đang hoạt động", "warn"); return; }
    if (!S.segments.length) { log("[regen][batch] Không có segment để tạo lại prompt", "warn"); return; }

    const checks = Array.from(document.querySelectorAll(".rchk"));
    const selected = checks
        .map((c, i) => (c.checked ? i : -1))
        .filter(i => i >= 0);

    if (!selected.length) { log("[regen][batch] Hãy chọn ít nhất 1 segment", "warn"); return; }

    $("chk-all").checked = false;
    selected.forEach(i => { if (checks[i]) checks[i].checked = false; });
    updateRegenerateSelectedVisibility();

    log(`[regen][batch] Bắt đầu tạo lại ${selected.length} video prompt | project_id=${S.projectId || "-"}`);
    for (let i = 0; i < selected.length; i++) {
        const idx = selected[i];
        log(`[regen][batch] Tiến trình ${i + 1}/${selected.length}: segment #${idx + 1}`);
        await regeneratePrompt(idx);
    }
    log(`[regen][batch] Hoàn tất tạo lại ${selected.length} video prompt`, "ok");
    updateRegenerateSelectedVisibility();
    updateContinueButtonVisibility();
}

// ═══════════════════════════════════════════════════════════════════════════
// Output Directory
// ═══════════════════════════════════════════════════════════════════════════
async function loadOutputDir() {
    try {
        const r = await fetch("/api/output-dir");
        const d = await r.json();
        $("cfg-output-dir").value = d.output_dir || "";
        $("output-dir-status").textContent = `Mặc định: ${d.default}`;
    } catch (e) { }
}

async function saveOutputDir() {
    const path = $("cfg-output-dir").value.trim();
    if (!path) { log("[output-dir] Vui lòng nhập đường dẫn thư mục lưu project", "err"); return; }
    try {
        const r = await fetch("/api/output-dir", {
            method: "POST", headers: CT_JSON,
            body: JSON.stringify({ path }),
        });
        const d = await r.json();
        if (d.error) { log(`[output-dir] Không thể lưu thư mục: ${d.error}`, "err"); $("output-dir-status").textContent = "✗ " + d.error; return; }
        $("output-dir-status").textContent = "✓ Đã lưu: " + d.output_dir;
        log(`[output-dir] Đã lưu thư mục output: ${d.output_dir}`, "ok");
        await loadP2PDownloadDir();
    } catch (e) { log(`[output-dir] Lỗi kết nối khi lưu thư mục: ${e.message || e}`, "err"); }
}

async function pickOutputDir() {
    const current = $("cfg-output-dir").value.trim();
    try {
        const r = await fetch("/api/output-dir/pick", {
            method: "POST",
            headers: CT_JSON,
            body: JSON.stringify({ initial_dir: current }),
        });
        const d = await r.json();
        if (d.error) {
            $("output-dir-status").textContent = "✗ " + d.error;
            log(`[output-dir] Không thể mở cửa sổ chọn thư mục: ${d.error}`, "err");
            return;
        }
        if (d.cancelled) return;
        $("cfg-output-dir").value = d.output_dir || "";
        $("output-dir-status").textContent = "✓ Đã chọn: " + (d.output_dir || "");
        log(`[output-dir] Đã chọn thư mục output: ${d.output_dir}`, "ok");
        await loadP2PDownloadDir();
    } catch (e) {
        log(`[output-dir] Lỗi khi mở bộ chọn thư mục: ${e.message || e}`, "err");
    }
}

async function loadP2PDownloadDir() {
    try {
        const r = await fetch("/api/p2p-download-dir");
        const d = await r.json();
        $("cfg-p2p-download-dir").value = d.p2p_download_dir || "";
        $("p2p-download-dir-status").textContent = `Mặc định: ${d.default}`;
    } catch (e) { }
}

async function saveP2PDownloadDir() {
    const path = $("cfg-p2p-download-dir").value.trim();
    if (!path) { log("[p2p] Vui lòng nhập đường dẫn thư mục lưu file P2P", "err"); return; }
    try {
        const r = await fetch("/api/p2p-download-dir", {
            method: "POST", headers: CT_JSON,
            body: JSON.stringify({ path }),
        });
        const d = await r.json();
        if (d.error) {
            log(`[p2p] Không thể lưu thư mục P2P: ${d.error}`, "err");
            $("p2p-download-dir-status").textContent = "✗ " + d.error;
            return;
        }
        $("p2p-download-dir-status").textContent = "✓ Đã lưu: " + d.p2p_download_dir;
        log(`[p2p] Đã lưu thư mục nhận P2P: ${d.p2p_download_dir}`, "ok");
    } catch (e) {
        log(`[p2p] Lỗi kết nối khi lưu thư mục P2P: ${e.message || e}`, "err");
    }
}

async function pickP2PDownloadDir() {
    const current = $("cfg-p2p-download-dir").value.trim();
    try {
        const r = await fetch("/api/p2p-download-dir/pick", {
            method: "POST",
            headers: CT_JSON,
            body: JSON.stringify({ initial_dir: current }),
        });
        const d = await r.json();
        if (d.error) {
            $("p2p-download-dir-status").textContent = "✗ " + d.error;
            log(`[p2p] Không thể mở cửa sổ chọn thư mục P2P: ${d.error}`, "err");
            return;
        }
        if (d.cancelled) return;
        $("cfg-p2p-download-dir").value = d.p2p_download_dir || "";
        $("p2p-download-dir-status").textContent = "✓ Đã chọn: " + (d.p2p_download_dir || "");
        log(`[p2p] Đã chọn thư mục nhận P2P: ${d.p2p_download_dir}`, "ok");
    } catch (e) {
        log(`[p2p] Lỗi khi mở bộ chọn thư mục P2P: ${e.message || e}`, "err");
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Log
// ═══════════════════════════════════════════════════════════════════════════
const MOJIBAKE_TOKENS = ["\u00C3", "\u00E2", "\u00E1\u00BB", "\u00E1\u00BA", "\u00F0\u0178", "\u00E2\u0153", "\u00E2\u20AC", "\u00C2"];
const CP1252_EXTRA_MAP = new Map([
    [0x20AC, 0x80], [0x201A, 0x82], [0x0192, 0x83], [0x201E, 0x84], [0x2026, 0x85],
    [0x2020, 0x86], [0x2021, 0x87], [0x02C6, 0x88], [0x2030, 0x89], [0x0160, 0x8A],
    [0x2039, 0x8B], [0x0152, 0x8C], [0x017D, 0x8E], [0x2018, 0x91], [0x2019, 0x92],
    [0x201C, 0x93], [0x201D, 0x94], [0x2022, 0x95], [0x2013, 0x96], [0x2014, 0x97],
    [0x02DC, 0x98], [0x2122, 0x99], [0x0161, 0x9A], [0x203A, 0x9B], [0x0153, 0x9C],
    [0x017E, 0x9E], [0x0178, 0x9F],
]);

function mojibakeScore(text) {
    if (!text) return 0;
    let score = 0;
    for (const token of MOJIBAKE_TOKENS) score += (text.match(new RegExp(token, "g")) || []).length;
    score += (text.match(/\uFFFD/g) || []).length * 4;
    return score;
}

function toCp1252Bytes(text) {
    const bytes = [];
    for (const ch of text) {
        const code = ch.codePointAt(0);
        if (code <= 0xFF) {
            bytes.push(code);
            continue;
        }
        const mapped = CP1252_EXTRA_MAP.get(code);
        if (mapped === undefined) return null;
        bytes.push(mapped);
    }
    return Uint8Array.from(bytes);
}

function normalizeMojibakeText(text) {
    if (typeof text !== "string") text = String(text ?? "");
    const sourceScore = mojibakeScore(text);
    if (sourceScore <= 0) return text;
    const bytes = toCp1252Bytes(text);
    if (!bytes) return text;
    try {
        const fixed = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
        if (!fixed || fixed.includes("\uFFFD")) return text;
        return mojibakeScore(fixed) < sourceScore ? fixed : text;
    } catch (_) {
        return text;
    }
}

function resolveLogLevel(cls, message) {
    const c = String(cls || "").toLowerCase();
    if (c === "err") return "ERROR";
    if (c === "warn") return "WARN";
    if (c === "ok") return "OK";

    const msg = String(message || "").toLowerCase();
    if (msg.includes("[fail]") || msg.includes("error") || msg.includes("timeout") || msg.includes("exception")) return "ERROR";
    if (msg.includes("warn") || msg.includes("cảnh báo")) return "WARN";
    if (msg.includes("[ok]") || msg.includes("success") || msg.includes("completed") || msg.includes("done")) return "OK";
    return "INFO";
}

function log(msg, cls = "", time = null) {
    const body = $("log-body");
    const el = document.createElement("div");
    el.className = `ll${cls ? ` l-${cls}` : ""}`;
    const ts = time || new Date().toLocaleTimeString("vi-VN", { hour12: false });
    const normalized = normalizeMojibakeText(msg);
    const level = resolveLogLevel(cls, normalized);
    el.textContent = `[${ts}] ${level} | ${normalized} |`;
    body.appendChild(el);
    body.scrollTop = body.scrollHeight;
}
function clearLog() { $("log-body").innerHTML = ""; }

// ═══════════════════════════════════════════════════════════════════════════
// Auto-Update Check (via GitHub API)
// ═══════════════════════════════════════════════════════════════════════════
(function autoCheckUpdate() {
    let attempts = 0;
    const maxAttempts = 20;

    async function poll() {
        try {
            const r = await fetch("/api/check-update");
            const d = await r.json();
            if (!d.checked) {
                if (++attempts < maxAttempts) setTimeout(poll, 5000);
                return;
            }
            if (d.error || !d.has_update) return;
            showUpdateBanner(d);
        } catch (_) { }
    }

    function showUpdateBanner(info) {
        if (document.getElementById("update-banner")) return;
        const banner = document.createElement("div");
        banner.id = "update-banner";
        banner.innerHTML = `
            <div class="update-banner-content">
                <span class="update-icon">\u{1F504}</span>
                <span class="update-text">
                    Có bản cập nhật mới: <strong>v${esc(info.local)}</strong> &rarr; <strong>v${esc(info.remote)}</strong>
                </span>
                <button class="btn sm primary" onclick="applyUpdate(this)">Cập nhật</button>
                <button class="btn sm" onclick="this.parentElement.parentElement.remove()">Bỏ qua</button>
            </div>
        `;
        document.body.prepend(banner);
    }

    setTimeout(poll, 3000);
})();

async function applyUpdate(btn) {
    if (btn) { btn.disabled = true; btn.textContent = "Đang tải..."; }
    try {
        const r = await fetch("/api/apply-update", { method: "POST" });
        const d = await r.json();
        if (d.ok) {
            const banner = document.getElementById("update-banner");
            if (banner) {
                banner.innerHTML = `
                    <div class="update-banner-content update-success">
                        <span class="update-icon">\u2705</span>
                        <span class="update-text">
                            Đã cập nhật thành công! Phiên bản mới đang khởi động...<br>
                            Cửa sổ này sẽ tự đóng sau 3 giây.
                        </span>
                    </div>
                `;
            }
            log("[update] Đã cập nhật. Đang đóng ứng dụng cũ...", "ok");
            // Auto-close after 3s — triggers AutoStudio.vbs cleanup
            setTimeout(() => {
                try { window.close(); } catch (_) { }
                // Fallback if window.close() blocked
                if (banner) banner.querySelector(".update-text").innerHTML =
                    "Phiên bản mới đã khởi động.<br><strong>Vui lòng đóng tab này.</strong>";
            }, 3000);
        } else {
            if (btn) { btn.disabled = false; btn.textContent = "Cập nhật"; }
            log("[update] Lỗi: " + (d.error || ""), "err");
        }
    } catch (e) {
        if (btn) { btn.disabled = false; btn.textContent = "Cập nhật"; }
        log("[update] Lỗi: " + e.message, "err");
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// YouTube Remix
// ═══════════════════════════════════════════════════════════════════════════
let _ytData = null;

function switchRemixTab(tabId) {
    const tabs = document.querySelectorAll("#page-remix .tab");
    const panes = document.querySelectorAll("#page-remix .tab-pane");
    tabs.forEach(t => t.classList.toggle("active", t.dataset.tab === tabId));
    panes.forEach(p => p.classList.toggle("active", p.id === "tab-" + tabId));
}

async function extractYouTube() {
    const inp = $("inp-yt-url");
    const btn = $("btn-yt-extract");
    const url = inp ? inp.value.trim() : "";
    if (!url) { log("[youtube] Vui lòng nhập URL YouTube.", "warn"); return; }

    if (btn) { btn.disabled = true; btn.textContent = "Đang trích xuất..."; }
    log(`[youtube] Đang trích xuất: ${url}...`);

    try {
        const r = await fetch("/api/youtube/extract", {
            method: "POST",
            headers: CT_JSON,
            body: JSON.stringify({ url }),
        });
        const d = await r.json();
        if (!d.ok) {
            log("[youtube] Lỗi: " + (d.error || ""), "err");
            if (btn) { btn.disabled = false; btn.textContent = "Trích xuất"; }
            return;
        }

        _ytData = d;

        // Populate Original Content tab
        const emptyEl = $("remix-original-empty");
        const contentEl = $("remix-original-content");
        if (emptyEl) emptyEl.classList.add("hidden");
        if (contentEl) contentEl.classList.remove("hidden");

        const titleEl = $("remix-og-title");
        const metaEl = $("remix-og-meta");
        const subEl = $("remix-og-subtitles");

        if (titleEl) titleEl.textContent = d.title || "(Không có tiêu đề)";

        // Populate individual metadata fields
        const dur = d.duration ? Math.floor(d.duration / 60) + ":" + String(d.duration % 60).padStart(2, "0") : "?";
        const dateEl = $("remix-og-date"); if (dateEl) dateEl.textContent = _formatYTDate(d.upload_date);
        const viewsEl = $("remix-og-views"); if (viewsEl) viewsEl.textContent = (d.view_count || 0).toLocaleString() + " views";
        const chanEl = $("remix-og-channel"); if (chanEl) chanEl.textContent = d.channel || "—";
        const durEl = $("remix-og-duration"); if (durEl) durEl.textContent = dur;

        // Tags inline
        const tagsEl = $("remix-og-tags");
        if (tagsEl) {
            const tags = Array.isArray(d.tags) ? d.tags : [];
            tagsEl.textContent = tags.length ? tags.join(", ") : "Không có từ khoá";
        }

        // Description
        const descEl = $("remix-og-desc");
        if (descEl) descEl.textContent = d.description || "(Không có mô tả)";

        // Subtitles
        if (subEl) {
            subEl.textContent = d.subtitles_text || "(Không có subtitle)";
        }

        // Auto switch to Original Content tab
        switchRemixTab("remix-original");

        log(`[youtube] Trích xuất thành công: "${d.title}" | ${d.subtitles_text ? d.subtitles_text.length + " ký tự subtitle" : "không có subtitle, dùng mô tả"}`, "ok");
    } catch (e) {
        log("[youtube] Lỗi: " + e.message, "err");
    }
    if (btn) { btn.disabled = false; btn.textContent = "Trích xuất"; }
}

function _formatYTDate(raw) {
    if (!raw || raw.length < 8) return raw || "—";
    return raw.slice(0, 4) + "-" + raw.slice(4, 6) + "-" + raw.slice(6, 8);
}

async function startRewritePipeline() {
    const ytUrl = ($("inp-yt-url")?.value || "").trim();
    if (!ytUrl) {
        log("[rewrite] Vui lòng nhập URL YouTube.", "warn");
        return;
    }
    if (S.running) {
        log("[rewrite] Pipeline đang chạy.", "warn");
        return;
    }

    const body = {
        youtube_url: ytUrl,
        youtube_data: _ytData && _ytData.ok ? _ytData : null,
        target_language: $("sel-remix-lang")?.value || "English",
        video_style_name: $("sel-remix-vstyle")?.value || $("sel-vstyle")?.value || "",
        model: $("sel-remix-model")?.value || $("sel-model")?.value || "",
        model_analyze: $("sel-remix-model-analyze")?.value || $("sel-remix-model")?.value || "",
        model_video: $("sel-remix-model-video")?.value || $("sel-model-video")?.value || "",
    };

    // Show progress
    const pa = $("remix-progress-area"); if (pa) pa.classList.remove("hidden");
    const btnStart = $("btn-remix-start"); if (btnStart) btnStart.disabled = true;
    const btnPause = $("btn-remix-pause"); if (btnPause) btnPause.disabled = false;
    const btnStop = $("btn-remix-stop"); if (btnStop) btnStop.disabled = false;

    // Clear previous output
    const scriptOut = $("remix-script-output"); if (scriptOut) scriptOut.value = "";
    const tblBody = $("remix-tbl-body"); if (tblBody) tblBody.innerHTML = '<div class="tbl-empty">Chưa có dữ liệu</div>';

    try {
        const r = await fetch("/api/pipeline/rewrite", {
            method: "POST",
            headers: CT_JSON,
            body: JSON.stringify(body),
        });
        const d = await r.json();
        if (d.ok) {
            log(`[rewrite] Bắt đầu pipeline remix...`, "ok");
        } else {
            log("[rewrite] Lỗi: " + (d.error || ""), "err");
            if (btnStart) btnStart.disabled = false;
        }
    } catch (e) {
        log("[rewrite] Lỗi: " + e.message, "err");
        if (btnStart) btnStart.disabled = false;
    }
}

async function runRemixStep(step) {
    if (S.running) {
        log("[rewrite] Pipeline đang chạy.", "warn");
        return;
    }
    const ytUrl = ($("inp-yt-url")?.value || "").trim();
    if (!ytUrl && step === "extract") {
        log("[rewrite] Vui lòng nhập URL YouTube.", "warn");
        return;
    }

    const body = {
        youtube_url: ytUrl,
        youtube_data: _ytData && _ytData.ok ? _ytData : null,
        target_language: $("sel-remix-lang")?.value || "English",
        video_style_name: $("sel-remix-vstyle")?.value || "",
        model: $("sel-remix-model")?.value || "",
        model_analyze: $("sel-remix-model-analyze")?.value || "",
        model_video: $("sel-remix-model-video")?.value || "",
        start_step: step,
        _original_text: _ytData?.subtitles_text || _ytData?.description || "",
        _video_title: _ytData?.title || "",
    };

    // Pass existing content/segments for later steps
    const scriptOut = $("remix-script-output");
    if (step === "split" || step === "video") {
        body._script = scriptOut?.value || "";
    }

    const pa = $("remix-progress-area"); if (pa) pa.classList.remove("hidden");
    log(`[rewrite] Chạy bước: ${step}...`);

    try {
        const r = await fetch("/api/pipeline/rewrite", {
            method: "POST",
            headers: CT_JSON,
            body: JSON.stringify(body),
        });
        const d = await r.json();
        if (!d.ok) log("[rewrite] Lỗi: " + (d.error || ""), "err");
    } catch (e) {
        log("[rewrite] Lỗi: " + e.message, "err");
    }
}

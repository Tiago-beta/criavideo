const IS_CAPACITOR_APP = typeof window !== "undefined" && !!window.Capacitor;
const API = IS_CAPACITOR_APP ? "https://criavideo.pro/api" : "/api";
const APP_TOKEN_KEY = "criavideo_token";
const LEVITA_TOKEN_KEY = "levita_token";

let token = localStorage.getItem(APP_TOKEN_KEY) || "";
let levitaToken = localStorage.getItem(LEVITA_TOKEN_KEY) || "";
let currentUser = null;
let providers = {
    google_enabled: false,
    google_client_id: "",
    levita_url: "https://levita.pro",
};
let authMode = "login";
let levitaSongs = [];

function getApiErrorMessage(body, fallback = "Erro inesperado") {
    if (!body) {
        return fallback;
    }

    const detail = body.detail ?? body.message ?? body.error ?? body;
    if (typeof detail === "string") {
        return detail;
    }

    if (Array.isArray(detail)) {
        const messages = detail.map((item) => {
            if (typeof item === "string") {
                return item;
            }
            if (item && typeof item === "object") {
                const location = Array.isArray(item.loc) ? `${item.loc.join(".")}: ` : "";
                return `${location}${item.msg || "Erro de validacao"}`;
            }
            return String(item);
        });
        return messages.join(" | ");
    }

    if (detail && typeof detail === "object") {
        return detail.message || JSON.stringify(detail);
    }

    return fallback;
}

function getHeaders(extra = {}) {
    return {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...extra,
    };
}

async function api(path, options = {}) {
    const response = await fetch(`${API}${path}`, {
        ...options,
        headers: getHeaders(options.headers || {}),
    });
    if (response.status === 401) {
        clearSession();
        showAuth("Sua sessao expirou. Entre novamente.");
        throw new Error("Unauthorized");
    }
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(getApiErrorMessage(data, response.statusText || "Erro inesperado"));
    }
    if (response.status === 204) {
        return null;
    }
    return response.json();
}

function setSession(accessToken, user, rawLevitaToken = null) {
    token = accessToken;
    currentUser = user;
    localStorage.setItem(APP_TOKEN_KEY, accessToken);
    if (rawLevitaToken) {
        levitaToken = rawLevitaToken;
        localStorage.setItem(LEVITA_TOKEN_KEY, rawLevitaToken);
    }
    renderSession();
}

function clearSession() {
    token = "";
    currentUser = null;
    localStorage.removeItem(APP_TOKEN_KEY);
}

function clearLevitaSession() {
    levitaToken = "";
    localStorage.removeItem(LEVITA_TOKEN_KEY);
}

function renderSession() {
    if (!currentUser) {
        return;
    }
    const sessionName = document.getElementById("session-name");
    const sessionMeta = document.getElementById("session-meta");
    if (sessionName) {
        sessionName.textContent = currentUser.name || "Cliente";
    }
    if (sessionMeta) {
        const sourceLabel =
            currentUser.source === "levita"
                ? "Levita"
                : currentUser.source === "google"
                    ? "Google"
                    : "Local";
        sessionMeta.textContent = sourceLabel;
    }
    // Profile page
    const profileName = document.getElementById("profile-name");
    const profileEmail = document.getElementById("profile-email");
    const profileBadge = document.getElementById("profile-role-badge");
    if (profileName) profileName.textContent = currentUser.name || "Usuario";
    if (profileEmail) profileEmail.textContent = currentUser.email || "-";
    if (profileBadge) {
        const src = currentUser.source === "levita" ? "Levita" : currentUser.source === "google" ? "Google" : "Local";
        profileBadge.textContent = src;
    }
}

function showAuth(message = "") {
    document.getElementById("auth-shell").hidden = false;
    document.getElementById("app-shell").hidden = true;
    setAuthStatus(message);
}

function showApp() {
    document.getElementById("auth-shell").hidden = true;
    document.getElementById("app-shell").hidden = false;
    renderSession();
}

function setAuthStatus(message = "") {
    const status = document.getElementById("auth-status");
    if (!message) {
        status.hidden = true;
        status.textContent = "";
        return;
    }
    status.hidden = false;
    status.textContent = message;
}

function setAuthMode(mode) {
    authMode = mode;
    const isLogin = mode === "login";
    document.getElementById("login-form").hidden = !isLogin;
    document.getElementById("register-form").hidden = isLogin;
    document.getElementById("auth-title").textContent = isLogin ? "Entrar" : "Criar conta";
    document.getElementById("auth-subtitle").textContent = isLogin
        ? "Acesse seus projetos e publique em multiplos canais."
        : "Crie sua conta para receber clientes e gerar videos fora do Levita.";
    document.getElementById("auth-switch-copy").textContent = isLogin ? "Nao tem conta?" : "Ja tem conta?";
    document.getElementById("auth-switch-button").textContent = isLogin ? "Criar conta" : "Entrar";
    setAuthStatus("");
}

function cleanUrlTokenParam() {
    const url = new URL(window.location.href);
    url.searchParams.delete("token");
    window.history.replaceState({}, "", url.pathname + url.search + url.hash);
}

async function loadProviders() {
    try {
        providers = await fetch("/api/auth/providers").then((response) => response.json());
    } catch (_) {
        providers = { ...providers };
    }
    initGoogleLogin();
}

function initGoogleLogin(remainingAttempts = 20) {
    if (!providers.google_enabled || !providers.google_client_id) {
        return;
    }
    if (window.google?.accounts?.id) {
        document.getElementById("google-login").hidden = false;
        window.google.accounts.id.initialize({
            client_id: providers.google_client_id,
            callback: handleGoogleCredential,
        });
        window.google.accounts.id.renderButton(
            document.getElementById("google-login-button"),
            { type: "standard", theme: "outline", size: "large", text: "signin_with", shape: "pill", width: 320 },
        );
        return;
    }
    if (remainingAttempts > 0) {
        window.setTimeout(() => initGoogleLogin(remainingAttempts - 1), 300);
    }
}

async function handleGoogleCredential(response) {
    try {
        const data = await fetch("/api/auth/google", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ credential: response.credential }),
        }).then(async (resp) => {
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(getApiErrorMessage(err, "Falha ao entrar com Google"));
            }
            return resp.json();
        });
        clearLevitaSession();
        setSession(data.access_token, data.user);
        cleanUrlTokenParam();
        showApp();
        initDashboard();
    } catch (error) {
        showAuth(error.message);
    }
}

async function hydrateSession() {
    if (!token) {
        return false;
    }
    try {
        const data = await api("/auth/me");
        currentUser = data.user;
        renderSession();
        return true;
    } catch (_) {
        return false;
    }
}

async function exchangeLevitaToken(rawToken) {
    const response = await fetch("/api/auth/exchange/levita", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: rawToken }),
    });
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(getApiErrorMessage(error, "Nao foi possivel validar o login do Levita"));
    }
    const data = await response.json();
    setSession(data.access_token, data.user, rawToken);
    cleanUrlTokenParam();
}

function redirectToLevita() {
    const redirect = encodeURIComponent(`${window.location.origin}/video`);
    window.location.href = `${providers.levita_url || "https://levita.pro"}/?redirect=${redirect}`;
}

async function loginWithLevitaCredentials(email, password) {
    const response = await fetch("/api/auth/login/levita", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
    });

    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(getApiErrorMessage(body, "Nao foi possivel entrar com credenciais do Levita"));
    }

    return body;
}

function bindAuthEvents() {
    document.getElementById("auth-switch-button").addEventListener("click", () => {
        setAuthMode(authMode === "login" ? "register" : "login");
    });
    document.getElementById("btn-levita-login").addEventListener("click", () => {
        redirectToLevita();
    });
    document.getElementById("login-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        setAuthStatus("");
        const email = document.getElementById("login-email").value;
        const password = document.getElementById("login-password").value;
        try {
            let data;
            try {
                data = await fetch("/api/auth/login", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ email, password }),
                }).then(async (resp) => {
                    const body = await resp.json().catch(() => ({}));
                    if (!resp.ok) {
                        throw new Error(getApiErrorMessage(body, "Falha ao entrar"));
                    }
                    return body;
                });
                clearLevitaSession();
            } catch (localError) {
                // Fallback: try Levita credentials directly to avoid forcing an external redirect.
                data = await loginWithLevitaCredentials(email, password);
            }

            setSession(data.access_token, data.user);
            showApp();
            initDashboard();
        } catch (error) {
            setAuthStatus(error.message);
        }
    });
    document.getElementById("register-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        setAuthStatus("");
        try {
            const data = await fetch("/api/auth/register", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    name: document.getElementById("register-name").value,
                    email: document.getElementById("register-email").value,
                    password: document.getElementById("register-password").value,
                }),
            }).then(async (resp) => {
                const body = await resp.json().catch(() => ({}));
                if (!resp.ok) {
                    throw new Error(getApiErrorMessage(body, "Falha ao criar conta"));
                }
                return body;
            });
            clearLevitaSession();
            setSession(data.access_token, data.user);
            showApp();
            initDashboard();
        } catch (error) {
            setAuthStatus(error.message);
        }
    });
    document.getElementById("btn-logout").addEventListener("click", () => {
        clearSession();
        clearLevitaSession();
        showAuth("Sessao encerrada.");
    });
}

function navigateTo(pageName) {
    document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
    const target = document.getElementById("page-" + pageName);
    if (target) target.classList.add("active");
    // Update sidebar active
    document.querySelectorAll(".sidebar-nav .nav-item").forEach((item) => {
        item.classList.toggle("active", item.dataset.page === pageName);
    });
    // Update mobile tabs active
    document.querySelectorAll(".mobile-nav-tab").forEach((tab) => {
        tab.classList.toggle("active", tab.dataset.mobilePage === pageName);
    });
    loadPageData(pageName);
}

function bindNavigation() {
    // Sidebar nav items
    document.querySelectorAll(".sidebar-nav .nav-item").forEach((link) => {
        link.addEventListener("click", (event) => {
            event.preventDefault();
            navigateTo(link.dataset.page);
        });
    });
    // Mobile bottom tabs
    document.querySelectorAll(".mobile-nav-tab").forEach((tab) => {
        tab.addEventListener("click", () => {
            navigateTo(tab.dataset.mobilePage);
        });
    });
    // Profile logout button
    const profileLogout = document.getElementById("btn-profile-logout");
    if (profileLogout) {
        profileLogout.addEventListener("click", () => {
            clearSession();
            clearLevitaSession();
            showAuth("Sessao encerrada.");
        });
    }
}

function bindDashboardEvents() {
    document.getElementById("btn-new-project").addEventListener("click", () => {
        resetCreateWizard();
        openModal("modal-new-project");
    });
    document.getElementById("btn-publish").addEventListener("click", async () => {
        const renderId = document.getElementById("pub-render-select").value;
        if (!renderId) {
            alert("Selecione um video");
            return;
        }
        const platforms = [];
        document.querySelectorAll("#publish-form-area .checkbox-group input:checked").forEach((checkbox) => {
            platforms.push(checkbox.value);
        });
        if (!platforms.length) {
            alert("Selecione pelo menos uma plataforma");
            return;
        }
        try {
            await api("/publish/", {
                method: "POST",
                body: JSON.stringify({
                    render_id: parseInt(renderId, 10),
                    platforms,
                    title: document.getElementById("pub-title").value,
                    description: document.getElementById("pub-description").value,
                }),
            });
            alert("Publicacao iniciada.");
            loadPublishJobs();
        } catch (error) {
            alert(`Erro: ${error.message}`);
        }
    });
    document.getElementById("btn-new-schedule").addEventListener("click", async () => {
        await loadAccountsForSelect();
        openModal("modal-new-schedule");
    });
    document.addEventListener("change", (event) => {
        if (event.target.id !== "np-song-select") {
            return;
        }
        const value = event.target.value;
        const manualFields = document.getElementById("np-manual-fields");
        const details = document.getElementById("np-song-details");
        if (value === "manual") {
            manualFields.hidden = false;
            details.hidden = true;
            return;
        }
        const selectedSong = levitaSongs[parseInt(value, 10)];
        if (value !== "" && selectedSong) {
            manualFields.hidden = true;
            details.hidden = false;
            details.innerHTML = `
                <p><strong>${esc(selectedSong.title || "Sem titulo")}</strong></p>
                ${selectedSong.artist ? `<p>${esc(selectedSong.artist)}</p>` : ""}
                ${selectedSong.duration ? `<p>${Math.round(selectedSong.duration)}s</p>` : ""}
                ${selectedSong.lyrics ? `<p>${esc(selectedSong.lyrics).slice(0, 280)}...</p>` : ""}
            `;
            return;
        }
        manualFields.hidden = true;
        details.hidden = true;
    });

    // ── Creation Wizard Event Bindings ──
    initCreateWizard();
}

async function bootstrap() {
    setAuthMode("login");
    bindAuthEvents();
    bindNavigation();
    bindDashboardEvents();
    await loadProviders();
    const params = new URLSearchParams(window.location.search);
    const levitaUrlToken = params.get("token");
    if (levitaUrlToken) {
        try {
            await exchangeLevitaToken(levitaUrlToken.trim());
        } catch (error) {
            showAuth(error.message);
            return;
        }
    }
    const authenticated = await hydrateSession();
    if (!authenticated) {
        showAuth();
        return;
    }
    showApp();
    initDashboard();
}

function initDashboard() {
    renderSession();
    const params = new URLSearchParams(window.location.search);
    const audioUrl = params.get("audio_url");
    if (audioUrl) {
        window.history.replaceState({}, "", window.location.pathname);
        quickCreate({
            song_title: params.get("song_title") || "",
            song_artist: params.get("song_artist") || "",
            audio_url: audioUrl,
            lyrics: params.get("lyrics") || "",
            duration: parseFloat(params.get("duration")) || 180,
            aspect_ratio: params.get("aspect") || "16:9",
        });
        return;
    }
    loadProjects();
}

function loadPageData(page) {
    if (page === "projects") {
        loadProjects();
    } else if (page === "publish") {
        loadRenders();
        loadPublishJobs();
    } else if (page === "schedule") {
        loadSchedules();
    } else if (page === "accounts") {
        loadAccounts();
    }
}

function openModal(id) {
    document.getElementById(id).classList.add("open");
}

function closeModal(id) {
    document.getElementById(id).classList.remove("open");
    if (id === "modal-player") {
        const video = document.getElementById("player-video");
        if (video) {
            video.pause();
            video.src = "";
        }
    }
}

async function loadLevitaSongs() {
    const levitaAuthToken = levitaToken || token;
    if (!levitaAuthToken) {
        levitaSongs = [];
        return [];
    }
    try {
        const response = await fetch(`${providers.levita_url || "https://levita.pro"}/api/feed/my-created-music`, {
            headers: { Authorization: `Bearer ${levitaAuthToken}` },
        });
        if (!response.ok) {
            return [];
        }
        const data = await response.json();
        levitaSongs = data.songs || [];
        return levitaSongs;
    } catch (_) {
        levitaSongs = [];
        return [];
    }
}

async function populateSongSelector() {
    const select = document.getElementById("np-song-select");
    const details = document.getElementById("np-song-details");
    details.hidden = true;
    select.innerHTML = "<option value=''>Carregando...</option>";
    const songs = await loadLevitaSongs();
    const baseOptions = ["<option value=''>Selecione uma musica</option>", "<option value='manual'>Inserir manualmente</option>"];
    if (!songs.length) {
        select.innerHTML = baseOptions.join("");
        document.getElementById("np-manual-fields").hidden = false;
        return;
    }
    document.getElementById("np-manual-fields").hidden = true;
    select.innerHTML = baseOptions.join("") + songs.map((song, index) => {
        const artist = song.artist ? ` - ${esc(song.artist)}` : "";
        return `<option value="${index}">${esc(song.title || "Sem titulo")}${artist}</option>`;
    }).join("");
}

async function loadProjects() {
    const container = document.getElementById("projects-list");
    try {
        const data = await api("/video/projects");
        if (!data.length) {
            container.innerHTML = "<p class='loading'>Nenhum projeto ainda. Crie o primeiro.</p>";
            return;
        }
        container.innerHTML = data.map((project) => {
            const created = project.created_at ? new Date(project.created_at).toLocaleDateString("pt-BR") : "-";
            return `
                <div class="card">
                    <h4>${esc(project.title)}</h4>
                    <p>${esc(project.track_title || "")} ${project.track_artist ? `- ${esc(project.track_artist)}` : ""}</p>
                    <p>${project.aspect_ratio} · ${project.created_at ? created : "Sem data"}</p>
                    <p><span class="badge badge-${badgeClass(project.status)}">${esc(project.status)}</span></p>
                    ${project.progress != null ? `<div class="progress-bar"><div class="progress-bar-fill" style="width:${project.progress}%"></div></div>` : ""}
                    ${project.error_message ? `<p style="color: var(--danger);">${esc(project.error_message)}</p>` : ""}
                    <div class="card-actions">
                        ${project.status === "completed" ? `<button class="btn btn-secondary btn-sm" onclick="watchVideo(${project.id})" type="button">Assistir</button>` : ""}
                        ${(project.status === "pending" || project.status === "failed") ? `<button class="btn btn-primary btn-sm" onclick="generateVideo(${project.id})" type="button">Gerar video</button>` : ""}
                        <button class="btn btn-provider btn-sm" onclick="deleteProject(${project.id})" type="button">Excluir</button>
                    </div>
                </div>
            `;
        }).join("");
    } catch (error) {
        container.innerHTML = `<p class="loading">Erro: ${esc(error.message)}</p>`;
    }
}

// ═══ Creation Wizard State ═══
let createMode = "wizard"; // "wizard" | "script" | "library"
let wizardStep = 1;
let wizardData = { topic: "", tone: "", voice: "", duration: 60, aspect: "16:9", style: "" };
let scriptStep = 1;
let scriptData = { text: "", voice: "", title: "", aspect: "16:9", style: "" };

function initCreateWizard() {
    // Tab switching
    document.querySelectorAll(".create-tab").forEach((tab) => {
        tab.addEventListener("click", () => switchCreateMode(tab.dataset.createMode));
    });

    // Wizard nav
    document.getElementById("wizard-next").addEventListener("click", wizardNext);
    document.getElementById("wizard-back").addEventListener("click", wizardBack);
    document.getElementById("wizard-create-btn").addEventListener("click", handleWizardCreate);

    // Script nav
    document.getElementById("script-next").addEventListener("click", scriptNext);
    document.getElementById("script-back").addEventListener("click", scriptBack);
    document.getElementById("script-create-btn").addEventListener("click", handleScriptCreate);

    // Script char count
    document.getElementById("script-text").addEventListener("input", () => {
        const len = document.getElementById("script-text").value.length;
        document.getElementById("script-char-count").textContent = len.toLocaleString("pt-BR");
    });

    // AI suggestion buttons
    document.getElementById("btn-ai-suggest-script").addEventListener("click", showAiSuggestPanel);
    document.getElementById("ai-suggest-cancel").addEventListener("click", hideAiSuggestPanel);
    document.getElementById("ai-suggest-generate").addEventListener("click", generateAiScript);

    // Wizard option clicks (event delegation)
    document.getElementById("modal-new-project").addEventListener("click", (e) => {
        const opt = e.target.closest(".wizard-option");
        if (opt) {
            const grid = opt.closest(".wizard-grid");
            grid.querySelectorAll(".wizard-option").forEach((o) => o.classList.remove("selected"));
            opt.classList.add("selected");
        }
        const dur = e.target.closest(".duration-option");
        if (dur) {
            dur.closest(".duration-options").querySelectorAll(".duration-option").forEach((d) => d.classList.remove("selected"));
            dur.classList.add("selected");
        }
    });
}

function switchCreateMode(mode) {
    createMode = mode;
    document.querySelectorAll(".create-tab").forEach((t) => {
        t.classList.toggle("active", t.dataset.createMode === mode);
    });
    document.querySelectorAll(".create-panel").forEach((p) => (p.hidden = true));
    const panel = document.getElementById(`create-panel-${mode}`);
    if (panel) panel.hidden = false;
    document.getElementById("ai-suggest-panel").hidden = true;
    document.getElementById("create-progress").hidden = true;

    if (mode === "library") {
        populateSongSelector();
    }
}

function resetCreateWizard() {
    createMode = "wizard";
    wizardStep = 1;
    wizardData = { topic: "", tone: "", voice: "", duration: 60, aspect: "16:9", style: "" };
    scriptStep = 1;
    scriptData = { text: "", voice: "", title: "", aspect: "16:9", style: "" };

    // Reset tabs
    document.querySelectorAll(".create-tab").forEach((t) => {
        t.classList.toggle("active", t.dataset.createMode === "wizard");
    });

    // Reset panels
    document.querySelectorAll(".create-panel").forEach((p) => (p.hidden = true));
    document.getElementById("create-panel-wizard").hidden = false;
    document.getElementById("ai-suggest-panel").hidden = true;
    document.getElementById("create-progress").hidden = true;

    // Reset wizard steps
    updateWizardUI("create-panel-wizard", wizardStep, 4, "wizard");
    updateWizardUI("create-panel-script", scriptStep, 3, "script");

    // Reset inputs
    document.getElementById("wizard-topic").value = "";
    document.getElementById("script-text").value = "";
    document.getElementById("script-char-count").textContent = "0";
    document.getElementById("script-title").value = "";

    // Reset selections
    document.querySelectorAll(".wizard-option.selected").forEach((o) => o.classList.remove("selected"));
    document.querySelectorAll(".duration-option").forEach((d) => {
        d.classList.toggle("selected", d.dataset.value === "60");
    });
}

function updateWizardUI(panelId, step, totalSteps, prefix) {
    const panel = document.getElementById(panelId);
    panel.querySelectorAll(".wizard-step").forEach((s) => {
        s.hidden = parseInt(s.dataset.step) !== step;
    });
    panel.querySelectorAll(".wizard-dot").forEach((dot, i) => {
        dot.classList.toggle("active", i < step);
    });
    const backBtn = document.getElementById(`${prefix}-back`);
    const nextBtn = document.getElementById(`${prefix}-next`);
    const createBtn = document.getElementById(`${prefix}-create-btn`);
    if (backBtn) backBtn.hidden = step <= 1;
    if (nextBtn) nextBtn.hidden = step >= totalSteps;
    if (createBtn) createBtn.hidden = step < totalSteps;
}

// ── Wizard (Assistente) Navigation ──

function wizardNext() {
    if (wizardStep === 1) {
        const topic = document.getElementById("wizard-topic").value.trim();
        if (!topic) { alert("Digite o tema do video."); return; }
        wizardData.topic = topic;
    }
    if (wizardStep === 2) {
        const sel = document.querySelector("#create-panel-wizard .wizard-step[data-step='2'] .wizard-option.selected");
        if (!sel) { alert("Escolha o tom da narracao."); return; }
        wizardData.tone = sel.dataset.value;
    }
    if (wizardStep === 3) {
        const sel = document.querySelector("#create-panel-wizard .wizard-step[data-step='3'] .wizard-option.selected");
        if (!sel) { alert("Escolha a voz."); return; }
        wizardData.voice = sel.dataset.value;
    }
    wizardStep = Math.min(wizardStep + 1, 4);
    updateWizardUI("create-panel-wizard", wizardStep, 4, "wizard");
}

function wizardBack() {
    wizardStep = Math.max(wizardStep - 1, 1);
    updateWizardUI("create-panel-wizard", wizardStep, 4, "wizard");
}

async function handleWizardCreate() {
    // Collect step 4 data
    const durBtn = document.querySelector("#create-panel-wizard .duration-option.selected");
    wizardData.duration = durBtn ? parseInt(durBtn.dataset.value) : 60;
    wizardData.aspect = document.getElementById("wizard-aspect").value;
    wizardData.style = document.getElementById("wizard-style").value;

    showCreateProgress("Gerando roteiro com IA...");

    try {
        // Step 1: Generate script
        const scriptResult = await api("/video/generate-script", {
            method: "POST",
            body: JSON.stringify({
                topic: wizardData.topic,
                tone: wizardData.tone,
                duration_seconds: wizardData.duration,
            }),
        });

        showCreateProgress("Gerando narracao com voz IA...");

        // Step 2: Generate audio + create project
        const result = await api("/video/generate-audio", {
            method: "POST",
            body: JSON.stringify({
                script: scriptResult.script,
                voice: wizardData.voice,
                title: wizardData.topic,
                aspect_ratio: wizardData.aspect,
                style_prompt: wizardData.style,
            }),
        });

        closeModal("modal-new-project");
        pollProject(result.id);
        loadProjects();
    } catch (error) {
        hideCreateProgress();
        alert(`Erro: ${error.message}`);
    }
}

// ── Script (Meu Roteiro) Navigation ──

function scriptNext() {
    if (scriptStep === 1) {
        const text = document.getElementById("script-text").value.trim();
        if (!text || text.length < 20) { alert("Escreva um roteiro com pelo menos 20 caracteres."); return; }
        scriptData.text = text;
    }
    if (scriptStep === 2) {
        const sel = document.querySelector("#create-panel-script .wizard-step[data-step='2'] .wizard-option.selected");
        if (!sel) { alert("Escolha a voz."); return; }
        scriptData.voice = sel.dataset.value;
    }
    scriptStep = Math.min(scriptStep + 1, 3);
    updateWizardUI("create-panel-script", scriptStep, 3, "script");
}

function scriptBack() {
    scriptStep = Math.max(scriptStep - 1, 1);
    updateWizardUI("create-panel-script", scriptStep, 3, "script");
}

async function handleScriptCreate() {
    scriptData.title = document.getElementById("script-title").value.trim();
    scriptData.aspect = document.getElementById("script-aspect").value;
    scriptData.style = document.getElementById("script-style").value;

    showCreateProgress("Gerando narracao com voz IA...");

    try {
        const result = await api("/video/generate-audio", {
            method: "POST",
            body: JSON.stringify({
                script: scriptData.text,
                voice: scriptData.voice,
                title: scriptData.title || "Video com roteiro",
                aspect_ratio: scriptData.aspect,
                style_prompt: scriptData.style,
            }),
        });

        closeModal("modal-new-project");
        pollProject(result.id);
        loadProjects();
    } catch (error) {
        hideCreateProgress();
        alert(`Erro: ${error.message}`);
    }
}

// ── AI Script Suggestion ──

function showAiSuggestPanel() {
    document.getElementById("create-panel-script").hidden = true;
    document.getElementById("ai-suggest-panel").hidden = false;
}

function hideAiSuggestPanel() {
    document.getElementById("ai-suggest-panel").hidden = true;
    document.getElementById("create-panel-script").hidden = false;
}

async function generateAiScript() {
    const topic = document.getElementById("ai-suggest-topic").value.trim();
    if (!topic) { alert("Digite o tema do video."); return; }

    showCreateProgress("Gerando roteiro com IA...");

    try {
        const result = await api("/video/generate-script", {
            method: "POST",
            body: JSON.stringify({
                topic,
                tone: document.getElementById("ai-suggest-tone").value,
                duration_seconds: parseInt(document.getElementById("ai-suggest-duration").value),
            }),
        });

        hideCreateProgress();
        document.getElementById("script-text").value = result.script;
        document.getElementById("script-char-count").textContent = result.script.length.toLocaleString("pt-BR");
        hideAiSuggestPanel();
    } catch (error) {
        hideCreateProgress();
        alert(`Erro ao gerar roteiro: ${error.message}`);
    }
}

// ── Progress helpers ──

function showCreateProgress(message) {
    document.querySelectorAll(".create-panel").forEach((p) => (p.hidden = true));
    document.getElementById("ai-suggest-panel").hidden = true;
    document.getElementById("create-progress").hidden = false;
    document.getElementById("create-progress-text").textContent = message;
}

function hideCreateProgress() {
    document.getElementById("create-progress").hidden = true;
    const panel = document.getElementById(`create-panel-${createMode}`);
    if (panel) panel.hidden = false;
}

// ── Library (existing flow, renamed) ──

async function createProjectFromLibrary() {
    const songValue = document.getElementById("np-song-select").value;
    let trackTitle = "";
    let trackArtist = "";
    let audioPath = "";
    let lyricsText = "";
    let trackDuration = 180;
    if (songValue === "manual") {
        trackTitle = document.getElementById("np-track-title").value;
        trackArtist = document.getElementById("np-artist").value;
        audioPath = document.getElementById("np-audio").value;
        lyricsText = document.getElementById("np-lyrics").value;
        trackDuration = parseInt(document.getElementById("np-duration").value, 10) || 180;
    } else if (songValue !== "" && levitaSongs[parseInt(songValue, 10)]) {
        const song = levitaSongs[parseInt(songValue, 10)];
        trackTitle = song.title || "";
        trackArtist = song.artist || "";
        audioPath = `${providers.levita_url || "https://levita.pro"}${song.audio_url}`;
        lyricsText = song.lyrics || "";
        trackDuration = Math.round(song.duration) || 180;
    } else {
        alert("Selecione uma musica ou use o modo manual.");
        return;
    }
    try {
        await api("/video/projects", {
            method: "POST",
            body: JSON.stringify({
                title: document.getElementById("np-title").value || trackTitle,
                track_title: trackTitle,
                track_artist: trackArtist,
                audio_path: audioPath,
                lyrics_text: lyricsText,
                track_duration: trackDuration,
                aspect_ratio: document.getElementById("np-aspect").value,
                style_prompt: document.getElementById("np-style").value,
            }),
        });
        closeModal("modal-new-project");
        loadProjects();
    } catch (error) {
        alert(`Erro ao criar projeto: ${error.message}`);
    }
}

async function generateVideo(id) {
    try {
        await api(`/video/projects/${id}/generate`, { method: "POST" });
        loadProjects();
        const poll = setInterval(async () => {
            try {
                const project = await api(`/video/projects/${id}`);
                loadProjects();
                if (project.status === "completed" || project.status === "failed") {
                    clearInterval(poll);
                }
            } catch (_) {
                clearInterval(poll);
            }
        }, 5000);
    } catch (error) {
        alert(`Erro: ${error.message}`);
    }
}

async function deleteProject(id) {
    if (!window.confirm("Excluir este projeto?")) {
        return;
    }
    try {
        await api(`/video/projects/${id}`, { method: "DELETE" });
        loadProjects();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    }
}

async function watchVideo(projectId) {
    try {
        const project = await api(`/video/projects/${projectId}`);
        if (!project.renders || !project.renders.length) {
            alert("Nenhum video renderizado encontrado.");
            return;
        }
        const render = project.renders[0];
        document.getElementById("player-title").textContent = project.title || "Video";
        const video = document.getElementById("player-video");
        video.src = render.video_url;
        video.load();
        const sizeMb = render.file_size ? `${(render.file_size / 1048576).toFixed(1)} MB` : "";
        const duration = render.duration ? `${Math.floor(render.duration / 60)}:${String(Math.floor(render.duration % 60)).padStart(2, "0")}` : "";
        document.getElementById("player-info").textContent = [render.format, duration, sizeMb].filter(Boolean).join(" · ");
        const download = document.getElementById("player-download");
        download.href = render.video_url;
        download.download = `${project.title || "video"}.mp4`;
        openModal("modal-player");
    } catch (error) {
        alert(`Erro ao carregar video: ${error.message}`);
    }
}

async function loadRenders() {
    try {
        const projects = await api("/video/projects");
        const select = document.getElementById("pub-render-select");
        select.innerHTML = "<option value=''>Selecione...</option>";
        for (const project of projects) {
            if (project.status !== "completed") {
                continue;
            }
            try {
                const detail = await api(`/video/projects/${project.id}`);
                for (const render of detail.renders || []) {
                    const duration = render.duration != null
                        ? `${Math.floor(render.duration / 60)}:${String(Math.round(render.duration % 60)).padStart(2, "0")}`
                        : "?";
                    select.innerHTML += `<option value="${render.id}">[${esc(project.title)}] ${render.format} - ${duration}</option>`;
                }
            } catch (_) {
                // ignore one broken project and continue
            }
        }
    } catch (_) {
        // keep select empty if request fails
    }
}

async function loadPublishJobs() {
    const container = document.getElementById("publish-jobs-list");
    try {
        const jobs = await api("/publish/jobs");
        if (!jobs.length) {
            container.innerHTML = "<p class='loading'>Nenhuma publicacao ainda.</p>";
            return;
        }
        container.innerHTML = `
            <table>
                <tr><th>ID</th><th>Plataforma</th><th>Status</th><th>URL</th><th>Data</th></tr>
                ${jobs.map((job) => `
                    <tr>
                        <td>${job.id}</td>
                        <td>${esc(job.platform)}</td>
                        <td><span class="badge badge-${badgeClass(job.status)}">${esc(job.status)}</span></td>
                        <td>${job.platform_url ? `<a href="${esc(job.platform_url)}" target="_blank" rel="noreferrer">Ver</a>` : "-"}</td>
                        <td>${job.published_at ? new Date(job.published_at).toLocaleString("pt-BR") : "-"}</td>
                    </tr>
                `).join("")}
            </table>
        `;
    } catch (error) {
        container.innerHTML = `<p class="loading">Erro: ${esc(error.message)}</p>`;
    }
}

async function loadAccountsForSelect() {
    try {
        const data = await api("/social/accounts");
        const select = document.getElementById("ns-account");
        if (!data.length) {
            select.innerHTML = "<option value=''>Conecte uma conta primeiro</option>";
            return;
        }
        select.innerHTML = data.map((account) => (
            `<option value="${account.id}">${esc(account.platform)} - ${esc(account.platform_username || "Conta conectada")}</option>`
        )).join("");
    } catch (_) {
        // ignore modal preload errors
    }
}

async function loadSchedules() {
    const container = document.getElementById("schedules-list");
    try {
        const schedules = await api("/schedule/");
        if (!schedules.length) {
            container.innerHTML = "<p class='loading'>Nenhum agendamento.</p>";
            return;
        }
        container.innerHTML = schedules.map((schedule) => `
            <div class="card">
                <h4>${esc(schedule.platform)} - ${esc(schedule.frequency)}</h4>
                <p>${esc(schedule.time_utc)} UTC</p>
                <p>Fila: ${schedule.queue_length || 0} videos</p>
                <p>Status: ${schedule.is_active ? '<span class="badge badge-completed">Ativo</span>' : '<span class="badge badge-failed">Pausado</span>'}</p>
                <div class="card-actions">
                    <button class="btn btn-secondary btn-sm" onclick="toggleSchedule(${schedule.id})" type="button">${schedule.is_active ? "Pausar" : "Ativar"}</button>
                    <button class="btn btn-provider btn-sm" onclick="deleteSchedule(${schedule.id})" type="button">Excluir</button>
                </div>
            </div>
        `).join("");
    } catch (error) {
        container.innerHTML = `<p class="loading">Erro: ${esc(error.message)}</p>`;
    }
}

async function createSchedule() {
    try {
        await api("/schedule/", {
            method: "POST",
            body: JSON.stringify({
                platform: document.getElementById("ns-platform").value,
                social_account_id: parseInt(document.getElementById("ns-account").value, 10),
                frequency: document.getElementById("ns-frequency").value,
                time_utc: document.getElementById("ns-time").value,
            }),
        });
        closeModal("modal-new-schedule");
        loadSchedules();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    }
}

async function toggleSchedule(id) {
    try {
        await api(`/schedule/${id}`, { method: "PATCH" });
        loadSchedules();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    }
}

async function deleteSchedule(id) {
    if (!window.confirm("Excluir agendamento?")) {
        return;
    }
    try {
        await api(`/schedule/${id}`, { method: "DELETE" });
        loadSchedules();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    }
}

async function connectPlatform(platform) {
    try {
        const data = await api(`/social/connect/${platform}`);
        if (!data.auth_url) {
            throw new Error("A plataforma nao retornou URL de autorizacao");
        }
        window.location.href = data.auth_url;
    } catch (error) {
        alert(`Erro ao conectar conta: ${error.message}`);
    }
}

async function loadAccounts() {
    const container = document.getElementById("accounts-list");
    try {
        const accounts = await api("/social/accounts");
        if (!accounts.length) {
            container.innerHTML = "<p class='loading'>Nenhuma conta conectada.</p>";
            return;
        }
        container.innerHTML = accounts.map((account) => `
            <div class="card">
                <h4>${esc(account.platform)}</h4>
                <p>${esc(account.platform_username || "Conta conectada")}</p>
                <div class="card-actions">
                    <button class="btn btn-provider btn-sm" onclick="disconnectAccount(${account.id})" type="button">Desconectar</button>
                </div>
            </div>
        `).join("");
    } catch (error) {
        container.innerHTML = `<p class="loading">Erro: ${esc(error.message)}</p>`;
    }
}

async function disconnectAccount(id) {
    if (!window.confirm("Desconectar esta conta?")) {
        return;
    }
    try {
        await api(`/social/accounts/${id}`, { method: "DELETE" });
        loadAccounts();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    }
}

async function quickCreate(songData) {
    const container = document.getElementById("projects-list");
    container.innerHTML = `
        <div class="card" style="text-align:center;">
            <h3>Preparando seu video...</h3>
            <p>${esc(songData.song_title || "Sua musica")}</p>
            <p>Estamos montando o projeto e gerando o estilo visual.</p>
            <div class="progress-bar"><div class="progress-bar-fill" style="width: 8%;"></div></div>
        </div>
    `;
    try {
        const result = await api("/video/quick-create", {
            method: "POST",
            body: JSON.stringify(songData),
        });
        container.innerHTML = `
            <div class="card" style="text-align:center;">
                <h3>Projeto criado</h3>
                <p><strong>${esc(result.title)}</strong></p>
                <p>${esc(result.description || "")}</p>
                <p>${esc(result.style_prompt || "")}</p>
                <div class="progress-bar"><div id="qc-progress" class="progress-bar-fill" style="width: 10%;"></div></div>
                <p id="qc-status" style="margin-top: 0.75rem; color: var(--accent-strong);">Gerando cenas...</p>
            </div>
        `;
        pollProject(result.id);
    } catch (error) {
        container.innerHTML = `
            <div class="card" style="text-align:center;">
                <h3>Erro ao criar</h3>
                <p>${esc(error.message)}</p>
                <button class="btn btn-primary" onclick="loadProjects()" type="button">Voltar para projetos</button>
            </div>
        `;
    }
}

function pollProject(projectId) {
    const poll = setInterval(async () => {
        try {
            const project = await api(`/video/projects/${projectId}`);
            const bar = document.getElementById("qc-progress");
            const status = document.getElementById("qc-status");
            if (bar) {
                bar.style.width = `${project.progress}%`;
            }
            const labels = {
                generating_scenes: "Gerando cenas com IA...",
                generating_clips: "Criando clipes...",
                rendering: "Renderizando video final...",
                completed: "Video pronto.",
                failed: "Erro na geracao.",
            };
            if (status) {
                status.textContent = labels[project.status] || project.status;
            }
            if (project.status === "completed" || project.status === "failed") {
                clearInterval(poll);
                setTimeout(() => loadProjects(), 1500);
            }
        } catch (_) {
            clearInterval(poll);
            loadProjects();
        }
    }, 4000);
}

function esc(value) {
    const div = document.createElement("div");
    div.textContent = value || "";
    return div.innerHTML;
}

function badgeClass(status) {
    if (status === "pending") return "pending";
    if (status && (status.includes("generat") || status.includes("render"))) return "rendering";
    if (status === "completed" || status === "published") return "completed";
    if (status === "failed") return "failed";
    return "pending";
}

window.closeModal = closeModal;
window.createProject = createProjectFromLibrary;
window.generateVideo = generateVideo;
window.deleteProject = deleteProject;
window.watchVideo = watchVideo;
window.createSchedule = createSchedule;
window.toggleSchedule = toggleSchedule;
window.deleteSchedule = deleteSchedule;
window.connectPlatform = connectPlatform;
window.disconnectAccount = disconnectAccount;
window.loadProjects = loadProjects;

bootstrap();

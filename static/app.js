console.log("[CriaVideo] app.js v176 loaded");
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
let _socialAccountsCache = [];
let _publishAccountSelection = {};
let _publishRenderOptions = {};
let _pendingConnectPlatform = "";
let _editingSocialAccountId = 0;
const PUBLISH_DRAFT_STORAGE_PREFIX = "publish_draft_";

const REALISTIC_PERSONA_TYPES = ["homem", "mulher", "crianca", "familia", "natureza", "desenho", "personalizado"];
const REALISTIC_PERSONA_LABELS = {
    homem: "Homem",
    mulher: "Mulher",
    crianca: "Crianca",
    familia: "Familia",
    natureza: "Natureza",
    desenho: "Desenho",
    personalizado: "Personalizado",
};
let _personaProfilesByType = {};
let _personaSelectionByContext = {
    wizard: {},
    script: {},
    ai: {},
    auto: {},
};
let _personaMultiSelectionByContext = {
    wizard: {},
    script: {},
    ai: {},
    auto: {},
};
let _personaManagerContext = "script";
let _personaManagerType = "natureza";
let _personaManagerMulti = false;
let personaManagerReferenceImageFile = null;
let _personaVoiceBuilderProfileId = 0;
let _personaPromptEditorProfileId = 0;
const PERSONA_REFERENCE_ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp"];
const PERSONA_REFERENCE_MAX_SIZE = 10 * 1024 * 1024;

// Simple toast notification
function showToast(msg, type = "info") {
    const existing = document.getElementById("_app_toast");
    if (existing) existing.remove();
    const el = document.createElement("div");
    el.id = "_app_toast";
    const bg = type === "error" ? "#ef4444" : type === "success" ? "#22c55e" : "#3b82f6";
    el.style.cssText = `position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:${bg};color:#fff;padding:10px 20px;border-radius:10px;font-size:13px;z-index:99999;box-shadow:0 4px 20px rgba(0,0,0,0.3);max-width:90vw;text-align:center;animation:fadeIn .2s`;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; setTimeout(() => el.remove(), 300); }, 3500);
}

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
                return `${location}${item.msg || "Erro de validação"}`;
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

async function apiForm(path, formData, options = {}) {
    let response = null;
    let lastError = null;
    for (let attempt = 0; attempt < 2; attempt++) {
        try {
            response = await fetch(`${API}${path}`, {
                method: options.method || "POST",
                ...options,
                body: formData,
                headers: {
                    ...(token ? { Authorization: `Bearer ${token}` } : {}),
                    ...(options.headers || {}),
                },
            });
            break;
        } catch (error) {
            lastError = error;
            const isNetworkError = error && /failed to fetch|networkerror|load failed/i.test(String(error.message || error));
            if (!isNetworkError || attempt === 1) {
                throw new Error("Falha de conexão ao enviar arquivos. Verifique a internet e tente novamente.");
            }
            await new Promise((resolve) => setTimeout(resolve, 800));
        }
    }
    if (!response) {
        throw new Error(lastError?.message || "Falha ao enviar requisição.");
    }
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
    if (profileName) profileName.textContent = currentUser.name || "Usuário";
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
    const subtitle = document.getElementById("auth-subtitle");
    if (subtitle) subtitle.textContent = isLogin
        ? "Acesse seus projetos e publique em múltiplos canais."
        : "Crie sua conta para receber clientes e gerar vídeos fora do Levita.";
    document.getElementById("auth-switch-copy").textContent = isLogin ? "Não tem conta?" : "Já tem conta?";
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
        throw new Error(getApiErrorMessage(error, "Não foi possível validar o login do Levita"));
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
        throw new Error(getApiErrorMessage(body, "Não foi possível entrar com credenciais do Levita"));
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
    const normalizedPage = (pageName === "accounts") ? "publish" : pageName;
    document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
    const target = document.getElementById("page-" + normalizedPage);
    if (target) target.classList.add("active");
    // Update sidebar active
    document.querySelectorAll(".sidebar-nav .nav-item").forEach((item) => {
        item.classList.toggle("active", item.dataset.page === normalizedPage);
    });
    // Update mobile tabs active
    document.querySelectorAll(".mobile-nav-tab").forEach((tab) => {
        tab.classList.toggle("active", tab.dataset.mobilePage === normalizedPage);
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
    // Logo click toggles sidebar
    const logo = document.querySelector(".sidebar-header .logo");
    if (logo) {
        logo.addEventListener("click", () => {
            document.getElementById("app").classList.toggle("sidebar-collapsed");
        });
    }
    // Toggle button reopens sidebar
    const sidebarToggle = document.getElementById("sidebar-toggle");
    if (sidebarToggle) {
        sidebarToggle.addEventListener("click", () => {
            document.getElementById("app").classList.remove("sidebar-collapsed");
        });
    }
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
    // Mobile profile avatar
    const mobileProfileBtn = document.getElementById("mobile-profile-btn");
    if (mobileProfileBtn) {
        mobileProfileBtn.addEventListener("click", () => {
            navigateTo("profile");
        });
    }
}

function ensurePublishDraftSelector() {
    const formArea = document.getElementById("publish-form-area");
    const renderSelect = document.getElementById("pub-render-select");
    if (!formArea || !renderSelect) {
        return;
    }

    if (document.getElementById("pub-draft-select")) {
        return;
    }

    const renderGroup = renderSelect.closest(".form-group");
    if (!renderGroup || !renderGroup.parentNode) {
        return;
    }

    let row = renderGroup.closest(".publish-select-row");
    if (!row) {
        row = document.createElement("div");
        row.className = "publish-select-row";
        renderGroup.parentNode.insertBefore(row, renderGroup);
        row.appendChild(renderGroup);
    }

    const draftGroup = document.createElement("div");
    draftGroup.className = "form-group publish-form-group";
    draftGroup.innerHTML = "<select id=\"pub-draft-select\" class=\"input\" aria-label=\"Selecionar rascunho salvo\"><option value=\"\">Meus rascunhos...</option></select>";
    row.appendChild(draftGroup);
}

function bindDashboardEvents() {
    ensurePublishDraftSelector();

    document.getElementById("btn-new-project").addEventListener("click", () => {
        resetCreateWizard();
        openModal("modal-new-project");
    });
    document.getElementById("btn-publish").addEventListener("click", submitPublishNow);
    document.getElementById("btn-save-draft").addEventListener("click", savePublishDraft);
    document.getElementById("btn-schedule-publish").addEventListener("click", openPublishScheduleModal);
    document.getElementById("pub-links-toggle").addEventListener("click", togglePublishLinks);
    document.getElementById("btn-save-links").addEventListener("click", savePublishLinksForAccount);
    document.getElementById("pub-render-select").addEventListener("change", (e) => {
        const renderId = e.target.value;
        if (renderId) {
            onRenderSelected(parseInt(renderId, 10));
        }
    });
    const draftSelect = document.getElementById("pub-draft-select");
    if (draftSelect) {
        draftSelect.addEventListener("change", async (event) => {
            const renderId = parseInt(event.target.value || "", 10);
            if (!renderId) {
                return;
            }
            await openPublishDraftFromList(renderId);
        });
    }
    document.getElementById("btn-regenerate-thumb").addEventListener("click", () => {
        const renderId = document.getElementById("pub-render-select").value;
        if (renderId) {
            const currentTitle = document.getElementById("pub-title").value;
            const currentDescription = document.getElementById("pub-description").value;
            generatePublishThumbnail(parseInt(renderId, 10), currentTitle, currentDescription);
        }
    });
    document.getElementById("btn-new-schedule").addEventListener("click", async () => {
        await loadAccountsForSelect();
        openModal("modal-new-schedule");
    });
    document.getElementById("btn-new-automation").addEventListener("click", () => {
        openNewAutomationModal();
    });
    document.querySelectorAll(".publish-top-tab").forEach((tabBtn) => {
        tabBtn.addEventListener("click", () => {
            setPublishTab(tabBtn.dataset.publishTab || "publish");
        });
    });
    document.querySelectorAll(".publish-platform-chip input").forEach((checkbox) => {
        checkbox.addEventListener("change", () => {
            renderPublishAccountSelectors();
        });
    });
    const schedulePlatformSelect = document.getElementById("ns-platform");
    if (schedulePlatformSelect) {
        schedulePlatformSelect.addEventListener("change", () => {
            refreshScheduleAccountOptions();
        });
    }
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
                <p><strong>${esc(selectedSong.title || "Sem título")}</strong></p>
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

function handleSocialCallbackResult() {
    const params = new URLSearchParams(window.location.search);
    const connected = String(params.get("social_connected") || "").toLowerCase();
    const socialError = String(params.get("social_error") || "").toLowerCase();
    const socialReason = (params.get("social_reason") || "").trim();
    if (!connected && !socialError) return;

    const cleanUrl = `${window.location.pathname}${window.location.hash || ""}`;
    window.history.replaceState({}, "", cleanUrl);

    if (connected) {
        alert(`${socialPlatformName(connected)} conectada com sucesso.`);
        navigateTo("accounts");
        return;
    }

    const platformName = socialPlatformName(socialError || "social");
    const reasonText = socialReason ? `\n\nDetalhes: ${socialReason}` : "";
    alert(`Não foi possível conectar ${platformName}.${reasonText}`);
    navigateTo("accounts");
}

function initDashboard() {
    renderSession();
    updateCreditsDisplay();
    const renameInput = document.getElementById("edit-project-title");
    if (renameInput) {
        renameInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                saveProjectEdit();
            }
        });
    }
    const connectLabelInput = document.getElementById("connect-account-label");
    if (connectLabelInput) {
        connectLabelInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                confirmConnectPlatform();
            }
        });
    }
    const editAccountInput = document.getElementById("edit-account-label");
    if (editAccountInput) {
        editAccountInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                saveSocialAccountLabel();
            }
        });
    }
    const publishScheduleInput = document.getElementById("pub-schedule-datetime");
    if (publishScheduleInput) {
        publishScheduleInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                confirmSchedulePublish();
            }
        });
    }

    handleSocialCallbackResult();
    _refreshPersonaContext("wizard", "natureza");
    _refreshPersonaContext("script", "natureza");
    _refreshPersonaContext("ai", "natureza");
    _refreshPersonaContext("auto", "natureza");

    const hashValue = String(window.location.hash || "").toLowerCase();
    if (hashValue.includes("/social")) {
        navigateTo("accounts");
        return;
    }

    const params = new URLSearchParams(window.location.search);
    const audioUrl = params.get("audio_url");
    if (audioUrl) {
        const requestedAspect =
            params.get("aspect") ||
            params.get("aspect_ratio") ||
            params.get("video_format") ||
            params.get("format") ||
            "16:9";
        const aspectRatio = ["16:9", "9:16", "1:1"].includes(requestedAspect)
            ? requestedAspect
            : "16:9";
        window.history.replaceState({}, "", window.location.pathname);
        quickCreate({
            song_title: params.get("song_title") || "",
            song_artist: params.get("song_artist") || "",
            audio_url: audioUrl,
            lyrics: params.get("lyrics") || "",
            duration: parseFloat(params.get("duration")) || 180,
            aspect_ratio: aspectRatio,
        });
        return;
    }
    loadProjects();
}

function loadPageData(page) {
    if (page === "projects") {
        loadProjects();
    } else if (page === "publish" || page === "accounts") {
        setPublishTab(page === "publish" ? "publish" : page);
    } else if (page === "automate") {
        loadAutoSchedules();
    } else if (page === "editor") {
        loadEditorVideosList();
    }
}

function setPublishTab(tabName) {
    const nextTab = ["publish", "accounts"].includes(tabName) ? tabName : "publish";
    document.querySelectorAll(".publish-top-tab").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.publishTab === nextTab);
    });
    document.querySelectorAll(".publish-tab-content").forEach((panel) => {
        panel.classList.toggle("active", panel.id === `publish-tab-${nextTab}`);
    });

    if (nextTab === "publish") {
        const preselectProjectId = _pendingPublishProjectId;
        _pendingPublishProjectId = 0;
        renderPublishDraftList();
        renderPublishAccountSelectors(true);
        loadRenders(preselectProjectId).then((preselected) => {
            if (preselected) {
                const renderId = document.getElementById("pub-render-select").value;
                if (renderId) {
                    onRenderSelected(parseInt(renderId, 10));
                }
            }
        });
        loadPublishJobs();
        loadSchedules();
    } else if (nextTab === "accounts") {
        loadAccounts();
    }
}

function openModal(id) {
    const modal = document.getElementById(id);
    if (!modal) {
        return;
    }
    // Move modal to <body> so it escapes any ancestor stacking context or flex container.
    if (modal.parentElement !== document.body) {
        document.body.appendChild(modal);
    }
    modal.classList.add("open");
    modal.style.display = "flex";
}

function closeModal(id) {
    const modal = document.getElementById(id);
    if (!modal) {
        return;
    }
    modal.classList.remove("open");
    modal.style.display = "";
    if (id === "modal-new-project") {
        stopKaraokeProgressPolling();
    }
    if (id === "modal-edit-project") {
        _renameProjectId = 0;
        const input = document.getElementById("edit-project-title");
        if (input) input.value = "";
    }
    if (id === "modal-connect-account") {
        _pendingConnectPlatform = "";
        const input = document.getElementById("connect-account-label");
        if (input) input.value = "";
        const keyInput = document.getElementById("connect-tiktok-client-key");
        const secretInput = document.getElementById("connect-tiktok-client-secret");
        if (keyInput) keyInput.value = "";
        if (secretInput) secretInput.value = "";
        const tiktokKeys = document.getElementById("connect-tiktok-keys");
        if (tiktokKeys) tiktokKeys.hidden = true;
    }
    if (id === "modal-edit-account") {
        _editingSocialAccountId = 0;
        const input = document.getElementById("edit-account-label");
        if (input) input.value = "";
    }
    if (id === "modal-publish-schedule") {
        const dtInput = document.getElementById("pub-schedule-datetime");
        if (dtInput) dtInput.value = "";
        const btn = document.getElementById("btn-confirm-schedule-publish");
        if (btn) {
            btn.disabled = false;
            btn.textContent = "Agendar";
        }
    }
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
    const baseOptions = ["<option value=''>Selecione uma música</option>", "<option value='manual'>Inserir manualmente</option>"];
    if (!songs.length) {
        select.innerHTML = baseOptions.join("");
        document.getElementById("np-manual-fields").hidden = false;
        return;
    }
    document.getElementById("np-manual-fields").hidden = true;
    select.innerHTML = baseOptions.join("") + songs.map((song, index) => {
        const artist = song.artist ? ` - ${esc(song.artist)}` : "";
        return `<option value="${index}">${esc(song.title || "Sem título")}${artist}</option>`;
    }).join("");
}

let _projectsCache = [];
let _copyFormatSourceProjectId = 0;
let _pendingPublishProjectId = 0;
let _renameProjectId = 0;

async function loadProjects() {
    const container = document.getElementById("projects-list");
    try {
        const data = await api("/video/projects");
        _projectsCache = data;
        // Filter out expired videos — no need to show them
        const visibleData = data.filter(p => !(p.status === "completed" && p.video_expired));
        if (!visibleData.length) {
            container.innerHTML = "<p class='loading'>Nenhum projeto ainda. Crie o primeiro.</p>";
            return;
        }
        container.innerHTML = visibleData.map((project) => {
            const dateStr = _renderExpiryOrDate(project);
            const statusPt = _statusPt(project.status);
            const isExpired = project.video_expired || false;
            const canWatch = project.status === "completed" && !isExpired;
            const thumbClick = canWatch ? `onclick="watchVideo(${project.id})" style="cursor:pointer"` : "";
            const thumb = project.thumbnail_url
                ? `<img class="card-thumb" src="${project.thumbnail_url}" alt="" loading="lazy" onerror="handleProjectThumbError(this, ${project.id}, ${canWatch})" ${thumbClick}>`
                : `<div class="card-thumb card-thumb-placeholder" ${thumbClick}><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5 3 19 12 5 21 5 3"/></svg></div>`;
            return `
                <div class="card">
                    ${thumb}
                    <div class="card-body">
                        <h4 class="card-title">${esc(project.title)}</h4>
                        ${project.status !== "completed" ? `<span class="badge badge-${badgeClass(project.status)}">${esc(statusPt)}</span>` : ""}
                        ${project.progress != null && project.status !== "completed" && project.status !== "failed" && project.status !== "pending" ? `<div class="progress-bar"><div class="progress-bar-fill" style="width:${project.progress}%"></div></div>` : ""}
                        ${project.error_message ? `<p class="card-error">${esc(project.error_message)}</p>` : ""}
                    </div>
                    <div class="card-footer">
                        <div class="card-actions">
                            ${canWatch ? `<button class="card-btn card-btn-watch" onclick="watchVideo(${project.id})" type="button" title="Assistir"><svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>` : ""}
                            ${canWatch ? `<button class="card-btn card-btn-publish" onclick="openPublishForProject(${project.id})" type="button" title="Publicar"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12"/><polyline points="8 7 12 3 16 7"/><rect x="4" y="15" width="16" height="6" rx="2"/></svg></button>` : ""}
                            ${(project.status === "pending" || project.status === "failed") ? `<button class="card-btn card-btn-generate" onclick="generateVideo(${project.id})" type="button" title="Gerar vídeo"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>` : ""}
                            ${canWatch ? `<button class="card-btn card-btn-similar" onclick="openCopyChoiceModal(${project.id})" type="button" title="Criar copia"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>` : (project.lyrics_text ? `<button class="card-btn card-btn-similar" onclick="createSimilar(${project.id})" type="button" title="Criar Semelhante"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>` : "")}
                            <button class="card-btn card-btn-edit" onclick="openRenameProjectModal(${project.id})" type="button" title="Editar nome"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg></button>
                            <button class="card-btn card-btn-delete" onclick="deleteProject(${project.id})" type="button" title="Excluir"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg></button>
                        </div>
                        <span class="card-date">${dateStr}</span>
                    </div>
                </div>
            `;
        }).join("");
        // Start polling for in-progress projects
        _pollInProgress(data);
        _startCountdownRefresh();
    } catch (error) {
        container.innerHTML = `<p class="loading">Erro: ${esc(error.message)}</p>`;
    }
}

function handleProjectThumbError(imgElement, projectId, canWatch) {
    const placeholder = document.createElement("div");
    placeholder.className = "card-thumb card-thumb-placeholder";
    if (canWatch) {
        placeholder.style.cursor = "pointer";
        placeholder.addEventListener("click", () => watchVideo(projectId));
    }
    placeholder.innerHTML = '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5 3 19 12 5 21 5 3"/></svg>';
    imgElement.replaceWith(placeholder);
}

function _statusPt(status) {
    const map = {
        "pending": "Pendente",
        "generating_scenes": "Gerando cenas...",
        "generating_clips": "Gerando clipes...",
        "rendering": "Renderizando...",
        "completed": "Concluído",
        "failed": "Falhou",
        "published": "Publicado",
    };
    return map[status] || status;
}

const RENDER_EXPIRY_HOURS = 48;

function _renderExpiryOrDate(project) {
    // For completed projects with a render, show countdown to expiry
    if (project.status === "completed" && project.video_expired) {
        return '<span class="expiry-expired">Expirado</span>';
    }
    if (project.status === "completed" && project.render_created_at) {
        const renderDate = new Date(project.render_created_at);
        const expiresAt = new Date(renderDate.getTime() + RENDER_EXPIRY_HOURS * 3600000);
        const now = new Date();
        const remaining = expiresAt - now;
        if (remaining <= 0) {
            return '<span class="expiry-expired">Expirado</span>';
        }
        const hours = Math.floor(remaining / 3600000);
        const mins = Math.floor((remaining % 3600000) / 60000);
        if (hours < 6) {
            return `<span class="expiry-urgent">⏳ ${hours}h ${String(mins).padStart(2,"0")}m</span>`;
        }
        return `<span class="expiry-countdown">⏳ ${hours}h ${String(mins).padStart(2,"0")}m</span>`;
    }
    // For non-completed projects, show creation date
    const dt = project.created_at ? new Date(project.created_at) : null;
    return dt ? `${String(dt.getHours()).padStart(2,"0")}:${String(dt.getMinutes()).padStart(2,"0")} · ${dt.toLocaleDateString("pt-BR")}` : "-";
}

// Auto-refresh countdown timers every minute
let _countdownTimer = null;
function _startCountdownRefresh() {
    if (_countdownTimer) clearInterval(_countdownTimer);
    _countdownTimer = setInterval(() => {
        const container = document.getElementById("projects-list");
        if (!container) return;
        for (const project of _projectsCache) {
            if (project.status !== "completed" || !project.render_created_at) continue;
            const cards = container.querySelectorAll(".card");
            for (const card of cards) {
                const btn = card.querySelector("[onclick*='watchVideo(" + project.id + ")']") ||
                            card.querySelector("[onclick*='deleteProject(" + project.id + ")']");
                if (btn) {
                    const dateSpan = card.querySelector(".card-date");
                    if (dateSpan) dateSpan.innerHTML = _renderExpiryOrDate(project);
                    break;
                }
            }
        }
    }, 60000);
}

let _pollTimer = null;
let _prevActiveIds = new Set();
function _pollInProgress(projects) {
    if (_pollTimer) clearInterval(_pollTimer);
    const active = projects.filter(p =>
        p.status !== "completed" && p.status !== "failed" && p.status !== "pending"
    );
    if (!active.length) return;
    _prevActiveIds = new Set(active.map(p => p.id));
    _pollTimer = setInterval(async () => {
        try {
            const data = await api("/video/projects");
            _projectsCache = data;
            const stillActive = data.filter(p =>
                p.status !== "completed" && p.status !== "failed" && p.status !== "pending"
            );
            // Detect newly completed projects
            const newlyCompleted = data.filter(p =>
                p.status === "completed" && _prevActiveIds.has(p.id)
            );
            _prevActiveIds = new Set(stillActive.map(p => p.id));
            // Update cards in-place instead of full re-render
            for (const p of data) {
                _updateCardInPlace(p);
            }
            if (!stillActive.length) {
                clearInterval(_pollTimer);
                _pollTimer = null;
                loadProjects(); // Full refresh to get thumbnails
                // Show expiry warning and auto-download newly completed videos
                if (newlyCompleted.length) {
                    _showExpiryWarning();
                    _autoDownloadCompleted(newlyCompleted);
                }
            }
        } catch (_) {
            clearInterval(_pollTimer);
            _pollTimer = null;
        }
    }, 3000);
}

function _showExpiryWarning() {
    const modal = document.getElementById("modal-expiry-warning");
    if (modal) {
        openModal("modal-expiry-warning");
    }
}

async function _autoDownloadCompleted(projects) {
    for (const p of projects) {
        try {
            const detail = await api(`/video/projects/${p.id}`);
            const render = _pickLatestAvailableRender(detail.renders || []);
            if (!render) continue;
            const a = document.createElement("a");
            a.href = render.video_url;
            a.download = `${p.title || "video"}.mp4`;
            a.style.display = "none";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        } catch (_) {}
    }
}

function _updateCardInPlace(project) {
    const container = document.getElementById("projects-list");
    const cards = container.querySelectorAll(".card");
    for (const card of cards) {
        const watchBtn = card.querySelector("[onclick*='watchVideo(" + project.id + ")']");
        const genBtn = card.querySelector("[onclick*='generateVideo(" + project.id + ")']");
        const simBtn = card.querySelector("[onclick*='createSimilar(" + project.id + ")']");
        const delBtn = card.querySelector("[onclick*='deleteProject(" + project.id + ")']");
        if (watchBtn || genBtn || simBtn || delBtn) {
            const body = card.querySelector(".card-body");
            // Update or create badge
            const isActive = project.status !== "completed" && project.status !== "failed" && project.status !== "pending";
            let badge = card.querySelector(".badge");
            if (project.status !== "completed") {
                if (!badge) {
                    badge = document.createElement("span");
                    body.appendChild(badge);
                }
                badge.textContent = _statusPt(project.status);
                badge.className = `badge badge-${badgeClass(project.status)}`;
            } else if (badge) {
                badge.remove();
            }
            // Update or create progress bar
            let barWrap = card.querySelector(".progress-bar");
            if (isActive && project.progress != null) {
                if (!barWrap) {
                    barWrap = document.createElement("div");
                    barWrap.className = "progress-bar";
                    barWrap.innerHTML = '<div class="progress-bar-fill"></div>';
                    body.appendChild(barWrap);
                }
                barWrap.querySelector(".progress-bar-fill").style.width = project.progress + "%";
            } else if (barWrap) {
                barWrap.remove();
            }
            break;
        }
    }
}

// ═══ Creation Wizard State ═══
let createMode = "wizard"; // "wizard" | "script" | "library"
let wizardStep = 1;
let wizardData = { topic: "", videoType: "imagens_ia", tone: "", voice: "", duration: 60, aspect: "16:9", style: "", realisticStyle: "" };
let scriptStep = 1;
let scriptData = {
    text: "",
    videoType: "imagens_ia",
    tone: "",
    voice: "",
    title: "",
    aspect: "16:9",
    style: "",
    useCustomImages: false,
    useCustomAudio: false,
    audioIsMusic: false,
    removeVocals: false,
    createNarration: true,
    enableSubtitles: true,
    subtitlePositionY: 80,
    enableAudioSpectrum: false,
    useTevoxiAudio: false,
    zoomImages: true,
    imageDisplaySeconds: 0,
    promptOptimized: false,
};
let _scriptTevoxiSongs = []; // cached Tevoxi songs for script realistic mode
let _scriptSelectedSong = null; // selected Tevoxi song for script realistic mode
let _scriptSelectedClip = null; // selected clip/full metadata for script mode
let _wizardTevoxiSongs = []; // cached Tevoxi songs for wizard realistic mode
let _wizardSelectedSong = null; // selected Tevoxi song for wizard realistic mode
let _wizardSelectedClip = null; // selected clip/full metadata for wizard mode
// Step flow arrays for each video type
const WIZARD_FLOW_NORMAL = [2, 1, 3, 4, 5, 6]; // type, topic, tone, voice, style, details
const WIZARD_FLOW_REALISTIC = [2, 1, 7]; // type, topic, realistic settings
const SCRIPT_FLOW_NORMAL = [2, 1, 3, 4, 5, 6]; // type, script, tone, voice, details, style
const SCRIPT_FLOW_REALISTIC = [2, 1, 7]; // type, script, realistic settings

function getWizardFlow() {
    return wizardData.videoType === "realista" ? WIZARD_FLOW_REALISTIC : WIZARD_FLOW_NORMAL;
}
function getScriptFlow() {
    return scriptData.videoType === "realista" ? SCRIPT_FLOW_REALISTIC : SCRIPT_FLOW_NORMAL;
}
const CREATE_PROGRESS_BASE = 8;
let karaokeProgressTimer = null;
let karaokeProgressOperationId = "";

// Smooth progress animation state
let _smoothProgressTarget = CREATE_PROGRESS_BASE;
let _smoothProgressCurrent = CREATE_PROGRESS_BASE;
let _smoothProgressTimer = null;

function _startSmoothProgress() {
    if (_smoothProgressTimer) return;
    _smoothProgressTimer = setInterval(() => {
        if (_smoothProgressCurrent >= _smoothProgressTarget) return;
        // Increment by small step toward target
        const gap = _smoothProgressTarget - _smoothProgressCurrent;
        const step = Math.max(0.3, gap * 0.08);
        _smoothProgressCurrent = Math.min(_smoothProgressTarget, _smoothProgressCurrent + step);
        const display = Math.round(_smoothProgressCurrent);
        const fill = document.getElementById("create-progress-fill");
        const percentEl = document.getElementById("create-progress-percent");
        if (fill) fill.style.width = `${display}%`;
        if (percentEl) percentEl.textContent = `${display}%`;
    }, 150);
}

function _stopSmoothProgress() {
    if (_smoothProgressTimer) {
        clearInterval(_smoothProgressTimer);
        _smoothProgressTimer = null;
    }
}

async function createSimilar(projectId) {
    const project = _projectsCache.find(p => p.id === projectId);
    if (!project || !project.lyrics_text) {
        alert("Roteiro não disponível para este projeto.");
        return;
    }

    const realisticArtists = new Set([
        "MiniMax Hailuo",
        "Wan 2.2",
        "Ultra High 2.2",
        "Seedance 2.0",
        "Grok",
        "Cria 3.0 speed",
    ]);
    const sourceLooksRealistic = (
        project.video_type === "realista"
        || project.video_type === "realistic"
        || realisticArtists.has((project.track_artist || "").trim())
    );
    const inferredVideoType = sourceLooksRealistic ? "realista" : "imagens_ia";

    // 1. Reset wizard state
    resetCreateWizard();

    // 2. Pre-fill form fields (while modal is still closed)
    const textEl = document.getElementById("script-text");
    if (textEl) textEl.value = project.lyrics_text;
    const countEl = document.getElementById("script-char-count");
    if (countEl) countEl.textContent = project.lyrics_text.length.toLocaleString("pt-BR");
    const titleEl = document.getElementById("script-title");
    if (titleEl) titleEl.value = project.title || "";
    if (project.style_prompt && !sourceLooksRealistic) {
        setSelectedStyles("script-style-tags", project.style_prompt);
    }
    const aspectEl = document.getElementById("script-aspect");
    if (aspectEl && project.aspect_ratio) aspectEl.value = project.aspect_ratio;
    const realisticAspectEl = document.getElementById("script-realistic-aspect");
    if (realisticAspectEl && project.aspect_ratio) realisticAspectEl.value = project.aspect_ratio;

    scriptData.videoType = inferredVideoType;
    scriptData.promptOptimized = sourceLooksRealistic;
    scriptStep = 1;

    document.querySelectorAll("#script-video-type-grid .video-type-card").forEach((card) => {
        card.classList.toggle("selected", card.dataset.type === scriptData.videoType);
    });
    adaptScriptStepForVideoType(scriptData.videoType);

    // 3. Open modal (same as clicking the new-project button)
    openModal("modal-new-project");

    // 4. Switch to script mode AFTER modal is visible (same as user clicking "Meu Roteiro")
    switchCreateMode("script");

    // 5. Always start at video type step before prompt
    updateFlowUI("create-panel-script", scriptStep, getScriptFlow(), "script");
}

function openCopyFormatModal(projectId) {
    if (!projectId) {
        projectId = _copyFormatSourceProjectId;
    }
    const project = _projectsCache.find(p => p.id === projectId);
    if (!project || project.status !== "completed") {
        alert("Somente vídeos concluídos podem ser copiados de formato.");
        return;
    }
    _copyFormatSourceProjectId = projectId;
    const sourceEl = document.getElementById("copy-format-source");
    if (sourceEl) {
        sourceEl.textContent = `Origem: ${project.title || "Vídeo"} (${project.aspect_ratio || "16:9"})`;
    }
    const selectEl = document.getElementById("copy-format-aspect");
    if (selectEl) {
        const fallback = project.aspect_ratio === "16:9" ? "9:16" : "16:9";
        selectEl.value = ["16:9", "9:16", "1:1"].includes(fallback) ? fallback : "9:16";
    }
    openModal("modal-copy-format");
}

function openCopyChoiceModal(projectId) {
    const project = _projectsCache.find(p => p.id === projectId);
    if (!project || project.status !== "completed") {
        alert("Somente vídeos concluídos podem ser copiados.");
        return;
    }
    _copyFormatSourceProjectId = projectId;
    const sourceEl = document.getElementById("copy-choice-source");
    if (sourceEl) {
        sourceEl.textContent = `Origem: ${project.title || "Vídeo"} (${project.aspect_ratio || "16:9"})`;
    }
    openModal("modal-copy-choice");
}

function chooseCopyScript() {
    const projectId = _copyFormatSourceProjectId;
    if (!projectId) {
        alert("Nenhum vídeo selecionado para cópia.");
        return;
    }
    closeModal("modal-copy-choice");
    _copyFormatSourceProjectId = 0;
    createSimilar(projectId);
}

function chooseCopyFormat() {
    if (!_copyFormatSourceProjectId) {
        alert("Nenhum vídeo selecionado para cópia.");
        return;
    }
    closeModal("modal-copy-choice");
    openCopyFormatModal();
}

async function createFormatCopy() {
    if (!_copyFormatSourceProjectId) {
        alert("Nenhum vídeo selecionado para cópia.");
        return;
    }
    const selectEl = document.getElementById("copy-format-aspect");
    const aspectRatio = selectEl ? selectEl.value : "9:16";
    try {
        await api(`/video/projects/${_copyFormatSourceProjectId}/copy-format`, {
            method: "POST",
            body: JSON.stringify({ aspect_ratio: aspectRatio }),
        });
        closeModal("modal-copy-format");
        _copyFormatSourceProjectId = 0;
        loadProjects();
    } catch (error) {
        alert(`Erro ao criar copia: ${error.message}`);
    }
}

let _editThumbFile = null; // File object for new thumbnail in edit modal

async function openRenameProjectModal(projectId) {
    const project = _projectsCache.find((p) => p.id === projectId);
    if (!project) {
        alert("Projeto não encontrado.");
        return;
    }

    _renameProjectId = project.id;
    _editThumbFile = null;
    const sourceEl = document.getElementById("edit-project-source");
    if (sourceEl) {
        sourceEl.textContent = `Projeto atual: ${project.title || "Vídeo"}`;
    }

    const input = document.getElementById("edit-project-title");
    if (input) {
        input.value = project.title || "";
    }

    const saveBtn = document.getElementById("edit-project-save-btn");
    if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = "Salvar";
    }

    // Reset thumbnail upload
    const thumbInput = document.getElementById("edit-thumb-input");
    if (thumbInput) thumbInput.value = "";
    const thumbPreview = document.getElementById("edit-thumb-preview");
    if (thumbPreview) { thumbPreview.hidden = true; thumbPreview.src = ""; }
    const thumbRemoveBtn = document.getElementById("edit-thumb-remove");
    if (thumbRemoveBtn) thumbRemoveBtn.hidden = true;

    // Downloads section — only show for completed projects
    const downloadsEl = document.getElementById("edit-project-downloads");
    if (downloadsEl) {
        if (project.status === "completed") {
            downloadsEl.hidden = false;
            try {
                const detail = await api(`/video/projects/${project.id}`);
                const renders = Array.isArray(detail.renders) ? detail.renders : [];
                const render = _pickLatestAvailableRender(renders) || _sortRendersNewestFirst(renders)[0] || null;
                const videoLink = document.getElementById("edit-download-video");
                const thumbLink = document.getElementById("edit-download-thumb");
                if (render && render.video_url && videoLink) {
                    videoLink.href = render.video_url;
                    videoLink.download = `${project.title || "video"}.mp4`;
                    videoLink.style.display = "";
                } else if (videoLink) {
                    videoLink.style.display = "none";
                }
                if (render && render.thumbnail_url && thumbLink) {
                    thumbLink.href = render.thumbnail_url;
                    thumbLink.download = `${project.title || "thumbnail"}.jpg`;
                    thumbLink.style.display = "";
                    // Show current thumbnail preview
                    if (thumbPreview) {
                        thumbPreview.src = render.thumbnail_url;
                        thumbPreview.hidden = false;
                    }
                } else if (thumbLink) {
                    thumbLink.style.display = "none";
                }
            } catch (e) {
                // Silently fail — downloads won't show
            }
        } else {
            downloadsEl.hidden = true;
        }
    }

    openModal("modal-edit-project");

    if (input) {
        window.setTimeout(() => {
            input.focus();
            input.select();
        }, 0);
    }
}

function handleEditThumbSelect(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    if (!file.type.match(/^image\/(jpeg|png|webp)$/)) {
        alert("Use JPG, PNG ou WebP.");
        event.target.value = "";
        return;
    }
    if (file.size > 10 * 1024 * 1024) {
        alert("Imagem excede 10MB.");
        event.target.value = "";
        return;
    }
    _editThumbFile = file;
    const preview = document.getElementById("edit-thumb-preview");
    if (preview) {
        preview.src = URL.createObjectURL(file);
        preview.hidden = false;
    }
    const removeBtn = document.getElementById("edit-thumb-remove");
    if (removeBtn) removeBtn.hidden = false;
}

function removeEditThumb() {
    _editThumbFile = null;
    const input = document.getElementById("edit-thumb-input");
    if (input) input.value = "";
    const preview = document.getElementById("edit-thumb-preview");
    if (preview) { preview.hidden = true; preview.src = ""; }
    const removeBtn = document.getElementById("edit-thumb-remove");
    if (removeBtn) removeBtn.hidden = true;
}

async function saveProjectEdit() {
    if (!_renameProjectId) {
        alert("Nenhum projeto selecionado.");
        return;
    }

    const input = document.getElementById("edit-project-title");
    const newTitle = (input?.value || "").trim();
    if (!newTitle) {
        alert("Digite um nome para o vídeo.");
        if (input) input.focus();
        return;
    }

    const saveBtn = document.getElementById("edit-project-save-btn");
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = "Salvando...";
    }

    try {
        // Update title
        const response = await api(`/video/projects/${_renameProjectId}/title`, {
            method: "PATCH",
            body: JSON.stringify({ title: newTitle }),
        });
        const cacheProject = _projectsCache.find((p) => p.id === _renameProjectId);
        if (cacheProject) {
            cacheProject.title = response?.title || newTitle;
        }

        // Upload new thumbnail if selected
        if (_editThumbFile) {
            try {
                const fd = new FormData();
                fd.append("file", _editThumbFile);
                await apiForm(`/video/projects/${_renameProjectId}/thumbnail`, fd);
            } catch (thumbErr) {
                alert(`Nome atualizado, mas erro ao enviar thumbnail: ${thumbErr.message}`);
            }
            _editThumbFile = null;
        }

        closeModal("modal-edit-project");
        loadProjects();
    } catch (error) {
        alert(`Erro ao atualizar: ${error.message}`);
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = "Salvar";
        }
    }
}

function initCreateWizard() {
    // Mode selection cards
    document.querySelectorAll(".mode-selection-card").forEach((card) => {
        card.addEventListener("click", () => {
            const mode = card.dataset.createMode;
            document.getElementById("create-mode-selection").hidden = true;
            switchCreateMode(mode);
        });
    });

    // Tab switching (kept for programmatic use)
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

    // Background music toggle
    const bgmToggle = document.getElementById("script-enable-bgm");
    if (bgmToggle) {
        bgmToggle.addEventListener("change", () => {
            if (bgmToggle.disabled) {
                bgmToggle.checked = false;
                return;
            }
            const area = document.getElementById("script-bgm-upload-area");
            if (area) area.hidden = !bgmToggle.checked;
            if (!bgmToggle.checked) {
                const fi = document.getElementById("script-bgm-file");
                if (fi) fi.value = "";
            }
        });
    }

    const subtitleToggle = document.getElementById("script-enable-subtitles");
    if (subtitleToggle) {
        subtitleToggle.addEventListener("change", () => {
            _updateScriptSubtitlePositionVisibility();
        });
    }

    // Video type card click handlers (event delegation)
    document.querySelectorAll(".video-type-grid").forEach((grid) => {
        grid.addEventListener("click", (e) => {
            const card = e.target.closest(".video-type-card");
            if (!card) return;
            grid.querySelectorAll(".video-type-card").forEach((c) => c.classList.remove("selected"));
            card.classList.add("selected");
        });
    });

    // Wizard topic style buttons (shown only for realistic mode)
    document.querySelectorAll("#wizard-topic-style-tags .style-tag").forEach((btn) => {
        btn.addEventListener("click", () => {
            document.querySelectorAll("#wizard-topic-style-tags .style-tag").forEach((t) => t.classList.remove("selected"));
            btn.classList.add("selected");
            wizardData.realisticStyle = btn.dataset.style || "";
        });
    });

    // Wizard option clicks (event delegation)
    document.getElementById("modal-new-project").addEventListener("click", (e) => {
        const personaTag = e.target.closest("#wizard-realistic-persona-tags .style-tag, #script-realistic-persona-tags .style-tag, #ai-suggest-persona-tags .style-tag");
        if (personaTag) {
            const group = personaTag.closest(".realistic-inspiration-tags");
            if (group) {
                group.querySelectorAll(".style-tag").forEach((t) => t.classList.remove("selected"));
                personaTag.classList.add("selected");

                const selectedPersona = _normalizeRealisticPersonaType(personaTag.dataset.persona || "natureza");
                if (group.id === "wizard-realistic-persona-tags") {
                    _refreshPersonaContext("wizard", selectedPersona);
                } else if (group.id === "script-realistic-persona-tags") {
                    _refreshPersonaContext("script", selectedPersona);
                } else if (group.id === "ai-suggest-persona-tags") {
                    setSelectedRealisticPersona(selectedPersona);
                }
            }
        }

        const opt = e.target.closest(".wizard-option");
        if (opt) {
            const grid = opt.closest(".wizard-grid");
            grid.querySelectorAll(".wizard-option").forEach((o) => o.classList.remove("selected"));
            opt.classList.add("selected");
            // When selecting a voice, deselect any persona selection AND cross-deselect between builtin/suno grids
            const voiceSelector = opt.closest(".voice-selector");
            if (voiceSelector) {
                voiceSelector.querySelectorAll(".persona-item.selected").forEach(o => o.classList.remove("selected"));
                // Cross-deselect: if selecting suno voice, deselect builtin; and vice-versa
                const voiceType = opt.dataset.voiceType;
                if (voiceType === "suno") {
                    voiceSelector.querySelectorAll('.wizard-option[data-voice-type="builtin"].selected').forEach(o => o.classList.remove("selected"));
                } else if (voiceType === "builtin") {
                    voiceSelector.querySelectorAll('.wizard-option[data-voice-type="suno"].selected').forEach(o => o.classList.remove("selected"));
                }
            }
        }
        const dur = e.target.closest(".duration-option");
        if (dur) {
            dur.closest(".duration-options").querySelectorAll(".duration-option").forEach((d) => d.classList.remove("selected"));
            dur.classList.add("selected");
        }
        const eng = e.target.closest(".engine-option");
        if (eng) {
            eng.closest(".engine-options").querySelectorAll(".engine-option").forEach((d) => d.classList.remove("selected"));
            eng.classList.add("selected");
            const engineVal = eng.dataset.value;
            const container = eng.closest(".form-group")?.parentElement;
            if (container) {
                // Auto-toggle music checkbox: engines with native audio → uncheck
                const hasNativeAudio = (engineVal === "grok" || engineVal === "seedance");
                const musicCb = container.querySelector("[id$='-realistic-music']");
                if (musicCb) {
                    const useScriptTevoxi = musicCb.id === "script-realistic-music"
                        && (document.getElementById("script-realistic-tevoxi")?.checked || false);
                    const useWizardTevoxi = musicCb.id === "wizard-realistic-music"
                        && (document.getElementById("wizard-realistic-tevoxi")?.checked || false);
                    musicCb.checked = (useScriptTevoxi || useWizardTevoxi) ? false : !hasNativeAudio;
                }
            }
        }
        const vbtn = e.target.closest(".voice-btn");
        if (vbtn) {
            vbtn.closest(".realistic-voice-grid").querySelectorAll(".voice-btn").forEach((d) => d.classList.remove("selected"));
            vbtn.classList.add("selected");
        }
    });

    // Narration checkbox toggles
    document.querySelectorAll("[id$='-realistic-narration']").forEach(cb => {
        cb.addEventListener("change", () => {
            const prefix = cb.id.replace("-realistic-narration", "");
            const opts = document.getElementById(`${prefix}-realistic-narration-options`);
            if (opts) opts.hidden = !cb.checked;
        });
    });
}

function switchCreateMode(mode) {
    console.log("[switchCreateMode] mode=", mode);
    createMode = mode;
    document.querySelectorAll(".create-tab").forEach((t) => {
        t.classList.toggle("active", t.dataset.createMode === mode);
    });
    document.getElementById("create-mode-selection").hidden = true;
    document.querySelectorAll(".create-panel").forEach((p) => (p.hidden = true));
    const panel = document.getElementById(`create-panel-${mode}`);
    if (panel) {
        panel.hidden = false;
        console.log("[switchCreateMode] panel found, hidden=", panel.hidden, "parentNode=", panel.parentNode && panel.parentNode.id);
        const steps = panel.querySelectorAll(".wizard-step");
        console.log("[switchCreateMode] steps count=", steps.length);
        steps.forEach(s => console.log("[switchCreateMode] step", s.dataset.step, "hidden=", s.hidden, "opacity=", getComputedStyle(s).opacity, "display=", getComputedStyle(s).display));
        const nav = panel.querySelector(".wizard-nav");
        console.log("[switchCreateMode] nav=", nav ? "found" : "NOT FOUND", "nav.hidden=", nav && nav.hidden);
    } else {
        console.log("[switchCreateMode] panel NOT FOUND for id=create-panel-" + mode);
    }
    document.getElementById("ai-suggest-panel").hidden = true;
    document.getElementById("create-progress").hidden = true;

    if (mode === "library") {
        populateSongSelector();
    }
}

// ── Flow-based Wizard UI Update ──

function updateFlowUI(panelId, stepIndex, flow, prefix) {
    const panel = document.getElementById(panelId);
    if (!panel) return;
    const currentDataStep = flow[stepIndex - 1];

    // Show/hide steps
    panel.querySelectorAll(".wizard-step").forEach((s) => {
        const show = parseInt(s.dataset.step) === currentDataStep;
        s.hidden = !show;
        s.classList.remove("wizard-step-enter");
        if (show) {
            // trigger fade-in animation on next frame
            requestAnimationFrame(() => s.classList.add("wizard-step-enter"));
        }
    });

    // Update dots dynamically
    const dotsContainer = document.getElementById(`${prefix}-dots-container`);
    if (dotsContainer) {
        dotsContainer.innerHTML = flow.map((_, i) =>
            `<span class="wizard-dot${i < stepIndex ? ' active' : ''}"></span>`
        ).join('');
    }

    // Update buttons
    const backBtn = document.getElementById(`${prefix}-back`);
    const nextBtn = document.getElementById(`${prefix}-next`);
    const createBtn = document.getElementById(`${prefix}-create-btn`);
    if (backBtn) backBtn.hidden = false; // Always show — step 1 goes back to mode selection
    if (nextBtn) nextBtn.hidden = stepIndex >= flow.length;
    if (createBtn) createBtn.hidden = stepIndex < flow.length;
}

// ── Shared Realistic Create Logic ──

async function handleRealisticVideoCreate(prompt, durationSelectorId, aspectSelectorId, musicCheckboxId, title, engineSelectorId, prefix, realisticStyle) {
    // Derive prefix from selector IDs if not provided
    if (!prefix) {
        prefix = durationSelectorId.startsWith("wizard") ? "wizard" : "script";
    }

    const useTevoxi = !!(document.getElementById(`${prefix}-realistic-tevoxi`)?.checked);
    const selectedTevoxiSong = !useTevoxi
        ? null
        : (prefix === "wizard" ? _wizardSelectedSong : _scriptSelectedSong);
    const selectedTevoxiClip = !useTevoxi
        ? null
        : (prefix === "wizard" ? _wizardSelectedClip : _scriptSelectedClip);

    if (useTevoxi && !selectedTevoxiSong) {
        alert("Selecione uma música do Tevoxi.");
        return;
    }
    if (useTevoxi && !selectedTevoxiClip) {
        alert("Escolha o trecho da música ou a música inteira.");
        return;
    }

    let finalPrompt = String(prompt || "").trim();
    if (useTevoxi && selectedTevoxiSong && selectedTevoxiClip) {
        const tevoxiContext = _buildTevoxiPromptContext(selectedTevoxiSong, selectedTevoxiClip);
        if (prefix === "wizard") {
            finalPrompt = finalPrompt ? `${finalPrompt}\n\n${tevoxiContext}` : tevoxiContext;
        } else if (!finalPrompt) {
            finalPrompt = tevoxiContext;
        }
    }

    if (!finalPrompt) {
        alert("Descreva a cena que você quer ver no vídeo.");
        return;
    }

    let finalTitle = String(title || "").trim();
    if (!finalTitle && selectedTevoxiSong) {
        finalTitle = String(selectedTevoxiSong.title || "").trim();
    }

    const useScriptPhotosToggle = prefix === "script"
        ? document.getElementById("script-use-photos")
        : null;
    const wantsReferenceImage = prefix === "script"
        ? !!(useScriptPhotosToggle && useScriptPhotosToggle.checked)
        : false;

    if (wantsReferenceImage && scriptPhotos.length === 0) {
        alert("Você ativou 'Usar minhas fotos no vídeo', mas ainda não enviou nenhuma foto.");
        return;
    }

    const durBtn = document.querySelector(`#${durationSelectorId} .duration-option.selected`);
    const duration = durBtn ? parseInt(durBtn.dataset.value, 10) : 10;
    const aspectEl = document.getElementById(aspectSelectorId);
    const aspect = aspectEl ? aspectEl.value : "16:9";
    const musicEl = document.getElementById(musicCheckboxId);
    const addMusic = musicEl ? musicEl.checked : true;
    const addMusicRequested = useTevoxi ? false : addMusic;
    const engineBtn = document.querySelector(`#${engineSelectorId} .engine-option.selected`);
    let engine = engineBtn ? engineBtn.dataset.value : "wan2";
    if (duration > 10 && engine !== "grok") {
        const engineSelector = document.getElementById(engineSelectorId);
        const grokBtn = engineSelector?.querySelector('.engine-option[data-value="grok"]');
        if (grokBtn) {
            engineSelector.querySelectorAll(".engine-option").forEach((d) => d.classList.remove("selected"));
            grokBtn.classList.add("selected");
        }
        engine = "grok";
        showToast("Duracoes acima de 10s usam Cria 3.0 speed automaticamente.");
    }
    const engineLabel = engine === "minimax"
        ? "MiniMax Hailuo"
        : engine === "wan2"
            ? "Ultra High 2.2"
            : engine === "grok"
                ? "Cria 3.0 speed"
                : "Seedance 2.0";
    const personaBtn = document.querySelector(`#${prefix}-realistic-persona-tags .style-tag.selected`);
    const interactionPersona = _normalizeRealisticPersonaType(personaBtn ? (personaBtn.dataset.persona || "") : "natureza");
    let personaProfileId = 0;
    let personaProfileIds = [];

    // Narration fields
    const narrationEl = document.getElementById(`${prefix}-realistic-narration`);
    const addNarration = narrationEl ? narrationEl.checked : false;
    const narrationTextEl = document.getElementById(`${prefix}-realistic-narration-text`);
    const narrationText = addNarration ? (narrationTextEl ? narrationTextEl.value.trim() : "") : "";
    const voiceBtn = document.querySelector(`#${prefix}-realistic-voices .voice-btn.selected`);
    const narrationVoice = voiceBtn ? voiceBtn.dataset.value : "onyx";

    // Show progress, hide create buttons
    const progressEl = document.getElementById("create-progress");
    if (progressEl) progressEl.hidden = false;
    const wizCreateBtn = document.getElementById("wizard-create-btn");
    const scrCreateBtn = document.getElementById("script-create-btn");
    if (wizCreateBtn) wizCreateBtn.hidden = true;
    if (scrCreateBtn) scrCreateBtn.hidden = true;
    setCreateProgress(CREATE_PROGRESS_BASE, "Gerando vídeo realista...", "Preparando...");
    _smoothProgressTarget = 10;
    _startSmoothProgress();

    try {
        const contextKey = prefix === "wizard" ? "wizard" : "script";
        personaProfileIds = await _ensurePersonaSelections(contextKey, interactionPersona);
        personaProfileId = personaProfileIds[0] || 0;

        const selectedPersonaProfiles = personaProfileIds
            .map((sid) => _getPersonaProfiles(interactionPersona).find((profile) => (parseInt(profile?.id || "0", 10) || 0) === sid))
            .filter(Boolean)
            .slice(0, 4);

        const dialogueEnabled = !!(addNarration && !narrationText);
        const dialogueCharacters = selectedPersonaProfiles
            .map((profile) => String(profile?.name || "").trim())
            .filter((name) => !!name)
            .slice(0, 4);
        if (dialogueEnabled && !dialogueCharacters.length) {
            dialogueCharacters.push("Personagem");
        }

        const dialogueVoiceProfileIds = selectedPersonaProfiles
            .map((profile) => _getPersonaVoiceProfileId(profile))
            .filter((id, idx, arr) => id > 0 && arr.indexOf(id) === idx)
            .slice(0, 4);

        const speechMode = dialogueEnabled
            ? "dialogue_auto"
            : (addNarration && narrationText ? "narration_manual" : "none");

        if (!wantsReferenceImage && !personaProfileIds.length) {
            throw new Error("Crie uma ou mais personas de interação primeiro para gerar o vídeo realista.");
        }

        // Upload reference image if available
        let imageUploadId = "";
        let imageUploadIds = [];
        const shouldUploadReferenceImage = scriptPhotos.length > 0 && (prefix !== "script" || wantsReferenceImage);
        if (shouldUploadReferenceImage) {
            setCreateProgress(5, "Gerando vídeo realista...", "Enviando imagem de referência...");
            const photosToUpload = scriptPhotos.slice(0, 6);
            for (const photo of photosToUpload) {
                const uploaded = await uploadTempFileWithRetry(photo, "image", "imagem de referência");
                imageUploadIds.push(uploaded.upload_id);
            }
            imageUploadId = imageUploadIds[0] || "";
            _smoothProgressTarget = 15;
        }

        const speechStatusLabel = speechMode === "dialogue_auto"
            ? "Otimizando prompt e preparando falas automaticas por personagem..."
            : speechMode === "narration_manual"
                ? "Otimizando prompt e preparando narracao do texto informado..."
                : "Otimizando prompt com IA...";
        setCreateProgress(10, "Gerando vídeo realista...", speechStatusLabel);
        _smoothProgressTarget = 15;

        const resp = await api("/video/generate-realistic", {
            method: "POST",
            body: JSON.stringify({
                prompt: finalPrompt,
                duration,
                aspect_ratio: aspect,
                generate_audio: addMusicRequested || addNarration || !!selectedTevoxiSong,
                add_music: addMusicRequested,
                add_narration: addNarration,
                narration_text: narrationText,
                narration_voice: narrationVoice,
                dialogue_enabled: dialogueEnabled,
                dialogue_characters: dialogueEnabled ? dialogueCharacters : [],
                dialogue_voice_profile_ids: dialogueEnabled ? dialogueVoiceProfileIds : [],
                dialogue_tone: "informativo",
                dialogue_duration: dialogueEnabled ? duration : 0,
                title: finalTitle || "",
                image_upload_id: imageUploadId,
                image_upload_ids: imageUploadIds,
                engine: engine,
                audio_url: selectedTevoxiSong ? (selectedTevoxiSong.audio_url || "") : "",
                lyrics: selectedTevoxiSong
                    ? (selectedTevoxiClip?.lyrics_excerpt || selectedTevoxiSong.lyrics || "")
                    : "",
                clip_start: selectedTevoxiClip ? Number(selectedTevoxiClip.clip_start || 0) : 0,
                clip_duration: selectedTevoxiClip ? Number(selectedTevoxiClip.clip_duration || 0) : 0,
                prompt_optimized: scriptData.promptOptimized || false,
                realistic_style: realisticStyle || "",
                interaction_persona: interactionPersona,
                persona_profile_id: personaProfileId,
                persona_profile_ids: personaProfileIds,
            }),
        });

        const projectId = resp.id;

        _smoothProgressTarget = 25;
        setCreateProgress(25, "Gerando vídeo realista...", `${engineLabel} está criando seu vídeo...`);

        await pollRealisticProgress(projectId, engineLabel);

        _stopSmoothProgress();
        setCreateProgress(100, "Concluído!", "Vídeo realista gerado com sucesso!");

        setTimeout(() => {
            closeModal("modal-new-project");
            resetCreateWizard();
            loadProjects();
        }, 1200);

    } catch (e) {
        _stopSmoothProgress();
        let msg = e.message || "Erro ao gerar vídeo realista.";
        if (msg.includes("flagged as sensitive") || msg.includes("E005")) {
            msg = "O conteudo do prompt foi considerado sensivel pelo modelo de IA. Tente reformular seu texto evitando temas violentos, sexuais ou controversos.";
        }
        setCreateProgress(0, "Erro", msg);
        alert(msg);
    }
}

async function pollRealisticProgress(projectId, engineLabel) {
    const maxWait = 12 * 60 * 1000; // 12 minutes
    const pollInterval = 4000;
    const start = Date.now();
    const label = engineLabel || "IA";

    while (Date.now() - start < maxWait) {
        await new Promise(r => setTimeout(r, pollInterval));

        try {
            const resp = await fetch(`${API}/video/projects/${projectId}`, {
                headers: getHeaders(),
            });
            if (!resp.ok) continue;
            const data = await resp.json();

            const progress = data.progress || 0;
            const status = data.status || "";

            _smoothProgressTarget = Math.max(_smoothProgressTarget, progress);
            setCreateProgress(progress, "Gerando vídeo realista...",
                progress < 15 ? "Otimizando prompt com IA..." :
                progress < 80 ? `${label} está criando seu vídeo...` :
                progress < 90 ? "Baixando vídeo gerado..." :
                progress < 95 ? "Gerando thumbnail..." :
                "Finalizando..."
            );

            if (status === "completed") return;
            if (status === "failed") {
                throw new Error(data.error_message || "Falha na geração do vídeo realista.");
            }
        } catch (e) {
            if (e.message && !e.message.includes("fetch")) throw e;
        }
    }
    throw new Error("Tempo limite excedido. O vídeo pode ainda estar sendo gerado — verifique seus projetos.");
}

function resetCreateWizard() {
    stopKaraokeProgressPolling();
    createMode = "wizard";
    wizardStep = 1;
    wizardData = { topic: "", videoType: "imagens_ia", tone: "", voice: "", voiceProfileId: 0, duration: 60, aspect: "16:9", style: "", realisticStyle: "" };
    scriptStep = 1;
    scriptData = {
        text: "",
        videoType: "imagens_ia",
        tone: "",
        voice: "",
        voiceProfileId: 0,
        title: "",
        aspect: "16:9",
        style: "",
        useCustomImages: false,
        useCustomAudio: false,
        useCustomVideo: false,
        audioIsMusic: false,
        removeVocals: false,
        createNarration: true,
        enableSubtitles: true,
        subtitlePositionY: 80,
        enableAudioSpectrum: false,
        useTevoxiAudio: false,
        zoomImages: true,
        imageDisplaySeconds: 0,
        promptOptimized: false,
    };

    // Reset tabs
    document.querySelectorAll(".create-tab").forEach((t) => {
        t.classList.toggle("active", t.dataset.createMode === "wizard");
    });

    // Show mode selection, hide panels
    document.getElementById("create-mode-selection").hidden = false;
    document.querySelectorAll(".create-panel").forEach((p) => (p.hidden = true));
    document.getElementById("ai-suggest-panel").hidden = true;
    document.getElementById("create-progress").hidden = true;
    _stopSmoothProgress();
    _smoothProgressTarget = CREATE_PROGRESS_BASE;
    _smoothProgressCurrent = CREATE_PROGRESS_BASE;
    setCreateProgress(CREATE_PROGRESS_BASE, "Processando...", "Gerando roteiro com IA...");

    // Reset wizard steps
    updateFlowUI("create-panel-wizard", wizardStep, getWizardFlow(), "wizard");
    updateFlowUI("create-panel-script", scriptStep, getScriptFlow(), "script");
    document.getElementById("wizard-topic").value = "";
    const topicInspirationEl = document.getElementById("wizard-topic-inspiration");
    if (topicInspirationEl) topicInspirationEl.hidden = true;
    document.querySelectorAll("#wizard-topic-style-tags .style-tag").forEach((t) => t.classList.remove("selected"));
    document.getElementById("script-text").value = "";
    document.getElementById("script-char-count").textContent = "0";
    document.getElementById("script-title").value = "";
    const bgmInput = document.getElementById("script-bgm-file");
    if (bgmInput) bgmInput.value = "";
    const bgmToggle = document.getElementById("script-enable-bgm");
    if (bgmToggle) {
        bgmToggle.checked = true;
        bgmToggle.disabled = false;
    }
    const bgmGroup = document.getElementById("script-bgm-group");
    if (bgmGroup) bgmGroup.classList.remove("is-locked");
    const bgmLockHint = document.getElementById("script-bgm-lock-hint");
    if (bgmLockHint) bgmLockHint.hidden = true;
    const bgmUploadArea = document.getElementById("script-bgm-upload-area");
    if (bgmUploadArea) bgmUploadArea.hidden = false;
    const bgmFileInput = document.getElementById("script-bgm-file");
    if (bgmFileInput) bgmFileInput.value = "";
    _scriptSelectedSong = null;
    _scriptSelectedClip = null;
    _wizardSelectedSong = null;
    _wizardSelectedClip = null;
    const scriptTevoxiCb = document.getElementById("script-realistic-tevoxi");
    if (scriptTevoxiCb) scriptTevoxiCb.checked = false;
    const scriptTevoxiPanel = document.getElementById("script-tevoxi-panel");
    if (scriptTevoxiPanel) scriptTevoxiPanel.hidden = true;
    const wizardTevoxiCb = document.getElementById("wizard-realistic-tevoxi");
    if (wizardTevoxiCb) wizardTevoxiCb.checked = false;
    const wizardTevoxiPanel = document.getElementById("wizard-tevoxi-panel");
    if (wizardTevoxiPanel) wizardTevoxiPanel.hidden = true;
    _renderScriptTevoxiSongs();
    _renderWizardTevoxiSongs();
    _updateScriptTevoxiSelectionUI();
    _updateWizardTevoxiSelectionUI();

    // Reset photo upload
    scriptPhotos = [];
    const photoCb = document.getElementById("script-use-photos");
    if (photoCb) photoCb.checked = false;
    const photoArea = document.getElementById("script-photo-area");
    if (photoArea) photoArea.hidden = true;
    const photoGrid = document.getElementById("script-photo-preview");
    if (photoGrid) photoGrid.innerHTML = "";
    const photoCount = document.getElementById("script-photo-count");
    if (photoCount) photoCount.hidden = true;
    const narChoice = document.getElementById("script-narration-choice");
    if (narChoice) narChoice.hidden = true;
    const narCb = document.getElementById("script-create-narration");
    if (narCb) narCb.checked = true;

    // Reset user audio upload
    scriptUserAudioFile = null;
    const userAudioCb = document.getElementById("script-use-user-audio");
    if (userAudioCb) userAudioCb.checked = false;
    const userAudioArea = document.getElementById("script-user-audio-area");
    if (userAudioArea) userAudioArea.hidden = true;
    const userAudioInput = document.getElementById("script-user-audio-input");
    if (userAudioInput) userAudioInput.value = "";
    const userAudioName = document.getElementById("script-user-audio-name");
    if (userAudioName) {
        userAudioName.hidden = true;
        userAudioName.textContent = "";
    }
    const audioIsMusicCb = document.getElementById("script-audio-is-music");
    if (audioIsMusicCb) audioIsMusicCb.checked = false;

    // Reset user video upload
    scriptUserVideoFile = null;
    const userVideoCb = document.getElementById("script-use-video");
    if (userVideoCb) userVideoCb.checked = false;
    const userVideoArea = document.getElementById("script-video-area");
    if (userVideoArea) userVideoArea.hidden = true;
    const userVideoInput = document.getElementById("script-video-input");
    if (userVideoInput) userVideoInput.value = "";
    const userVideoName = document.getElementById("script-video-name");
    if (userVideoName) {
        userVideoName.hidden = true;
        userVideoName.textContent = "";
    }
    const videoNarChoice = document.getElementById("script-video-narration-choice");
    if (videoNarChoice) videoNarChoice.hidden = true;
    const videoNarCb = document.getElementById("script-video-create-narration");
    if (videoNarCb) videoNarCb.checked = true;

    // Reset thumbnail upload
    scriptThumbFile = null;
    const thumbFileInput = document.getElementById("script-thumb-file");
    if (thumbFileInput) thumbFileInput.value = "";
    const thumbPreview = document.getElementById("script-thumb-preview");
    if (thumbPreview) { thumbPreview.hidden = true; thumbPreview.src = ""; }
    const thumbRemoveBtn = document.getElementById("script-thumb-remove");
    if (thumbRemoveBtn) thumbRemoveBtn.hidden = true;

    // Reset subtitle toggle
    const subCb = document.getElementById("script-enable-subtitles");
    if (subCb) subCb.checked = true;
    const subtitlePos = document.getElementById("script-subtitle-position-y");
    if (subtitlePos) subtitlePos.value = "80";
    const audioSpectrumCb = document.getElementById("script-enable-audio-spectrum");
    if (audioSpectrumCb) audioSpectrumCb.checked = false;
    const audioSpectrumGroup = document.getElementById("script-audio-spectrum-group");
    if (audioSpectrumGroup) audioSpectrumGroup.hidden = true;
    const imageSecondsInput = document.getElementById("script-image-seconds");
    if (imageSecondsInput) imageSecondsInput.value = "";
    toggleScriptPhotoDependentFields();
    toggleAudioMusicOptions();
    _updateScriptSubtitlePositionVisibility();
    _updateScriptDetailsForTevoxiMode();

    // Reset selections
    document.querySelectorAll(".wizard-option.selected").forEach((o) => o.classList.remove("selected"));
    document.querySelectorAll(".duration-option").forEach((d) => {
        d.classList.toggle("selected", d.dataset.value === "60");
    });
    // Reset style tags
    document.querySelectorAll(".style-tag.selected").forEach((t) => t.classList.remove("selected"));
    const defWizardPersona = document.querySelector('#wizard-realistic-persona-tags [data-persona="natureza"]');
    if (defWizardPersona) defWizardPersona.classList.add("selected");
    const defScriptPersona = document.querySelector('#script-realistic-persona-tags [data-persona="natureza"]');
    if (defScriptPersona) defScriptPersona.classList.add("selected");
    const defAiPersona = document.querySelector('#ai-suggest-persona-tags [data-persona="natureza"]');
    if (defAiPersona) {
        document.querySelectorAll("#ai-suggest-persona-tags .style-tag").forEach((t) => t.classList.remove("selected"));
        defAiPersona.classList.add("selected");
    }

    [
        "wizard-realistic-multi-persona",
        "script-realistic-multi-persona",
        "ai-realistic-multi-persona",
        "auto-realistic-multi-persona",
    ].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.checked = false;
    });

    _personaSelectionByContext.wizard = {};
    _personaSelectionByContext.script = {};
    _personaSelectionByContext.ai = {};
    _personaMultiSelectionByContext.wizard = {};
    _personaMultiSelectionByContext.script = {};
    _personaMultiSelectionByContext.ai = {};
    _refreshPersonaContext("wizard", "natureza");
    _refreshPersonaContext("script", "natureza");
    _refreshPersonaContext("ai", "natureza");

    // Load voice profiles into selectors
    loadVoiceProfiles();
    // Initialize voice preview buttons
    initVoicePreview();
    // Initialize style tag toggles
    initStyleTags();
    // Initialize pause option buttons
    initPauseOptions();
    // Reset video type cards
    document.querySelectorAll(".video-type-card").forEach(c => {
        c.classList.toggle("selected", c.dataset.type === "imagens_ia");
    });
    // Reset realistic settings in both panels
    ["wizard-realistic-duration", "script-realistic-duration"].forEach(id => {
        document.querySelectorAll(`#${id} .duration-option`).forEach(d => {
            d.classList.toggle("selected", d.dataset.value === "10");
        });
    });
    document.querySelectorAll("#ai-suggest-realistic-duration .duration-option").forEach((d) => {
        d.classList.toggle("selected", d.dataset.value === "10");
    });
    ["wizard-realistic-aspect", "script-realistic-aspect"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.value = "16:9";
    });
    ["wizard-realistic-audio", "script-realistic-audio"].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.checked = true;
    });
    // Reset script step adaptations back to normal mode
    adaptScriptStepForVideoType("imagens_ia");
    // Update UI for both wizards
    updateFlowUI("create-panel-wizard", wizardStep, getWizardFlow(), "wizard");
    updateFlowUI("create-panel-script", scriptStep, getScriptFlow(), "script");
}

// ── Wizard (Assistente) Navigation ──

function wizardNext() {
    const flow = getWizardFlow();
    const currentDataStep = flow[wizardStep - 1];

    if (currentDataStep === 2) {
        // Capture video type selection
        const sel = document.querySelector("#wizard-video-type-grid .video-type-card.selected");
        if (!sel) { alert("Escolha o tipo de vídeo."); return; }
        wizardData.videoType = sel.dataset.type;
        // Show/hide style buttons on topic step for realistic mode
        const topicInspirationEl = document.getElementById("wizard-topic-inspiration");
        if (topicInspirationEl) topicInspirationEl.hidden = wizardData.videoType !== "realista";
    }
    if (currentDataStep === 1) {
        const topic = document.getElementById("wizard-topic").value.trim();
        if (!topic) { alert("Digite o tema do vídeo."); return; }
        wizardData.topic = topic;
    }
    if (currentDataStep === 3) {
        const sel = document.querySelector("#create-panel-wizard .wizard-step[data-step='3'] .wizard-option.selected");
        if (!sel) { alert("Escolha o tom da narração."); return; }
        wizardData.tone = sel.dataset.value;
    }
    if (currentDataStep === 4) {
        const personaSel = document.querySelector("#wizard-persona-list .persona-item.selected");
        const builtinSel = document.querySelector("#create-panel-wizard .wizard-step[data-step='4'] .wizard-option[data-voice-type='builtin'].selected");
        const sunoSel = document.querySelector("#create-panel-wizard .wizard-step[data-step='4'] .wizard-option[data-voice-type='suno'].selected");
        if (personaSel) {
            wizardData.voice = personaSel.dataset.value;
            wizardData.voiceProfileId = parseInt(personaSel.dataset.profileId || "0");
            wizardData.voiceType = "custom";
        } else if (sunoSel) {
            wizardData.voice = sunoSel.dataset.value;
            wizardData.voiceProfileId = 0;
            wizardData.voiceType = "suno";
        } else if (builtinSel) {
            wizardData.voice = builtinSel.dataset.value;
            wizardData.voiceProfileId = 0;
            wizardData.voiceType = "builtin";
        } else {
            alert("Escolha a voz."); return;
        }
    }
    wizardStep = Math.min(wizardStep + 1, flow.length);
    updateFlowUI("create-panel-wizard", wizardStep, getWizardFlow(), "wizard");
}

function wizardBack() {
    if (wizardStep <= 1) {
        // Go back to mode selection
        document.getElementById("create-panel-wizard").hidden = true;
        document.getElementById("create-mode-selection").hidden = false;
        return;
    }
    wizardStep = Math.max(wizardStep - 1, 1);
    // When going back to video type step, reset to normal flow so dots update
    if (getWizardFlow()[wizardStep - 1] === 2) {
        wizardData.videoType = wizardData.videoType; // keep current type
    }
    updateFlowUI("create-panel-wizard", wizardStep, getWizardFlow(), "wizard");
}

async function handleWizardCreate() {
    // Check if this is a realistic video
    if (wizardData.videoType === "realista") {
        await handleRealisticVideoCreate(
            wizardData.topic,
            "wizard-realistic-duration",
            "wizard-realistic-aspect",
            "wizard-realistic-music",
            wizardData.topic,
            "wizard-realistic-engine",
            "wizard",
            wizardData.realisticStyle || ""
        );
        return;
    }

    // Collect step 5 (style) + step 6 (duration/format) data
    const durBtn = document.querySelector("#create-panel-wizard .duration-option.selected");
    wizardData.duration = durBtn ? parseInt(durBtn.dataset.value) : 60;
    wizardData.aspect = document.getElementById("wizard-aspect").value;
    wizardData.style = getSelectedStyles("wizard-style-tags");
    wizardData.pauseLevel = getSelectedPause("wizard-pause-options");

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

        showCreateProgress("Gerando narração com voz IA...");

        // Step 2: Generate audio + create project
        const result = await api("/video/generate-audio", {
            method: "POST",
            body: JSON.stringify({
                script: scriptResult.script,
                voice: wizardData.voice,
                voice_profile_id: wizardData.voiceProfileId,
                voice_type: wizardData.voiceType || "",
                title: wizardData.topic,
                aspect_ratio: wizardData.aspect,
                style_prompt: wizardData.style,
                pause_level: wizardData.pauseLevel,
                tone: wizardData.tone,
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
    const flow = getScriptFlow();
    const currentDataStep = flow[scriptStep - 1];

    if (currentDataStep === 2) {
        // Capture video type selection (first step)
        const selectedCard = document.querySelector("#script-video-type-grid .video-type-card.selected");
        if (!selectedCard) { alert("Escolha o tipo de vídeo."); return; }
        scriptData.videoType = selectedCard.dataset.type;
        // Adapt next step UI for video type
        adaptScriptStepForVideoType(scriptData.videoType);
    }

    if (currentDataStep === 1) {
        const title = document.getElementById("script-title").value.trim();
        const text = document.getElementById("script-text").value.trim();

        // Realistic mode: only need prompt text, optionally photos/audio
        if (scriptData.videoType === "realista") {
            if (!title && !text) { alert("Escreva um título ou um prompt para o vídeo."); return; }
            scriptData.title = title || text.substring(0, 100);
            scriptData.text = text;
            const usePhotos = document.getElementById("script-use-photos").checked;
            scriptData.useCustomImages = usePhotos && scriptPhotos.length > 0;
            const useUserAudioToggle = document.getElementById("script-use-user-audio")?.checked;
            const hasUserAudio = useUserAudioToggle && !!scriptUserAudioFile;
            scriptData.useCustomAudio = hasUserAudio;
            // Realistic flow: advance normally to step 7 (realistic settings)
        } else {

        const usePhotos = document.getElementById("script-use-photos").checked;
        const useVideo = document.getElementById("script-use-video") ? document.getElementById("script-use-video").checked : false;
        const hasVideo = useVideo && !!scriptUserVideoFile;
        const useUserAudioToggle = document.getElementById("script-use-user-audio")
            ? document.getElementById("script-use-user-audio").checked
            : false;
        const hasUserAudio = useUserAudioToggle && !!scriptUserAudioFile;
        const createNarration = !usePhotos || document.getElementById("script-create-narration").checked;
        const videoCreateNarration = hasVideo ? !!document.getElementById("script-video-create-narration")?.checked : false;
        if (!title) { alert("Digite o título do projeto."); return; }

        if (hasVideo) {
            scriptData.title = title;
            scriptData.useCustomVideo = true;
            scriptData.useCustomImages = false;
            scriptData.useCustomAudio = false;
            scriptData.audioIsMusic = false;
            scriptData.removeVocals = false;
            scriptData.createNarration = videoCreateNarration;
            scriptData.text = videoCreateNarration ? text : "";
            if (videoCreateNarration && (!text || text.length < 20)) {
                alert("Escreva um roteiro com pelo menos 20 caracteres para a narração.");
                return;
            }
        } else {
            if (useUserAudioToggle && !hasUserAudio) {
                alert("Envie um áudio para usar no vídeo.");
                return;
            }
            if (createNarration && !hasUserAudio && (!text || text.length < 20)) {
                alert("Escreva um roteiro com pelo menos 20 caracteres.");
                return;
            }
            if (!createNarration && scriptPhotos.length === 0 && !hasUserAudio) {
                alert("Envie fotos para criar o vídeo sem narração.");
                return;
            }
            scriptData.title = title;
            scriptData.useCustomVideo = false;
            scriptData.useCustomImages = usePhotos && scriptPhotos.length > 0;
            scriptData.useCustomAudio = hasUserAudio;
            scriptData.audioIsMusic = hasUserAudio ? !!document.getElementById("script-audio-is-music")?.checked : false;
            scriptData.removeVocals = hasUserAudio && scriptData.audioIsMusic;
            scriptData.createNarration = hasUserAudio ? false : createNarration;
            scriptData.text = hasUserAudio ? text : (createNarration ? text : "");

            const bgmToggle = document.getElementById("script-enable-bgm");
            const bgmUploadArea = document.getElementById("script-bgm-upload-area");
            const bgmInput = document.getElementById("script-bgm-file");
            if (bgmToggle && scriptData.useCustomAudio && scriptData.audioIsMusic) {
                bgmToggle.checked = false;
                if (bgmUploadArea) bgmUploadArea.hidden = true;
                if (bgmInput) bgmInput.value = "";
            }
        }
        } // end else (not realistic)

        // Narration skip (only for normal flow, not realistic)
        if (scriptData.videoType !== "realista") {
            const needsNarration = scriptData.createNarration && !scriptData.useCustomAudio;
            if (!needsNarration) {
                scriptStep = 5;
                updateFlowUI("create-panel-script", scriptStep, getScriptFlow(), "script");
                return;
            }
        }
    }

    if (currentDataStep === 3) {
        if (!scriptData.text) {
            scriptData.tone = "informativo";
        } else {
            const sel = document.querySelector("#create-panel-script .wizard-step[data-step='3'] .wizard-option.selected");
            if (!sel) { alert("Escolha o tom da narração."); return; }
            scriptData.tone = sel.dataset.value;
        }
    }

    if (currentDataStep === 4) {
        if (!scriptData.text) {
            scriptData.voice = "onyx";
            scriptData.voiceProfileId = 0;
            scriptData.voiceType = "builtin";
        } else {
            const personaSel = document.querySelector("#script-persona-list .persona-item.selected");
            const builtinSel = document.querySelector("#create-panel-script .wizard-step[data-step='4'] .wizard-option[data-voice-type='builtin'].selected");
            const sunoSel = document.querySelector("#create-panel-script .wizard-step[data-step='4'] .wizard-option[data-voice-type='suno'].selected");
            if (personaSel) {
                scriptData.voice = personaSel.dataset.value;
                scriptData.voiceProfileId = parseInt(personaSel.dataset.profileId || "0");
                scriptData.voiceType = "custom";
            } else if (sunoSel) {
                scriptData.voice = sunoSel.dataset.value;
                scriptData.voiceProfileId = 0;
                scriptData.voiceType = "suno";
            } else if (builtinSel) {
                scriptData.voice = builtinSel.dataset.value;
                scriptData.voiceProfileId = 0;
                scriptData.voiceType = "builtin";
            } else {
                alert("Escolha a voz."); return;
            }
        }
    }

    scriptStep = Math.min(scriptStep + 1, flow.length);
    updateFlowUI("create-panel-script", scriptStep, getScriptFlow(), "script");
}

function scriptBack() {
    if (scriptStep <= 1) {
        // Go back to mode selection
        document.getElementById("create-panel-script").hidden = true;
        document.getElementById("create-mode-selection").hidden = false;
        return;
    }
    const flow = getScriptFlow();
    const currentDataStep = flow[scriptStep - 1];

    // If at details step (data-step 5) and narration was skipped, go back to script step (position 2)
    if (currentDataStep === 5 && !scriptData.createNarration) {
        scriptStep = 2;
    } else {
        scriptStep = Math.max(scriptStep - 1, 1);
    }
    updateFlowUI("create-panel-script", scriptStep, getScriptFlow(), "script");
}

async function handleScriptCreate() {
    // Check if this is a realistic video
    if (scriptData.videoType === "realista") {
        const scriptText = document.getElementById("script-text").value.trim();
        const prompt = scriptText || scriptData.title || "";
        const realisticTitle = (document.getElementById("script-title").value || "").trim() || scriptData.title || "";
        await handleRealisticVideoCreate(
            prompt,
            "script-realistic-duration",
            "script-realistic-aspect",
            "script-realistic-music",
            realisticTitle,
            "script-realistic-engine"
        );
        return;
    }

    scriptData.title = document.getElementById("script-title").value.trim();
    scriptData.text = document.getElementById("script-text").value.trim();
    scriptData.aspect = document.getElementById("script-aspect").value;
    scriptData.style = getSelectedStyles("script-style-tags");
    scriptData.pauseLevel = getSelectedPause("script-pause-options");
    const usePhotosSelected = document.getElementById("script-use-photos").checked;
    const useVideoSelected = document.getElementById("script-use-video") ? document.getElementById("script-use-video").checked : false;
    const useAudioSelected = document.getElementById("script-use-user-audio")
        ? document.getElementById("script-use-user-audio").checked
        : false;
    scriptData.zoomImages = true;
    scriptData.imageDisplaySeconds = usePhotosSelected
        ? (parseFloat(document.getElementById("script-image-seconds").value || "0") || 0)
        : 0;
    scriptData.useCustomVideo = useVideoSelected && !!scriptUserVideoFile;
    scriptData.useCustomImages = !scriptData.useCustomVideo && usePhotosSelected && scriptPhotos.length > 0;
    scriptData.useCustomAudio = !scriptData.useCustomVideo && useAudioSelected && !!scriptUserAudioFile;
    scriptData.audioIsMusic = scriptData.useCustomAudio ? !!document.getElementById("script-audio-is-music")?.checked : false;
    scriptData.removeVocals = scriptData.useCustomAudio && scriptData.audioIsMusic;

    if (scriptData.useCustomVideo) {
        const videoNarCb = document.getElementById("script-video-create-narration");
        scriptData.createNarration = videoNarCb ? videoNarCb.checked : false;
        scriptData.enableSubtitles = document.getElementById("script-enable-subtitles").checked;
    } else {
        scriptData.createNarration = scriptData.useCustomAudio
            ? false
            : (!scriptData.useCustomImages || document.getElementById("script-create-narration").checked);
        scriptData.enableSubtitles = document.getElementById("script-enable-subtitles").checked;
    }

    if (!scriptData.createNarration && !scriptData.useCustomAudio && !scriptData.useCustomVideo) {
        scriptData.text = "";
        scriptData.voice = "";
        scriptData.voiceProfileId = 0;
        scriptData.voiceType = "";
    }
    if (scriptData.useCustomAudio && scriptData.audioIsMusic) {
        scriptData.enableSubtitles = true;
    }
    const bgmEnabled = document.getElementById("script-enable-bgm") ? document.getElementById("script-enable-bgm").checked : true;
    const bgmFileInput = document.getElementById("script-bgm-file");
    const bgmFile = bgmEnabled && bgmFileInput && bgmFileInput.files ? bgmFileInput.files[0] : null;

    if (useAudioSelected && !scriptData.useCustomAudio && !scriptData.useCustomVideo) {
        alert("Selecione um arquivo de áudio para usar no vídeo.");
        return;
    }

    if (useVideoSelected && !scriptData.useCustomVideo) {
        alert("Selecione um vídeo para enviar.");
        return;
    }

    if (!scriptData.text && !scriptData.useCustomImages && !scriptData.useCustomAudio && !scriptData.useCustomVideo) {
        alert("Sem narração, envie fotos, vídeo ou áudio para criar um vídeo personalizado.");
        return;
    }

    // Credit check: estimate minutes from word count
    const wordCount = scriptData.text ? scriptData.text.split(/\s+/).filter(Boolean).length : 0;
    const estMinutes = Math.max(1, Math.ceil(wordCount / 150));
    const creditsNeeded = estMinutes * _creditsPerMinute;
    if (_userCredits < creditsNeeded) {
        showCreditsPurchaseModal();
        return;
    }

    const startMessage = scriptData.useCustomVideo
        ? "Preparando vídeo com legendas..."
        : scriptData.useCustomAudio
        ? "Preparando vídeo a partir do seu áudio..."
        : (scriptData.text ? "Gerando narração com voz IA..." : "Preparando vídeo com fotos (música automática se não enviar)...");
    const startStage = scriptData.removeVocals ? "Removendo voz..." : "Processando...";
    showCreateProgress(startMessage, { progress: 12, stage: startStage });

    try {
        const uploadedImageIds = [];
        let uploadedMusicId = "";
        let uploadedMainAudioId = "";
        let uploadedVideoId = "";
        let uploadedThumbId = "";
        let karaokeOperationId = "";

        if (scriptData.useCustomVideo && scriptUserVideoFile) {
            showCreateProgress("Enviando vídeo...", { progress: 15, stage: "Enviando arquivos..." });
            const uploadedVideo = await uploadTempFileWithRetry(scriptUserVideoFile, "video", "video");
            uploadedVideoId = uploadedVideo.upload_id || "";
        }

        if (scriptData.useCustomImages) {
            for (let i = 0; i < scriptPhotos.length; i++) {
                const uploadProgress = Math.round(15 + ((i + 1) / scriptPhotos.length) * 25);
                showCreateProgress(`Enviando foto ${i + 1}/${scriptPhotos.length}...`, {
                    progress: uploadProgress,
                    stage: "Enviando arquivos...",
                });
                const uploaded = await uploadTempFileWithRetry(scriptPhotos[i], "image", `foto ${i + 1}`);
                uploadedImageIds.push(uploaded.upload_id);
            }
        }

        if (bgmFile) {
            showCreateProgress("Enviando fundo musical...", { progress: 42, stage: "Enviando arquivos..." });
            const uploadedAudio = await uploadTempFileWithRetry(bgmFile, "audio", "audio");
            uploadedMusicId = uploadedAudio.upload_id || "";
        }

        if (scriptData.useCustomAudio && scriptUserAudioFile) {
            showCreateProgress("Enviando áudio principal...", { progress: 48, stage: "Enviando arquivos..." });
            const uploadedMainAudio = await uploadTempFileWithRetry(scriptUserAudioFile, "audio", "audio principal");
            uploadedMainAudioId = uploadedMainAudio.upload_id || "";
        }

        if (scriptData.removeVocals) {
            karaokeOperationId = createKaraokeOperationId();
            showCreateProgress("Removendo voz do áudio...", { progress: 52, stage: "Removendo voz..." });
            startKaraokeProgressPolling(karaokeOperationId);
        } else {
            showCreateProgress(startMessage, { progress: 52, stage: "Processando..." });
        }

        if (scriptThumbFile) {
            showCreateProgress("Enviando thumbnail...", { progress: 54, stage: "Enviando arquivos..." });
            const uploadedThumb = await uploadTempFileWithRetry(scriptThumbFile, "image", "thumbnail");
            uploadedThumbId = uploadedThumb.upload_id || "";
        }

        const formData = new FormData();
        formData.append("script", scriptData.text);
        formData.append("voice", scriptData.voice || "");
        formData.append("voice_profile_id", String(scriptData.voiceProfileId || 0));
        formData.append("voice_type", scriptData.voiceType || "");
        formData.append("title", scriptData.title || "Vídeo com roteiro");
        formData.append("aspect_ratio", scriptData.aspect);
        formData.append("style_prompt", scriptData.style);
        formData.append("pause_level", scriptData.pauseLevel || "normal");
        formData.append("tone", scriptData.tone || "informativo");
        formData.append("enable_subtitles", scriptData.enableSubtitles ? "true" : "false");
        formData.append("zoom_images", scriptData.zoomImages ? "true" : "false");
        formData.append("image_display_seconds", String(scriptData.imageDisplaySeconds > 0 ? scriptData.imageDisplaySeconds : 0));
        formData.append("use_custom_audio", scriptData.useCustomAudio ? "true" : "false");
        formData.append("audio_is_music", scriptData.audioIsMusic ? "true" : "false");
        formData.append("remove_vocals", scriptData.removeVocals ? "true" : "false");

        const disableBackgroundMusic = scriptData.useCustomAudio || !bgmEnabled;
        formData.append("no_background_music", disableBackgroundMusic ? "true" : "false");
        if (karaokeOperationId) {
            formData.append("karaoke_operation_id", karaokeOperationId);
        }

        if (uploadedMainAudioId) {
            formData.append("custom_audio_id", uploadedMainAudioId);
        }
        if (uploadedVideoId) {
            formData.append("custom_video_id", uploadedVideoId);
        }
        if (uploadedThumbId) {
            formData.append("custom_thumbnail_id", uploadedThumbId);
        }
        if (uploadedMusicId && !scriptData.useCustomAudio) {
            formData.append("background_music_id", uploadedMusicId);
        }
        if (uploadedImageIds.length > 0) {
            for (const uploadId of uploadedImageIds) {
                formData.append("custom_image_ids", uploadId);
            }
        }

        const result = await apiForm("/video/generate-audio", formData);
        stopKaraokeProgressPolling();
        setCreateProgress(100, "Concluído", "Áudio processado com sucesso.");

        closeModal("modal-new-project");
        updateCreditsDisplay();
        pollProject(result.id);
        loadProjects();
    } catch (error) {
        stopKaraokeProgressPolling();
        hideCreateProgress();
        if (error.message && error.message.includes("insuficientes")) {
            showCreditsPurchaseModal();
        } else {
            alert(`Erro: ${error.message}`);
        }
    }
}

async function uploadTempFileWithRetry(file, kind, label) {
    // Try simple direct upload first (most reliable)
    const endpoint = kind === "audio" ? "/video/upload-temp-audio" : kind === "video" ? "/video/upload-temp-video" : "/video/upload-temp-image";
    const maxRetries = 5;

    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
            const fd = new FormData();
            fd.append("file", file);
            showCreateProgress(`Enviando ${label}...`, { stage: "Enviando arquivos..." });
            const result = await apiForm(endpoint, fd);
            return result;
        } catch (error) {
            if (attempt === maxRetries) {
                throw new Error(`Falha ao enviar ${label} apos ${maxRetries} tentativas. Verifique sua conexao.`);
            }
            const delay = Math.min(5000, 500 * Math.pow(2, attempt - 1));
            showCreateProgress(`Reenviando ${label} (${attempt}/${maxRetries})...`, { stage: "Enviando arquivos..." });
            await new Promise((resolve) => setTimeout(resolve, delay));
        }
    }
}

// ── AI Script Suggestion ──

// ── Photo Upload (Meu Roteiro) ──
let scriptPhotos = []; // array of File objects
let scriptUserAudioFile = null;
let scriptUserVideoFile = null; // single File object for custom video
const MAX_PHOTOS = 20;
const MAX_AI_SCRIPT_PHOTO_ANALYSIS = 8;
const MAX_PHOTO_SIZE = 10 * 1024 * 1024; // 10MB
const MAX_AUDIO_SIZE = 80 * 1024 * 1024; // 80MB
const MAX_VIDEO_SIZE = 500 * 1024 * 1024; // 500MB

function togglePhotoUpload() {
    const checked = document.getElementById("script-use-photos").checked;
    document.getElementById("script-photo-area").hidden = !checked;
    // Photos and video are mutually exclusive
    if (checked) {
        const videoCb = document.getElementById("script-use-video");
        if (videoCb && videoCb.checked) {
            videoCb.checked = false;
            toggleVideoUpload();
        }
    }
    toggleScriptPhotoDependentFields();
    if (!checked) {
        scriptData.createNarration = true;
        const narCb = document.getElementById("script-create-narration");
        if (narCb) narCb.checked = true;
    }
    updateNarrationChoiceVisibility();
}

function toggleVideoUpload() {
    const checked = document.getElementById("script-use-video").checked;
    const area = document.getElementById("script-video-area");
    if (area) area.hidden = !checked;
    // Video and photos are mutually exclusive
    if (checked) {
        const photoCb = document.getElementById("script-use-photos");
        if (photoCb && photoCb.checked) {
            photoCb.checked = false;
            document.getElementById("script-photo-area").hidden = true;
        }
    }
    if (!checked) {
        scriptUserVideoFile = null;
        const input = document.getElementById("script-video-input");
        if (input) input.value = "";
        const nameEl = document.getElementById("script-video-name");
        if (nameEl) { nameEl.hidden = true; nameEl.textContent = ""; }
        const narChoice = document.getElementById("script-video-narration-choice");
        if (narChoice) narChoice.hidden = true;
    }
    toggleScriptPhotoDependentFields();
}

function handleUserVideoSelect(event) {
    const file = event.target.files && event.target.files[0] ? event.target.files[0] : null;
    if (!file) return;

    const validTypes = ["video/mp4", "video/quicktime", "video/x-msvideo", "video/webm", "video/x-matroska"];
    if (!file.type.startsWith("video/") && !validTypes.includes(file.type)) {
        alert("Formato não suportado. Envie um vídeo MP4, MOV, AVI ou WEBM.");
        event.target.value = "";
        return;
    }
    if (file.size > MAX_VIDEO_SIZE) {
        alert("Vídeo excede 500MB. Reduza o tamanho e tente novamente.");
        event.target.value = "";
        return;
    }

    scriptUserVideoFile = file;
    const nameEl = document.getElementById("script-video-name");
    if (nameEl) {
        nameEl.hidden = false;
        nameEl.textContent = "Vídeo selecionado: " + file.name + " (" + (file.size / 1024 / 1024).toFixed(1) + "MB)";
    }
    const narChoice = document.getElementById("script-video-narration-choice");
    if (narChoice) narChoice.hidden = false;
}

function toggleScriptVideoNarration() {
    // Narration toggle for custom video mode — controls whether to add AI narration over the video
}

// ── Thumbnail upload for new project ──
let scriptThumbFile = null;

function handleScriptThumbSelect(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    if (!file.type.match(/^image\/(jpeg|png|webp)$/)) {
        alert("Use JPG, PNG ou WebP.");
        event.target.value = "";
        return;
    }
    if (file.size > 10 * 1024 * 1024) {
        alert("Imagem excede 10MB.");
        event.target.value = "";
        return;
    }
    scriptThumbFile = file;
    const preview = document.getElementById("script-thumb-preview");
    if (preview) {
        preview.src = URL.createObjectURL(file);
        preview.hidden = false;
    }
    const removeBtn = document.getElementById("script-thumb-remove");
    if (removeBtn) removeBtn.hidden = false;
}

function removeScriptThumb() {
    scriptThumbFile = null;
    const input = document.getElementById("script-thumb-file");
    if (input) input.value = "";
    const preview = document.getElementById("script-thumb-preview");
    if (preview) { preview.hidden = true; preview.src = ""; }
    const removeBtn = document.getElementById("script-thumb-remove");
    if (removeBtn) removeBtn.hidden = true;
}

function toggleUserAudioUpload() {
    const checked = document.getElementById("script-use-user-audio").checked;
    const area = document.getElementById("script-user-audio-area");
    if (area) area.hidden = !checked;
    toggleScriptPhotoDependentFields();

    if (!checked) {
        scriptUserAudioFile = null;
        const input = document.getElementById("script-user-audio-input");
        if (input) input.value = "";
        const nameEl = document.getElementById("script-user-audio-name");
        if (nameEl) {
            nameEl.hidden = true;
            nameEl.textContent = "";
        }
    }

    toggleAudioMusicOptions();
}

function toggleAudioMusicOptions() {
    const useAudio = document.getElementById("script-use-user-audio")?.checked && !!scriptUserAudioFile;
    const isMusic = !!document.getElementById("script-audio-is-music")?.checked;
    scriptData.audioIsMusic = useAudio && isMusic;
    scriptData.removeVocals = useAudio && isMusic;
}

function handleUserAudioSelect(event) {
    const file = event.target.files && event.target.files[0] ? event.target.files[0] : null;
    if (!file) return;

    if (!file.type.startsWith("audio/")) {
        alert("Formato não suportado. Envie um arquivo de áudio válido.");
        event.target.value = "";
        return;
    }
    if (file.size > MAX_AUDIO_SIZE) {
        alert("Áudio excede 80MB. Reduza o tamanho e tente novamente.");
        event.target.value = "";
        return;
    }

    scriptUserAudioFile = file;
    const nameEl = document.getElementById("script-user-audio-name");
    if (nameEl) {
        nameEl.hidden = false;
        nameEl.textContent = `Audio selecionado: ${file.name}`;
    }
    toggleAudioMusicOptions();
}

function toggleScriptPhotoDependentFields() {
    const usePhotos = document.getElementById("script-use-photos").checked;
    const useVideo = document.getElementById("script-use-video") ? document.getElementById("script-use-video").checked : false;
    const useUserAudio = document.getElementById("script-use-user-audio")
        ? document.getElementById("script-use-user-audio").checked
        : false;
    const imageSecondsGroup = document.getElementById("script-photo-seconds-group");
    const subtitlesGroup = document.getElementById("script-subtitles-group");
    if (imageSecondsGroup) imageSecondsGroup.hidden = !usePhotos || useVideo;
    // Subtitles group is always visible so user can toggle subtitles for any mode
}

function toggleScriptNarration() {
    // In realistic mode, narration controls don't apply
    if (scriptData.videoType === "realista") {
        const textarea = document.getElementById("script-text");
        const aiBtn = document.getElementById("btn-ai-suggest-script");
        if (textarea) { textarea.disabled = false; textarea.placeholder = "Cole ou escreva seu prompt aqui..."; }
        if (aiBtn) aiBtn.disabled = false;
        return;
    }
    const usePhotos = document.getElementById("script-use-photos").checked;
    const createNarration = document.getElementById("script-create-narration").checked;
    const textarea = document.getElementById("script-text");
    const aiBtn = document.getElementById("btn-ai-suggest-script");
    scriptData.createNarration = !usePhotos || createNarration;
    const disableText = usePhotos && !createNarration;
    textarea.disabled = disableText;
    if (aiBtn) aiBtn.disabled = disableText;
    if (disableText) {
        textarea.placeholder = "Narração desativada. O vídeo será criado com fotos + fundo musical.";
    } else {
        textarea.placeholder = "Cole ou escreva o roteiro completo da narração aqui...";
    }
}

function updateNarrationChoiceVisibility() {
    const usePhotos = document.getElementById("script-use-photos").checked;
    const narChoice = document.getElementById("script-narration-choice");
    const narCb = document.getElementById("script-create-narration");
    // In realistic mode, never show narration choice
    if (scriptData.videoType === "realista") {
        if (narChoice) narChoice.hidden = true;
        toggleScriptNarration();
        return;
    }
    const wasHidden = narChoice ? narChoice.hidden : true;
    const shouldShow = usePhotos && scriptPhotos.length > 0;
    if (narChoice) narChoice.hidden = !shouldShow;
    // When photos are first added, default narration to OFF
    if (shouldShow && wasHidden && narCb) {
        narCb.checked = false;
    }
    toggleScriptNarration();
}

function _isScriptTevoxiMainAudioMode() {
    const enabled = !!(document.getElementById("script-realistic-tevoxi")?.checked);
    if (!enabled) return false;
    if (scriptData.videoType === "realista") return false;
    return !!_scriptSelectedSong && !!_scriptSelectedClip;
}

function _updateScriptSubtitlePositionVisibility() {
    const subtitleToggle = document.getElementById("script-enable-subtitles");
    const subtitlePosGroup = document.getElementById("script-subtitle-position-group");
    if (!subtitlePosGroup) return;
    const subtitlesEnabled = subtitleToggle ? subtitleToggle.checked : true;
    subtitlePosGroup.hidden = !subtitlesEnabled;
}

function _updateScriptDetailsForTevoxiMode() {
    const tevoxiMode = _isScriptTevoxiMainAudioMode();

    const bgmGroup = document.getElementById("script-bgm-group");
    const bgmLockHint = document.getElementById("script-bgm-lock-hint");
    const bgmToggle = document.getElementById("script-enable-bgm");
    const bgmUploadArea = document.getElementById("script-bgm-upload-area");
    const bgmFileInput = document.getElementById("script-bgm-file");

    if (bgmToggle) {
        if (tevoxiMode) {
            bgmToggle.checked = false;
            bgmToggle.disabled = true;
            if (bgmFileInput) bgmFileInput.value = "";
            if (bgmUploadArea) bgmUploadArea.hidden = true;
            if (bgmGroup) bgmGroup.classList.add("is-locked");
            if (bgmLockHint) bgmLockHint.hidden = false;
        } else {
            bgmToggle.disabled = false;
            if (bgmUploadArea) bgmUploadArea.hidden = !bgmToggle.checked;
            if (bgmGroup) bgmGroup.classList.remove("is-locked");
            if (bgmLockHint) bgmLockHint.hidden = true;
        }
    }

    const audioSpectrumGroup = document.getElementById("script-audio-spectrum-group");
    const audioSpectrumCb = document.getElementById("script-enable-audio-spectrum");
    if (audioSpectrumGroup) audioSpectrumGroup.hidden = !tevoxiMode;
    if (!tevoxiMode && audioSpectrumCb) audioSpectrumCb.checked = false;

    _updateScriptSubtitlePositionVisibility();
}

function handlePhotoSelect(event) {
    const files = Array.from(event.target.files || []);
    addPhotos(files);
    event.target.value = "";
}

function addPhotos(files) {
    for (const file of files) {
        if (scriptPhotos.length >= MAX_PHOTOS) {
            alert(`Maximo de ${MAX_PHOTOS} fotos atingido.`);
            break;
        }
        if (!file.type.match(/^image\/(jpeg|png|webp)$/)) {
            alert(`Formato não suportado: ${file.name}. Use JPG, PNG ou WebP.`);
            continue;
        }
        if (file.size > MAX_PHOTO_SIZE) {
            alert(`${file.name} excede 10MB. Reduza o tamanho.`);
            continue;
        }
        scriptPhotos.push(file);
    }
    renderPhotoPreview();
}

function removePhoto(index) {
    scriptPhotos.splice(index, 1);
    renderPhotoPreview();
}

function renderPhotoPreview() {
    const grid = document.getElementById("script-photo-preview");
    const countEl = document.getElementById("script-photo-count");
    const numEl = document.getElementById("script-photo-num");

    grid.innerHTML = "";
    scriptPhotos.forEach((file, i) => {
        const div = document.createElement("div");
        div.className = "photo-preview-item";
        const img = document.createElement("img");
        img.src = URL.createObjectURL(file);
        img.onload = () => URL.revokeObjectURL(img.src);
        const btn = document.createElement("button");
        btn.className = "photo-remove-btn";
        btn.type = "button";
        btn.textContent = "\u00d7";
        btn.onclick = () => removePhoto(i);
        div.appendChild(img);
        div.appendChild(btn);
        grid.appendChild(div);
    });

    countEl.hidden = scriptPhotos.length === 0;
    numEl.textContent = scriptPhotos.length;
    updateNarrationChoiceVisibility();
}

// Drag and drop support
document.addEventListener("DOMContentLoaded", () => {
    const dz = document.getElementById("script-photo-dropzone");
    if (dz) {
        dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
        dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
        dz.addEventListener("drop", (e) => {
            e.preventDefault();
            dz.classList.remove("dragover");
            const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith("image/"));
            addPhotos(files);
        });
    }

    const adz = document.getElementById("script-audio-dropzone");
    if (adz) {
        adz.addEventListener("dragover", (e) => { e.preventDefault(); adz.classList.add("dragover"); });
        adz.addEventListener("dragleave", () => adz.classList.remove("dragover"));
        adz.addEventListener("drop", (e) => {
            e.preventDefault();
            adz.classList.remove("dragover");
            const file = Array.from(e.dataTransfer.files).find(f => f.type.startsWith("audio/"));
            if (!file) return;
            const input = document.getElementById("script-user-audio-input");
            if (input) input.value = "";
            handleUserAudioSelect({ target: { files: [file] } });
        });
    }

    const vdz = document.getElementById("script-video-dropzone");
    if (vdz) {
        vdz.addEventListener("dragover", (e) => { e.preventDefault(); vdz.classList.add("dragover"); });
        vdz.addEventListener("dragleave", () => vdz.classList.remove("dragover"));
        vdz.addEventListener("drop", (e) => {
            e.preventDefault();
            vdz.classList.remove("dragover");
            const file = Array.from(e.dataTransfer.files).find(f => f.type.startsWith("video/"));
            if (!file) return;
            const input = document.getElementById("script-video-input");
            if (input) input.value = "";
            handleUserVideoSelect({ target: { files: [file] } });
        });
    }

    const personaSubtype = document.getElementById("persona-manager-nature-subtype");
    if (personaSubtype) {
        personaSubtype.addEventListener("change", _updatePersonaManagerFormByType);
    }
    const drawingStyle = document.getElementById("persona-manager-drawing-style");
    if (drawingStyle) {
        drawingStyle.addEventListener("change", _updatePersonaManagerFormByType);
    }
    const personaVoiceSelect = document.getElementById("persona-manager-voice-profile");
    if (personaVoiceSelect) {
        personaVoiceSelect.addEventListener("change", () => {
            const selectedId = parseInt(personaVoiceSelect.value || "0", 10) || 0;
            const playBtn = document.getElementById("persona-manager-voice-play");
            if (playBtn) {
                playBtn.disabled = !selectedId;
                playBtn.classList.toggle("disabled", !selectedId);
            }
        });
    }
});

function adaptScriptStepForVideoType(videoType) {
    const isRealistic = videoType === "realista";
    const videoSection = document.getElementById("script-video-upload-section");
    const textarea = document.getElementById("script-text");
    if (videoSection) videoSection.hidden = isRealistic;
    if (textarea) {
        textarea.placeholder = isRealistic
            ? "Cole ou escreva seu prompt aqui..."
            : "Cole ou escreva o roteiro completo da narração aqui...";
    }
    // Reset video toggle if switching to realistic
    if (isRealistic) {
        const videoCb = document.getElementById("script-use-video");
        if (videoCb && videoCb.checked) {
            videoCb.checked = false;
            toggleVideoUpload();
        }
    }
    _updateScriptDetailsForTevoxiMode();
    _updateScriptSubtitlePositionVisibility();
}

function _normalizeRealisticPersonaType(value) {
    const raw = String(value || "").trim().toLowerCase();
    const mapping = {
        "criança": "crianca",
        crianca: "crianca",
        "família": "familia",
        familia: "familia",
        personalizada: "personalizado",
        custom: "personalizado",
    };
    const normalized = mapping[raw] || raw;
    return REALISTIC_PERSONA_TYPES.includes(normalized) ? normalized : "natureza";
}

function _getRealisticPersonaTypeByContext(context) {
    const key = String(context || "script").toLowerCase();
    let selector = "#script-realistic-persona-tags .style-tag.selected";
    if (key === "wizard") selector = "#wizard-realistic-persona-tags .style-tag.selected";
    if (key === "ai") selector = "#ai-suggest-persona-tags .style-tag.selected";
    if (key === "auto") selector = "#auto-realistic-persona-tags .style-tag.selected";
    const selected = document.querySelector(selector);
    return _normalizeRealisticPersonaType(selected ? selected.dataset.persona : "natureza");
}

function _getRealisticPersonaPreviewElement(context) {
    if (context === "wizard") return document.getElementById("wizard-realistic-persona-preview");
    if (context === "ai") return document.getElementById("ai-suggest-persona-preview");
    if (context === "auto") return document.getElementById("auto-realistic-persona-preview");
    return document.getElementById("script-realistic-persona-preview");
}

function _getPersonaProfiles(personaType) {
    return _personaProfilesByType[_normalizeRealisticPersonaType(personaType)] || [];
}

function _getMultiPersonaCheckbox(context) {
    if (context === "wizard") return document.getElementById("wizard-realistic-multi-persona");
    if (context === "ai") return document.getElementById("ai-realistic-multi-persona");
    if (context === "auto") return document.getElementById("auto-realistic-multi-persona");
    return document.getElementById("script-realistic-multi-persona");
}

function _supportsInlineMultiPersona(context) {
    return context === "wizard" || context === "script" || context === "ai" || context === "auto";
}

function _isMultiPersonaEnabled(context) {
    if (_supportsInlineMultiPersona(context)) {
        return true;
    }
    const cb = _getMultiPersonaCheckbox(context);
    return !!(cb && cb.checked);
}

function toggleMultiPersona(context, enabled) {
    const ctx = ["wizard", "script", "ai", "auto"].includes(context) ? context : "script";
    const type = _getRealisticPersonaTypeByContext(ctx);
    const selectedIds = _getSelectedPersonaProfileIds(ctx, type);
    const isEnabled = !!enabled;

    if (!isEnabled && selectedIds.length > 1) {
        _setSelectedPersonaProfileIds(ctx, type, [selectedIds[0]]);
    }
    _renderPersonaPreview(ctx);
}
window.toggleMultiPersona = toggleMultiPersona;

function togglePersonaSelectionFromPreview(context, profileId) {
    const ctx = ["wizard", "script", "ai", "auto"].includes(context) ? context : "script";
    const pid = parseInt(profileId || "0", 10) || 0;
    if (!pid) return;

    const type = _getRealisticPersonaTypeByContext(ctx);
    const selectedIds = _getSelectedPersonaProfileIds(ctx, type);

    if (selectedIds.includes(pid)) {
        if (selectedIds.length === 1) {
            return;
        }
        _setSelectedPersonaProfileIds(
            ctx,
            type,
            selectedIds.filter((sid) => sid !== pid),
        );
    } else {
        _setSelectedPersonaProfileIds(ctx, type, [...selectedIds, pid]);
    }

    _renderPersonaPreview(ctx);
}
window.togglePersonaSelectionFromPreview = togglePersonaSelectionFromPreview;

async function _loadPersonaProfiles(personaType, ensureDefault = false) {
    const type = _normalizeRealisticPersonaType(personaType);
    const query = new URLSearchParams({
        persona_type: type,
        ensure_default: ensureDefault ? "true" : "false",
    });
    const result = await api(`/persona/profiles?${query.toString()}`);
    const profiles = Array.isArray(result?.profiles) ? result.profiles : [];
    _personaProfilesByType[type] = profiles;
    return profiles;
}

function _getSelectedPersonaProfileId(context, personaType) {
    const type = _normalizeRealisticPersonaType(personaType);
    const ctx = _personaSelectionByContext[context] || {};
    const raw = parseInt(ctx[type] || "0", 10) || 0;
    if (raw > 0) return raw;
    const ids = _getSelectedPersonaProfileIds(context, type);
    return ids.length ? ids[0] : 0;
}

function _setSelectedPersonaProfileId(context, personaType, profileId) {
    const type = _normalizeRealisticPersonaType(personaType);
    if (!_personaSelectionByContext[context]) {
        _personaSelectionByContext[context] = {};
    }
    const pid = parseInt(profileId || "0", 10) || 0;
    _personaSelectionByContext[context][type] = pid;

    if (!_personaMultiSelectionByContext[context]) {
        _personaMultiSelectionByContext[context] = {};
    }
    _personaMultiSelectionByContext[context][type] = pid ? [pid] : [];
}

function _getSelectedPersonaProfileIds(context, personaType) {
    const type = _normalizeRealisticPersonaType(personaType);
    const ctx = _personaMultiSelectionByContext[context] || {};
    const rawList = Array.isArray(ctx[type]) ? ctx[type] : [];
    const normalized = [];
    rawList.forEach((value) => {
        const pid = parseInt(value || "0", 10) || 0;
        if (pid > 0 && !normalized.includes(pid)) {
            normalized.push(pid);
        }
    });

    if (normalized.length) {
        return normalized;
    }

    const fallback = _personaSelectionByContext[context] || {};
    const single = parseInt(fallback[type] || "0", 10) || 0;
    return single ? [single] : [];
}

function _setSelectedPersonaProfileIds(context, personaType, profileIds) {
    const type = _normalizeRealisticPersonaType(personaType);
    if (!_personaMultiSelectionByContext[context]) {
        _personaMultiSelectionByContext[context] = {};
    }
    if (!_personaSelectionByContext[context]) {
        _personaSelectionByContext[context] = {};
    }

    const normalized = [];
    (Array.isArray(profileIds) ? profileIds : []).forEach((value) => {
        const pid = parseInt(value || "0", 10) || 0;
        if (pid > 0 && !normalized.includes(pid)) {
            normalized.push(pid);
        }
    });

    _personaMultiSelectionByContext[context][type] = normalized;
    _personaSelectionByContext[context][type] = normalized[0] || 0;
}

function _getSelectedPersonaProfile(context, personaType) {
    const type = _normalizeRealisticPersonaType(personaType);
    const profiles = _getPersonaProfiles(type);
    const selectedIds = _getSelectedPersonaProfileIds(context, type);
    let selected = profiles.find((profile) => parseInt(profile.id, 10) === (selectedIds[0] || 0));
    if (!selected) {
        selected = profiles.find((profile) => !!profile.is_default) || profiles[0] || null;
        if (selected) {
            _setSelectedPersonaProfileIds(context, type, [selected.id]);
        }
    }
    return selected;
}

function _renderPersonaPreview(context) {
    const el = _getRealisticPersonaPreviewElement(context);
    if (!el) return;

    const type = _getRealisticPersonaTypeByContext(context);
    const profiles = _getPersonaProfiles(type);
    if (!profiles.length) {
        el.innerHTML = '<div class="realistic-persona-empty">Nenhuma persona disponível para este tipo ainda.</div>';
        return;
    }

    let selectedIds = _getSelectedPersonaProfileIds(context, type)
        .filter((sid) => profiles.some((profile) => parseInt(profile.id, 10) === sid));

    if (!selectedIds.length) {
        const fallback = profiles.find((profile) => !!profile.is_default) || profiles[0] || null;
        selectedIds = fallback ? [parseInt(fallback.id, 10) || 0] : [];
        _setSelectedPersonaProfileIds(context, type, selectedIds);
    }

    if (_supportsInlineMultiPersona(context)) {
        const selectedSet = new Set(selectedIds);
        const cards = profiles.map((profile) => {
            const pid = parseInt(profile.id, 10) || 0;
            const isSelected = selectedSet.has(pid);
            const selectedClass = isSelected ? " selected" : "";
            const profileName = esc(profile.name || `Persona ${pid}`);
            const imageHtml = profile.image_url
                ? `<img class="realistic-persona-thumb" src="${profile.image_url}" alt="Persona ${esc(profile.name || "")}">`
                : '<div class="realistic-persona-thumb"></div>';

            return `
                <button
                    class="realistic-persona-option${selectedClass}"
                    type="button"
                    onclick="togglePersonaSelectionFromPreview('${context}', ${pid})"
                    title="${profileName}"
                    aria-label="${profileName}"
                    aria-pressed="${isSelected ? "true" : "false"}">
                    ${imageHtml}
                </button>
            `;
        }).join("");

        el.innerHTML = `
            <div class="realistic-persona-grid">
                ${cards}
            </div>
        `;
        return;
    }

    const selectedProfiles = selectedIds
        .map((sid) => profiles.find((profile) => parseInt(profile.id, 10) === sid))
        .filter(Boolean);

    const profile = selectedProfiles[0] || _getSelectedPersonaProfile(context, type);
    if (!profile && !selectedProfiles.length) {
        el.innerHTML = '<div class="realistic-persona-empty">Nenhuma persona disponível para este tipo ainda.</div>';
        return;
    }

    if (_isMultiPersonaEnabled(context) && selectedProfiles.length > 1) {
        const thumbs = selectedProfiles.slice(0, 4).map((item) => {
            if (item.image_url) {
                return `<img class="realistic-persona-thumb" src="${item.image_url}" alt="Persona ${esc(item.name || "")}">`;
            }
            return '<div class="realistic-persona-thumb"></div>';
        }).join("");
        const names = selectedProfiles.slice(0, 4).map((item) => esc(item.name || `Persona ${item.id}`)).join(", ");
        const extra = selectedProfiles.length > 4 ? ` e +${selectedProfiles.length - 4}` : "";

        el.innerHTML = `
            <div class="realistic-persona-card">
                <div style="display:flex; gap:0.45rem; align-items:center;">${thumbs}</div>
                <div class="realistic-persona-meta">
                    <div class="realistic-persona-name">${selectedProfiles.length} personas selecionadas</div>
                    <div class="realistic-persona-sub">${names}${extra}</div>
                </div>
            </div>
        `;
        return;
    }

    const imageHtml = profile.image_url
        ? `<img class="realistic-persona-thumb" src="${profile.image_url}" alt="Persona ${esc(profile.name || "")}">`
        : '<div class="realistic-persona-thumb"></div>';
    const subtitle = profile.is_default ? "Padrao deste tipo" : "Persona personalizada";

    el.innerHTML = `
        <div class="realistic-persona-card">
            ${imageHtml}
            <div class="realistic-persona-meta">
                <div class="realistic-persona-name">${esc(profile.name || `Persona ${profile.id}`)}</div>
                <div class="realistic-persona-sub">${esc(subtitle)} - ${esc(REALISTIC_PERSONA_LABELS[type] || type)}</div>
            </div>
        </div>
    `;
}

async function _refreshPersonaContext(context, forcedPersonaType = "") {
    const type = _normalizeRealisticPersonaType(forcedPersonaType || _getRealisticPersonaTypeByContext(context));
    try {
        await _loadPersonaProfiles(type, false);
    } catch (error) {
        const previewEl = _getRealisticPersonaPreviewElement(context);
        if (previewEl) {
            previewEl.innerHTML = `<div class="realistic-persona-empty">${esc(error.message || "Falha ao carregar personas")}</div>`;
        }
        return;
    }

    const profiles = _getPersonaProfiles(type);
    let selectedIds = _getSelectedPersonaProfileIds(context, type)
        .filter((sid) => profiles.some((profile) => parseInt(profile.id, 10) === sid));

    if (!_isMultiPersonaEnabled(context) && selectedIds.length > 1) {
        selectedIds = [selectedIds[0]];
    }

    if (!selectedIds.length) {
        const fallback = profiles.find((profile) => !!profile.is_default) || profiles[0] || null;
        selectedIds = fallback ? [fallback.id] : [];
    }

    _setSelectedPersonaProfileIds(context, type, selectedIds);

    _renderPersonaPreview(context);
}

function _refreshAllPersonaPreviews() {
    _renderPersonaPreview("wizard");
    _renderPersonaPreview("script");
    _renderPersonaPreview("ai");
    _renderPersonaPreview("auto");
}

async function _ensurePersonaSelection(context, personaType) {
    const type = _normalizeRealisticPersonaType(personaType);
    if (!_getPersonaProfiles(type).length) {
        await _loadPersonaProfiles(type, false);
    }
    const selected = _getSelectedPersonaProfile(context, type);
    return selected ? (parseInt(selected.id, 10) || 0) : 0;
}

async function _ensurePersonaSelections(context, personaType) {
    const type = _normalizeRealisticPersonaType(personaType);
    if (!_getPersonaProfiles(type).length) {
        await _loadPersonaProfiles(type, false);
    }
    const profiles = _getPersonaProfiles(type);
    let selectedIds = _getSelectedPersonaProfileIds(context, type)
        .filter((sid) => profiles.some((profile) => parseInt(profile.id, 10) === sid));

    if (!_isMultiPersonaEnabled(context) && selectedIds.length > 1) {
        selectedIds = [selectedIds[0]];
    }

    if (!selectedIds.length) {
        const fallback = profiles.find((profile) => !!profile.is_default) || profiles[0] || null;
        selectedIds = fallback ? [parseInt(fallback.id, 10)] : [];
    }

    _setSelectedPersonaProfileIds(context, type, selectedIds);
    return selectedIds;
}

function _updatePersonaManagerFormByType() {
    const isNature = _personaManagerType === "natureza";
    const isDrawing = _personaManagerType === "desenho";
    const isCustom = _personaManagerType === "personalizado";
    const isHuman = !isNature && !isDrawing && !isCustom;
    const humanFields = document.getElementById("persona-manager-human-fields");
    const natureSubtypeGroup = document.getElementById("persona-manager-nature-subtype-group");
    const natureOtherGroup = document.getElementById("persona-manager-nature-other-group");
    const natureSubtypeEl = document.getElementById("persona-manager-nature-subtype");
    const drawingStyleGroup = document.getElementById("persona-manager-drawing-style-group");
    const drawingOtherGroup = document.getElementById("persona-manager-drawing-other-group");
    const drawingStyleEl = document.getElementById("persona-manager-drawing-style");
    const customDescGroup = document.getElementById("persona-manager-custom-desc-group");

    if (humanFields) humanFields.hidden = !isHuman;
    if (natureSubtypeGroup) natureSubtypeGroup.hidden = !isNature;
    if (natureOtherGroup) {
        const isNatureOther = isNature && natureSubtypeEl && natureSubtypeEl.value === "outros";
        natureOtherGroup.hidden = !isNatureOther;
    }
    if (drawingStyleGroup) drawingStyleGroup.hidden = !isDrawing;
    if (drawingOtherGroup) {
        const isDrawingOther = isDrawing && drawingStyleEl && drawingStyleEl.value === "outros";
        drawingOtherGroup.hidden = !isDrawingOther;
    }
    if (customDescGroup) customDescGroup.hidden = !isCustom;
}

function _formatDrawingStyleLabel(styleValue, customStyleValue) {
    const style = String(styleValue || "").trim().toLowerCase();
    const customStyle = String(customStyleValue || "").trim();
    const labels = {
        cartoon: "Cartoon",
        "3d": "3D",
        anime: "Anime",
        comic: "Comic",
        manga: "Manga",
        pixar: "Pixar",
        pixel_art: "Pixel Art",
        aquarela: "Aquarela",
        outros: "Personalizado",
    };

    if (!style) {
        return "Cartoon";
    }
    if (style === "outros") {
        return customStyle || labels.outros;
    }
    return labels[style] || style;
}

function _getPersonaVoiceProfileId(profile) {
    if (!profile || typeof profile !== "object") return 0;

    const topLevel = parseInt(profile.voice_profile_id || "0", 10) || 0;
    if (topLevel > 0) return topLevel;

    const attrs = (profile.attributes && typeof profile.attributes === "object") ? profile.attributes : {};
    const fromAttrs = parseInt(attrs.voice_profile_id || "0", 10) || 0;
    return fromAttrs > 0 ? fromAttrs : 0;
}

function _getPersonaProfileById(profileId, personaType = "") {
    const pid = parseInt(profileId || "0", 10) || 0;
    if (!pid) return null;
    const type = _normalizeRealisticPersonaType(personaType || _personaManagerType);
    const profiles = _getPersonaProfiles(type);
    return profiles.find((item) => (parseInt(item?.id || "0", 10) || 0) === pid) || null;
}

function _buildPersonaVoiceDescriptionSeed(profile) {
    if (!profile || typeof profile !== "object") return "";
    const type = _normalizeRealisticPersonaType(profile.persona_type || _personaManagerType);
    const attrs = (profile.attributes && typeof profile.attributes === "object") ? profile.attributes : {};
    const hints = [];

    if (type === "homem") hints.push("homem adulto");
    else if (type === "mulher") hints.push("mulher adulta");
    else if (type === "crianca") hints.push("crianca");
    else if (type === "familia") hints.push("personagem familiar");
    else if (type === "natureza") hints.push(`personagem natureza ${attrs.subtipo || ""}`.trim());
    else if (type === "desenho") hints.push(`personagem desenho ${_formatDrawingStyleLabel(attrs.estilo_desenho, attrs.estilo_desenho_custom)}`.trim());
    else if (type === "personalizado") hints.push("personagem personalizado");

    [attrs.idade_aparente, attrs.expressao, attrs.descricao_persona, attrs.descricao_extra]
        .map((value) => String(value || "").trim())
        .filter((value) => !!value)
        .forEach((value) => hints.push(value));

    return hints.filter(Boolean).slice(0, 6).join(", ");
}

function _getVoiceProfileNameById(voiceProfileId) {
    const pid = parseInt(voiceProfileId || "0", 10) || 0;
    if (!pid) return "Sem voz";
    const profile = (voiceProfiles || []).find((item) => (parseInt(item?.id || "0", 10) || 0) === pid);
    return profile ? (profile.name || `Voz ${pid}`) : `Voz ${pid}`;
}

function _buildPersonaVoiceOptions(selectedVoiceProfileId = 0) {
    const selectedId = parseInt(selectedVoiceProfileId || "0", 10) || 0;
    const options = [`<option value="0">Sem voz vinculada</option>`];
    (voiceProfiles || []).forEach((profile) => {
        const pid = parseInt(profile?.id || "0", 10) || 0;
        if (!pid) return;
        const selectedAttr = pid === selectedId ? ' selected' : '';
        const defaultSuffix = profile.is_default ? " (Padrao)" : "";
        options.push(`<option value="${pid}"${selectedAttr}>${esc(profile.name || `Voz ${pid}`)}${defaultSuffix}</option>`);
    });
    return options.join("");
}

function _renderPersonaManagerCreateVoiceSelect() {
    const selectEl = document.getElementById("persona-manager-voice-profile");
    if (!selectEl) return;

    const currentValue = parseInt(selectEl.value || "0", 10) || 0;
    let selectedId = currentValue;
    if (!selectedId) {
        const defaultVoice = (voiceProfiles || []).find((item) => !!item?.is_default);
        selectedId = parseInt(defaultVoice?.id || "0", 10) || 0;
    }
    selectEl.innerHTML = _buildPersonaVoiceOptions(selectedId);
    selectEl.value = String(selectedId || 0);

    const playBtn = document.getElementById("persona-manager-voice-play");
    if (playBtn) {
        playBtn.disabled = !selectedId;
        playBtn.classList.toggle("disabled", !selectedId);
    }
}

function _buildPersonaManagerMeta(profile) {
    const typeLabel = REALISTIC_PERSONA_LABELS[_personaManagerType] || _personaManagerType;
    const attrs = (profile && typeof profile.attributes === "object" && profile.attributes) ? profile.attributes : {};
    const parts = [typeLabel];

    if (_personaManagerType === "desenho") {
        const styleLabel = _formatDrawingStyleLabel(attrs.estilo_desenho, attrs.estilo_desenho_custom);
        parts.push(`Estilo: ${styleLabel}`);
    }

    if (profile?.is_default) {
        parts.push("Padrao");
    }

    const voiceProfileId = _getPersonaVoiceProfileId(profile);
    if (voiceProfileId > 0) {
        parts.push(`Voz: ${_getVoiceProfileNameById(voiceProfileId)}`);
    }

    return parts.join(" - ");
}

function _renderPersonaManagerList() {
    const listEl = document.getElementById("persona-manager-list");
    if (!listEl) return;

    const profiles = _getPersonaProfiles(_personaManagerType);
    if (!profiles.length) {
        listEl.innerHTML = '<p class="muted">Nenhuma persona criada ainda para este tipo.</p>';
        return;
    }

    const selectedIds = _getSelectedPersonaProfileIds(_personaManagerContext, _personaManagerType);
    listEl.innerHTML = profiles.map((profile) => {
        const pid = parseInt(profile.id, 10) || 0;
        const isSelected = selectedIds.includes(pid);
        const selectedClass = isSelected ? " selected" : "";
        const metaText = _buildPersonaManagerMeta(profile);
        const canEditPrompt = String(profile?.prompt_text || "").trim().length >= 12;
        const voiceProfileId = _getPersonaVoiceProfileId(profile);
        const voiceOptions = _buildPersonaVoiceOptions(voiceProfileId);
        const voicePlayDisabled = voiceProfileId <= 0;
        const imageUrl = profile.image_url || "";
        const image = imageUrl
            ? `
                <div class="persona-manager-photo-wrap">
                    <img class="persona-manager-photo" src="${imageUrl}" alt="${esc(profile.name || "Persona")}">
                    <button
                        class="btn btn-secondary btn-sm persona-manager-photo-expand"
                        type="button"
                        onclick="openPersonaImagePreview(${pid}, event)"
                        title="Expandir imagem">
                        <svg viewBox="0 0 24 24" aria-hidden="true">
                            <path d="M14 3h7v7"></path>
                            <path d="M10 21H3v-7"></path>
                            <path d="M21 3l-8 8"></path>
                            <path d="M3 21l8-8"></path>
                        </svg>
                    </button>
                </div>
            `
            : '<div class="persona-manager-photo-wrap"><div class="persona-manager-photo"></div></div>';

        return `
            <div
                class="persona-manager-card${selectedClass}"
                role="button"
                tabindex="0"
                onclick="handlePersonaCardClick(${pid}, event)"
                onkeydown="handlePersonaCardKeydown(event, ${pid})">
                ${image}
                <div class="persona-manager-info">
                    <div class="persona-manager-name">${esc(profile.name || `Persona ${pid}`)}</div>
                    <div class="persona-manager-meta">${esc(metaText)}</div>
                </div>
                <div class="persona-manager-voice-row">
                    <select class="input persona-manager-voice-select" onchange="setPersonaVoiceFromManager(${pid}, this.value)">
                        ${voiceOptions}
                    </select>
                    <button
                        class="btn btn-secondary btn-sm persona-manager-voice-play${voicePlayDisabled ? " disabled" : ""}"
                        type="button"
                        onclick="previewPersonaVoiceFromManager(${pid})"
                        title="Ouvir prévia da voz"
                        ${voicePlayDisabled ? "disabled" : ""}>▶</button>
                </div>
                <div class="persona-manager-voice-link-row">
                    <button class="btn btn-secondary btn-sm persona-manager-voice-link" type="button" onclick="openPersonaVoiceBuilder(${pid})">Vincular voz por descrição</button>
                </div>
                <div class="persona-manager-actions">
                    <button
                        class="btn btn-secondary btn-sm persona-manager-action-icon"
                        type="button"
                        onclick="openPersonaPromptEditor(${pid})"
                        title="Editar prompt"
                        ${canEditPrompt ? "" : "disabled"}>
                        <svg viewBox="0 0 24 24" aria-hidden="true">
                            <path d="M12 20h9"></path>
                            <path d="M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4Z"></path>
                        </svg>
                    </button>
                    <button
                        class="btn btn-secondary btn-sm persona-manager-action-icon"
                        type="button"
                        onclick="setDefaultPersonaFromManager(${pid})"
                        title="Definir como padrão">
                        <svg viewBox="0 0 24 24" aria-hidden="true">
                            <path d="m12 3 2.9 5.8 6.4.9-4.6 4.5 1.1 6.4-5.8-3-5.8 3 1.1-6.4L2.7 9.7l6.4-.9Z"></path>
                        </svg>
                    </button>
                    <button
                        class="btn btn-secondary btn-sm persona-manager-action-icon persona-manager-action-danger"
                        type="button"
                        onclick="deletePersonaFromManager(${pid})"
                        title="Excluir persona">
                        <svg viewBox="0 0 24 24" aria-hidden="true">
                            <path d="M3 6h18"></path>
                            <path d="M8 6V4h8v2"></path>
                            <path d="M19 6l-1 14H6L5 6"></path>
                            <path d="M10 11v6"></path>
                            <path d="M14 11v6"></path>
                        </svg>
                    </button>
                </div>
            </div>
        `;
    }).join("");
}

function handlePersonaCardClick(profileId, event) {
    const pid = parseInt(profileId || "0", 10) || 0;
    if (!pid) return;

    const target = event?.target;
    if (target && target.closest("button,select,input,textarea,label,a")) {
        return;
    }

    if (_personaManagerMulti) {
        togglePersonaSelectionFromManager(pid);
    } else {
        selectPersonaFromManager(pid);
    }
}

function handlePersonaCardKeydown(event, profileId) {
    const key = String(event?.key || "");
    if (key !== "Enter" && key !== " ") {
        return;
    }
    event.preventDefault();
    handlePersonaCardClick(profileId, event);
}

function openPersonaImagePreview(profileId, event) {
    if (event?.stopPropagation) {
        event.stopPropagation();
    }

    const pid = parseInt(profileId || "0", 10) || 0;
    if (!pid) return;

    const profile = _getPersonaProfileById(pid, _personaManagerType);
    const imageUrl = String(profile?.image_url || "").trim();
    if (!imageUrl) {
        showToast("Esta persona não possui imagem disponível.");
        return;
    }

    const titleEl = document.getElementById("persona-image-viewer-title");
    if (titleEl) {
        titleEl.textContent = profile?.name ? `Imagem - ${profile.name}` : "Imagem da persona";
    }

    const imageEl = document.getElementById("persona-image-viewer-img");
    if (imageEl) {
        imageEl.src = imageUrl;
        imageEl.alt = profile?.name ? `Imagem da persona ${profile.name}` : "Imagem da persona";
    }

    openModal("modal-persona-image-viewer");
}

async function _refreshPersonaManagerList() {
    const listEl = document.getElementById("persona-manager-list");
    if (listEl) listEl.innerHTML = '<p class="muted">Carregando personas...</p>';
    try {
        await _loadPersonaProfiles(_personaManagerType, false);
        _renderPersonaManagerList();
        _refreshAllPersonaPreviews();
    } catch (error) {
        if (listEl) listEl.innerHTML = `<p class="muted">${esc(error.message || "Falha ao carregar personas")}</p>`;
    }
}

async function setPersonaVoiceFromManager(profileId, voiceProfileIdValue) {
    const pid = parseInt(profileId || "0", 10) || 0;
    if (!pid) return;
    const voiceProfileId = parseInt(voiceProfileIdValue || "0", 10) || 0;

    try {
        await api(`/persona/profiles/${pid}/voice`, {
            method: "PUT",
            body: JSON.stringify({ voice_profile_id: voiceProfileId }),
        });
        await _refreshPersonaManagerList();
        showToast("Voz da persona atualizada.", "success");
    } catch (error) {
        alert(`Erro ao atualizar voz da persona: ${error.message}`);
    }
}

async function previewPersonaVoiceFromManager(profileId) {
    const pid = parseInt(profileId || "0", 10) || 0;
    if (!pid) return;

    const profiles = _getPersonaProfiles(_personaManagerType);
    const profile = profiles.find((item) => (parseInt(item?.id || "0", 10) || 0) === pid);
    const voiceProfileId = _getPersonaVoiceProfileId(profile);
    if (!voiceProfileId) {
        showToast("Esta persona ainda não tem voz vinculada.");
        return;
    }
    await previewVoice(voiceProfileId);
}

async function previewPersonaCreateVoice() {
    const selectEl = document.getElementById("persona-manager-voice-profile");
    const voiceProfileId = parseInt(selectEl?.value || "0", 10) || 0;
    if (!voiceProfileId) {
        showToast("Selecione uma voz para ouvir a prévia.");
        return;
    }
    await previewVoice(voiceProfileId);
}

function openPersonaVoiceBuilder(profileId) {
    const pid = parseInt(profileId || "0", 10) || 0;
    if (!pid) return;

    const profile = _getPersonaProfileById(pid, _personaManagerType);
    if (!profile) {
        alert("Persona nao encontrada.");
        return;
    }

    _personaVoiceBuilderProfileId = pid;
    const titleEl = document.getElementById("persona-voice-builder-title");
    if (titleEl) {
        titleEl.textContent = `Criar voz para ${profile.name || "persona"}`;
    }

    const hiddenProfileId = document.getElementById("persona-voice-builder-profile-id");
    if (hiddenProfileId) hiddenProfileId.value = String(pid);

    const nameEl = document.getElementById("persona-voice-builder-name");
    if (nameEl) {
        const suggestedName = `Voz ${profile.name || "Persona"}`;
        nameEl.value = suggestedName.slice(0, 80);
    }

    const descriptionEl = document.getElementById("persona-voice-builder-description");
    if (descriptionEl) {
        descriptionEl.value = _buildPersonaVoiceDescriptionSeed(profile);
    }

    const providerEl = document.getElementById("persona-voice-builder-provider");
    if (providerEl) {
        providerEl.value = "elevenlabs";
    }

    const statusEl = document.getElementById("persona-voice-builder-status");
    if (statusEl) {
        statusEl.textContent = "Descreva o estilo da voz (ex: mulher, jovem, alegre).";
    }

    openModal("modal-persona-voice-builder");
}

function addPersonaVoiceTrait(trait) {
    const value = String(trait || "").trim();
    if (!value) return;

    const descriptionEl = document.getElementById("persona-voice-builder-description");
    if (!descriptionEl) return;

    const current = String(descriptionEl.value || "").trim();
    const parts = current ? current.split(",").map((item) => item.trim()).filter(Boolean) : [];
    const exists = parts.some((item) => item.toLowerCase() === value.toLowerCase());
    if (!exists) {
        parts.push(value);
        descriptionEl.value = parts.join(", ");
    }
    descriptionEl.focus();
}

async function createPersonaVoiceFromDescription() {
    const profileIdInput = document.getElementById("persona-voice-builder-profile-id");
    const pid = parseInt(profileIdInput?.value || _personaVoiceBuilderProfileId || "0", 10) || 0;
    if (!pid) {
        alert("Persona invalida para vincular voz.");
        return;
    }

    const nameEl = document.getElementById("persona-voice-builder-name");
    const descriptionEl = document.getElementById("persona-voice-builder-description");
    const providerEl = document.getElementById("persona-voice-builder-provider");
    const statusEl = document.getElementById("persona-voice-builder-status");
    const saveBtn = document.getElementById("persona-voice-builder-save");

    const name = String(nameEl?.value || "").trim();
    const description = String(descriptionEl?.value || "").trim();
    const provider = String(providerEl?.value || "elevenlabs").trim().toLowerCase();

    if (description.length < 4) {
        alert("Descreva melhor a voz (minimo 4 caracteres).");
        return;
    }

    const personaProfile = _getPersonaProfileById(pid, _personaManagerType);
    const personaName = String(personaProfile?.name || "").trim();

    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = "Gerando...";
    }
    if (statusEl) {
        statusEl.textContent = provider === "elevenlabs"
            ? "Gerando voz no ElevenLabs..."
            : "Gerando voz por descricao com IA...";
    }

    try {
        const response = await api("/voice/profiles/from-description", {
            method: "POST",
            body: JSON.stringify({
                name,
                description,
                persona_name: personaName,
                persona_type: _personaManagerType,
                provider,
                is_default: false,
            }),
        });

        const voiceProfileId = parseInt(response?.profile?.id || "0", 10) || 0;
        if (!voiceProfileId) {
            throw new Error("Nao foi possivel criar o perfil de voz.");
        }

        await api(`/persona/profiles/${pid}/voice`, {
            method: "PUT",
            body: JSON.stringify({ voice_profile_id: voiceProfileId }),
        });

        await loadVoiceProfiles();
        await _refreshPersonaManagerList();

        const providerInfo = response?.provider_info || {};
        if (!providerInfo.gpt_tts_available) {
            showToast("OpenAI indisponivel: voz criada com perfil base/fallback.", "info");
        }
        if (provider === "elevenlabs" && !providerInfo.elevenlabs_available) {
            showToast("ElevenLabs nao configurado neste servidor.", "info");
        }
        if (providerInfo.provider_requested && providerInfo.provider_effective && providerInfo.provider_requested !== providerInfo.provider_effective) {
            showToast(`Provider ajustado automaticamente para ${providerInfo.provider_effective}.`, "info");
        }
        if (provider === "elevenlabs" && providerInfo.selected_voice_id && statusEl) {
            statusEl.textContent = `Voice ID selecionado: ${providerInfo.selected_voice_id}`;
        }

        closeModal("modal-persona-voice-builder");
        showToast("Voz criada e vinculada a persona.", "success");
        await previewVoice(voiceProfileId);
    } catch (error) {
        if (statusEl) {
            statusEl.textContent = `Falha: ${error.message}`;
        }
        alert(`Erro ao criar voz da persona: ${error.message}`);
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = "Gerar e vincular";
        }
    }
}

function removePersonaReferenceImage() {
    const inputEl = document.getElementById("persona-manager-reference-image");
    if (inputEl) {
        inputEl.value = "";
    }

    const previewEl = document.getElementById("persona-manager-reference-preview");
    if (previewEl) {
        const blobUrl = previewEl.dataset.objectUrl || "";
        if (blobUrl.startsWith("blob:")) {
            try {
                URL.revokeObjectURL(blobUrl);
            } catch {}
        }
        previewEl.src = "";
        previewEl.hidden = true;
        delete previewEl.dataset.objectUrl;
    }

    const removeBtn = document.getElementById("persona-manager-reference-remove");
    if (removeBtn) {
        removeBtn.hidden = true;
    }

    personaManagerReferenceImageFile = null;
}

function _setPersonaReferenceImage(file) {
    if (!file) {
        removePersonaReferenceImage();
        return false;
    }

    if (file.type && !PERSONA_REFERENCE_ALLOWED_TYPES.includes(file.type)) {
        alert("Formato de imagem não suportado. Use JPG, PNG ou WEBP.");
        removePersonaReferenceImage();
        return false;
    }

    if ((file.size || 0) > PERSONA_REFERENCE_MAX_SIZE) {
        alert("Imagem muito grande (max 10MB).");
        removePersonaReferenceImage();
        return false;
    }

    personaManagerReferenceImageFile = file;

    const previewEl = document.getElementById("persona-manager-reference-preview");
    if (previewEl) {
        const previousUrl = previewEl.dataset.objectUrl || "";
        if (previousUrl.startsWith("blob:")) {
            try {
                URL.revokeObjectURL(previousUrl);
            } catch {}
        }
        const objectUrl = URL.createObjectURL(file);
        previewEl.src = objectUrl;
        previewEl.dataset.objectUrl = objectUrl;
        previewEl.hidden = false;
    }

    const removeBtn = document.getElementById("persona-manager-reference-remove");
    if (removeBtn) {
        removeBtn.hidden = false;
    }

    return true;
}

function handlePersonaReferenceImageSelect(event) {
    const file = event?.target?.files?.[0] || null;
    _setPersonaReferenceImage(file);
}

function handlePersonaReferenceImagePaste(event) {
    const items = Array.from(event?.clipboardData?.items || []);
    const imageItem = items.find((item) => item.kind === "file" && (item.type || "").startsWith("image/"));
    if (!imageItem) {
        return;
    }

    const file = imageItem.getAsFile();
    if (!file) {
        return;
    }

    event.preventDefault();
    _setPersonaReferenceImage(file);
}

async function openPersonaManager(context = "script") {
    _personaManagerContext = ["wizard", "script", "ai", "auto"].includes(context) ? context : "script";
    _personaManagerType = _getRealisticPersonaTypeByContext(_personaManagerContext);
    _personaManagerMulti = _isMultiPersonaEnabled(_personaManagerContext);

    const titleEl = document.getElementById("persona-manager-title");
    if (titleEl) titleEl.textContent = `Gerenciar personas (${REALISTIC_PERSONA_LABELS[_personaManagerType] || _personaManagerType})`;

    const subtitleEl = document.getElementById("persona-manager-subtitle");
    if (subtitleEl) {
        subtitleEl.textContent = _personaManagerMulti
            ? "Selecione varias personas para compor cenas com casal, amigos ou grupos."
            : "Escolha ou gere personas para manter o mesmo personagem nos videos realistas.";
    }

    const nameEl = document.getElementById("persona-manager-name");
    if (nameEl) nameEl.value = "";
    const ageEl = document.getElementById("persona-manager-age");
    if (ageEl) ageEl.value = "";
    const skinEl = document.getElementById("persona-manager-skin");
    if (skinEl) skinEl.value = "";
    const hairEl = document.getElementById("persona-manager-hair");
    if (hairEl) hairEl.value = "";
    const extraEl = document.getElementById("persona-manager-extra");
    if (extraEl) extraEl.value = "";
    const subtypeEl = document.getElementById("persona-manager-nature-subtype");
    if (subtypeEl) subtypeEl.value = "gato";
    const otherEl = document.getElementById("persona-manager-nature-other");
    if (otherEl) otherEl.value = "";
    const drawingStyleEl = document.getElementById("persona-manager-drawing-style");
    if (drawingStyleEl) drawingStyleEl.value = "cartoon";
    const drawingOtherEl = document.getElementById("persona-manager-drawing-other");
    if (drawingOtherEl) drawingOtherEl.value = "";
    const customDescEl = document.getElementById("persona-manager-custom-desc");
    if (customDescEl) customDescEl.value = "";
    removePersonaReferenceImage();

    await loadVoiceProfiles();
    _renderPersonaManagerCreateVoiceSelect();

    _updatePersonaManagerFormByType();
    openModal("modal-persona-manager");
    await _refreshPersonaManagerList();
}

async function createPersonaFromManager() {
    const button = document.getElementById("persona-manager-create-btn");
    if (button) {
        button.disabled = true;
        button.textContent = "Gerando...";
    }

    try {
        const name = (document.getElementById("persona-manager-name")?.value || "").trim();
        const age = (document.getElementById("persona-manager-age")?.value || "").trim();
        const skin = (document.getElementById("persona-manager-skin")?.value || "").trim();
        const hair = (document.getElementById("persona-manager-hair")?.value || "").trim();
        const drawingStyle = (document.getElementById("persona-manager-drawing-style")?.value || "cartoon").trim();
        const drawingOther = (document.getElementById("persona-manager-drawing-other")?.value || "").trim();
        const customDesc = (document.getElementById("persona-manager-custom-desc")?.value || "").trim();
        const extra = (document.getElementById("persona-manager-extra")?.value || "").trim();
        const selectedVoiceProfileId = parseInt(document.getElementById("persona-manager-voice-profile")?.value || "0", 10) || 0;

        const attributes = {};
        if (_personaManagerType === "natureza") {
            const subtype = document.getElementById("persona-manager-nature-subtype")?.value || "gato";
            attributes.subtipo = subtype;
            if (subtype === "outros") {
                const other = (document.getElementById("persona-manager-nature-other")?.value || "").trim();
                if (other) attributes.outros_texto = other;
            }
        } else if (_personaManagerType === "desenho") {
            attributes.estilo_desenho = drawingStyle || "cartoon";
            if (attributes.estilo_desenho === "outros") {
                if (!drawingOther) {
                    alert("Descreva o estilo personalizado antes de gerar a persona de desenho.");
                    return;
                }
                attributes.estilo_desenho_custom = drawingOther;
            }
        } else if (_personaManagerType === "personalizado") {
            if (!customDesc) {
                alert("Descreva sua persona personalizada antes de gerar.");
                return;
            }
            attributes.descricao_persona = customDesc;
        } else {
            if (!age || !skin || !hair) {
                alert("Preencha idade, cor da pele e cor do cabelo antes de gerar a persona.");
                return;
            }
            if (_personaManagerType === "familia") {
                attributes.faixa_etaria = age;
                attributes.cor_pele = skin;
                attributes.cabelo = hair;
            } else {
                attributes.idade_aparente = age;
                attributes.cor_pele = skin;
                attributes.cabelo = hair;
            }
        }
        if (extra) {
            attributes.descricao_extra = extra;
        }

        let response = null;
        if (personaManagerReferenceImageFile) {
            const formData = new FormData();
            formData.append("persona_type", _personaManagerType);
            formData.append("name", name);
            formData.append("attributes_json", JSON.stringify(attributes));
            formData.append("reference_image", personaManagerReferenceImageFile, personaManagerReferenceImageFile.name || "reference.png");
            response = await apiForm("/persona/profiles/from-reference", formData);
        } else {
            response = await api("/persona/profiles", {
                method: "POST",
                body: JSON.stringify({
                    persona_type: _personaManagerType,
                    name,
                    attributes,
                }),
            });
        }

        const createdId = parseInt(response?.profile?.id || "0", 10) || 0;
        if (createdId) {
            if (selectedVoiceProfileId > 0) {
                try {
                    await api(`/persona/profiles/${createdId}/voice`, {
                        method: "PUT",
                        body: JSON.stringify({ voice_profile_id: selectedVoiceProfileId }),
                    });
                } catch (voiceError) {
                    console.warn("Failed to link voice profile to persona:", voiceError);
                }
            }

            if (_personaManagerMulti) {
                const selectedIds = _getSelectedPersonaProfileIds(_personaManagerContext, _personaManagerType);
                _setSelectedPersonaProfileIds(_personaManagerContext, _personaManagerType, [...selectedIds, createdId]);
            } else {
                _setSelectedPersonaProfileId(_personaManagerContext, _personaManagerType, createdId);
            }
        }

        await _refreshPersonaManagerList();
        removePersonaReferenceImage();
    } catch (error) {
        alert(`Erro ao criar persona: ${error.message}`);
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = "Gerar nova persona";
        }
    }
}

function openPersonaPromptEditor(profileId) {
    const pid = parseInt(profileId || "0", 10) || 0;
    if (!pid) return;

    const profile = _getPersonaProfileById(pid, _personaManagerType);
    if (!profile) {
        alert("Persona nao encontrada.");
        return;
    }

    const sourcePrompt = String(profile.prompt_text || "").trim();
    if (sourcePrompt.length < 12) {
        alert("Esta persona nao possui prompt salvo para edicao.");
        return;
    }

    _personaPromptEditorProfileId = pid;

    const titleEl = document.getElementById("persona-prompt-editor-title");
    if (titleEl) {
        titleEl.textContent = `Editar prompt de ${profile.name || `Persona ${pid}`}`;
    }

    const profileIdEl = document.getElementById("persona-prompt-editor-profile-id");
    if (profileIdEl) {
        profileIdEl.value = String(pid);
    }

    const nameEl = document.getElementById("persona-prompt-editor-name");
    if (nameEl) {
        const baseName = String(profile.name || `Persona ${pid}`).trim();
        nameEl.value = `${baseName} editada`.slice(0, 80);
    }

    const promptEl = document.getElementById("persona-prompt-editor-prompt");
    if (promptEl) {
        promptEl.value = sourcePrompt;
    }

    const defaultEl = document.getElementById("persona-prompt-editor-set-default");
    if (defaultEl) {
        defaultEl.checked = false;
    }

    openModal("modal-persona-prompt-editor");
    if (promptEl) {
        promptEl.focus();
        const end = promptEl.value.length;
        promptEl.setSelectionRange(end, end);
    }
}

async function createPersonaFromPromptEditor() {
    const profileIdEl = document.getElementById("persona-prompt-editor-profile-id");
    const pid = parseInt(profileIdEl?.value || _personaPromptEditorProfileId || "0", 10) || 0;
    if (!pid) {
        alert("Persona invalida para edicao de prompt.");
        return;
    }

    const name = String(document.getElementById("persona-prompt-editor-name")?.value || "").trim();
    const promptText = String(document.getElementById("persona-prompt-editor-prompt")?.value || "").trim();
    const setDefault = !!document.getElementById("persona-prompt-editor-set-default")?.checked;
    if (promptText.length < 12) {
        alert("Descreva melhor o prompt antes de gerar.");
        return;
    }

    const saveBtn = document.getElementById("persona-prompt-editor-save");
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = "Gerando...";
    }

    try {
        const response = await api(`/persona/profiles/${pid}/remix`, {
            method: "POST",
            body: JSON.stringify({
                name,
                prompt_text: promptText,
                set_default: setDefault,
            }),
        });

        const createdId = parseInt(response?.profile?.id || "0", 10) || 0;
        if (createdId) {
            if (_personaManagerMulti) {
                const selectedIds = _getSelectedPersonaProfileIds(_personaManagerContext, _personaManagerType);
                _setSelectedPersonaProfileIds(_personaManagerContext, _personaManagerType, [...selectedIds, createdId]);
            } else {
                _setSelectedPersonaProfileId(_personaManagerContext, _personaManagerType, createdId);
            }
        }

        await _refreshPersonaManagerList();
        closeModal("modal-persona-prompt-editor");
        showToast("Nova persona criada a partir do prompt.", "success");
    } catch (error) {
        alert(`Erro ao editar prompt da persona: ${error.message}`);
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = "Gerar nova persona";
        }
    }
}

async function selectPersonaFromManager(profileId) {
    const pid = parseInt(profileId || "0", 10) || 0;
    if (!pid) return;
    _setSelectedPersonaProfileId(_personaManagerContext, _personaManagerType, pid);
    _renderPersonaManagerList();
    _refreshAllPersonaPreviews();
}

async function togglePersonaSelectionFromManager(profileId) {
    const pid = parseInt(profileId || "0", 10) || 0;
    if (!pid) return;

    const selectedIds = _getSelectedPersonaProfileIds(_personaManagerContext, _personaManagerType);
    if (selectedIds.includes(pid)) {
        _setSelectedPersonaProfileIds(
            _personaManagerContext,
            _personaManagerType,
            selectedIds.filter((sid) => sid !== pid),
        );
    } else {
        _setSelectedPersonaProfileIds(
            _personaManagerContext,
            _personaManagerType,
            [...selectedIds, pid],
        );
    }

    _renderPersonaManagerList();
    _refreshAllPersonaPreviews();
}

async function setDefaultPersonaFromManager(profileId) {
    const pid = parseInt(profileId || "0", 10) || 0;
    if (!pid) return;

    try {
        await api("/persona/profiles/default", {
            method: "POST",
            body: JSON.stringify({ profile_id: pid }),
        });
        await _refreshPersonaManagerList();
        showToast("Persona padrao atualizada.", "success");
    } catch (error) {
        alert(`Erro ao definir padrao: ${error.message}`);
    }
}

async function deletePersonaFromManager(profileId) {
    const pid = parseInt(profileId || "0", 10) || 0;
    if (!pid) return;
    if (!confirm("Excluir esta persona?")) return;

    try {
        await api(`/persona/profiles/${pid}`, { method: "DELETE" });

        ["wizard", "script", "ai", "auto"].forEach((ctx) => {
            const selectedIds = _getSelectedPersonaProfileIds(ctx, _personaManagerType);
            if (selectedIds.includes(pid)) {
                _setSelectedPersonaProfileIds(
                    ctx,
                    _personaManagerType,
                    selectedIds.filter((sid) => sid !== pid),
                );
            }
        });

        await _refreshPersonaManagerList();
    } catch (error) {
        alert(`Erro ao excluir persona: ${error.message}`);
    }
}

function getSelectedRealisticPersona() {
    const sel = document.querySelector("#script-realistic-persona-tags .style-tag.selected")
        || document.querySelector("#wizard-realistic-persona-tags .style-tag.selected");
    return sel ? (sel.dataset.persona || "natureza") : "natureza";
}

function setSelectedRealisticPersona(persona) {
    const normalized = _normalizeRealisticPersonaType(persona || "natureza");
    ["script-realistic-persona-tags", "wizard-realistic-persona-tags", "ai-suggest-persona-tags"].forEach((id) => {
        const container = document.getElementById(id);
        if (!container) return;
        container.querySelectorAll(".style-tag").forEach((tag) => {
            tag.classList.toggle("selected", tag.dataset.persona === normalized);
        });
    });

    _refreshPersonaContext("script", normalized);
    _refreshPersonaContext("wizard", normalized);
    _refreshPersonaContext("ai", normalized);
}

function showAiSuggestPanel() {
    const isRealistic = scriptData.videoType === "realista";
    if (isRealistic) {
        const selectedPersona = getSelectedRealisticPersona();
        setSelectedRealisticPersona(selectedPersona);
        _refreshPersonaContext("ai", selectedPersona);
    }
    const hasScriptTevoxiClip = isRealistic
        && (document.getElementById("script-realistic-tevoxi")?.checked || false)
        && !!_scriptSelectedSong
        && !!_scriptSelectedClip;

    const aiHintEl = document.getElementById("ai-suggest-hint");
    const aiTopicEl = document.getElementById("ai-suggest-topic");

    // Adapt AI suggest panel for mode
    document.getElementById("ai-suggest-title").textContent = isRealistic ? "Gerar prompt com IA" : "Gerar roteiro com IA";
    aiHintEl.textContent = isRealistic
        ? "Descreva a cena e escolha a duração para a IA criar um prompt cinematográfico profissional"
        : "Descreva o tema e a IA criara um roteiro completo";
    aiTopicEl.placeholder = isRealistic
        ? "Ex: uma cachorra adotou um gatinho, produto girando..."
        : "Ex: beneficios da meditacao, como fazer pao caseiro...";
    if (hasScriptTevoxiClip) {
        aiHintEl.textContent = "A IA vai analisar o trecho selecionado do Tevoxi para sugerir o roteiro visual.";
        if (!aiTopicEl.value.trim()) {
            aiTopicEl.value = _buildTevoxiAiTopicSeed(_scriptSelectedSong, _scriptSelectedClip);
        }
    }
    document.getElementById("ai-suggest-tone-group").hidden = isRealistic;
    document.getElementById("ai-suggest-style-group").hidden = !isRealistic;
    document.getElementById("ai-suggest-persona-group").hidden = !isRealistic;
    document.getElementById("ai-suggest-realistic-duration-group").hidden = !isRealistic;
    document.getElementById("ai-suggest-duration-group").hidden = isRealistic;
    document.getElementById("ai-suggest-generate-text").textContent = isRealistic ? "Gerar Prompt" : "Gerar Roteiro";
    document.getElementById("create-panel-script").hidden = true;
    document.getElementById("ai-suggest-panel").hidden = false;
}

function hideAiSuggestPanel() {
    document.getElementById("ai-suggest-panel").hidden = true;
    document.getElementById("create-panel-script").hidden = false;
}

async function generateAiScript() {
    const isRealistic = scriptData.videoType === "realista";
    const hasScriptTevoxiClip = isRealistic
        && (document.getElementById("script-realistic-tevoxi")?.checked || false)
        && !!_scriptSelectedSong
        && !!_scriptSelectedClip;
    let topic = document.getElementById("ai-suggest-topic").value.trim();
    if (!topic && hasScriptTevoxiClip) {
        topic = _buildTevoxiAiTopicSeed(_scriptSelectedSong, _scriptSelectedClip);
    }
    if (!topic) { alert("Digite o tema do vídeo."); return; }

    if (isRealistic) {
        // Generate optimized prompt for the selected engine
        const style = document.getElementById("ai-suggest-style").value;
        const selectedPersonaBtn = document.querySelector("#ai-suggest-persona-tags .style-tag.selected");
        const interactionPersona = selectedPersonaBtn ? (selectedPersonaBtn.dataset.persona || "natureza") : "natureza";
        setSelectedRealisticPersona(interactionPersona);
        const realisticDurationBtn = document.querySelector("#ai-suggest-realistic-duration .duration-option.selected");
        const realisticDuration = realisticDurationBtn ? parseInt(realisticDurationBtn.dataset.value, 10) : 10;
        let engineBtn = document.querySelector("#script-realistic-engine .engine-option.selected") || document.querySelector("#wizard-realistic-engine .engine-option.selected");
        let engine = engineBtn ? engineBtn.dataset.value : "wan2";
        if (realisticDuration > 10 && engine !== "grok") {
            const engineSelector = document.getElementById("script-realistic-engine") || document.getElementById("wizard-realistic-engine");
            const grokBtn = engineSelector?.querySelector('.engine-option[data-value="grok"]');
            if (grokBtn && engineSelector) {
                engineSelector.querySelectorAll(".engine-option").forEach((d) => d.classList.remove("selected"));
                grokBtn.classList.add("selected");
                engineBtn = grokBtn;
            }
            engine = "grok";
            showToast("Duracoes acima de 10s usam Cria 3.0 speed automaticamente.");
        }
        let selectedPersonaIds = [];
        try {
            selectedPersonaIds = await _ensurePersonaSelections("ai", interactionPersona);
        } catch (_) {
            selectedPersonaIds = [];
        }
        const usePhotosToggle = document.getElementById("script-use-photos");
        const hasReferenceImage = selectedPersonaIds.length > 0 || (scriptPhotos.length > 0 && (!usePhotosToggle || usePhotosToggle.checked));
        const engineLabel = engine === "grok"
            ? "Cria 3.0 speed"
            : engine === "minimax"
                ? "MiniMax"
                : engine === "wan2"
                    ? "Ultra High 2.2"
                    : "Seedance";
        showCreateProgress("Gerando prompt cinematográfico com IA...", {
            progress: 30,
            stage: `Otimizando prompt ${engineLabel}...`,
        });
        try {
            const tevoxiContext = hasScriptTevoxiClip
                ? _buildTevoxiPromptContext(_scriptSelectedSong, _scriptSelectedClip)
                : "";
            const topicWithContext = tevoxiContext ? `${topic}\n\n${tevoxiContext}` : topic;
            const result = await api("/video/generate-realistic-prompt", {
                method: "POST",
                body: JSON.stringify({
                    topic: topicWithContext,
                    style,
                    engine,
                    duration: realisticDuration,
                    interaction_persona: interactionPersona,
                    has_reference_image: hasReferenceImage,
                }),
            });
            hideCreateProgress();
            document.getElementById("script-text").value = result.prompt;
            document.getElementById("script-char-count").textContent = result.prompt.length.toLocaleString("pt-BR");
            scriptData.promptOptimized = true;
            hideAiSuggestPanel();
        } catch (error) {
            hideCreateProgress();
            alert(`Erro ao gerar prompt: ${error.message}`);
        }
        return;
    }

    // Normal script generation
    const usePhotos = document.getElementById("script-use-photos")?.checked && scriptPhotos.length > 0;
    showCreateProgress(usePhotos ? "Preparando analise das fotos..." : "Gerando roteiro com IA...", {
        progress: 12,
        stage: usePhotos ? "Enviando fotos..." : "Criando roteiro...",
    });

    try {
        const uploadedImageIds = [];
        if (usePhotos) {
            const photosToAnalyze = scriptPhotos.slice(0, MAX_AI_SCRIPT_PHOTO_ANALYSIS);
            for (let i = 0; i < photosToAnalyze.length; i++) {
                const uploadProgress = Math.round(12 + ((i + 1) / photosToAnalyze.length) * 33);
                showCreateProgress(`Enviando foto ${i + 1}/${photosToAnalyze.length} para analise...`, {
                    progress: uploadProgress,
                    stage: "Enviando fotos...",
                });
                const uploaded = await uploadTempFileWithRetry(photosToAnalyze[i], "image", `foto ${i + 1}`);
                if (uploaded?.upload_id) {
                    uploadedImageIds.push(uploaded.upload_id);
                }
            }
        }

        showCreateProgress(uploadedImageIds.length > 0 ? "Analisando fotos e criando roteiro..." : "Gerando roteiro com IA...", {
            progress: 50,
            stage: "Criando roteiro...",
        });

        const result = await api("/video/generate-script", {
            method: "POST",
            body: JSON.stringify({
                topic,
                tone: document.getElementById("ai-suggest-tone").value,
                duration_seconds: parseInt(document.getElementById("ai-suggest-duration").value, 10),
                custom_image_ids: uploadedImageIds,
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

function setCreateProgress(progress, stage = "Processando...", message = "") {
    const normalized = Number.isFinite(progress) ? Math.max(0, Math.min(100, Math.round(progress))) : CREATE_PROGRESS_BASE;
    const stageEl = document.getElementById("create-progress-stage");
    const textEl = document.getElementById("create-progress-text");

    // Set target for smooth animation
    _smoothProgressTarget = normalized;
    if (_smoothProgressCurrent > normalized) _smoothProgressCurrent = normalized; // allow reset down
    _startSmoothProgress();

    // Update text immediately
    if (stageEl) stageEl.textContent = stage || "Processando...";
    if (textEl && message) textEl.textContent = message;

    // If 100%, snap immediately
    if (normalized >= 100) {
        _smoothProgressCurrent = 100;
        const fill = document.getElementById("create-progress-fill");
        const percentEl = document.getElementById("create-progress-percent");
        if (fill) fill.style.width = "100%";
        if (percentEl) percentEl.textContent = "100%";
        _stopSmoothProgress();
    }
}

function showCreateProgress(message, options = {}) {
    document.querySelectorAll(".create-panel").forEach((p) => (p.hidden = true));
    document.getElementById("ai-suggest-panel").hidden = true;
    document.getElementById("create-progress").hidden = false;
    const progress = Number.isFinite(options.progress) ? options.progress : CREATE_PROGRESS_BASE;
    const stage = options.stage || "Processando...";
    setCreateProgress(progress, stage, message);
}

function hideCreateProgress() {
    stopKaraokeProgressPolling();
    _stopSmoothProgress();
    _smoothProgressTarget = CREATE_PROGRESS_BASE;
    _smoothProgressCurrent = CREATE_PROGRESS_BASE;
    document.getElementById("create-progress").hidden = true;
    const panel = document.getElementById(`create-panel-${createMode}`);
    if (panel) panel.hidden = false;
}

function createKaraokeOperationId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
    }
    return `karaoke-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function stopKaraokeProgressPolling() {
    if (karaokeProgressTimer) {
        clearInterval(karaokeProgressTimer);
        karaokeProgressTimer = null;
    }
    karaokeProgressOperationId = "";
    // Restore verbose text visibility
    const progressTextEl = document.getElementById("create-progress-text");
    if (progressTextEl) progressTextEl.hidden = false;
}

function startKaraokeProgressPolling(operationId) {
    if (!operationId) {
        return;
    }
    stopKaraokeProgressPolling();
    karaokeProgressOperationId = operationId;

    // Hide verbose message text during vocal removal — keep only spinner, stage and %
    const progressTextEl = document.getElementById("create-progress-text");
    if (progressTextEl) progressTextEl.hidden = true;

    const pollOnce = async () => {
        if (!karaokeProgressOperationId || karaokeProgressOperationId !== operationId) {
            return;
        }
        try {
            const state = await api(`/video/karaoke-progress/${operationId}`);
            if (!state || karaokeProgressOperationId !== operationId) {
                return;
            }

            const status = String(state.status || "running").toLowerCase();
            const serverProgress = Number(state.progress);
            const progress = Number.isFinite(serverProgress)
                ? Math.max(CREATE_PROGRESS_BASE, Math.min(100, Math.round(serverProgress)))
                : CREATE_PROGRESS_BASE;
            const stage = status === "failed"
                ? "Falha na remocao"
                : status === "completed"
                    ? "Remocao concluida"
                    : "Removendo voz...";
            setCreateProgress(progress, stage);

            if (status === "failed") {
                stopKaraokeProgressPolling();
            }
        } catch (_) {
            // Ignore transient poll errors; request completion will still drive final UI state.
        }
    };

    pollOnce();
    karaokeProgressTimer = setInterval(pollOnce, 2000);
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
        alert("Selecione uma música ou use o modo manual.");
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
                style_prompt: getSelectedStyles("np-style-tags"),
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
        loadProjects(); // Will auto-start polling via _pollInProgress
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

function _sortRendersNewestFirst(renders) {
    return [...(Array.isArray(renders) ? renders : [])].sort((a, b) => {
        const idA = Number(a?.id || 0);
        const idB = Number(b?.id || 0);
        return idB - idA;
    });
}

function _pickLatestAvailableRender(renders) {
    return _sortRendersNewestFirst(renders).find((item) => item && item.video_url) || null;
}

async function watchVideo(projectId) {
    try {
        const project = await api(`/video/projects/${projectId}`);
        if (!project.renders || !project.renders.length) {
            alert("Nenhum vídeo renderizado encontrado.");
            return;
        }
        const render = _pickLatestAvailableRender(project.renders);
        if (!render) {
            alert("Este vídeo não está mais disponível para reprodução.");
            return;
        }
        const playerModal = document.getElementById("modal-player");
        const video = document.getElementById("player-video");
        if (!playerModal || !video) {
            window.open(render.video_url, "_blank");
            return;
        }
        document.getElementById("player-title").textContent = project.title || "Vídeo";
        // Open modal first so mobile browsers render video track correctly.
        openModal("modal-player");
        video.pause();
        video.removeAttribute("src");
        video.load();
        video.setAttribute("playsinline", "");
        video.setAttribute("webkit-playsinline", "");
        video.src = render.video_url;
        video.load();
        const tryPlay = () => {
            const playPromise = video.play();
            if (playPromise && typeof playPromise.catch === "function") {
                playPromise.catch(() => {});
            }
        };
        if (video.readyState >= 2) {
            tryPlay();
        } else {
            video.addEventListener("loadeddata", tryPlay, { once: true });
        }
        const sizeMb = render.file_size ? `${(render.file_size / 1048576).toFixed(1)} MB` : "";
        const duration = render.duration ? `${Math.floor(render.duration / 60)}:${String(Math.floor(render.duration % 60)).padStart(2, "0")}` : "";
        // Show expiry countdown in player
        let expiryInfo = "";
        if (render.created_at) {
            const renderDate = new Date(render.created_at);
            const expiresAt = new Date(renderDate.getTime() + RENDER_EXPIRY_HOURS * 3600000);
            const remaining = expiresAt - new Date();
            if (remaining > 0) {
                const h = Math.floor(remaining / 3600000);
                const m = Math.floor((remaining % 3600000) / 60000);
                expiryInfo = `⏳ Expira em ${h}h ${String(m).padStart(2,"0")}m`;
            }
        }
        document.getElementById("player-info").textContent = [render.format, duration, sizeMb, expiryInfo].filter(Boolean).join(" · ");
        const download = document.getElementById("player-download");
        download.href = render.video_url;
        download.download = `${project.title || "video"}.mp4`;
    } catch (error) {
        alert(`Erro ao carregar video: ${error.message}`);
    }
}

async function loadRenders(preselectProjectId = 0) {
    try {
        const projects = await api("/video/projects");
        const select = document.getElementById("pub-render-select");
        if (!select) {
            _publishRenderOptions = {};
            return false;
        }
        const wantedProjectId = parseInt(preselectProjectId, 10) || 0;
        let preselectRenderId = "";
        const renderOptions = {};
        select.innerHTML = "<option value=''>Selecione aqui...</option>";
        for (const project of projects) {
            if (project.status !== "completed") {
                continue;
            }
            try {
                const detail = await api(`/video/projects/${project.id}`);
                const orderedRenders = _sortRendersNewestFirst(detail.renders || []);
                for (const render of orderedRenders) {
                    if (!render.video_url) {
                        continue;
                    }
                    const duration = render.duration != null
                        ? `${Math.floor(render.duration / 60)}:${String(Math.round(render.duration % 60)).padStart(2, "0")}`
                        : "?";
                    const optionLabel = `[${project.title || "Sem título"}] ${render.format} - ${duration}`;
                    select.innerHTML += `<option value="${render.id}">${esc(optionLabel)}</option>`;
                    renderOptions[String(render.id)] = optionLabel;
                    if (wantedProjectId && project.id === wantedProjectId && !preselectRenderId) {
                        preselectRenderId = String(render.id);
                    }
                }
            } catch (_) {
                // ignore one broken project and continue
            }
        }
        _publishRenderOptions = renderOptions;
        renderPublishDraftList();
        if (preselectRenderId) {
            select.value = preselectRenderId;
            renderPublishDraftPicker();
            return true;
        }
        renderPublishDraftPicker();
        return false;
    } catch (_) {
        _publishRenderOptions = {};
        renderPublishDraftList();
        // keep select empty if request fails
        return false;
    }
}

function openPublishForProject(projectId) {
    _pendingPublishProjectId = parseInt(projectId, 10) || 0;
    const project = _projectsCache.find((p) => p.id === projectId);
    if (project) {
        const titleInput = document.getElementById("pub-title");
        if (titleInput && !titleInput.value.trim()) {
            titleInput.value = project.title || "";
        }
    }

    navigateTo("publish");
}

function getCheckedPublishPlatforms() {
    return ["youtube"];
}

function buildPublishPayload(scheduledAt = "") {
    const renderId = document.getElementById("pub-render-select").value;
    if (!renderId) {
        alert("Selecione um vídeo");
        return null;
    }

    const platforms = getCheckedPublishPlatforms();
    if (!platforms.length) {
        alert("Selecione pelo menos uma plataforma");
        return null;
    }

    const accountIds = {};
    for (const platform of platforms) {
        const select = document.getElementById(`pub-account-${platform}`);
        const selectedAccountId = parseInt(select?.value || "", 10);
        if (!selectedAccountId) {
            alert(`Selecione uma conta para ${socialPlatformName(platform)}.`);
            return null;
        }
        accountIds[platform] = selectedAccountId;
    }

    const descField = document.getElementById("pub-description");
    const hashtagsField = document.getElementById("pub-hashtags");
    const hashtagText = (hashtagsField?.value || "").trim();
    let fullDesc = (descField?.value || "").trim();
    if (hashtagText) {
        fullDesc = fullDesc ? `${fullDesc}\n\n${hashtagText}` : hashtagText;
    }

    const linksText = getPublishLinksForPayload();
    if (linksText) {
        fullDesc = fullDesc ? `${fullDesc}\n\n${linksText}` : linksText;
    }

    const payload = {
        render_id: parseInt(renderId, 10),
        platforms,
        account_ids: accountIds,
        title: (document.getElementById("pub-title")?.value || "").trim(),
        description: fullDesc,
    };
    if (scheduledAt) {
        payload.scheduled_at = scheduledAt;
    }
    return payload;
}

// ---- Publish Links (per-account social / important links for video descriptions) ----

function togglePublishLinks() {
    const body = document.getElementById("pub-links-body");
    const arrow = document.getElementById("pub-links-arrow");
    if (body.hidden) {
        body.hidden = false;
        arrow.classList.add("open");
    } else {
        body.hidden = true;
        arrow.classList.remove("open");
    }
}

function getSelectedPublishAccountId() {
    const platforms = getCheckedPublishPlatforms();
    if (!platforms.length) return null;
    const sel = document.getElementById(`pub-account-${platforms[0]}`);
    return sel ? parseInt(sel.value || "", 10) || null : null;
}

function getSelectedPublishAccount() {
    const accountId = getSelectedPublishAccountId();
    if (!accountId || !_socialAccountsCache.length) return null;
    return _socialAccountsCache.find(a => a.id === accountId) || null;
}

function loadPublishLinksForCurrentAccount() {
    const account = getSelectedPublishAccount();
    const field = document.getElementById("pub-links");
    const label = document.getElementById("pub-links-account-label");
    if (!field) return;
    if (account) {
        field.value = account.publish_links || "";
        if (label) label.textContent = socialAccountDisplayName(account);
    } else {
        field.value = "";
        if (label) label.textContent = "";
    }
}

async function savePublishLinksForAccount() {
    const accountId = getSelectedPublishAccountId();
    if (!accountId) {
        alert("Selecione uma plataforma e conta primeiro.");
        return;
    }
    const field = document.getElementById("pub-links");
    const links = (field?.value || "").trim();
    try {
        await api(`/publish/links/${accountId}`, {
            method: "PUT",
            body: JSON.stringify({ links }),
        });
        // Update cache
        const cached = _socialAccountsCache.find(a => a.id === accountId);
        if (cached) cached.publish_links = links;
        alert("Links salvos com sucesso!");
    } catch (e) {
        alert("Erro ao salvar links: " + (e.message || e));
    }
}

function getPublishLinksForPayload() {
    const platforms = getCheckedPublishPlatforms();
    const seen = new Set();
    const allLinks = [];
    for (const platform of platforms) {
        const sel = document.getElementById(`pub-account-${platform}`);
        const accountId = parseInt(sel?.value || "", 10);
        if (!accountId || seen.has(accountId)) continue;
        seen.add(accountId);
        const account = _socialAccountsCache.find(a => a.id === accountId);
        if (account && (account.publish_links || "").trim()) {
            allLinks.push(account.publish_links.trim());
        }
    }
    return allLinks.join("\n\n");
}

function getPublishDraftStorageKey(renderId) {
    return `${PUBLISH_DRAFT_STORAGE_PREFIX}${renderId}`;
}

function clearPublishThumbnail() {
    const thumbArea = document.getElementById("pub-thumbnail-area");
    const thumbLoading = document.getElementById("pub-thumbnail-loading");
    const thumbPreview = document.getElementById("pub-thumbnail-preview");
    const btnRegen = document.getElementById("btn-regenerate-thumb");

    if (thumbArea) thumbArea.hidden = true;
    if (thumbLoading) thumbLoading.hidden = true;
    if (thumbPreview) {
        thumbPreview.hidden = true;
        thumbPreview.src = "";
        thumbPreview.removeAttribute("data-raw-url");
    }
    if (btnRegen) btnRegen.hidden = true;
}

function getPublishThumbnailUrlFromForm() {
    const thumbPreview = document.getElementById("pub-thumbnail-preview");
    if (!thumbPreview || thumbPreview.hidden) {
        return "";
    }

    const raw = String(thumbPreview.dataset.rawUrl || "").trim();
    if (raw) {
        return raw;
    }

    const src = String(thumbPreview.getAttribute("src") || "").trim();
    if (!src) {
        return "";
    }

    return src.replace(/\?t=\d+$/, "");
}

function applyPublishDraftThumbnail(thumbnailUrl) {
    const cleanUrl = String(thumbnailUrl || "").trim();
    if (!cleanUrl) {
        clearPublishThumbnail();
        return;
    }

    const thumbArea = document.getElementById("pub-thumbnail-area");
    const thumbLoading = document.getElementById("pub-thumbnail-loading");
    const thumbPreview = document.getElementById("pub-thumbnail-preview");
    const btnRegen = document.getElementById("btn-regenerate-thumb");

    if (thumbArea) thumbArea.hidden = false;
    if (thumbLoading) thumbLoading.hidden = true;
    if (thumbPreview) {
        thumbPreview.dataset.rawUrl = cleanUrl;
        thumbPreview.src = `${cleanUrl}?t=${Date.now()}`;
        thumbPreview.hidden = false;
    }
    if (btnRegen) btnRegen.hidden = false;
}

function getAllPublishDrafts() {
    const drafts = [];
    for (let i = 0; i < localStorage.length; i += 1) {
        const key = localStorage.key(i);
        if (!key || !key.startsWith(PUBLISH_DRAFT_STORAGE_PREFIX)) {
            continue;
        }

        const renderId = parseInt(key.slice(PUBLISH_DRAFT_STORAGE_PREFIX.length), 10);
        if (!Number.isFinite(renderId) || renderId <= 0) {
            continue;
        }

        const draft = readPublishDraft(renderId);
        if (!draft) {
            continue;
        }

        drafts.push({
            render_id: renderId,
            title: String(draft.title || ""),
            description: String(draft.description || ""),
            hashtags: String(draft.hashtags || ""),
            thumbnail_url: String(draft.thumbnail_url || ""),
            platforms: Array.isArray(draft.platforms) ? draft.platforms : [],
            account_ids: draft.account_ids && typeof draft.account_ids === "object" ? draft.account_ids : {},
            updated_at: draft.updated_at || "",
        });
    }

    drafts.sort((a, b) => {
        const timeA = new Date(a.updated_at || 0).getTime();
        const timeB = new Date(b.updated_at || 0).getTime();
        return timeB - timeA;
    });
    return drafts;
}

function getPublishRenderLabel(renderId) {
    const mappedLabel = _publishRenderOptions[String(renderId)];
    if (mappedLabel) {
        return mappedLabel;
    }

    const select = document.getElementById("pub-render-select");
    if (select) {
        const match = Array.from(select.options).find((option) => option.value === String(renderId));
        if (match && match.textContent) {
            return match.textContent;
        }
    }

    return `Render #${renderId}`;
}

function formatPublishDraftDate(rawValue) {
    const date = new Date(rawValue || "");
    if (Number.isNaN(date.getTime())) {
        return "-";
    }
    return date.toLocaleString("pt-BR");
}

function collectPublishDraftFromForm() {
    const platforms = getCheckedPublishPlatforms();
    const accountIds = {};
    for (const platform of platforms) {
        const selectedAccountId = parseInt(document.getElementById(`pub-account-${platform}`)?.value || "", 10);
        if (selectedAccountId) {
            accountIds[platform] = selectedAccountId;
        }
    }

    return {
        title: document.getElementById("pub-title")?.value || "",
        description: document.getElementById("pub-description")?.value || "",
        hashtags: document.getElementById("pub-hashtags")?.value || "",
        thumbnail_url: getPublishThumbnailUrlFromForm(),
        platforms,
        account_ids: accountIds,
        updated_at: new Date().toISOString(),
    };
}

function renderPublishDraftPicker(drafts = null) {
    const picker = document.getElementById("pub-draft-select");
    if (!picker) {
        return;
    }

    const allDrafts = Array.isArray(drafts) ? drafts : getAllPublishDrafts();
    if (!allDrafts.length) {
        picker.innerHTML = "<option value=''>Meus rascunhos...</option>";
        picker.disabled = true;
        return;
    }

    const currentRenderId = String(document.getElementById("pub-render-select")?.value || "");
    const hasCurrentDraft = allDrafts.some((draft) => String(draft.render_id) === currentRenderId);

    picker.disabled = false;
    picker.innerHTML = [
        "<option value=''>Escolha um rascunho...</option>",
        ...allDrafts.map((draft) => {
            const renderLabel = getPublishRenderLabel(draft.render_id);
            const compactLabel = renderLabel.length > 58 ? `${renderLabel.slice(0, 58)}...` : renderLabel;
            return `<option value="${draft.render_id}">${esc(compactLabel)}</option>`;
        }),
    ].join("");
    picker.value = hasCurrentDraft ? currentRenderId : "";
}

function renderPublishDraftList() {
    const drafts = getAllPublishDrafts();
    renderPublishDraftPicker(drafts);

    const container = document.getElementById("publish-drafts-list");
    if (!container) {
        return;
    }

    if (!drafts.length) {
        container.innerHTML = "<p class='publish-drafts-empty'>Nenhum rascunho salvo neste navegador.</p>";
        return;
    }

    container.innerHTML = `
        <div class="publish-drafts-list">
            ${drafts.map((draft) => {
                const title = draft.title.trim() || "Sem título";
                const description = draft.description.trim();
                const descriptionPreview = description
                    ? (description.length > 140 ? `${description.slice(0, 140).trim()}...` : description)
                    : "Sem descrição.";
                const platforms = draft.platforms.length
                    ? draft.platforms.map((item) => socialPlatformName(item)).join(", ")
                    : "Sem plataformas selecionadas";
                const updatedAt = formatPublishDraftDate(draft.updated_at);
                const renderLabel = getPublishRenderLabel(draft.render_id);
                return `
                    <div class="publish-draft-item">
                        <div class="publish-draft-head">
                            <h4 class="publish-draft-title">${esc(title)}</h4>
                            <span class="publish-draft-meta">Atualizado em ${esc(updatedAt)}</span>
                        </div>
                        <p class="publish-draft-meta">Video: ${esc(renderLabel)}</p>
                        <p class="publish-draft-meta">Plataformas: ${esc(platforms)}</p>
                        <p class="publish-draft-desc">${esc(descriptionPreview)}</p>
                        <div class="publish-draft-actions">
                            <button class="btn btn-secondary btn-sm" type="button" onclick="openPublishDraftFromList(${draft.render_id})">Abrir</button>
                            <button class="btn btn-provider btn-sm" type="button" onclick="overwritePublishDraftFromList(${draft.render_id})">Sobrescrever</button>
                            <button class="btn btn-secondary btn-sm" type="button" onclick="deletePublishDraftFromList(${draft.render_id})">Excluir</button>
                        </div>
                    </div>
                `;
            }).join("")}
        </div>
    `;
}

function readPublishDraft(renderId) {
    try {
        const raw = localStorage.getItem(getPublishDraftStorageKey(renderId));
        if (!raw) return null;
        const data = JSON.parse(raw);
        return data && typeof data === "object" ? data : null;
    } catch (_) {
        return null;
    }
}

async function applyPublishDraft(renderId) {
    const draft = readPublishDraft(renderId);
    if (!draft) return false;

    const titleInput = document.getElementById("pub-title");
    const descInput = document.getElementById("pub-description");
    const hashtagsInput = document.getElementById("pub-hashtags");

    if (titleInput) titleInput.value = String(draft.title || "");
    if (descInput) descInput.value = String(draft.description || "");
    if (hashtagsInput) hashtagsInput.value = String(draft.hashtags || "");

    if (draft.account_ids && typeof draft.account_ids === "object") {
        Object.entries(draft.account_ids).forEach(([platform, accountId]) => {
            _publishAccountSelection[platform] = String(accountId || "");
        });
    }

    await renderPublishAccountSelectors(true);

    if (draft.account_ids && typeof draft.account_ids === "object") {
        Object.entries(draft.account_ids).forEach(([platform, accountId]) => {
            const select = document.getElementById(`pub-account-${platform}`);
            if (!select) return;
            const target = String(accountId || "");
            const hasOption = Array.from(select.options).some((option) => option.value === target);
            if (hasOption) {
                select.value = target;
                _publishAccountSelection[platform] = target;
            }
        });
    }

    applyPublishDraftThumbnail(draft.thumbnail_url || "");

    return true;
}

function savePublishDraft() {
    const renderId = parseInt(document.getElementById("pub-render-select")?.value || "", 10);
    if (!renderId) {
        alert("Selecione um vídeo para salvar rascunho.");
        return;
    }

    const draft = collectPublishDraftFromForm();
    localStorage.setItem(getPublishDraftStorageKey(renderId), JSON.stringify(draft));
    renderPublishDraftList();
    alert("Rascunho salvo.");
}

async function openPublishDraftFromList(renderId) {
    const parsedRenderId = parseInt(renderId, 10);
    if (!Number.isFinite(parsedRenderId) || parsedRenderId <= 0) {
        alert("Rascunho inválido.");
        return;
    }

    const select = document.getElementById("pub-render-select");
    if (!select) {
        return;
    }

    const hasRenderOption = () => Array.from(select.options).some((option) => option.value === String(parsedRenderId));
    if (!hasRenderOption()) {
        await loadRenders();
    }
    if (!hasRenderOption()) {
        alert("Este vídeo não está mais disponível para abrir o rascunho.");
        return;
    }

    select.value = String(parsedRenderId);
    await onRenderSelected(parsedRenderId);

    const draftSelect = document.getElementById("pub-draft-select");
    if (draftSelect) {
        draftSelect.value = String(parsedRenderId);
    }

    const formArea = document.getElementById("publish-form-area");
    if (formArea) {
        formArea.scrollIntoView({ behavior: "smooth", block: "start" });
    }
}

function overwritePublishDraftFromList(renderId) {
    const parsedRenderId = parseInt(renderId, 10);
    if (!Number.isFinite(parsedRenderId) || parsedRenderId <= 0) {
        alert("Rascunho inválido.");
        return;
    }

    if (!window.confirm("Sobrescrever este rascunho com os dados atuais do formulario?")) {
        return;
    }

    const draft = collectPublishDraftFromForm();
    localStorage.setItem(getPublishDraftStorageKey(parsedRenderId), JSON.stringify(draft));
    renderPublishDraftList();
    alert("Rascunho sobrescrito.");
}

function deletePublishDraftFromList(renderId) {
    const parsedRenderId = parseInt(renderId, 10);
    if (!Number.isFinite(parsedRenderId) || parsedRenderId <= 0) {
        alert("Rascunho inválido.");
        return;
    }

    if (!window.confirm("Excluir este rascunho?")) {
        return;
    }

    localStorage.removeItem(getPublishDraftStorageKey(parsedRenderId));
    renderPublishDraftList();
    alert("Rascunho excluido.");
}

async function submitPublishNow() {
    const payload = buildPublishPayload();
    if (!payload) return;

    try {
        await api("/publish/", {
            method: "POST",
            body: JSON.stringify(payload),
        });
        alert("Publicação iniciada.");
        loadPublishJobs();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    }
}

function _toDatetimeLocalValue(date) {
    const pad = (value) => String(value).padStart(2, "0");
    const year = date.getFullYear();
    const month = pad(date.getMonth() + 1);
    const day = pad(date.getDate());
    const hours = pad(date.getHours());
    const minutes = pad(date.getMinutes());
    return `${year}-${month}-${day}T${hours}:${minutes}`;
}

function openPublishScheduleModal() {
    const renderId = document.getElementById("pub-render-select")?.value;
    if (!renderId) {
        alert("Selecione um vídeo antes de agendar.");
        return;
    }

    const dtInput = document.getElementById("pub-schedule-datetime");
    if (dtInput && !dtInput.value) {
        const oneHourAhead = new Date(Date.now() + 60 * 60 * 1000);
        dtInput.value = _toDatetimeLocalValue(oneHourAhead);
    }
    openModal("modal-publish-schedule");
}

async function confirmSchedulePublish() {
    const dtInput = document.getElementById("pub-schedule-datetime");
    const rawValue = (dtInput?.value || "").trim();
    if (!rawValue) {
        alert("Escolha data e horário para agendar.");
        if (dtInput) dtInput.focus();
        return;
    }

    const scheduledDate = new Date(rawValue);
    if (Number.isNaN(scheduledDate.getTime())) {
        alert("Data/hora invalida.");
        return;
    }
    if (scheduledDate.getTime() <= Date.now() + 30000) {
        alert("Escolha um horário futuro para o agendamento.");
        return;
    }

    const payload = buildPublishPayload(scheduledDate.toISOString());
    if (!payload) return;

    const confirmBtn = document.getElementById("btn-confirm-schedule-publish");
    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = "Agendando...";
    }

    try {
        await api("/publish/", {
            method: "POST",
            body: JSON.stringify(payload),
        });
        closeModal("modal-publish-schedule");
        alert("Publicação agendada com sucesso.");
        loadPublishJobs();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = "Agendar";
        }
    }
}

async function onRenderSelected(renderId) {
    const aiLoading = document.getElementById("pub-ai-loading");
    const titleInput = document.getElementById("pub-title");
    const descInput = document.getElementById("pub-description");
    const hashtagsInput = document.getElementById("pub-hashtags");

    // Show AI loading
    aiLoading.hidden = false;
    clearPublishThumbnail();

    const draftApplied = await applyPublishDraft(renderId);
    if (draftApplied) {
        aiLoading.hidden = true;
        renderPublishDraftPicker();
        return;
    }

    // First: get AI suggestions for title/description
    let aiTitle = "";
    try {
        const data = await api("/publish/ai-suggest", {
            method: "POST",
            body: JSON.stringify({ render_id: renderId }),
        });
        titleInput.value = data.title || "";
        descInput.value = data.description || "";
        hashtagsInput.value = data.hashtags || "";
        aiTitle = data.title || "";
    } catch (err) {
        console.warn("AI suggest failed:", err);
    }

    // Then: generate thumbnail using the AI title for impactful text
    await generatePublishThumbnail(renderId, aiTitle, descInput.value || "");
    aiLoading.hidden = true;
    renderPublishDraftPicker();
}

async function generatePublishThumbnail(renderId, customTitle, customDescription = "") {
    const thumbArea = document.getElementById("pub-thumbnail-area");
    const thumbLoading = document.getElementById("pub-thumbnail-loading");
    const thumbPreview = document.getElementById("pub-thumbnail-preview");
    const btnRegen = document.getElementById("btn-regenerate-thumb");

    thumbArea.hidden = false;
    thumbLoading.hidden = false;
    thumbPreview.hidden = true;
    btnRegen.hidden = true;

    try {
        const body = { render_id: renderId };
        if (customTitle) body.custom_title = customTitle;
        if (customDescription) body.custom_description = customDescription;
        const data = await api("/publish/generate-thumbnail", {
            method: "POST",
            body: JSON.stringify(body),
        });
        if (data.thumbnail_url) {
            thumbPreview.dataset.rawUrl = data.thumbnail_url;
            thumbPreview.src = data.thumbnail_url + "?t=" + Date.now();
            thumbPreview.hidden = false;
            btnRegen.hidden = false;
        }
    } catch (err) {
        console.warn("Thumbnail generation failed:", err);
    } finally {
        thumbLoading.hidden = true;
    }
}

function friendlyPublishError(raw) {
    if (!raw) return "Erro desconhecido. Tente novamente mais tarde.";
    const lower = raw.toLowerCase();
    if (lower.includes("youtube data api") && lower.includes("not been used")) {
        return "A API do YouTube não está ativada no projeto Google Cloud.\n\nPasso a passo:\n1. Acesse console.cloud.google.com\n2. Selecione o projeto do CriaVideo\n3. Vá em APIs e Serviços > Biblioteca\n4. Busque 'YouTube Data API v3' e clique em Ativar\n5. Aguarde alguns minutos e tente publicar novamente.";
    }
    if (lower.includes("accessnotconfigured") || lower.includes("api has not been enabled")) {
        return "Uma API necessária não está ativada no Google Cloud. Acesse console.cloud.google.com, ative a API indicada e tente novamente.";
    }
    if (lower.includes("invalid_grant") || lower.includes("token has been expired") || lower.includes("token has been revoked")) {
        return "Sua conexão com a plataforma expirou.\n\nPasso a passo:\n1. Vá na aba 'Contas' na página de publicação\n2. Desconecte a conta afetada\n3. Conecte novamente\n4. Tente publicar de novo.";
    }
    if (lower.includes("custom video thumbnails") || lower.includes("thumbnails/set")) {
        return "O vídeo foi publicado, mas o YouTube bloqueou a thumbnail personalizada desta conta/canal.\n\nComo resolver:\n1. No YouTube Studio, confirme se o canal está verificado (telefone)\n2. Ative recursos avançados/intermediários da conta\n3. Aguarde alguns minutos após a verificação\n4. Publique novamente para aplicar a thumbnail";
    }
    if (lower.includes("quota") || lower.includes("rate limit") || lower.includes("too many requests")) {
        return "Limite de uso da API atingido. Aguarde algumas horas e tente novamente, ou verifique sua cota no painel do Google Cloud.";
    }
    if (lower.includes("forbidden") || lower.includes("403")) {
        return "Acesso negado pela plataforma. Verifique se a conta conectada tem permissão para publicar vídeos e se todas as APIs necessárias estão ativadas.";
    }
    if (lower.includes("unauthorized") || lower.includes("401")) {
        return "Autenticacao falhou.\n\nPasso a passo:\n1. Va na aba 'Contas'\n2. Desconecte e reconecte a conta\n3. Tente publicar novamente.";
    }
    if (lower.includes("not found") || lower.includes("file not found") || lower.includes("render file")) {
        return "O arquivo de vídeo não foi encontrado no servidor. Tente renderizar o vídeo novamente antes de publicar.";
    }
    if (lower.includes("social account not found")) {
        return "A conta social não foi encontrada. Reconecte sua conta na aba 'Contas' e tente novamente.";
    }
    if (lower.includes("network") || lower.includes("timeout") || lower.includes("connection")) {
        return "Erro de conexão com a plataforma. Verifique sua internet e tente novamente em alguns minutos.";
    }
    return "Erro ao publicar: " + raw + "\n\nSe o problema persistir, entre em contato com o suporte.";
}

async function loadPublishJobs() {
    const container = document.getElementById("publish-jobs-list");
    try {
        const jobs = await api("/publish/jobs");
        if (!jobs.length) {
            container.innerHTML = "<p class='loading'>Nenhuma publicação ainda.</p>";
            return;
        }
        container.innerHTML = `
            <table>
                <tr><th>ID</th><th>Plataforma</th><th>Conta</th><th>Status</th><th>URL</th><th>Data</th></tr>
                ${jobs.map((job) => `
                    <tr>
                        <td>${job.id}</td>
                        <td>${esc(job.platform)}</td>
                        <td>${esc(job.account_label || "Conta conectada")}</td>
                        <td>
                            <span class="badge badge-${badgeClass(job.status)}">${esc(job.status)}</span>
                            ${job.error_message ? `<button class="btn-see-error" onclick="showPublishError(${job.id})" title="${job.status === "failed" ? "Ver motivo da falha" : "Ver detalhes do aviso"}">${job.status === "failed" ? "Ver motivo" : "Ver aviso"}</button>` : ""}
                        </td>
                        <td>${job.platform_url ? `<a href="${esc(job.platform_url)}" target="_blank" rel="noreferrer">Ver</a>` : "-"}</td>
                        <td>${(job.published_at || job.scheduled_at) ? new Date(job.published_at || job.scheduled_at).toLocaleString("pt-BR") : "-"}</td>
                    </tr>
                `).join("")}
            </table>
        `;
        container._publishJobs = jobs;
    } catch (error) {
        container.innerHTML = `<p class="loading">Erro: ${esc(error.message)}</p>`;
    }
}

function showPublishError(jobId) {
    const container = document.getElementById("publish-jobs-list");
    const jobs = container._publishJobs || [];
    const job = jobs.find((j) => j.id === jobId);
    if (!job) return;
    const friendly = friendlyPublishError(job.error_message || "");
    openModal("modal-publish-error");
    const title = document.getElementById("publish-error-title");
    if (title) {
        title.textContent = job.status === "failed" ? "Motivo da falha" : "Aviso da publicação";
    }
    const body = document.getElementById("publish-error-body");
    if (body) body.textContent = friendly;
}
window.showPublishError = showPublishError;

function socialAccountDisplayName(account) {
    if (!account) return "Conta conectada";
    return account.account_label || account.platform_username || "Conta conectada";
}

async function renderPublishAccountSelectors(forceReload = false) {
    const container = document.getElementById("pub-account-selectors");
    if (!container) return;

    const selectedPlatforms = getCheckedPublishPlatforms();

    if (!selectedPlatforms.length) {
        container.hidden = true;
        container.innerHTML = "";
        return;
    }

    try {
        if (forceReload || !_socialAccountsCache.length) {
            _socialAccountsCache = await api("/social/accounts");
        }

        container.hidden = false;
        container.innerHTML = selectedPlatforms.map((platform) => {
            const accounts = _socialAccountsCache.filter((account) => account.platform === platform);
            const platformName = socialPlatformName(platform);

            let selectedAccountId = _publishAccountSelection[platform] || "";
            if (!accounts.some((account) => String(account.id) === String(selectedAccountId))) {
                selectedAccountId = accounts[0] ? String(accounts[0].id) : "";
            }
            _publishAccountSelection[platform] = selectedAccountId;

            const options = accounts.length
                ? accounts.map((account) => {
                    const label = socialAccountDisplayName(account);
                    const usernameSuffix = account.platform_username && account.platform_username !== label
                        ? ` (${account.platform_username})`
                        : "";
                    const optionLabel = `${label}${usernameSuffix}`;
                    const selectedAttr = String(account.id) === String(selectedAccountId) ? "selected" : "";
                    return `<option value="${account.id}" ${selectedAttr}>${esc(optionLabel)}</option>`;
                }).join("")
                : "<option value=''>Conecte uma conta em Contas sociais</option>";

            const helpText = accounts.length
                ? "Escolha qual conta desta plataforma será usada na publicação."
                : "Nenhuma conta conectada para esta plataforma.";

            return `
                <div class="publish-account-row">
                    <div class="publish-account-label">
                        <strong>${esc(platformName)}</strong>
                        <span>Conta de destino</span>
                    </div>
                    <select id="pub-account-${platform}" data-platform="${platform}" class="input" aria-label="Conta ${esc(platformName)}">
                        ${options}
                    </select>
                    <div class="publish-account-help">${esc(helpText)}</div>
                </div>
            `;
        }).join("");

        container.querySelectorAll("select[data-platform]").forEach((select) => {
            select.addEventListener("change", () => {
                const platform = select.dataset.platform;
                _publishAccountSelection[platform] = select.value;
                loadPublishLinksForCurrentAccount();
            });
        });
        loadPublishLinksForCurrentAccount();
    } catch (error) {
        container.hidden = false;
        container.innerHTML = `<p class="loading">Erro ao carregar contas: ${esc(error.message)}</p>`;
    }
}

async function loadAccountsForSelect() {
    try {
        _socialAccountsCache = await api("/social/accounts");
        refreshScheduleAccountOptions();
    } catch (_) {
        // ignore modal preload errors
    }
}

function refreshScheduleAccountOptions() {
    const platformSelect = document.getElementById("ns-platform");
    const accountSelect = document.getElementById("ns-account");
    if (!platformSelect || !accountSelect) return;

    const platform = platformSelect.value;
    const previous = accountSelect.value;
    const filtered = (_socialAccountsCache || []).filter((account) => account.platform === platform);

    if (!filtered.length) {
        accountSelect.innerHTML = "<option value=''>Conecte uma conta desta plataforma</option>";
        return;
    }

    accountSelect.innerHTML = filtered.map((account) => {
        const label = socialAccountDisplayName(account);
        const selectedAttr = String(account.id) === String(previous) ? "selected" : "";
        return `<option value="${account.id}" ${selectedAttr}>${esc(label)}</option>`;
    }).join("");

    if (!filtered.some((account) => String(account.id) === String(accountSelect.value))) {
        accountSelect.value = String(filtered[0].id);
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
                <p>Conta: ${esc(schedule.account_label || "Conta conectada")}</p>
                <p>${esc(schedule.time_local || schedule.time_utc)}</p>
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
    const accountId = parseInt(document.getElementById("ns-account").value, 10);
    if (!accountId) {
        alert("Selecione uma conta social para o agendamento.");
        return;
    }
    try {
        await api("/schedule/", {
            method: "POST",
            body: JSON.stringify({
                platform: document.getElementById("ns-platform").value,
                social_account_id: accountId,
                frequency: document.getElementById("ns-frequency").value,
                time_local: document.getElementById("ns-time").value,
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
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

/* ═══════════════════════════════════════════════════════════
   Automation (auto-schedules) — CRUD + wizard
   ═══════════════════════════════════════════════════════════ */

let _autoWizardStep = 1;
let _autoWizardThemes = []; // temporary list while creating
let _autoTevoxiSongs = [];  // cached Tevoxi songs
let _autoSelectedSong = null; // selected Tevoxi song for shorts
let _autoShortsCount = 3;  // default shorts count
let _autoSubtitleCfg = null;

function _buildAutoSubtitleCfg(styleName = "destaque") {
    const st = _getSubStyle(styleName || "destaque");
    return {
        style_name: st.name,
        style_label: st.label,
        x: 50,
        y: 82,
        font_size: st.fontSize,
        font_color: st.fontColor,
        bg_color: st.bgColor || "",
        outline_color: st.outlineColor || "",
        font_family: st.fontFamily,
        bold: !!st.bold,
        italic: !!st.italic,
    };
}

function _resetAutoSubtitleCfg() {
    _autoSubtitleCfg = _buildAutoSubtitleCfg("destaque");
}

function _renderAutoSubtitleStyleGrid() {
    const grid = document.getElementById("auto-subtitle-style-grid");
    if (!grid) return;
    if (!_autoSubtitleCfg) _resetAutoSubtitleCfg();

    grid.innerHTML = SUBTITLE_STYLES.map(st => {
        const active = _autoSubtitleCfg.style_name === st.name;
        const previewStyle = [
            `font-family:${st.fontFamily}`,
            `color:${st.fontColor}`,
            `font-size:11px`,
            `font-weight:${st.bold ? "bold" : "normal"}`,
            `font-style:${st.italic ? "italic" : "normal"}`,
            st.bgColor ? `background:${st.bgColor};padding:2px 4px;border-radius:3px;` : "",
            st.outlineColor
                ? `text-shadow:-1px -1px 0 ${st.outlineColor},1px -1px 0 ${st.outlineColor},-1px 1px 0 ${st.outlineColor},1px 1px 0 ${st.outlineColor};`
                : "",
        ].join(";");
        return `
            <div class="editor-sub-style-card${active ? " active" : ""}" onclick="_autoPickSubtitleStyle('${st.name}')">
                <div class="editor-sub-style-preview" style="${previewStyle}">Abc</div>
                <span>${esc(st.label)}</span>
            </div>
        `;
    }).join("");
}

function _syncAutoSubtitleControls() {
    if (!_autoSubtitleCfg) _resetAutoSubtitleCfg();
    const yInput = document.getElementById("auto-subtitle-y");
    const sizeInput = document.getElementById("auto-subtitle-size");
    if (yInput) yInput.value = String(Math.round(_autoSubtitleCfg.y || 82));
    if (sizeInput) sizeInput.value = String(Math.round(_autoSubtitleCfg.font_size || 28));
    const yValue = document.getElementById("auto-subtitle-y-value");
    const sizeValue = document.getElementById("auto-subtitle-size-value");
    if (yValue) yValue.textContent = `${Math.round(_autoSubtitleCfg.y || 82)}%`;
    if (sizeValue) sizeValue.textContent = `${Math.round(_autoSubtitleCfg.font_size || 28)}px`;
}

function _renderAutoSubtitlePreview() {
    if (!_autoSubtitleCfg) _resetAutoSubtitleCfg();
    const caption = document.getElementById("auto-subtitle-preview-caption");
    if (!caption) return;

    const cfg = _autoSubtitleCfg;
    const fs = Math.max(8, Math.round(cfg.font_size || 28));
    caption.style.left = `${cfg.x || 50}%`;
    caption.style.top = `${cfg.y || 82}%`;
    caption.style.fontFamily = cfg.font_family || "Arial, sans-serif";
    caption.style.fontSize = `${fs}px`;
    caption.style.fontWeight = cfg.bold ? "700" : "400";
    caption.style.fontStyle = cfg.italic ? "italic" : "normal";
    caption.style.color = cfg.font_color || "#ffffff";
    caption.style.background = cfg.bg_color || "transparent";

    const hasBg = !!String(cfg.bg_color || "").trim();
    const padY = Math.max(2, Math.round(fs * 0.15));
    const padX = Math.max(4, Math.round(fs * 0.28));
    caption.style.padding = hasBg ? `${padY}px ${padX}px` : "0";
    caption.style.borderRadius = `${Math.max(4, Math.round(fs * 0.22))}px`;
    caption.style.letterSpacing = `${Math.max(0, Math.round(fs * 0.01 * 10) / 10)}px`;

    if (cfg.outline_color) {
        const o = Math.max(1, Math.round(fs * 0.06));
        caption.style.textShadow = `-${o}px -${o}px 0 ${cfg.outline_color}, ${o}px -${o}px 0 ${cfg.outline_color}, -${o}px ${o}px 0 ${cfg.outline_color}, ${o}px ${o}px 0 ${cfg.outline_color}`;
    } else {
        const blur = Math.max(3, Math.round(fs * 0.25));
        caption.style.textShadow = `0 ${Math.max(1, Math.round(fs * 0.08))}px ${blur}px rgba(0,0,0,0.75)`;
    }
}

function _updateAutoSubtitleSummary() {
    if (!_autoSubtitleCfg) _resetAutoSubtitleCfg();
    const summary = document.getElementById("auto-subtitle-setup-summary");
    if (!summary) return;
    const label = _autoSubtitleCfg.style_label || "Destaque";
    const y = Math.round(_autoSubtitleCfg.y || 82);
    const fs = Math.round(_autoSubtitleCfg.font_size || 28);
    summary.textContent = `${label} · Posicao ${y}% · ${fs}px`;
}

function _autoPickSubtitleStyle(styleName) {
    const st = _getSubStyle(styleName);
    const y = _autoSubtitleCfg?.y ?? 82;
    _autoSubtitleCfg = {
        style_name: st.name,
        style_label: st.label,
        x: 50,
        y,
        font_size: st.fontSize,
        font_color: st.fontColor,
        bg_color: st.bgColor || "",
        outline_color: st.outlineColor || "",
        font_family: st.fontFamily,
        bold: !!st.bold,
        italic: !!st.italic,
    };
    _renderAutoSubtitleStyleGrid();
    _syncAutoSubtitleControls();
    _renderAutoSubtitlePreview();
    _updateAutoSubtitleSummary();
}

function _autoSetSubtitleY(value, skipRender = false) {
    if (!_autoSubtitleCfg) _resetAutoSubtitleCfg();
    const y = Math.max(5, Math.min(95, parseInt(value, 10) || 82));
    _autoSubtitleCfg.y = y;
    const yValue = document.getElementById("auto-subtitle-y-value");
    if (yValue) yValue.textContent = `${y}%`;
    if (!skipRender) {
        const yInput = document.getElementById("auto-subtitle-y");
        if (yInput) yInput.value = String(y);
    }
    _renderAutoSubtitlePreview();
    _updateAutoSubtitleSummary();
}

function _autoSubtitleNudgeY(delta) {
    if (!_autoSubtitleCfg) _resetAutoSubtitleCfg();
    _autoSetSubtitleY((_autoSubtitleCfg.y || 82) + delta);
}

function _autoSetSubtitleFontSize(value, skipRender = false) {
    if (!_autoSubtitleCfg) _resetAutoSubtitleCfg();
    const fs = Math.max(8, Math.min(72, parseInt(value, 10) || 28));
    _autoSubtitleCfg.font_size = fs;
    const sizeValue = document.getElementById("auto-subtitle-size-value");
    if (sizeValue) sizeValue.textContent = `${fs}px`;
    if (!skipRender) {
        const sizeInput = document.getElementById("auto-subtitle-size");
        if (sizeInput) sizeInput.value = String(fs);
    }
    _renderAutoSubtitlePreview();
    _updateAutoSubtitleSummary();
}

function _autoSubtitleNudgeSize(delta) {
    if (!_autoSubtitleCfg) _resetAutoSubtitleCfg();
    _autoSetSubtitleFontSize((_autoSubtitleCfg.font_size || 28) + delta);
}

function toggleAutoSubtitleSetup(checked) {
    const setupRow = document.getElementById("auto-subtitle-setup-row");
    if (setupRow) setupRow.hidden = !checked;
    if (!checked) {
        closeAutoSubtitleModal();
        return;
    }
    if (!_autoSubtitleCfg) _resetAutoSubtitleCfg();
    _updateAutoSubtitleSummary();
    openAutoSubtitleModal();
}

function openAutoSubtitleModal() {
    const enabled = document.getElementById("auto-realistic-subtitles")?.checked || false;
    if (!enabled) return;
    if (!_autoSubtitleCfg) _resetAutoSubtitleCfg();
    _renderAutoSubtitleStyleGrid();
    _syncAutoSubtitleControls();
    _renderAutoSubtitlePreview();
    _updateAutoSubtitleSummary();
    openModal("modal-auto-subtitle");
}

function closeAutoSubtitleModal() {
    closeModal("modal-auto-subtitle");
}

function confirmAutoSubtitleModal() {
    _updateAutoSubtitleSummary();
    closeAutoSubtitleModal();
}

function _getAutoSubtitleSettingsForSchedule() {
    if (!_autoSubtitleCfg) _resetAutoSubtitleCfg();
    return {
        style_name: _autoSubtitleCfg.style_name,
        x: _autoSubtitleCfg.x,
        y: _autoSubtitleCfg.y,
        font_size: _autoSubtitleCfg.font_size,
        font_color: _autoSubtitleCfg.font_color,
        bg_color: _autoSubtitleCfg.bg_color,
        outline_color: _autoSubtitleCfg.outline_color,
        font_family: _autoSubtitleCfg.font_family,
        bold: _autoSubtitleCfg.bold,
        italic: _autoSubtitleCfg.italic,
        font_size_mode: "preview_px",
        preview_reference_width: 240,
    };
}

async function loadAutoSchedules() {
    const container = document.getElementById("auto-schedules-list");
    if (!container) return;
    try {
        const data = await api("/automation/schedules");
        if (!data.length) {
            container.innerHTML = "<p class='loading'>Nenhuma automação criada.</p>";
            return;
        }
        container.innerHTML = data.map(renderAutoCard).join("");
    } catch (error) {
        container.innerHTML = `<p class="loading">Erro: ${esc(error.message)}</p>`;
    }
}

function renderAutoCard(s) {
    const isTestAccount = !s.social_account_id;
    const typeBadge = s.video_type === "realistic" || s.video_type === "musical_shorts"
        ? '<span class="badge badge-shorts">Realista</span>'
        : '<span class="badge badge-completed">Imagens IA</span>';
    const modeBadge = s.creation_mode === "manual"
        ? '<span class="badge">Manual</span>'
        : '<span class="badge badge-queued">Auto</span>';
    const statusBadge = s.is_active
        ? '<span class="badge badge-completed">Ativo</span>'
        : '<span class="badge badge-failed">Pausado</span>';

    const themes = (s.themes || []);
    const pendingCount = themes.filter(t => t.status === "pending").length;
    const doneCount = themes.filter(t => t.status === "done" || t.status === "completed").length;

    const themeListHtml = themes.map(t => {
        let icon, statusClass, statusLabel;
        if (t.status === "done" || t.status === "completed") {
            icon = "✅"; statusClass = "theme-done"; statusLabel = isTestAccount ? "Concluído (teste)" : "Publicado";
        } else if (t.status === "processing") {
            icon = "⏳"; statusClass = "theme-processing"; statusLabel = "Criando...";
        } else if (t.status === "error" || t.status === "failed") {
            icon = "❌"; statusClass = "theme-failed"; statusLabel = "Falhou";
        } else {
            icon = "📅"; statusClass = "theme-pending"; statusLabel = "";
        }
        const dateLabel = t.scheduled_date ? `<span class="theme-date">${esc(t.scheduled_date)}</span>` : "";
        const statusBadge = statusLabel ? `<span class="theme-badge ${statusClass}">${statusLabel}</span>` : "";
        const errorBtn = (t.status === "error" || t.status === "failed") && t.error_message
            ? `<button class="theme-error-btn" data-error="${esc(t.error_message).replace(/"/g, '&quot;')}" onclick="showThemeError(this)" type="button" title="Ver motivo">Ver motivo</button>`
            : "";
        return `<li class="auto-theme-item ${statusClass}">
            <span class="theme-status">${icon}</span>
            <span class="theme-text">${esc(t.theme)}</span>
            ${dateLabel}
            ${statusBadge}
            ${errorBtn}
            <button class="theme-remove" onclick="deleteAutoTheme(${t.id}, ${s.id})" type="button" title="Remover">&times;</button>
        </li>`;
    }).join("");

    const freq = s.frequency === "weekly"
        ? `Semanal (${["Seg","Ter","Qua","Qui","Sex","Sab","Dom"][s.day_of_week || 0]})`
        : "Diario";

    const addThemeHtml = `
            <div class="auto-theme-add" style="margin-top:0.5rem">
                <input type="text" class="input" placeholder="Novo tema..." id="add-theme-input-${s.id}" maxlength="200">
                <button class="btn btn-primary btn-sm" type="button" onclick="addAutoThemeToSchedule(${s.id})">+</button>
            </div>`;

    return `<div class="auto-card" id="auto-card-${s.id}">
        <div class="auto-card-header">
            <h4>${esc(s.name || "Automação")}</h4>
            ${statusBadge}
        </div>
        <div class="auto-card-badges">${typeBadge} ${modeBadge}</div>
        <div class="auto-card-meta">
            <span>${freq} as ${esc(s.time_local || s.time_utc)}</span>
            <span>${pendingCount} pendentes / ${doneCount} feitos</span>
            <span>Conta: ${esc(s.account_label || (isTestAccount ? "Conta de teste (sem publicação)" : "Conta conectada"))}</span>
        </div>
        <div class="auto-card-detail">
            <strong>Temas:</strong>
            <ul class="auto-theme-list">${themeListHtml || "<li class='loading'>Sem temas</li>"}</ul>
            ${addThemeHtml}
        </div>
        <div class="auto-card-actions">
            <button class="btn btn-secondary btn-sm" onclick="toggleAutoSchedule(${s.id},${s.is_active?'false':'true'})" type="button">${s.is_active ? "Pausar" : "Ativar"}</button>
            <button class="btn btn-provider btn-sm" onclick="deleteAutoSchedule(${s.id})" type="button">Excluir</button>
        </div>
    </div>`;
}

async function toggleAutoSchedule(id, newState) {
    try {
        await api(`/automation/schedules/${id}`, {
            method: "PATCH",
            body: JSON.stringify({ is_active: newState }),
        });
        loadAutoSchedules();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    }
}

async function deleteAutoSchedule(id) {
    if (!window.confirm("Excluir esta automacao e todos os temas?")) return;
    try {
        await api(`/automation/schedules/${id}`, { method: "DELETE" });
        loadAutoSchedules();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    }
}

function showThemeError(btn) {
    const msg = btn.getAttribute("data-error") || "Erro desconhecido";
    alert(msg);
}

async function deleteAutoTheme(themeId, scheduleId) {
    try {
        await api(`/automation/themes/${themeId}`, { method: "DELETE" });
        loadAutoSchedules();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    }
}

async function addAutoThemeToSchedule(scheduleId) {
    const input = document.getElementById(`add-theme-input-${scheduleId}`);
    if (!input) return;
    const theme = input.value.trim();
    if (!theme) return;
    try {
        await api(`/automation/schedules/${scheduleId}/themes`, {
            method: "POST",
            body: JSON.stringify({ themes: [theme] }),
        });
        input.value = "";
        loadAutoSchedules();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    }
}

/* ── Automation Wizard (modal-new-automation) ── */

function openNewAutomationModal() {
    _autoWizardStep = 1;
    _autoWizardThemes = [];
    _autoSelectedSong = null;
    _autoShortsCount = 3;
    _clipAudioBuffer = null;
    _clipWaveformPeaks = [];
    _clipSongDuration = 0;
    _clipStart = 0;
    _clipDuration = 20;
    _clipDragging = null;
    _clipDragX = 0;
    _clipWaveformLoading = false;
    _clipPlaying = false;
    _clearClipAudioElementSource();

    // reset video type selection (use video-type-card class from new grid)
    document.querySelectorAll("#modal-new-automation .auto-video-type-grid .video-type-card").forEach(c => c.classList.remove("selected"));
    const imgCard = document.querySelector('#modal-new-automation [data-video-type="imagens_ia"]');
    if (imgCard) imgCard.classList.add("selected");

    // reset creation mode
    document.querySelectorAll("#modal-new-automation .auto-type-card").forEach(c => c.classList.remove("active"));
    const autoBtn = document.querySelector('#modal-new-automation [data-creation-mode="auto"]');
    if (autoBtn) autoBtn.classList.add("active");

    const manual = document.getElementById("auto-manual-settings");
    if (manual) manual.hidden = true;
    const realisticPanel = document.getElementById("auto-realistic-settings");
    if (realisticPanel) realisticPanel.hidden = true;
    const tevoxiPanel = document.getElementById("auto-tevoxi-panel");
    if (tevoxiPanel) tevoxiPanel.hidden = true;
    const tevoxiCb = document.getElementById("auto-realistic-tevoxi");
    if (tevoxiCb) tevoxiCb.checked = false;
    const subsCb = document.getElementById("auto-realistic-subtitles");
    if (subsCb) subsCb.checked = false;
    const subtitleRow = document.getElementById("auto-subtitle-setup-row");
    if (subtitleRow) subtitleRow.hidden = true;
    _resetAutoSubtitleCfg();
    _updateAutoSubtitleSummary();
    closeAutoSubtitleModal();

    // reset realistic style tags
    document.querySelectorAll("#auto-realistic-style-tags .style-tag").forEach(t => t.classList.remove("selected"));
    const defStyle = document.querySelector('#auto-realistic-style-tags [data-style="cinematic"]');
    if (defStyle) defStyle.classList.add("selected");
    document.querySelectorAll("#auto-realistic-persona-tags .style-tag").forEach(t => t.classList.remove("selected"));
    const defPersona = document.querySelector('#auto-realistic-persona-tags [data-persona="natureza"]');
    if (defPersona) defPersona.classList.add("selected");
    const autoMultiPersona = document.getElementById("auto-realistic-multi-persona");
    if (autoMultiPersona) autoMultiPersona.checked = false;
    _personaSelectionByContext.auto = {};
    _personaMultiSelectionByContext.auto = {};
    _refreshPersonaContext("auto", "natureza");

    // reset engine selection
    _setAutoRealisticEngine("wan2");

    // reset duration selection
    document.querySelectorAll("#auto-realistic-duration .duration-option").forEach(d => d.classList.remove("selected"));
    const defDur = document.querySelector('#auto-realistic-duration [data-value="7"]');
    if (defDur) defDur.classList.add("selected");

    document.getElementById("auto-theme-list").innerHTML = "";
    const themeInput = document.getElementById("auto-theme-input");
    if (themeInput) themeInput.value = "";

    // load social accounts for step 4
    loadAutoAccountOptions();

    // reset name/time
    const nameEl = document.getElementById("auto-name");
    if (nameEl) nameEl.value = "";
    const timeEl = document.getElementById("auto-time");
    if (timeEl) timeEl.value = "14:00";
    const freqEl = document.getElementById("auto-frequency");
    if (freqEl) freqEl.value = "daily";
    const dowGroup = document.getElementById("auto-dow-group");
    if (dowGroup) dowGroup.hidden = true;

    _applyAutoRealisticEngineRules();

    showAutoStep(1);
    openModal("modal-new-automation");
}

function showAutoStep(step) {
    _autoWizardStep = step;
    const totalSteps = 4; // Always 4 steps: type → mode → themes → schedule

    document.querySelectorAll("#modal-new-automation .auto-step").forEach(el => {
        el.classList.toggle("active", parseInt(el.dataset.autoStep) === step);
    });

    // Update dots dynamically
    const dotsContainer = document.querySelector("#modal-new-automation .automation-steps-dots");
    if (dotsContainer) {
        dotsContainer.innerHTML = "";
        for (let i = 1; i <= totalSteps; i++) {
            const dot = document.createElement("span");
            dot.className = "auto-dot" + (i <= step ? " active" : "");
            dot.dataset.autoStep = i;
            dotsContainer.appendChild(dot);
        }
    }

    // Adapt step 3 for Tevoxi clip mode vs text themes
    if (step === 3) {
        const isTevoxiClipMode = _isAutoTevoxiClipMode();
        const title = document.getElementById("auto-step3-title");
        const desc = document.getElementById("auto-step3-desc");
        const themeAddRow = document.getElementById("auto-theme-add-row");
        const clipAddRow = document.getElementById("auto-clip-add-row");
        if (isTevoxiClipMode) {
            if (title) title.textContent = "Trechos da música";
            if (desc) desc.textContent = "Selecione trechos da música para criar um short de cada trecho.";
            if (themeAddRow) themeAddRow.hidden = true;
            if (clipAddRow) clipAddRow.hidden = false;
        } else {
            if (title) title.textContent = "Temas da playlist";
            if (desc) desc.textContent = "Adicione os temas dos vídeos. O sistema criará um vídeo por agendamento na ordem da lista.";
            if (themeAddRow) themeAddRow.hidden = false;
            if (clipAddRow) clipAddRow.hidden = true;
        }
    }

    const btnBack = document.getElementById("auto-btn-back");
    const btnNext = document.getElementById("auto-btn-next");
    const btnCreate = document.getElementById("auto-btn-create");
    if (btnBack) btnBack.hidden = step === 1;
    if (btnNext) btnNext.hidden = step === totalSteps;
    if (btnCreate) btnCreate.hidden = step !== totalSteps;
}

function _isAutoTevoxiClipMode() {
    const vt = getSelectedAutoVideoType();
    const useTevoxi = document.getElementById("auto-realistic-tevoxi")?.checked || false;
    return vt === "realista" && useTevoxi && _autoSelectedSong;
}

function _isAutoTevoxiShortMode() {
    const vt = getSelectedAutoVideoType();
    const useTevoxi = document.getElementById("auto-realistic-tevoxi")?.checked || false;
    return vt === "realista" && useTevoxi;
}

function _setAutoRealisticEngine(engineValue) {
    const options = document.querySelectorAll("#auto-realistic-engine .engine-option");
    if (!options.length) return;

    let selected = null;
    options.forEach((o) => {
        const isSelected = o.dataset.value === engineValue;
        o.classList.toggle("selected", isSelected);
        if (isSelected) selected = o;
    });

    if (!selected) {
        selected = document.querySelector('#auto-realistic-engine [data-value="wan2"]');
        if (selected) selected.classList.add("selected");
    }

    const isGrok = selected?.dataset.value === "grok";
    document.querySelectorAll("#auto-realistic-duration .grok-only").forEach(btn => { btn.hidden = !isGrok; });
    if (!isGrok) {
        document.querySelectorAll("#auto-realistic-duration .duration-option.grok-only.selected").forEach(btn => {
            btn.classList.remove("selected");
            const def = document.querySelector('#auto-realistic-duration [data-value="7"]');
            if (def) def.classList.add("selected");
        });
    }
}

function _applyAutoRealisticEngineRules() {
    const engineGroup = document.getElementById("auto-realistic-engine-group");
    const forceGrok = _isAutoTevoxiShortMode();
    if (engineGroup) engineGroup.hidden = forceGrok;

    if (forceGrok) {
        _setAutoRealisticEngine("grok");
        return;
    }

    const selected = document.querySelector("#auto-realistic-engine .engine-option.selected");
    if (!selected) _setAutoRealisticEngine("wan2");
}

function autoStepNext() {
    const totalSteps = 4;

    // Validation for step 3 (themes/clips)
    if (_autoWizardStep === 3 && _autoWizardThemes.length === 0) {
        if (_isAutoTevoxiClipMode()) {
            alert("Clique em '+ Adicionar trecho' para selecionar trechos da música.");
        } else {
            alert("Digite o tema e aperte no botão + para adicionar.");
            const addBtn = document.getElementById("auto-add-theme-btn");
            if (addBtn) { addBtn.classList.add("btn-error-pulse"); setTimeout(() => addBtn.classList.remove("btn-error-pulse"), 2000); }
        }
        return;
    }
    if (_autoWizardStep < totalSteps) showAutoStep(_autoWizardStep + 1);
}

function autoStepBack() {
    if (_autoWizardStep > 1) showAutoStep(_autoWizardStep - 1);
}

// Event delegation for realistic settings in automation modal
document.addEventListener("DOMContentLoaded", () => {
    const autoModal = document.getElementById("modal-new-automation");
    if (!autoModal) return;
    autoModal.addEventListener("click", (e) => {
        // Style tag click
        const tag = e.target.closest("#auto-realistic-style-tags .style-tag");
        if (tag) {
            document.querySelectorAll("#auto-realistic-style-tags .style-tag").forEach(t => t.classList.remove("selected"));
            tag.classList.add("selected");
        }
        const persona = e.target.closest("#auto-realistic-persona-tags .style-tag");
        if (persona) {
            document.querySelectorAll("#auto-realistic-persona-tags .style-tag").forEach(t => t.classList.remove("selected"));
            persona.classList.add("selected");
            _refreshPersonaContext("auto", persona.dataset.persona || "natureza");
        }
        // Engine option click
        const eng = e.target.closest("#auto-realistic-engine .engine-option");
        if (eng) {
            if (_isAutoTevoxiShortMode()) {
                _setAutoRealisticEngine("grok");
            } else {
                _setAutoRealisticEngine(eng.dataset.value || "wan2");
            }
        }
        // Duration option click
        const dur = e.target.closest("#auto-realistic-duration .duration-option");
        if (dur) {
            document.querySelectorAll("#auto-realistic-duration .duration-option").forEach(o => o.classList.remove("selected"));
            dur.classList.add("selected");
        }
    });
});

/* ── Tevoxi Song Selection (for Realistic + Tevoxi music) ── */

function toggleScriptTevoxiSongs() {
    const checked = document.getElementById("script-realistic-tevoxi")?.checked;
    const panel = document.getElementById("script-tevoxi-panel");
    if (panel) panel.hidden = !checked;
    if (checked) _loadScriptTevoxiSongsIfNeeded();
    if (checked) {
        const musicCb = document.getElementById("script-realistic-music");
        if (musicCb) musicCb.checked = false;
    }
    _updateScriptTevoxiSelectionUI();
}

function toggleWizardTevoxiSongs() {
    const checked = document.getElementById("wizard-realistic-tevoxi")?.checked;
    const panel = document.getElementById("wizard-tevoxi-panel");
    if (panel) panel.hidden = !checked;
    if (checked) _loadWizardTevoxiSongsIfNeeded();
    if (checked) {
        const musicCb = document.getElementById("wizard-realistic-music");
        if (musicCb) musicCb.checked = false;
    }
    _updateWizardTevoxiSelectionUI();
}

function _formatTevoxiClipLabel(song, clip) {
    if (!song || !clip) return "";
    const songTitle = String(song.title || "Música").trim() || "Música";
    const clipDuration = Number(clip.clip_duration || 0);
    if (clipDuration <= 0) {
        return `🎵 ${songTitle} · música inteira`;
    }
    const clipStart = Math.max(0, Number(clip.clip_start || 0));
    const clipEnd = clipStart + clipDuration;
    return `🎵 ${songTitle} · ${_formatDuration(clipStart)} - ${_formatDuration(clipEnd)}`;
}

function _extractLyricsExcerptForClip(song, clipStart, clipDuration, totalDuration) {
    const raw = String(song?.lyrics || "").replace(/\s+/g, " ").trim();
    if (!raw) return "";

    const total = Number(totalDuration || song?.duration || 0);
    if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(clipDuration) || clipDuration <= 0 || clipDuration >= (total - 0.2)) {
        return raw.slice(0, 1200);
    }

    const startRatio = Math.max(0, Math.min(1, Number(clipStart || 0) / total));
    const endRatio = Math.max(startRatio, Math.min(1, (Number(clipStart || 0) + Number(clipDuration || 0)) / total));
    let from = Math.max(0, Math.floor(raw.length * startRatio) - 140);
    let to = Math.min(raw.length, Math.ceil(raw.length * endRatio) + 140);

    if (to <= from) {
        from = 0;
        to = Math.min(raw.length, 700);
    }

    const excerpt = raw.slice(from, to).trim();
    return excerpt || raw.slice(0, 700);
}

function _buildTevoxiSelectionPayload(song, clipStart, clipDuration, totalDuration) {
    const total = Math.max(1, Number(totalDuration || song?.duration || 120));
    let normalizedStart = Math.max(0, Math.round(Number(clipStart || 0) * 10) / 10);
    let normalizedDuration = Math.max(0, Math.round(Number(clipDuration || 0) * 10) / 10);
    const isFull = normalizedDuration <= 0 || normalizedDuration >= (total - 0.2);
    if (isFull) {
        normalizedStart = 0;
        normalizedDuration = 0;
    }

    return {
        clip_start: normalizedStart,
        clip_duration: normalizedDuration,
        song_duration: Math.round(total * 10) / 10,
        lyrics_excerpt: _extractLyricsExcerptForClip(song, normalizedStart, isFull ? total : normalizedDuration, total),
    };
}

function _buildTevoxiPromptContext(song, clip) {
    if (!song) return "";
    const songTitle = String(song.title || "Música").trim() || "Música";
    const clipDuration = Number(clip?.clip_duration || 0);
    const clipStart = Math.max(0, Number(clip?.clip_start || 0));
    const clipInfo = clipDuration > 0
        ? `Use como referência principal o trecho entre ${_formatDuration(clipStart)} e ${_formatDuration(clipStart + clipDuration)} da música \"${songTitle}\".`
        : `Use como referência principal a música inteira \"${songTitle}\".`;
    const excerpt = String(clip?.lyrics_excerpt || "").trim();
    if (!excerpt) {
        return clipInfo;
    }
    return `${clipInfo} Letra de referência: ${excerpt}`;
}

function _buildScriptTevoxiPrompt(song, clip) {
    if (!song) return "";

    const songTitle = String(song.title || "Música").trim() || "Música";
    const clipDuration = Number(clip?.clip_duration || 0);
    const clipStart = Math.max(0, Number(clip?.clip_start || 0));
    const excerpt = String(clip?.lyrics_excerpt || song?.lyrics || "").trim();

    const lines = [
        `Crie um roteiro de vídeo inspirado na música \"${songTitle}\".`,
        clipDuration > 0
            ? `Use como base principal o trecho entre ${_formatDuration(clipStart)} e ${_formatDuration(clipStart + clipDuration)}.`
            : "Use como base principal a música inteira.",
        "Mantenha o ritmo, a emoção e a mensagem desse trecho na narrativa visual.",
    ];

    if (excerpt) {
        lines.push("", "Trecho transcrito da música:", excerpt);
    } else {
        lines.push("", "Transcrição do trecho indisponível. Baseie-se no título e no clima da música.");
    }

    return lines.join("\n").slice(0, 20000);
}

function _buildTevoxiAiTopicSeed(song, clip) {
    if (!song) return "";
    const baseLabel = _formatTevoxiClipLabel(song, clip).replace(/^🎵\s*/, "").trim();
    const excerpt = String(clip?.lyrics_excerpt || "").trim();
    if (excerpt) {
        return `${baseLabel}. Analise este trecho e sugira um roteiro visual coerente com a letra: ${excerpt.slice(0, 260)}`;
    }
    return `${baseLabel}. Sugira um roteiro visual coerente com o ritmo e a emoção da música.`;
}

function _updateScriptTevoxiSelectionUI() {
    const summaryEl = document.getElementById("script-tevoxi-selection");
    if (!summaryEl) return;
    const enabled = document.getElementById("script-realistic-tevoxi")?.checked || false;
    if (!enabled || !_scriptSelectedSong || !_scriptSelectedClip) {
        summaryEl.hidden = true;
        summaryEl.textContent = "";
        return;
    }
    summaryEl.hidden = false;
    summaryEl.textContent = _formatTevoxiClipLabel(_scriptSelectedSong, _scriptSelectedClip);
}

function _updateWizardTevoxiSelectionUI() {
    const summaryEl = document.getElementById("wizard-tevoxi-selection");
    if (!summaryEl) return;
    const enabled = document.getElementById("wizard-realistic-tevoxi")?.checked || false;
    if (!enabled || !_wizardSelectedSong || !_wizardSelectedClip) {
        summaryEl.hidden = true;
        summaryEl.textContent = "";
        return;
    }
    summaryEl.hidden = false;
    summaryEl.textContent = _formatTevoxiClipLabel(_wizardSelectedSong, _wizardSelectedClip);
}

async function _loadScriptTevoxiSongsIfNeeded() {
    const list = document.getElementById("script-song-list");
    if (!list) return;
    if (_scriptTevoxiSongs.length > 0) {
        _renderScriptTevoxiSongs();
        return;
    }
    list.innerHTML = '<p class="loading">Carregando músicas do Tevoxi...</p>';
    try {
        _scriptTevoxiSongs = await api("/automation/tevoxi-songs");
        _renderScriptTevoxiSongs();
    } catch (e) {
        list.innerHTML = `<p class="loading">Erro: ${esc(e.message)}</p>`;
    }
}

function _renderScriptTevoxiSongs() {
    const list = document.getElementById("script-song-list");
    if (!list) return;
    if (!_scriptTevoxiSongs.length) {
        list.innerHTML = '<p class="loading">Nenhuma música encontrada no Tevoxi.</p>';
        return;
    }
    list.innerHTML = _scriptTevoxiSongs.map((s, i) => {
        const dur = Number(s.duration) > 0 ? _formatDuration(Number(s.duration)) : "";
        const genres = (Array.isArray(s.genres) ? s.genres : [])
            .map(g => String(g || "").trim())
            .filter(Boolean)
            .join(", ");
        const meta = [genres, dur].filter(Boolean).join(" · ");
        const selected = _scriptSelectedSong && _scriptSelectedSong.job_id === s.job_id;
        return `<button class="auto-song-item${selected ? ' active' : ''}" type="button" onclick="selectScriptTevoxiSong(${i})">
            <div class="song-info">
                <strong>${esc(s.title || 'Sem título')}</strong>
                <span class="muted">${esc(meta || 'Sem detalhes')}</span>
            </div>
            <span class="song-check">${selected ? '✓' : ''}</span>
        </button>`;
    }).join("");
}

function selectScriptTevoxiSong(index) {
    _scriptSelectedSong = _scriptTevoxiSongs[index] || null;
    _scriptSelectedClip = null;
    _renderScriptTevoxiSongs();
    _updateScriptTevoxiSelectionUI();
    if (_scriptSelectedSong) {
        openClipSelector("script", _scriptSelectedSong);
    }
}

async function _loadWizardTevoxiSongsIfNeeded() {
    const list = document.getElementById("wizard-song-list");
    if (!list) return;
    if (_wizardTevoxiSongs.length > 0) {
        _renderWizardTevoxiSongs();
        return;
    }
    list.innerHTML = '<p class="loading">Carregando músicas do Tevoxi...</p>';
    try {
        _wizardTevoxiSongs = await api("/automation/tevoxi-songs");
        _renderWizardTevoxiSongs();
    } catch (e) {
        list.innerHTML = `<p class="loading">Erro: ${esc(e.message)}</p>`;
    }
}

function _renderWizardTevoxiSongs() {
    const list = document.getElementById("wizard-song-list");
    if (!list) return;
    if (!_wizardTevoxiSongs.length) {
        list.innerHTML = '<p class="loading">Nenhuma música encontrada no Tevoxi.</p>';
        return;
    }
    list.innerHTML = _wizardTevoxiSongs.map((s, i) => {
        const dur = Number(s.duration) > 0 ? _formatDuration(Number(s.duration)) : "";
        const genres = (Array.isArray(s.genres) ? s.genres : [])
            .map(g => String(g || "").trim())
            .filter(Boolean)
            .join(", ");
        const meta = [genres, dur].filter(Boolean).join(" · ");
        const selected = _wizardSelectedSong && _wizardSelectedSong.job_id === s.job_id;
        return `<button class="auto-song-item${selected ? ' active' : ''}" type="button" onclick="selectWizardTevoxiSong(${i})">
            <div class="song-info">
                <strong>${esc(s.title || 'Sem título')}</strong>
                <span class="muted">${esc(meta || 'Sem detalhes')}</span>
            </div>
            <span class="song-check">${selected ? '✓' : ''}</span>
        </button>`;
    }).join("");
}

function selectWizardTevoxiSong(index) {
    _wizardSelectedSong = _wizardTevoxiSongs[index] || null;
    _wizardSelectedClip = null;
    _renderWizardTevoxiSongs();
    _updateWizardTevoxiSelectionUI();
    if (_wizardSelectedSong) {
        openClipSelector("wizard", _wizardSelectedSong);
    }
}

function toggleAutoTevoxiSongs() {
    const checked = document.getElementById("auto-realistic-tevoxi")?.checked;
    const panel = document.getElementById("auto-tevoxi-panel");
    if (panel) panel.hidden = !checked;
    if (checked) _loadTevoxiSongsIfNeeded();
    // When Tevoxi is checked, uncheck generic music
    if (checked) {
        const musicCb = document.getElementById("auto-realistic-music");
        if (musicCb) musicCb.checked = false;
    }

    _applyAutoRealisticEngineRules();
}

async function _loadTevoxiSongsIfNeeded() {
    const list = document.getElementById("auto-song-list");
    if (!list) return;
    if (_autoTevoxiSongs.length > 0) {
        _renderTevoxiSongs();
        return;
    }
    list.innerHTML = '<p class="loading">Carregando músicas do Tevoxi...</p>';
    try {
        _autoTevoxiSongs = await api("/automation/tevoxi-songs");
        if (!_autoTevoxiSongs.length) {
            list.innerHTML = '<p class="loading">Nenhuma música encontrada no Tevoxi.</p>';
            return;
        }
        _renderTevoxiSongs();
    } catch (e) {
        list.innerHTML = `<p class="loading">Erro: ${esc(e.message)}</p>`;
    }
}

function _renderTevoxiSongs() {
    const list = document.getElementById("auto-song-list");
    if (!list) return;
    list.innerHTML = _autoTevoxiSongs.map((s, i) => {
        const dur = Number(s.duration) > 0 ? _formatDuration(Number(s.duration)) : "";
        const genres = (Array.isArray(s.genres) ? s.genres : [])
            .map(g => String(g || "").trim())
            .filter(Boolean)
            .join(", ");
        const meta = [genres, dur].filter(Boolean).join(" · ");
        const selected = _autoSelectedSong && _autoSelectedSong.job_id === s.job_id;
        return `<button class="auto-song-item${selected ? ' active' : ''}" type="button" onclick="selectTevoxiSong(${i})">
            <div class="song-info">
                <strong>${esc(s.title || 'Sem título')}</strong>
                <span class="muted">${esc(meta || 'Sem detalhes')}</span>
            </div>
            <span class="song-check">${selected ? '✓' : ''}</span>
        </button>`;
    }).join("");
}

function _formatDuration(seconds) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

function selectTevoxiSong(index) {
    _autoSelectedSong = _autoTevoxiSongs[index] || null;
    _renderTevoxiSongs();
}

/* ══════════════════════════════════════════
   Clip Selector for Tevoxi Songs
   ══════════════════════════════════════════ */
let _clipAudioBuffer = null;
let _clipWaveformPeaks = [];
let _clipSongDuration = 0;
let _clipStart = 0;
let _clipDuration = 20;
let _clipDragging = null;
let _clipDragX = 0;
let _clipPreviewCtx = null;
let _clipPreviewSource = null;
let _clipPreviewRaf = null;
let _clipPlaying = false;
let _clipWaveformLoading = false;
let _clipAudioObjectUrl = "";
let _clipSelectorContext = "auto";
let _clipSelectedSong = null;

function _getClipAudioUrl(song) {
    if (!song) return "";
    if (song.job_id) {
        return `${API}/automation/tevoxi-audio/${encodeURIComponent(song.job_id)}`;
    }
    return song.audio_url || "";
}

function _clearClipAudioElementSource() {
    const audio = document.getElementById("clip-audio");
    if (audio) {
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
    }
    if (_clipAudioObjectUrl) {
        URL.revokeObjectURL(_clipAudioObjectUrl);
        _clipAudioObjectUrl = "";
    }
}

function _setClipPlayButton(isPlaying) {
    const btn = document.getElementById("clip-play-btn");
    if (!btn) return;
    if (isPlaying) {
        btn.style.paddingLeft = "0";
        btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>';
    } else {
        btn.style.paddingLeft = "2px";
        btn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>';
    }
}

function _getClipSegmentBounds() {
    const audio = document.getElementById("clip-audio");
    let total = Number(_clipSongDuration || 0);
    if ((!Number.isFinite(total) || total <= 0) && audio && Number.isFinite(audio.duration) && audio.duration > 0) {
        total = Number(audio.duration);
    }
    if (!Number.isFinite(total) || total <= 0) {
        total = 1;
    }

    const start = Math.max(0, Math.min(Number(_clipStart || 0), total));
    let duration = Number(_clipDuration || 0);
    if (!Number.isFinite(duration) || duration <= 0) {
        duration = total - start;
    }
    duration = Math.max(0.05, Math.min(duration, Math.max(0.05, total - start)));
    const end = Math.min(total, start + duration);
    return { start, end, duration, total };
}

function _ensureClipPlaybackInSelection(forceRestart = false) {
    if (!_clipPlaying) return;
    const audio = document.getElementById("clip-audio");
    if (!audio) return;

    const seg = _getClipSegmentBounds();
    const now = Number(audio.currentTime || 0);
    const shouldRestart = forceRestart || now < seg.start || now >= seg.end;

    if (shouldRestart) {
        try {
            audio.currentTime = seg.start;
        } catch (_) {
            // Ignore seek failures; next tick retries.
        }
    }

    if (audio.paused && _clipPlaying) {
        const p = audio.play();
        if (p && typeof p.then === "function") {
            p.catch(() => _stopClipPreview());
        }
    }

    _updateClipPlayhead(Number(audio.currentTime || seg.start));
}

function _tickClipPreviewLoop() {
    if (!_clipPlaying) return;

    const audio = document.getElementById("clip-audio");
    if (!audio) {
        _stopClipPreview();
        return;
    }

    const seg = _getClipSegmentBounds();
    let t = Number(audio.currentTime || seg.start);

    // Keep playback looping strictly inside the selected segment.
    if (t < seg.start || t >= seg.end) {
        try {
            audio.currentTime = seg.start;
            t = seg.start;
        } catch (_) {
            // Ignore and keep current value until next frame.
        }
        if (audio.paused && _clipPlaying) {
            const p = audio.play();
            if (p && typeof p.then === "function") {
                p.catch(() => _stopClipPreview());
            }
        }
    }

    _updateClipPlayhead(t);
    _clipPreviewRaf = requestAnimationFrame(_tickClipPreviewLoop);
}

function _stopClipPreview() {
    if (_clipPreviewRaf) {
        cancelAnimationFrame(_clipPreviewRaf);
        _clipPreviewRaf = null;
    }
    const audio = document.getElementById("clip-audio");
    if (audio) {
        audio.pause();
        audio.onended = null;
    }
    _clipPlaying = false;
    _setClipPlayButton(false);
    const playhead = document.getElementById("clip-waveform-playhead");
    if (playhead) playhead.style.display = "none";
}

function _resolveClipSong(context, songOverride = null) {
    if (songOverride) return songOverride;
    if (context === "script") return _scriptSelectedSong;
    if (context === "wizard") return _wizardSelectedSong;
    return _autoSelectedSong;
}

function _getStoredClipSelection(context) {
    if (context === "script") return _scriptSelectedClip;
    if (context === "wizard") return _wizardSelectedClip;
    return null;
}

function _setClipApplyButtonLabel(context) {
    const btn = document.getElementById("clip-apply-btn");
    if (!btn) return;
    btn.textContent = context === "auto" ? "Adicionar trecho" : "Usar no vídeo";
}

function openClipSelector(context = "auto", songOverride = null) {
    const normalizedContext = ["auto", "script", "wizard"].includes(context) ? context : "auto";
    const song = _resolveClipSong(normalizedContext, songOverride);
    if (!song) {
        alert("Selecione uma música primeiro.");
        return;
    }

    _clipSelectorContext = normalizedContext;
    _clipSelectedSong = song;

    _clipSongDuration = Math.max(1, Number(song.duration || 120));
    const stored = _getStoredClipSelection(normalizedContext);
    if (stored) {
        _clipStart = Math.max(0, Number(stored.clip_start || 0));
        const storedDuration = Number(stored.clip_duration || 0);
        _clipDuration = storedDuration > 0 ? storedDuration : _clipSongDuration;
    } else {
        _clipStart = 0;
        _clipDuration = Math.min(20, _clipSongDuration || 20);
    }
    if (_clipDuration > _clipSongDuration) {
        _clipDuration = _clipSongDuration;
    }
    if (_clipStart + _clipDuration > _clipSongDuration) {
        _clipStart = Math.max(0, _clipSongDuration - _clipDuration);
    }

    _clipPlaying = false;
    _clipAudioBuffer = null;
    _clipWaveformPeaks = [];
    _clipDragging = null;
    _clipDragX = 0;

    document.getElementById("clip-song-title").textContent = song.title || "Música";
    _updateClipDurationButtons();
    _setClipApplyButtonLabel(normalizedContext);
    _setClipPlayButton(false);
    openModal("modal-clip-selector");

    // Reset duration selection
    const quickDuration = (_clipDuration >= (_clipSongDuration - 0.2)) ? 0 : Math.round(_clipDuration);
    document.querySelectorAll("#clip-duration-options .duration-option").forEach(b => {
        b.classList.toggle("selected", parseInt(b.dataset.value, 10) === quickDuration);
    });

    const audioUrl = _getClipAudioUrl(song);
    _clearClipAudioElementSource();

    requestAnimationFrame(() => {
        _syncClipCanvasSize();
        _drawClipLoadingPlaceholder();
        _updateClipSelection();
        _loadClipWaveform(song, audioUrl);
    });
}

function closeClipSelector() {
    _stopClipPreview();
    const audio = document.getElementById("clip-audio");
    if (audio) audio.currentTime = 0;
    _clearClipAudioElementSource();
    _clipSelectedSong = null;
    _clipSelectorContext = "auto";
    closeModal("modal-clip-selector");
}

function _syncClipCanvasSize() {
    const canvas = document.getElementById("clip-waveform-canvas");
    if (!canvas) return;
    const container = document.getElementById("clip-waveform-container");
    const width = container ? container.clientWidth : 300;
    canvas.width = width;
    canvas.height = 48;
    canvas.style.width = `${width}px`;
    canvas.style.height = "48px";
}

function _drawClipLoadingPlaceholder() {
    const canvas = document.getElementById("clip-waveform-canvas");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    _syncClipCanvasSize();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "rgba(255,255,255,0.08)";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "rgba(255,255,255,0.25)";
    ctx.font = "13px sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Carregando áudio...", canvas.width / 2, 28);
}

async function _loadClipWaveform(song, audioUrl) {
    if (_clipWaveformLoading || !audioUrl) {
        if (!audioUrl) {
            _drawClipFallbackPeaks();
            _updateClipSelection();
        }
        return;
    }
    _clipWaveformLoading = true;

    _clipAudioBuffer = null;
    _clipWaveformPeaks = [];

    try {
        const authHeaders = token ? { Authorization: `Bearer ${token}` } : {};
        const resp = await fetch(audioUrl, {
            method: "GET",
            headers: authHeaders,
            cache: "no-store",
            credentials: "same-origin",
        });
        if (resp.status === 401) {
            clearSession();
            showAuth("Sua sessao expirou. Entre novamente.");
            throw new Error("Unauthorized");
        }
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const arrayBuf = await resp.arrayBuffer();
        const contentType = resp.headers.get("content-type") || "audio/mpeg";
        const audioBlob = new Blob([arrayBuf.slice(0)], { type: contentType });
        if (_clipAudioObjectUrl) {
            URL.revokeObjectURL(_clipAudioObjectUrl);
        }
        _clipAudioObjectUrl = URL.createObjectURL(audioBlob);
        const audio = document.getElementById("clip-audio");
        if (audio) {
            audio.pause();
            audio.currentTime = 0;
            audio.src = _clipAudioObjectUrl;
            audio.load();
        }

        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const decoded = await audioCtx.decodeAudioData(arrayBuf);

        _clipAudioBuffer = decoded;
        _clipSongDuration = decoded.duration || _clipSongDuration;

        if (_clipDuration > _clipSongDuration) {
            _clipDuration = _clipSongDuration;
        }
        if (_clipStart + _clipDuration > _clipSongDuration) {
            _clipStart = Math.max(0, _clipSongDuration - _clipDuration);
        }

        _extractClipPeaksAndDraw();
        _updateClipDurationButtons();
        _updateClipSelection();

        try { await audioCtx.close(); } catch (_) {}
    } catch (e) {
        console.warn("[Clip] waveform load failed:", e);
        _clipAudioBuffer = null;
        const audio = document.getElementById("clip-audio");
        if (audio && song?.audio_url) {
            audio.src = song.audio_url;
            audio.load();
        }
        if (!Number.isFinite(_clipSongDuration) || _clipSongDuration <= 0) {
            _clipSongDuration = Math.max(1, Number(song?.duration || 120));
        }
        _drawClipFallbackPeaks();
        _updateClipDurationButtons();
        _updateClipSelection();
    } finally {
        _clipWaveformLoading = false;
    }
}

function _extractClipPeaksAndDraw() {
    const canvas = document.getElementById("clip-waveform-canvas");
    if (!canvas || !_clipAudioBuffer) return;

    _syncClipCanvasSize();
    const channelData = _clipAudioBuffer.getChannelData(0);
    const numBars = Math.floor(canvas.width / 2);
    if (numBars <= 0) return;

    const samplesPerBar = Math.floor(channelData.length / numBars);
    if (samplesPerBar <= 0) return;

    _clipWaveformPeaks = [];
    let maxPeak = 0;
    for (let i = 0; i < numBars; i++) {
        let peak = 0;
        const start = i * samplesPerBar;
        for (let j = start; j < start + samplesPerBar && j < channelData.length; j++) {
            const value = Math.abs(channelData[j]);
            if (value > peak) peak = value;
        }
        _clipWaveformPeaks.push(peak);
        if (peak > maxPeak) maxPeak = peak;
    }

    if (maxPeak > 0) {
        _clipWaveformPeaks = _clipWaveformPeaks.map(p => Math.pow(p / maxPeak, 0.6));
    }

    _drawClipWaveform();
}

function _drawClipFallbackPeaks() {
    const canvas = document.getElementById("clip-waveform-canvas");
    if (!canvas) return;
    _syncClipCanvasSize();

    const numBars = Math.floor(canvas.width / 2);
    const seedSong = _clipSelectedSong || _resolveClipSong(_clipSelectorContext);
    const seedSource = String((seedSong && (seedSong.job_id || seedSong.title)) || "clip");
    let seed = 0;
    for (let i = 0; i < seedSource.length; i++) {
        seed += seedSource.charCodeAt(i) * (i + 1);
    }

    _clipWaveformPeaks = [];
    for (let i = 0; i < numBars; i++) {
        const base = Math.sin((i + 1 + seed) * 0.13) * Math.cos((i + seed) * 0.047);
        const value = 0.22 + Math.abs(base) * 0.72;
        _clipWaveformPeaks.push(value);
    }

    _drawClipWaveform();
}

function _drawClipWaveform() {
    const canvas = document.getElementById("clip-waveform-canvas");
    if (!canvas || !_clipWaveformPeaks.length) return;

    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    const numBars = _clipWaveformPeaks.length;
    const gap = 1;
    const barW = Math.max(1, (w / numBars) - gap);
    const step = barW + gap;

    ctx.fillStyle = "rgba(255,255,255,0.35)";
    for (let i = 0; i < numBars; i++) {
        const x = i * step;
        const barH = Math.max(2, _clipWaveformPeaks[i] * (h - 4));
        const y = (h - barH) / 2;
        ctx.fillRect(x, y, barW, barH);
    }
}

function _drawClipWaveformWithSelection() {
    const canvas = document.getElementById("clip-waveform-canvas");
    if (!canvas || !_clipWaveformPeaks.length || !_clipSongDuration) return;

    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    const numBars = _clipWaveformPeaks.length;
    const gap = 1;
    const barW = Math.max(1, (w / numBars) - gap);
    const step = barW + gap;

    ctx.fillStyle = "rgba(255,255,255,0.20)";
    for (let i = 0; i < numBars; i++) {
        const x = i * step;
        const barH = Math.max(2, _clipWaveformPeaks[i] * (h - 4));
        const y = (h - barH) / 2;
        ctx.fillRect(x, y, barW, barH);
    }

    if (_clipDuration > 0 && _clipDuration < _clipSongDuration) {
        const selX = (_clipStart / _clipSongDuration) * w;
        const selW = (_clipDuration / _clipSongDuration) * w;
        ctx.save();
        ctx.beginPath();
        ctx.rect(selX, 0, selW, h);
        ctx.clip();
        ctx.fillStyle = "rgba(224,160,48,0.95)";
        for (let i = 0; i < numBars; i++) {
            const x = i * step;
            const barH = Math.max(2, _clipWaveformPeaks[i] * (h - 4));
            const y = (h - barH) / 2;
            ctx.fillRect(x, y, barW, barH);
        }
        ctx.restore();
    }
}

function _updateClipDurationButtons() {
    document.querySelectorAll("#clip-duration-options .duration-option").forEach(b => {
        const val = parseInt(b.dataset.value, 10);
        if (val > 0 && val > _clipSongDuration) {
            b.hidden = true;
        } else {
            b.hidden = false;
        }
    });
}

function _selectClipDuration(val) {
    if (!_clipSongDuration) return;
    _clipDuration = val === 0 ? _clipSongDuration : val;
    _clipDuration = Math.min(_clipDuration, _clipSongDuration);
    if (_clipStart + _clipDuration > _clipSongDuration) {
        _clipStart = Math.max(0, _clipSongDuration - _clipDuration);
    }
    document.querySelectorAll("#clip-duration-options .duration-option").forEach(b => {
        b.classList.toggle("selected", parseInt(b.dataset.value, 10) === val);
    });
    _updateClipSelection();
}

function _updateClipDragHint() {
    const hint = document.getElementById("clip-waveform-drag-hint");
    const wrap = document.getElementById("clip-waveform-wrap");
    const container = document.getElementById("clip-waveform-container");
    if (!hint) return;

    const canDrag = _clipDuration > 0 && _clipSongDuration > 0 && _clipDuration < _clipSongDuration && wrap && container;
    if (!canDrag) {
        hint.style.display = "none";
        return;
    }

    const cw = container.clientWidth || 0;
    if (cw <= 0) {
        hint.style.display = "none";
        return;
    }

    const centerRatio = (_clipStart + (_clipDuration / 2)) / _clipSongDuration;
    const centerPx = Math.max(0, Math.min(cw, centerRatio * cw));
    const leftPx = (container.offsetLeft || 0) + centerPx;
    const topPx = (container.offsetTop || 0) + container.offsetHeight + 4;

    hint.style.left = `${leftPx}px`;
    hint.style.top = `${topPx}px`;
    hint.style.display = "flex";
}

function _updateClipSelection(restartPlayback = true) {
    const canvas = document.getElementById("clip-waveform-canvas");
    const container = document.getElementById("clip-waveform-container");
    const selection = document.getElementById("clip-waveform-selection");
    const label = document.getElementById("clip-time-label");
    if (!canvas || !container || !selection || !_clipSongDuration) return;

    const cw = container.clientWidth || 300;
    if (canvas.width !== cw) {
        canvas.width = cw;
        canvas.style.width = `${cw}px`;
    }

    if (_clipDuration <= 0 || _clipDuration >= _clipSongDuration) {
        selection.style.display = "none";
        if (label) label.textContent = `0:00 - ${_formatDuration(_clipSongDuration)}`;
        _drawClipWaveform();
        _updateClipDragHint();
        if (_clipPlaying && restartPlayback) {
            _ensureClipPlaybackInSelection(true);
        }
        return;
    }

    selection.style.display = "";
    const maxStart = Math.max(0, _clipSongDuration - _clipDuration);
    _clipStart = Math.max(0, Math.min(_clipStart, maxStart));

    const leftPx = (_clipStart / _clipSongDuration) * cw;
    const widthPx = (_clipDuration / _clipSongDuration) * cw;
    selection.style.left = `${leftPx}px`;
    selection.style.width = `${widthPx}px`;

    if (label) {
        const end = _clipStart + _clipDuration;
        label.textContent = `${_formatDuration(_clipStart)} - ${_formatDuration(end)}`;
    }

    _drawClipWaveformWithSelection();
    _updateClipDragHint();

    if (_clipPlaying && restartPlayback) {
        _ensureClipPlaybackInSelection(true);
    }
}

function _updateClipPlayhead(currentTime) {
    const playhead = document.getElementById("clip-waveform-playhead");
    const container = document.getElementById("clip-waveform-container");
    if (!playhead || !container || !_clipSongDuration) return;

    const cw = container.clientWidth || 300;
    const px = (currentTime / _clipSongDuration) * cw;
    playhead.style.left = `${Math.max(0, Math.min(cw, px))}px`;
    playhead.style.display = "block";
}

function _initClipWaveformDrag() {
    const container = document.getElementById("clip-waveform-container");
    const selection = document.getElementById("clip-waveform-selection");
    const dragHint = document.getElementById("clip-waveform-drag-hint");
    if (!container) return;

    function getTimeFromX(clientX) {
        const rect = container.getBoundingClientRect();
        const x = Math.max(0, Math.min(clientX - rect.left, rect.width));
        return (x / rect.width) * _clipSongDuration;
    }

    function onStart(e) {
        if (!_clipSongDuration) return;
        if (_clipDuration <= 0 || _clipDuration >= _clipSongDuration) return;
        e.preventDefault();

        const clientX = e.touches ? e.touches[0].clientX : e.clientX;
        const target = e.target;

        if (target && target.id === "clip-handle-left") {
            _clipDragging = "left";
        } else if (target && target.id === "clip-handle-right") {
            _clipDragging = "right";
        } else {
            _clipDragging = "region";
        }
        _clipDragX = clientX;
    }

    function onMove(e) {
        if (!_clipDragging || !_clipSongDuration) return;
        e.preventDefault();

        const clientX = e.touches ? e.touches[0].clientX : e.clientX;
        const clipDur = _clipDuration || 0;
        if (clipDur <= 0) return;

        const rect = container.getBoundingClientRect();
        const deltaX = clientX - _clipDragX;
        const deltaSec = (deltaX / rect.width) * _clipSongDuration;

        if (_clipDragging === "region") {
            const maxStart = Math.max(0, _clipSongDuration - clipDur);
            _clipStart = Math.max(0, Math.min(_clipStart + deltaSec, maxStart));
        } else if (_clipDragging === "left") {
            const currentEnd = _clipStart + clipDur;
            const newStart = Math.max(0, _clipStart + deltaSec);
            const newDur = currentEnd - newStart;
            if (newDur >= 5) {
                _clipStart = newStart;
                _clipDuration = Math.round(newDur);
                document.querySelectorAll("#clip-duration-options .duration-option").forEach(b => b.classList.remove("selected"));
            }
        } else if (_clipDragging === "right") {
            const newDur = clipDur + deltaSec;
            const maxDur = _clipSongDuration - _clipStart;
            if (newDur >= 5 && newDur <= maxDur) {
                _clipDuration = Math.round(newDur);
                document.querySelectorAll("#clip-duration-options .duration-option").forEach(b => b.classList.remove("selected"));
            }
        }

        _clipDragX = clientX;
        _updateClipSelection();
    }

    function onEnd() {
        _clipDragging = null;
    }

    if (selection) {
        selection.addEventListener("mousedown", onStart);
        selection.addEventListener("touchstart", onStart, { passive: false });
    }
    if (dragHint) {
        dragHint.addEventListener("mousedown", onStart);
        dragHint.addEventListener("touchstart", onStart, { passive: false });
    }

    container.addEventListener("mousedown", (e) => {
        if (e.target === container || e.target.tagName === "CANVAS") {
            if (_clipDuration <= 0 || !_clipSongDuration) return;
            const time = getTimeFromX(e.clientX);
            const maxStart = Math.max(0, _clipSongDuration - _clipDuration);
            _clipStart = Math.max(0, Math.min(time - _clipDuration / 2, maxStart));
            _updateClipSelection();
        }
    });

    container.addEventListener("touchstart", (e) => {
        if (e.target === container || e.target.tagName === "CANVAS") {
            if (_clipDuration <= 0 || !_clipSongDuration) return;
            const time = getTimeFromX(e.touches[0].clientX);
            const maxStart = Math.max(0, _clipSongDuration - _clipDuration);
            _clipStart = Math.max(0, Math.min(time - _clipDuration / 2, maxStart));
            _updateClipSelection();
        }
    }, { passive: true });

    document.addEventListener("mousemove", onMove);
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("mouseup", onEnd);
    document.addEventListener("touchend", onEnd);
}

function toggleClipPreview() {
    if (_clipPlaying) {
        _stopClipPreview();
        return;
    }

    const audio = document.getElementById("clip-audio");
    if (!audio || !audio.src) {
        alert("Não foi possível carregar o áudio para preview.");
        return;
    }

    const seg = _getClipSegmentBounds();
    if (seg.duration <= 0) return;

    _clipPlaying = true;
    _setClipPlayButton(true);
    _updateClipPlayhead(seg.start);

    audio.pause();
    try {
        audio.currentTime = seg.start;
    } catch (_) {
        // keep going; some browsers update currentTime only after play starts
    }

    audio.onended = () => {
        if (!_clipPlaying) return;
        _ensureClipPlaybackInSelection(true);
    };

    const playPromise = audio.play();
    if (playPromise && typeof playPromise.then === "function") {
        playPromise
            .then(() => {
                _clipPreviewRaf = requestAnimationFrame(_tickClipPreviewLoop);
            })
            .catch(() => {
                _stopClipPreview();
                alert("Não foi possível reproduzir este trecho.");
            });
    } else {
        _clipPreviewRaf = requestAnimationFrame(_tickClipPreviewLoop);
    }
}

function addClipToThemes() {
    const song = _clipSelectedSong || _resolveClipSong(_clipSelectorContext);
    if (!song) return;

    const payload = _buildTevoxiSelectionPayload(song, _clipStart, _clipDuration, _clipSongDuration);

    if (_clipSelectorContext === "script") {
        _scriptSelectedSong = song;
        _scriptSelectedClip = payload;

        const scriptPrompt = _buildScriptTevoxiPrompt(song, payload);
        const scriptTextEl = document.getElementById("script-text");
        if (scriptTextEl) {
            scriptTextEl.value = scriptPrompt;
        }
        scriptData.text = scriptPrompt;
        const scriptCountEl = document.getElementById("script-char-count");
        if (scriptCountEl) {
            scriptCountEl.textContent = scriptPrompt.length.toLocaleString("pt-BR");
        }

        _renderScriptTevoxiSongs();
        _updateScriptTevoxiSelectionUI();
        closeClipSelector();
        return;
    }

    if (_clipSelectorContext === "wizard") {
        _wizardSelectedSong = song;
        _wizardSelectedClip = payload;
        if (!String(wizardData.topic || "").trim()) {
            const autoTopic = _buildTevoxiAiTopicSeed(song, payload);
            wizardData.topic = autoTopic;
            const wizardTopicEl = document.getElementById("wizard-topic");
            if (wizardTopicEl && !wizardTopicEl.value.trim()) {
                wizardTopicEl.value = autoTopic;
            }
        }
        _renderWizardTevoxiSongs();
        _updateWizardTevoxiSelectionUI();
        closeClipSelector();
        return;
    }

    const end = payload.clip_duration > 0
        ? (payload.clip_start + payload.clip_duration)
        : Number(song.duration || _clipSongDuration || 0);
    const label = payload.clip_duration > 0
        ? `🎵 ${song.title} (${_formatDuration(payload.clip_start)} - ${_formatDuration(end)})`
        : `🎵 ${song.title} (música inteira)`;
    const autoClipDuration = payload.clip_duration > 0
        ? payload.clip_duration
        : Math.max(1, Number(song.duration || payload.song_duration || 120));
    const selectedPersona = document.querySelector("#auto-realistic-persona-tags .style-tag.selected");
    const interactionPersona = _normalizeRealisticPersonaType(selectedPersona ? selectedPersona.dataset.persona : "natureza");
    const personaProfileId = _getSelectedPersonaProfileId("auto", interactionPersona);

    // Store as object with clip metadata
    _autoWizardThemes.push({
        text: label,
        custom_settings: {
            tevoxi_job_id: song.job_id,
            tevoxi_title: song.title,
            tevoxi_audio_url: song.audio_url,
            tevoxi_lyrics: song.lyrics || "",
            tevoxi_duration: song.duration || 120,
            clip_start: payload.clip_start,
            clip_duration: autoClipDuration,
            interaction_persona: interactionPersona,
            persona_profile_id: personaProfileId,
        },
    });
    renderAutoWizardThemes();
    closeClipSelector();
}

// Event listeners for clip selector
document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("clip-duration-options")?.addEventListener("click", e => {
        const btn = e.target.closest(".duration-option");
        if (btn) _selectClipDuration(parseInt(btn.dataset.value, 10));
    });
    _initClipWaveformDrag();

    window.addEventListener("resize", () => {
        const modal = document.getElementById("modal-clip-selector");
        if (modal && modal.classList.contains("open")) {
            _syncClipCanvasSize();
            _updateClipSelection(false);
        }
    });
});

function selectAutoVideoType(type) {
    document.querySelectorAll('#modal-new-automation .auto-video-type-grid .video-type-card').forEach(c => {
        c.classList.toggle("selected", c.dataset.videoType === type);
    });
    // update manual settings visibility based on current mode
    updateAutoManualPanels();
}

function selectAutoCreationMode(mode) {
    document.querySelectorAll('#modal-new-automation [data-auto-step="2"] .auto-type-card').forEach(c => {
        c.classList.toggle("active", c.dataset.creationMode === mode);
    });
    updateAutoManualPanels();
}

function updateAutoManualPanels() {
    const videoType = getSelectedAutoVideoType();
    const mode = getSelectedAutoCreationMode();
    const narrationPanel = document.getElementById("auto-manual-settings");
    const realisticPanel = document.getElementById("auto-realistic-settings");
    if (narrationPanel) narrationPanel.hidden = !(mode === "manual" && videoType === "imagens_ia");
    if (realisticPanel) realisticPanel.hidden = !(mode === "manual" && videoType === "realista");
    _applyAutoRealisticEngineRules();
}

function toggleAutoMusicLyrics() {
    const mode = document.getElementById("auto-music-mode")?.value;
    const lyricsGroup = document.getElementById("auto-music-lyrics-group");
    const vocalistGroup = document.getElementById("auto-music-vocalist-group");
    if (lyricsGroup) lyricsGroup.hidden = mode !== "lyrics";
    if (vocalistGroup) vocalistGroup.hidden = mode === "instrumental";
}

function getSelectedAutoVideoType() {
    const activeCard = document.querySelector('#modal-new-automation .auto-video-type-grid .video-type-card.selected');
    return activeCard ? activeCard.dataset.videoType : "imagens_ia";
}

function getSelectedAutoCreationMode() {
    const activeCard = document.querySelector('#modal-new-automation [data-auto-step="2"] .auto-type-card.active');
    return activeCard ? activeCard.dataset.creationMode : "auto";
}

function addAutoTheme() {
    const input = document.getElementById("auto-theme-input");
    const text = (input?.value || "").trim();
    if (!text) return;
    _autoWizardThemes.push(text);
    input.value = "";
    renderAutoWizardThemes();
    input.focus();
}

function removeAutoWizardTheme(index) {
    _autoWizardThemes.splice(index, 1);
    renderAutoWizardThemes();
}

function renderAutoWizardThemes() {
    const ul = document.getElementById("auto-theme-list");
    if (!ul) return;
    ul.innerHTML = _autoWizardThemes.map((t, i) => {
        const label = typeof t === "string" ? t : (t.text || t.theme || "");
        return `
        <li class="auto-theme-item">
            <span class="theme-status">${i + 1}.</span>
            <span class="theme-text">${esc(label)}</span>
            <button class="theme-remove" onclick="removeAutoWizardTheme(${i})" type="button">&times;</button>
        </li>`;
    }).join("");
}

async function loadAutoAccountOptions() {
    const select = document.getElementById("auto-account");
    if (!select) return;
    try {
        const accounts = await api("/social/accounts");
        const platform = document.getElementById("auto-platform")?.value || "youtube";
        const filtered = accounts.filter(a => a.platform === platform);
        const currentValue = (select.value || "").trim();
        const accountOptions = filtered.map(a => {
            const label = socialAccountDisplayName(a);
            return `<option value="${a.id}">${esc(label)}</option>`;
        });
        accountOptions.push("<option value='__test__'>Conta de teste (gera vídeo e não publica)</option>");

        select.innerHTML = accountOptions.join("");

        const hasCurrent = Array.from(select.options).some(o => o.value === currentValue);
        if (hasCurrent) {
            select.value = currentValue;
        } else if (filtered.length > 0) {
            select.value = String(filtered[0].id);
        } else {
            select.value = "__test__";
        }
    } catch {
        select.innerHTML = "<option value='__test__'>Conta de teste (gera vídeo e não publica)</option>";
        select.value = "__test__";
    }
}

async function createAutoSchedule() {
    const name = (document.getElementById("auto-name")?.value || "").trim();
    if (!name) {
        alert("Informe um nome para a automacao.");
        return;
    }

    const videoType = getSelectedAutoVideoType();

    if (_autoWizardThemes.length === 0) {
        alert("Digite o tema e aperte no botão + para adicionar.");
        showAutoStep(3);
        const addBtn = document.getElementById("auto-add-theme-btn");
        if (addBtn) { addBtn.classList.add("btn-error-pulse"); setTimeout(() => addBtn.classList.remove("btn-error-pulse"), 2000); }
        return;
    }

    const accountRaw = (document.getElementById("auto-account")?.value || "").trim();
    let accountId = null;
    if (accountRaw === "__test__") {
        accountId = null;
    } else {
        const parsed = parseInt(accountRaw || "0", 10);
        if (!parsed) {
            alert("Selecione uma conta social ou a Conta de teste.");
            return;
        }
        accountId = parsed;
    }

    const creationMode = getSelectedAutoCreationMode();
    const platform = document.getElementById("auto-platform")?.value || "youtube";
    const frequency = document.getElementById("auto-frequency")?.value || "daily";
    const timeUtc = document.getElementById("auto-time")?.value || "14:00";
    const userTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const dayOfWeek = frequency === "weekly" ? parseInt(document.getElementById("auto-dow")?.value || "0", 10) : null;

    let defaultSettings = null;
    let finalVideoType = videoType === "imagens_ia" ? "narration" : "realistic";

    // Prepare themes: normalize to objects {text, custom_settings?}
    let themes = _autoWizardThemes.map(t => {
        if (typeof t === "string") return { text: t };
        return t; // already { text, custom_settings }
    });

    if (videoType === "imagens_ia" && creationMode === "manual") {
        defaultSettings = {
            tone: document.getElementById("auto-tone")?.value || "informativo",
            voice: document.getElementById("auto-voice")?.value || "onyx",
            style: document.getElementById("auto-style")?.value || "cinematic, vibrant colors, dynamic lighting",
            duration: parseInt(document.getElementById("auto-duration")?.value || "120", 10),
            aspect_ratio: document.getElementById("auto-aspect")?.value || "16:9",
        };
    } else if (videoType === "realista") {
        // Collect realistic settings
        const selectedStyle = document.querySelector("#auto-realistic-style-tags .style-tag.selected");
        const selectedPersona = document.querySelector("#auto-realistic-persona-tags .style-tag.selected");
        const interactionPersona = _normalizeRealisticPersonaType(selectedPersona ? selectedPersona.dataset.persona : "natureza");
        let personaProfileId = 0;
        let personaProfileIds = [];
        try {
            personaProfileIds = await _ensurePersonaSelections("auto", interactionPersona);
            personaProfileId = personaProfileIds[0] || 0;
        } catch (error) {
            alert(`Erro ao carregar persona: ${error.message}`);
            return;
        }
        if (!personaProfileIds.length) {
            alert("Crie uma ou mais personas de interação antes de salvar a automação realista.");
            return;
        }
        const selectedEngine = document.querySelector("#auto-realistic-engine .engine-option.selected");
        const selectedDur = document.querySelector("#auto-realistic-duration .duration-option.selected");
        const useTevoxi = document.getElementById("auto-realistic-tevoxi")?.checked || false;
        const useMusic = document.getElementById("auto-realistic-music")?.checked || false;
        const enableSubs = document.getElementById("auto-realistic-subtitles")?.checked || false;

        defaultSettings = {
            realistic_style: selectedStyle ? selectedStyle.dataset.style : "cinematic",
            interaction_persona: interactionPersona,
            persona_profile_id: personaProfileId,
            persona_profile_ids: personaProfileIds,
            engine: useTevoxi ? "grok" : (selectedEngine ? selectedEngine.dataset.value : "wan2"),
            duration: selectedDur ? parseInt(selectedDur.dataset.value) : 7,
            aspect_ratio: document.getElementById("auto-realistic-aspect")?.value || "9:16",
            add_music: useMusic && !useTevoxi,
            use_tevoxi: useTevoxi,
            enable_subtitles: enableSubs,
        };

        if (enableSubs) {
            defaultSettings.subtitle_settings = _getAutoSubtitleSettingsForSchedule();
        }

        // For clip mode, tevoxi data is per-theme (in custom_settings).
        // For non-clip mode, put song-level defaults.
        if (useTevoxi && _autoSelectedSong && !themes.some(t => t.custom_settings?.clip_start !== undefined)) {
            defaultSettings.tevoxi_job_id = _autoSelectedSong.job_id;
            defaultSettings.tevoxi_title = _autoSelectedSong.title;
            defaultSettings.tevoxi_audio_url = _autoSelectedSong.audio_url;
            defaultSettings.tevoxi_lyrics = _autoSelectedSong.lyrics || "";
            defaultSettings.tevoxi_duration = _autoSelectedSong.duration || 120;
        }
    }

    const btn = document.getElementById("auto-btn-create");
    if (btn) { btn.disabled = true; btn.textContent = "Criando..."; }

    try {
        await api("/automation/schedules", {
            method: "POST",
            body: JSON.stringify({
                name,
                video_type: finalVideoType,
                creation_mode: creationMode,
                platform,
                social_account_id: accountId,
                frequency,
                time_local: timeUtc,
                timezone: userTimezone,
                day_of_week: dayOfWeek,
                default_settings: defaultSettings,
                themes,
            }),
        });
        closeModal("modal-new-automation");
        loadAutoSchedules();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Ativar Automação"; }
    }
}

async function connectPlatform(platform) {
    const normalized = String(platform || "").toLowerCase();
    if (!["youtube", "tiktok", "instagram"].includes(normalized)) {
        alert("Plataforma invalida.");
        return;
    }

    _pendingConnectPlatform = normalized;
    const platformEl = document.getElementById("connect-account-platform");
    if (platformEl) {
        platformEl.textContent = `Plataforma: ${socialPlatformName(normalized)}`;
    }

    const input = document.getElementById("connect-account-label");
    if (input) {
        input.value = "";
    }

    // Show/hide TikTok credentials fields
    const tiktokKeys = document.getElementById("connect-tiktok-keys");
    if (tiktokKeys) {
        tiktokKeys.hidden = normalized !== "tiktok";
    }
    const keyInput = document.getElementById("connect-tiktok-client-key");
    const secretInput = document.getElementById("connect-tiktok-client-secret");
    if (keyInput) keyInput.value = "";
    if (secretInput) secretInput.value = "";

    const confirmBtn = document.getElementById("connect-account-confirm-btn");
    if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = "Continuar";
    }

    openModal("modal-connect-account");
    if (input) {
        window.setTimeout(() => input.focus(), 0);
    }
}

async function confirmConnectPlatform() {
    if (!_pendingConnectPlatform) {
        alert("Selecione uma plataforma para conectar.");
        return;
    }

    const input = document.getElementById("connect-account-label");
    const accountLabel = (input?.value || "").trim();
    if (!accountLabel) {
        alert("Informe um nome para identificar esta conta.");
        if (input) input.focus();
        return;
    }

    // For TikTok, require client_key and client_secret
    let tiktokClientKey = "";
    let tiktokClientSecret = "";
    if (_pendingConnectPlatform === "tiktok") {
        tiktokClientKey = (document.getElementById("connect-tiktok-client-key")?.value || "").trim();
        tiktokClientSecret = (document.getElementById("connect-tiktok-client-secret")?.value || "").trim();
        if (!tiktokClientKey || !tiktokClientSecret) {
            alert("Informe o Client Key e Client Secret do TikTok.");
            return;
        }
    }

    const confirmBtn = document.getElementById("connect-account-confirm-btn");
    if (confirmBtn) {
        confirmBtn.disabled = true;
        confirmBtn.textContent = "Conectando...";
    }

    try {
        const query = new URLSearchParams({ account_label: accountLabel });
        if (tiktokClientKey) query.set("client_key", tiktokClientKey);
        if (tiktokClientSecret) query.set("client_secret", tiktokClientSecret);
        const data = await api(`/social/connect/${_pendingConnectPlatform}?${query.toString()}`);
        if (!data.auth_url) {
            throw new Error("A plataforma não retornou URL de autorização");
        }
        window.location.href = data.auth_url;
    } catch (error) {
        alert(formatSocialConnectError(error.message, _pendingConnectPlatform));
    } finally {
        if (confirmBtn) {
            confirmBtn.disabled = false;
            confirmBtn.textContent = "Continuar";
        }
    }
}

function formatSocialConnectError(rawMessage, platform) {
    const message = String(rawMessage || "Erro desconhecido");
    const lower = message.toLowerCase();
    if (platform === "instagram" && (lower.includes("facebook_app_id") || lower.includes("facebook app_id") || lower.includes("instagram oauth não configurado"))) {
        return [
            "Erro ao conectar Instagram: faltam configurações no servidor.",
            "",
            "Como resolver:",
            "1. Criar/abrir um app no Meta for Developers",
            "2. Habilitar Facebook Login e permissões do Instagram",
            "3. Definir Redirect URI: https://criavideo.pro/api/social/callback/instagram",
            "4. Configurar no servidor (.env): FACEBOOK_APP_ID e FACEBOOK_APP_SECRET",
            "5. Executar deploy e tentar conectar novamente",
        ].join("\n");
    }
    return `Erro ao conectar conta: ${message}`;
}

function socialPlatformName(platform) {
    const key = String(platform || "").toLowerCase();
    if (key === "youtube") return "YouTube";
    if (key === "tiktok") return "TikTok";
    if (key === "instagram") return "Instagram";
    return key ? `${key.charAt(0).toUpperCase()}${key.slice(1)}` : "Conta social";
}

function socialPlatformIcon(platform) {
    const key = String(platform || "").toLowerCase();
    if (key === "youtube") {
        return '<svg viewBox="0 0 24 24" fill="none"><rect x="2.5" y="5.5" width="19" height="13" rx="4" stroke="currentColor" stroke-width="1.9"></rect><path d="M10 9.2L15.8 12L10 14.8V9.2Z" fill="currentColor"></path></svg>';
    }
    if (key === "tiktok") {
        return '<svg viewBox="0 0 24 24" fill="none"><path d="M14.4 5.2C15 6.9 16.4 8.1 18.2 8.4V11.1C16.9 11 15.7 10.6 14.7 10V14.8C14.7 18.1 12.2 20.5 9 20.5C5.8 20.5 3.3 18.1 3.3 14.8C3.3 11.6 5.8 9.1 9 9.1C9.4 9.1 9.9 9.2 10.3 9.3V12.1C9.9 11.9 9.5 11.8 9 11.8C7.3 11.8 6 13.1 6 14.8C6 16.6 7.3 17.8 9 17.8C10.7 17.8 12 16.6 12 14.8V3.5H14.4V5.2Z" fill="currentColor"></path></svg>';
    }
    if (key === "instagram") {
        return '<svg viewBox="0 0 24 24" fill="none"><rect x="3.5" y="3.5" width="17" height="17" rx="5.5" stroke="currentColor" stroke-width="1.9"></rect><circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="1.9"></circle><circle cx="17.2" cy="6.8" r="1.2" fill="currentColor"></circle></svg>';
    }
    return '<svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.9"></circle><path d="M8 12h8M12 8v8" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"></path></svg>';
}

async function loadAccounts() {
    const container = document.getElementById("accounts-list");
    try {
        const accounts = await api("/social/accounts");
        _socialAccountsCache = accounts;
        if (!accounts.length) {
            container.innerHTML = "<p class='loading'>Nenhuma conta conectada.</p>";
            renderPublishAccountSelectors(true);
            refreshScheduleAccountOptions();
            return;
        }
        container.innerHTML = accounts.map((account) => {
            const platform = String(account.platform || "").toLowerCase();
            const platformName = socialPlatformName(platform);
            const platformClass = `social-platform-${platform.replace(/[^a-z0-9_-]/g, "")}`;
            const accountLabel = socialAccountDisplayName(account);
            const usernameSuffix = account.platform_username && account.platform_username !== accountLabel
                ? ` · ${account.platform_username}`
                : "";
            return `
            <div class="card social-account-card ${platformClass}">
                <div class="social-account-head">
                    <span class="social-account-icon" aria-hidden="true">${socialPlatformIcon(platform)}</span>
                    <div class="social-account-meta">
                        <h4 class="social-account-platform">${esc(accountLabel)}</h4>
                        <p class="social-account-user">${esc(platformName)}${esc(usernameSuffix)}</p>
                    </div>
                </div>
                <div class="card-actions social-account-actions">
                    <span class="social-account-status">Conectada</span>
                    <div class="social-account-buttons">
                        <button class="btn btn-secondary btn-sm" onclick="openEditSocialAccountModal(${account.id})" type="button">Editar nome</button>
                        <button class="btn btn-provider btn-sm" onclick="disconnectAccount(${account.id})" type="button">Desconectar</button>
                    </div>
                </div>
            </div>
            `;
        }).join("");
        renderPublishAccountSelectors(true);
        refreshScheduleAccountOptions();
    } catch (error) {
        container.innerHTML = `<p class="loading">Erro: ${esc(error.message)}</p>`;
    }
}

function openEditSocialAccountModal(accountId) {
    const account = (_socialAccountsCache || []).find((item) => item.id === accountId);
    if (!account) {
        alert("Conta não encontrada.");
        return;
    }

    _editingSocialAccountId = account.id;
    const platformEl = document.getElementById("edit-account-platform");
    if (platformEl) {
        platformEl.textContent = `Plataforma: ${socialPlatformName(account.platform || "")}`;
    }

    const input = document.getElementById("edit-account-label");
    if (input) {
        input.value = socialAccountDisplayName(account);
    }

    const saveBtn = document.getElementById("edit-account-save-btn");
    if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = "Salvar";
    }

    openModal("modal-edit-account");
    if (input) {
        window.setTimeout(() => {
            input.focus();
            input.select();
        }, 0);
    }
}

async function saveSocialAccountLabel() {
    if (!_editingSocialAccountId) {
        alert("Nenhuma conta selecionada.");
        return;
    }

    const input = document.getElementById("edit-account-label");
    const accountLabel = (input?.value || "").trim();
    if (!accountLabel) {
        alert("Digite um nome para a conta.");
        if (input) input.focus();
        return;
    }

    const saveBtn = document.getElementById("edit-account-save-btn");
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = "Salvando...";
    }

    try {
        const updated = await api(`/social/accounts/${_editingSocialAccountId}`, {
            method: "PATCH",
            body: JSON.stringify({ account_label: accountLabel }),
        });

        const cached = (_socialAccountsCache || []).find((item) => item.id === _editingSocialAccountId);
        if (cached) {
            cached.account_label = updated?.account_label || accountLabel;
        }

        closeModal("modal-edit-account");
        await loadAccounts();
        await renderPublishAccountSelectors(true);
        refreshScheduleAccountOptions();
    } catch (error) {
        alert(`Erro ao editar nome: ${error.message}`);
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = "Salvar";
        }
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
    // Credit check
    const estMinutes = Math.max(1, Math.ceil((songData.duration || 60) / 60));
    const creditsNeeded = estMinutes * _creditsPerMinute;
    if (_userCredits < creditsNeeded) {
        showCreditsPurchaseModal();
        return;
    }
    const container = document.getElementById("projects-list");
    container.innerHTML = `
        <div class="card" style="text-align:center;">
            <h3>Preparando seu video...</h3>
            <p>${esc(songData.song_title || "Sua música")}</p>
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
        updateCreditsDisplay();
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
                rendering: "Renderizando vídeo final...",
                completed: "Vídeo pronto.",
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

function toggleCollapsible(btn) {
    const expanded = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-expanded", String(!expanded));
    const body = btn.nextElementSibling;
    if (body) body.hidden = expanded;
}
window.toggleCollapsible = toggleCollapsible;

window.closeModal = closeModal;
window.createProject = createProjectFromLibrary;
window.generateVideo = generateVideo;
window.deleteProject = deleteProject;
window.watchVideo = watchVideo;
window.openPublishForProject = openPublishForProject;
window.createSimilar = createSimilar;
window.openRenameProjectModal = openRenameProjectModal;
window.saveProjectTitle = saveProjectEdit;
window.openCopyChoiceModal = openCopyChoiceModal;
window.chooseCopyScript = chooseCopyScript;
window.chooseCopyFormat = chooseCopyFormat;
window.openCopyFormatModal = openCopyFormatModal;
window.createFormatCopy = createFormatCopy;
window.createSchedule = createSchedule;
window.toggleSchedule = toggleSchedule;
window.deleteSchedule = deleteSchedule;
window.connectPlatform = connectPlatform;
window.confirmConnectPlatform = confirmConnectPlatform;
window.confirmSchedulePublish = confirmSchedulePublish;
window.openEditSocialAccountModal = openEditSocialAccountModal;
window.saveSocialAccountLabel = saveSocialAccountLabel;
window.disconnectAccount = disconnectAccount;
window.openPublishDraftFromList = openPublishDraftFromList;
window.overwritePublishDraftFromList = overwritePublishDraftFromList;
window.deletePublishDraftFromList = deletePublishDraftFromList;
window.loadProjects = loadProjects;

// ── Style Tags System ──
function initStyleTags() {
    document.querySelectorAll(".style-tag").forEach((tag) => {
        tag.addEventListener("click", () => {
            const container = tag.closest(".style-tags");
            if (!container) return;
            container.querySelectorAll(".style-tag").forEach(t => t.classList.remove("selected"));
            tag.classList.add("selected");
        });
    });
}

function initPauseOptions() {
    document.querySelectorAll(".pause-option").forEach((btn) => {
        btn.addEventListener("click", () => {
            const container = btn.closest(".pause-options");
            container.querySelectorAll(".pause-option").forEach(b => b.classList.remove("selected"));
            btn.classList.add("selected");
        });
    });
}

function getSelectedPause(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return "normal";
    const sel = container.querySelector(".pause-option.selected");
    return sel ? sel.dataset.value : "normal";
}

function getSelectedStyles(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return "";
    const selected = Array.from(container.querySelectorAll(".style-tag.selected"))
        .map(t => t.dataset.value)
        .filter(v => v !== "ia_escolhe");
    return selected.join(", ");
}

function setSelectedStyles(containerId, styleStr) {
    const container = document.getElementById(containerId);
    if (!container) return;
    const values = styleStr.toLowerCase().split(/[,\s]+/).map(s => s.trim()).filter(Boolean);
    container.querySelectorAll(".style-tag").forEach(tag => {
        tag.classList.toggle("selected", values.includes(tag.dataset.value));
    });
}

// ── Voice Preview System ──
let _voicePreviewAudio = null;

function initVoicePreview() {
    document.querySelectorAll(".voice-preview-btn").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const voiceId = btn.dataset.voice;
            // If already playing this voice, stop it
            if (_voicePreviewAudio && btn.classList.contains("playing")) {
                _voicePreviewAudio.pause();
                _voicePreviewAudio = null;
                btn.classList.remove("playing");
                return;
            }
            // Stop any other playing preview
            if (_voicePreviewAudio) {
                _voicePreviewAudio.pause();
                document.querySelectorAll(".voice-preview-btn.playing").forEach(b => b.classList.remove("playing"));
            }
            btn.classList.add("playing");
            _voicePreviewAudio = new Audio(`/api/video/voice-demo/${voiceId}`);
            _voicePreviewAudio.play().catch(() => {});
            _voicePreviewAudio.onended = () => {
                btn.classList.remove("playing");
                _voicePreviewAudio = null;
            };
        });
    });
}

// ── Voice Profile System (Levita-style) ──

let voiceProfiles = [];
let personaMediaRecorder = null;
let personaRecordedChunks = [];
let personaRecordingTimer = null;
let personaSampleBlobs = {}; // keyed by prefix: 'wizard' or 'script'

async function loadVoiceProfiles() {
    try {
        voiceProfiles = await api("/voice/profiles");
    } catch {
        voiceProfiles = [];
    }
    renderPersonaList("wizard");
    renderPersonaList("script");
}

function renderPersonaList(prefix) {
    const container = document.getElementById(`${prefix}-persona-list`);
    if (!container) return;
    if (!voiceProfiles.length) {
        container.innerHTML = '';
        return;
    }
    container.innerHTML = voiceProfiles.map(p => {
        const badge = p.is_default ? '<span class="persona-item-badge">Padrao</span>' : '';
        return `<div class="persona-item" data-profile-id="${p.id}" data-value="${p.builtin_voice || 'alloy'}" data-voice-type="profile" onclick="selectPersona(this, '${prefix}')">
            <div class="persona-item-icon">🎤</div>
            <div class="persona-item-info">
                <div class="persona-item-name">${esc(p.name)}</div>
                <div class="persona-item-meta">${p.has_custom_voice ? '✅ Voz clonada' : (p.has_sample ? 'Com amostra' : 'Voz IA')}${badge ? ' · ' : ''}${badge}</div>
            </div>
            <div class="persona-item-actions">
                <button class="btn-icon-sm" onclick="event.stopPropagation();deleteVoiceProfile(${p.id})" title="Excluir" style="color:#e74c3c;width:28px;height:28px;font-size:0.9rem">✕</button>
            </div>
        </div>`;
    }).join('');
}

function selectPersona(el, prefix) {
    // Deselect all options in this voice selector (both persona items and wizard-options)
    const selector = document.getElementById(`${prefix}-voice-selector`);
    selector.querySelectorAll('.persona-item.selected, .wizard-option.selected').forEach(o => o.classList.remove('selected'));
    el.classList.add('selected');
}

function toggleMinhaVoz(prefix) {
    const btn = document.getElementById(`${prefix}-minha-voz-btn`);
    const panel = document.getElementById(`${prefix}-persona-panel`);
    const isOpen = !panel.classList.contains('hidden');
    
    if (isOpen) {
        panel.classList.add('hidden');
        btn.classList.remove('active');
    } else {
        panel.classList.remove('hidden');
        btn.classList.add('active');
        loadVoiceProfiles();
    }
}

// ── Persona Recording (inline in panel) ──

async function startPersonaRecording(prefix) {
    if (personaMediaRecorder && personaMediaRecorder.state === "recording") {
        stopPersonaRecording(prefix);
        return;
    }
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        personaRecordedChunks = [];
        personaMediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
        personaMediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) personaRecordedChunks.push(e.data);
        };
        personaMediaRecorder.onstop = () => {
            stream.getTracks().forEach(t => t.stop());
            const blob = new Blob(personaRecordedChunks, { type: "audio/webm" });
            personaSampleBlobs[prefix] = blob;
            showPersonaPreview(prefix, blob);
        };
        personaMediaRecorder.start();

        document.getElementById(`${prefix}-recording-area`).hidden = false;
        let seconds = 0;
        personaRecordingTimer = setInterval(() => {
            seconds++;
            const el = document.getElementById(`${prefix}-rec-time`);
            if (el) el.textContent = `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
            if (seconds >= 30) stopPersonaRecording(prefix);
        }, 1000);
    } catch (err) {
        alert("Não foi possível acessar o microfone. Verifique as permissoes do navegador.");
    }
}

function stopPersonaRecording(prefix) {
    if (personaMediaRecorder && personaMediaRecorder.state === "recording") {
        personaMediaRecorder.stop();
    }
    clearInterval(personaRecordingTimer);
    const area = document.getElementById(`${prefix}-recording-area`);
    if (area) area.hidden = true;
}

async function handlePersonaUpload(event, prefix) {
    const file = event.target.files[0];
    if (!file) return;
    const hint = document.querySelector(`#${prefix}-persona-panel .persona-hint`);
    if (hint) hint.textContent = "Processando áudio...";
    const result = await trimAudioTo30s(file);
    if (result.tooLarge) {
        alert("Áudio muito grande. Grave pelo microfone ou envie um arquivo menor (max 10MB).");
        if (hint) hint.textContent = "Grave ou envie 10-30s falando para criar seu perfil de voz";
        event.target.value = '';
        return;
    }
    personaSampleBlobs[prefix] = result.blob;
    showPersonaPreview(prefix, result.blob);
    if (result.wasTrimmed && hint) {
        const min = Math.floor(result.duration / 60);
        const sec = String(Math.floor(result.duration % 60)).padStart(2, '0');
        hint.textContent = `Audio cortado para 30s (original: ${min}:${sec})`;
    } else if (hint) {
        hint.textContent = "Grave ou envie 10-30s falando para criar seu perfil de voz";
    }
    event.target.value = '';
}

async function trimAudioTo30s(blob) {
    if (blob.size > 15 * 1024 * 1024) {
        return { blob: null, wasTrimmed: false, duration: 0, tooLarge: true };
    }
    try {
        const audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const arrayBuffer = await blob.arrayBuffer();
        const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
        const duration = audioBuffer.duration;
        if (duration <= 30) {
            audioContext.close();
            return { blob, wasTrimmed: false, duration };
        }
        const rate = audioBuffer.sampleRate;
        const channels = audioBuffer.numberOfChannels;
        const maxSamples = Math.floor(30 * rate);
        const offlineCtx = new OfflineAudioContext(channels, maxSamples, rate);
        const source = offlineCtx.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(offlineCtx.destination);
        source.start(0, 0, 30);
        const rendered = await offlineCtx.startRendering();
        audioContext.close();
        const wavBlob = audioBufferToWav(rendered);
        return { blob: wavBlob, wasTrimmed: true, duration };
    } catch (e) {
        console.warn("Could not trim audio:", e);
        return { blob, wasTrimmed: false, duration: 0 };
    }
}

function audioBufferToWav(buffer) {
    const numCh = buffer.numberOfChannels;
    const rate = buffer.sampleRate;
    const bps = 16;
    const blockAlign = numCh * (bps / 8);
    const dataSize = buffer.length * blockAlign;
    const buf = new ArrayBuffer(44 + dataSize);
    const v = new DataView(buf);
    const ws = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    ws(0, 'RIFF'); v.setUint32(4, 36 + dataSize, true);
    ws(8, 'WAVE'); ws(12, 'fmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true);
    v.setUint16(22, numCh, true); v.setUint32(24, rate, true);
    v.setUint32(28, rate * blockAlign, true);
    v.setUint16(32, blockAlign, true); v.setUint16(34, bps, true);
    ws(36, 'data'); v.setUint32(40, dataSize, true);
    let off = 44;
    for (let i = 0; i < buffer.length; i++) {
        for (let ch = 0; ch < numCh; ch++) {
            const s = Math.max(-1, Math.min(1, buffer.getChannelData(ch)[i]));
            v.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
            off += 2;
        }
    }
    return new Blob([buf], { type: 'audio/wav' });
}

function showPersonaPreview(prefix, blob) {
    const url = URL.createObjectURL(blob);
    document.getElementById(`${prefix}-persona-audio`).src = url;
    document.getElementById(`${prefix}-persona-preview`).hidden = false;
    document.getElementById(`${prefix}-persona-name`).value = '';
    document.getElementById(`${prefix}-persona-name`).focus();
}

function cancelPersonaPreview(prefix) {
    document.getElementById(`${prefix}-persona-preview`).hidden = true;
    document.getElementById(`${prefix}-persona-audio`).src = '';
    personaSampleBlobs[prefix] = null;
}

async function savePersonaVoice(prefix) {
    const nameInput = document.getElementById(`${prefix}-persona-name`);
    const name = nameInput.value.trim();
    if (!name) { alert("Digite um nome para o perfil."); return; }

    const blob = personaSampleBlobs[prefix];
    if (!blob) { alert("Grave ou envie um áudio primeiro."); return; }

    try {
        // Create profile with default base voice (alloy)
        const profile = await api("/voice/profiles", {
            method: "POST",
            body: JSON.stringify({
                name: name,
                builtin_voice: "alloy",
                tts_instructions: "",
                is_default: true,
            }),
        });

        // Upload the sample — server auto-clones via Fish Audio
        if (profile.id) {
            const formData = new FormData();
            const fname = blob.type === 'audio/wav' ? 'sample.wav' : 'sample.webm';
            formData.append("file", blob, fname);
            const resp = await fetch(`/api/voice/profiles/${profile.id}/upload-sample`, {
                method: "POST",
                headers: { "Authorization": `Bearer ${token}` },
                body: formData,
            });
            const result = await resp.json();
            if (result.cloned) {
                alert("✅ Voz clonada com sucesso! Seus vídeos usarão sua voz.");
            } else if (result.clone_error) {
                alert("⚠️ Perfil salvo, mas a clonagem falhou: " + result.clone_error);
            }
        }

        personaSampleBlobs[prefix] = null;
        cancelPersonaPreview(prefix);
        await loadVoiceProfiles();

        // Select the new profile
        if (profile.id) {
            setTimeout(() => {
                const item = document.querySelector(`#${prefix}-persona-list .persona-item[data-profile-id="${profile.id}"]`);
                if (item) selectPersona(item, prefix);
            }, 200);
        }
    } catch (error) {
        alert(`Erro ao salvar: ${error.message}`);
    }
}

// ── Voice Manager Modal (for managing profiles with IA settings) ──

function openVoiceManager() {
    openModal("modal-voice-manager");
    showVoiceProfilesList();
    loadVoiceManagerProfiles();
}

async function loadVoiceManagerProfiles() {
    try {
        voiceProfiles = await api("/voice/profiles");
    } catch {
        voiceProfiles = [];
    }
    renderVoiceManagerList();
}

function renderVoiceManagerList() {
    const container = document.getElementById("vm-profiles-list");
    if (!voiceProfiles.length) {
        container.innerHTML = '<p class="muted">Nenhum perfil de voz criado ainda. Crie um para personalizar suas narracoes!</p>';
        return;
    }
    container.innerHTML = voiceProfiles.map(p => {
        const icon = p.has_sample ? '🎤' : '🔊';
        const meta = [];
        if (p.builtin_voice) {
            const names = {onyx:'Grave',echo:'Suave',ash:'Natural M',nova:'Clara',shimmer:'Suave F',coral:'Natural F',alloy:'Neutra',fable:'Narrativa',sage:'Calma'};
            meta.push('Base: ' + (names[p.builtin_voice] || p.builtin_voice));
        }
        if (p.has_custom_voice) meta.push('Voz clonada');
        if (p.has_sample) meta.push('Com amostra');
        if (p.tts_instructions) meta.push('Instrucoes personalizadas');

        return `<div class="vm-profile-card ${p.is_default ? 'is-default' : ''}">
            <div class="vm-profile-icon">${icon}</div>
            <div class="vm-profile-info">
                <div class="vm-profile-name">${p.name}${p.is_default ? ' <span style="color:var(--accent);font-size:0.75rem">✦ Padrao</span>' : ''}</div>
                <div class="vm-profile-meta">${meta.join(' · ')}</div>
            </div>
            <div class="vm-profile-actions">
                ${!p.is_default ? `<button class="btn-icon-sm" onclick="setDefaultVoice(${p.id})" title="Definir como padrao">⭐</button>` : ''}
                <button class="btn-icon-sm" onclick="previewVoice(${p.id})" title="Ouvir preview">▶</button>
                <button class="btn-icon-sm" onclick="deleteVoiceProfile(${p.id})" title="Excluir" style="color:#e74c3c">✕</button>
            </div>
        </div>`;
    }).join('');
}

function showVoiceProfilesList() {
    document.getElementById("vm-profiles-section").hidden = false;
    document.getElementById("vm-create-section").hidden = true;
}

function showCreateVoiceProfile() {
    document.getElementById("vm-profiles-section").hidden = true;
    document.getElementById("vm-create-section").hidden = false;
    document.getElementById("vm-name").value = "";
    document.getElementById("vm-instructions").value = "";
    document.getElementById("vm-set-default").checked = true;
    document.querySelectorAll("#vm-base-voice-grid .wizard-option").forEach(o => o.classList.remove("selected"));
}

function cancelCreateVoiceProfile() {
    showVoiceProfilesList();
}

async function saveVoiceProfile() {
    const name = document.getElementById("vm-name").value.trim();
    if (!name) { alert("Digite um nome para o perfil."); return; }

    const baseSel = document.querySelector("#vm-base-voice-grid .wizard-option.selected");
    if (!baseSel) { alert("Escolha uma voz base."); return; }

    const btn = document.getElementById("vm-save-btn");
    btn.textContent = "Salvando...";
    btn.disabled = true;

    try {
        await api("/voice/profiles", {
            method: "POST",
            body: JSON.stringify({
                name: name,
                builtin_voice: baseSel.dataset.value,
                tts_instructions: document.getElementById("vm-instructions").value.trim(),
                is_default: document.getElementById("vm-set-default").checked,
            }),
        });

        await loadVoiceManagerProfiles();
        showVoiceProfilesList();
        renderPersonaList("wizard");
        renderPersonaList("script");
    } catch (error) {
        alert(`Erro ao salvar: ${error.message}`);
    } finally {
        btn.textContent = "Salvar Perfil";
        btn.disabled = false;
    }
}

async function setDefaultVoice(profileId) {
    try {
        await api(`/voice/profiles/${profileId}/set-default`, { method: "POST" });
        await loadVoiceManagerProfiles();
        renderPersonaList("wizard");
        renderPersonaList("script");
    } catch (error) {
        alert(`Erro: ${error.message}`);
    }
}

async function previewVoice(profileId) {
    try {
        const result = await api(`/voice/profiles/${profileId}/preview`, { method: "POST" });
        if (result.preview_url) {
            const audio = new Audio(result.preview_url + "?t=" + Date.now());
            audio.play();
        }
    } catch (error) {
        alert(`Erro ao gerar preview: ${error.message}`);
    }
}

async function deleteVoiceProfile(profileId) {
    if (!confirm("Excluir este perfil de voz?")) return;
    try {
        await api(`/voice/profiles/${profileId}`, { method: "DELETE" });
        await loadVoiceProfiles();
        await loadVoiceManagerProfiles();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    }
}

window.toggleMinhaVoz = toggleMinhaVoz;
window.selectPersona = selectPersona;
window.startPersonaRecording = startPersonaRecording;
window.stopPersonaRecording = stopPersonaRecording;
window.handlePersonaUpload = handlePersonaUpload;
window.savePersonaVoice = savePersonaVoice;
window.cancelPersonaPreview = cancelPersonaPreview;
window.openPersonaManager = openPersonaManager;
window.handlePersonaReferenceImageSelect = handlePersonaReferenceImageSelect;
window.handlePersonaReferenceImagePaste = handlePersonaReferenceImagePaste;
window.removePersonaReferenceImage = removePersonaReferenceImage;
window.createPersonaFromManager = createPersonaFromManager;
window.selectPersonaFromManager = selectPersonaFromManager;
window.setPersonaVoiceFromManager = setPersonaVoiceFromManager;
window.previewPersonaVoiceFromManager = previewPersonaVoiceFromManager;
window.previewPersonaCreateVoice = previewPersonaCreateVoice;
window.openPersonaVoiceBuilder = openPersonaVoiceBuilder;
window.addPersonaVoiceTrait = addPersonaVoiceTrait;
window.createPersonaVoiceFromDescription = createPersonaVoiceFromDescription;
window.setDefaultPersonaFromManager = setDefaultPersonaFromManager;
window.deletePersonaFromManager = deletePersonaFromManager;
window.openVoiceManager = openVoiceManager;
window.showCreateVoiceProfile = showCreateVoiceProfile;
window.cancelCreateVoiceProfile = cancelCreateVoiceProfile;
window.saveVoiceProfile = saveVoiceProfile;
window.setDefaultVoice = setDefaultVoice;
window.previewVoice = previewVoice;
window.deleteVoiceProfile = deleteVoiceProfile;

// ============ CREDITS SYSTEM ============
let _userCredits = 0;
let _creditsPerMinute = 5;
let _creditPackages = [];
let _selectedCreditPkg = 0;

async function updateCreditsDisplay() {
    const countEl = document.getElementById("credits-count");
    if (countEl && _userCredits > 0) countEl.textContent = _userCredits;
    try {
        const data = await api("/credits");
        _userCredits = data.credits;
        _creditsPerMinute = data.creditsPerMinute || 5;
        _creditPackages = data.packages || [];
        if (countEl) countEl.textContent = _userCredits;
    } catch {}
}

function showCreditsPurchaseModal() {
    const existing = document.getElementById("credits-modal-overlay");
    if (existing) existing.remove();

    const pkgs = _creditPackages.length ? _creditPackages : [
        { credits: 100, price: 4.99 },
        { credits: 250, price: 9.99 },
        { credits: 600, price: 19.99 },
    ];

    let pkgHtml = "";
    pkgs.forEach((p, i) => {
        const sel = i === 0 ? " credit-package-selected" : "";
        const badge = i === pkgs.length - 1
            ? '<span class="credit-pkg-badge">Melhor custo</span>'
            : "";
        pkgHtml += `
            <label class="credit-package${sel}" data-pkg="${i}" onclick="selectCreditPackage(${i})">
                <span class="credit-pkg-amount">${p.credits} créditos</span>
                <span class="credit-pkg-price">R$ ${p.price.toFixed(2).replace(".", ",")}</span>
                ${badge}
            </label>`;
    });

    const overlay = document.createElement("div");
    overlay.id = "credits-modal-overlay";
    overlay.className = "credits-modal-overlay";
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    overlay.innerHTML = `
        <div class="credits-modal">
            <button class="credits-modal-close" onclick="document.getElementById('credits-modal-overlay').remove()">&times;</button>
            <h2 class="credits-modal-title">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#f0a030" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>
                Comprar Créditos
            </h2>
            <div class="credit-packages">${pkgHtml}</div>
            <div class="credits-cta">
                <button class="credits-btn credits-btn-pix" onclick="purchaseCredits('pix')">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><circle cx="17.5" cy="17.5" r="3.5"/></svg>
                    Pagar com PIX
                </button>
                <button class="credits-btn credits-btn-card" onclick="purchaseCredits('card')">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>
                    Pagar com Cartão
                </button>
            </div>
            <p class="credits-hint">Cada minuto de vídeo consome ${_creditsPerMinute} créditos</p>
        </div>
    `;
    document.body.appendChild(overlay);
    _selectedCreditPkg = 0;
}

function selectCreditPackage(idx) {
    _selectedCreditPkg = idx;
    document.querySelectorAll(".credit-package").forEach((el, i) => {
        el.classList.toggle("credit-package-selected", i === idx);
    });
}

async function purchaseCredits(method) {
    try {
        const endpoint = method === "pix" ? "/credits/purchase/pix" : "/credits/purchase/card";
        const data = await api(endpoint, {
            method: "POST",
            body: JSON.stringify({ packageIndex: _selectedCreditPkg }),
        });
        document.getElementById("credits-modal-overlay")?.remove();

        if (method === "pix" && data.pixCopiaECola) {
            showPixQrModal(data);
            pollCreditStatus(data.reference);
        } else if (data.checkoutUrl) {
            window.open(data.checkoutUrl, "_blank");
            pollCreditStatus(data.reference);
        }
    } catch (err) {
        alert(err.message || "Erro ao processar compra.");
    }
}

function showPixQrModal(data) {
    const existing = document.getElementById("pix-modal-overlay");
    if (existing) existing.remove();

    const overlay = document.createElement("div");
    overlay.id = "pix-modal-overlay";
    overlay.className = "pix-modal-overlay";
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    overlay.innerHTML = `
        <div class="pix-modal">
            <h3>Pague com PIX</h3>
            ${data.qrBase64 ? `<img class="pix-qr-img" src="data:image/png;base64,${data.qrBase64}" alt="QR Code PIX"/>` : ""}
            <div class="pix-code-box" id="pix-code">${data.pixCopiaECola}</div>
            <button class="pix-copy-btn" onclick="navigator.clipboard.writeText(document.getElementById('pix-code').textContent);this.textContent='Copiado!';">Copiar código PIX</button>
            <p class="pix-waiting">Aguardando pagamento...</p>
        </div>
    `;
    document.body.appendChild(overlay);
}

async function pollCreditStatus(reference) {
    for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 5000));
        try {
            const data = await api(`/credits/status/${encodeURIComponent(reference)}`);
            if (data.status === "confirmed") {
                document.getElementById("pix-modal-overlay")?.remove();
                alert(`${data.credits} créditos adicionados!`);
                updateCreditsDisplay();
                return;
            }
        } catch {}
    }
}

// Wire up sidebar credits click
document.getElementById("sidebar-credits")?.addEventListener("click", () => {
    showCreditsPurchaseModal();
});

window.selectCreditPackage = selectCreditPackage;
window.purchaseCredits = purchaseCredits;

/* ══════════════════════════════════════════════════════════════
   VIDEO EDITOR ENGINE
   ══════════════════════════════════════════════════════════════ */
const _editor = {
    projectId: 0,
    videoUrl: "",
    duration: 0,
    sourceAspectRatio: "9:16",
    outputAspectRatio: "source",
    playing: false,
    activeTool: "text",
    subtitleListOpen: false,
    selectedClip: { kind: "", id: "", track: "" },
    // Edit state
    texts: [],          // {id, content, startTime, endTime, x, y, fontSize, color, fontFamily, bold, italic}
    subtitles: [],      // {id, text, startTime, endTime, styleName, x, y, fontSize, fontColor, bgColor, outlineColor, fontFamily, bold, italic}
    videoSegments: [],  // {id, start, end}
    audioSegments: [],  // {id, start, end}
    selectedTracks: ["video"],
    trimStart: 0,
    trimEnd: 0,
    musicUrl: "",
    _musicFile: null,
    _musicServerPath: "",
    _musicSource: "audio", // audio | video
    musicVolume: 80,
    originalVolume: 100,
    _lastMusicVolume: 80,
    _lastOriginalVolume: 100,
    filter: "none",
    stickers: [],       // {id, emoji, x, y, startTime, endTime, size}
    mediaLayers: [],    // Optional layered media stack order
    quality: "original",
    // Undo/redo
    undoStack: [],
    redoStack: [],
    _nextId: 1,
};

let _editorTimelineDrag = null;
let _editorTimelineScrub = null;
let _editorMediaLayerDrag = null;
let _editorMusicPreviewAudio = null;
let _editorMusicPreviewWarned = false;

function _editorGenId() { return _editor._nextId++; }

// ── Subtitle style presets ──
const SUBTITLE_STYLES = [
    { name: "classico", label: "Classico", fontFamily: "Arial, sans-serif", fontSize: 28, fontColor: "#ffffff", bgColor: "rgba(0,0,0,0.6)", outlineColor: "", bold: true, italic: false },
    { name: "destaque", label: "Destaque", fontFamily: "Arial Black, sans-serif", fontSize: 32, fontColor: "#facc15", bgColor: "rgba(0,0,0,0.7)", outlineColor: "#000000", bold: true, italic: false },
    { name: "neon", label: "Neon", fontFamily: "Arial, sans-serif", fontSize: 30, fontColor: "#00ff88", bgColor: "", outlineColor: "#00ff88", bold: true, italic: false },
    { name: "minimalista", label: "Minimalista", fontFamily: "Manrope, sans-serif", fontSize: 24, fontColor: "#ffffff", bgColor: "", outlineColor: "#000000", bold: false, italic: false },
    { name: "impacto", label: "Impacto", fontFamily: "Arial Black, sans-serif", fontSize: 38, fontColor: "#ffffff", bgColor: "#e11d48", outlineColor: "", bold: true, italic: false },
    { name: "elegante", label: "Elegante", fontFamily: "Georgia, serif", fontSize: 26, fontColor: "#f0d9b5", bgColor: "rgba(0,0,0,0.5)", outlineColor: "", bold: false, italic: true },
    { name: "moderno", label: "Moderno", fontFamily: "Outfit, sans-serif", fontSize: 30, fontColor: "#ffffff", bgColor: "#3b82f6", outlineColor: "", bold: true, italic: false },
    { name: "karaoke", label: "Karaoke", fontFamily: "Arial Black, sans-serif", fontSize: 34, fontColor: "#ffffff", bgColor: "#7c3aed", outlineColor: "#000000", bold: true, italic: false },
    { name: "sombra", label: "Sombra", fontFamily: "Arial, sans-serif", fontSize: 28, fontColor: "#ffffff", bgColor: "", outlineColor: "#333333", bold: true, italic: false },
    { name: "retro", label: "Retro", fontFamily: "Courier New, monospace", fontSize: 26, fontColor: "#fbbf24", bgColor: "rgba(0,0,0,0.8)", outlineColor: "", bold: true, italic: false },
    { name: "arco_iris", label: "Colorido", fontFamily: "Arial Black, sans-serif", fontSize: 32, fontColor: "#ff6b6b", bgColor: "#10b981", outlineColor: "#000000", bold: true, italic: false },
    { name: "cinema", label: "Cinema", fontFamily: "Georgia, serif", fontSize: 22, fontColor: "#e2e8f0", bgColor: "", outlineColor: "#000000", bold: false, italic: false },
    { name: "viral", label: "Viral", fontFamily: "Arial Black, sans-serif", fontSize: 36, fontColor: "#ffffff", bgColor: "#f97316", outlineColor: "#000000", bold: true, italic: false },
    { name: "suave", label: "Suave", fontFamily: "Manrope, sans-serif", fontSize: 24, fontColor: "#d4d4d8", bgColor: "rgba(255,255,255,0.12)", outlineColor: "", bold: false, italic: false },
];

function _getSubStyle(name) {
    return SUBTITLE_STYLES.find(s => s.name === name) || SUBTITLE_STYLES[0];
}

function _editorSaveState() {
    const snap = JSON.stringify({
        texts: _editor.texts,
        subtitles: _editor.subtitles,
        videoSegments: _editor.videoSegments,
        audioSegments: _editor.audioSegments,
        selectedTracks: _editor.selectedTracks,
        trimStart: _editor.trimStart,
        trimEnd: _editor.trimEnd,
        outputAspectRatio: _editor.outputAspectRatio,
        musicUrl: _editor.musicUrl,
        _musicSource: _editor._musicSource,
        _musicServerPath: _editor._musicServerPath,
        musicVolume: _editor.musicVolume,
        originalVolume: _editor.originalVolume,
        filter: _editor.filter,
        stickers: _editor.stickers,
        mediaLayers: _editor.mediaLayers,
        quality: _editor.quality,
    });
    _editor.undoStack.push(snap);
    _editor.redoStack = [];
    if (_editor.undoStack.length > 50) _editor.undoStack.shift();
    _updateUndoRedoBtns();
}

function _editorUndo() {
    if (!_editor.undoStack.length) return;
    const current = JSON.stringify({
        texts: _editor.texts, subtitles: _editor.subtitles, trimStart: _editor.trimStart,
        videoSegments: _editor.videoSegments,
        audioSegments: _editor.audioSegments,
        selectedTracks: _editor.selectedTracks,
        trimEnd: _editor.trimEnd, outputAspectRatio: _editor.outputAspectRatio,
        musicUrl: _editor.musicUrl,
        _musicSource: _editor._musicSource,
        _musicServerPath: _editor._musicServerPath,
        musicVolume: _editor.musicVolume,
        originalVolume: _editor.originalVolume, filter: _editor.filter, stickers: _editor.stickers, mediaLayers: _editor.mediaLayers, quality: _editor.quality,
    });
    _editor.redoStack.push(current);
    const snap = JSON.parse(_editor.undoStack.pop());
    Object.assign(_editor, snap);
    _editorSetMusicPreviewSource(_editor.musicUrl || "");
    _editorSyncAudioSegmentsWithVideoIfNoExternalAudio();
    _editor.selectedClip = { kind: "", id: "", track: "" };
    _updateUndoRedoBtns();
    _editorApplyAspectRatio();
    _editorRefreshQuickActions();
    _editorRenderMediaLayers();
    _editorRenderProps();
    _editorRenderTimeline();
}

function _editorRedo() {
    if (!_editor.redoStack.length) return;
    const current = JSON.stringify({
        texts: _editor.texts, subtitles: _editor.subtitles, trimStart: _editor.trimStart,
        videoSegments: _editor.videoSegments,
        audioSegments: _editor.audioSegments,
        selectedTracks: _editor.selectedTracks,
        trimEnd: _editor.trimEnd, outputAspectRatio: _editor.outputAspectRatio,
        musicUrl: _editor.musicUrl,
        _musicSource: _editor._musicSource,
        _musicServerPath: _editor._musicServerPath,
        musicVolume: _editor.musicVolume,
        originalVolume: _editor.originalVolume, filter: _editor.filter, stickers: _editor.stickers, mediaLayers: _editor.mediaLayers, quality: _editor.quality,
    });
    _editor.undoStack.push(current);
    const snap = JSON.parse(_editor.redoStack.pop());
    Object.assign(_editor, snap);
    _editorSetMusicPreviewSource(_editor.musicUrl || "");
    _editorSyncAudioSegmentsWithVideoIfNoExternalAudio();
    _editor.selectedClip = { kind: "", id: "", track: "" };
    _updateUndoRedoBtns();
    _editorApplyAspectRatio();
    _editorRefreshQuickActions();
    _editorRenderMediaLayers();
    _editorRenderProps();
    _editorRenderTimeline();
}

function _updateUndoRedoBtns() {
    const undo = document.getElementById("editor-undo-btn");
    const redo = document.getElementById("editor-redo-btn");
    if (undo) undo.disabled = !_editor.undoStack.length;
    if (redo) redo.disabled = !_editor.redoStack.length;
}

function _normalizeAspectValue(value) {
    return ["9:16", "16:9", "1:1", "source"].includes(value) ? value : "source";
}

function _resolveAspectRatio() {
    const source = ["9:16", "16:9", "1:1"].includes(_editor.sourceAspectRatio)
        ? _editor.sourceAspectRatio
        : "9:16";
    if (_editor.outputAspectRatio === "source") return source;
    return _normalizeAspectValue(_editor.outputAspectRatio).replace("source", source);
}

function _editorApplyAspectRatio() {
    const wrapper = document.getElementById("editor-canvas-wrapper");
    if (!wrapper) return;

    const resolved = _resolveAspectRatio();
    const cssVal = {
        "9:16": "9 / 16",
        "16:9": "16 / 9",
        "1:1": "1 / 1",
    }[resolved] || "9 / 16";
    wrapper.style.setProperty("--editor-aspect-ratio", cssVal);

    const sel = document.getElementById("editor-aspect-select");
    if (sel) sel.value = _normalizeAspectValue(_editor.outputAspectRatio);

    const video = document.getElementById("editor-video");
    if (video && !video.paused) {
        _editorDrawOverlays(video.currentTime);
    } else {
        _editorDrawOverlays(video?.currentTime || 0);
    }

    _editorRenderMediaLayers();
}

function _editorSetOutputAspectRatio(value) {
    _editor.outputAspectRatio = _normalizeAspectValue(value);
    _editorApplyAspectRatio();
}
window._editorSetOutputAspectRatio = _editorSetOutputAspectRatio;

function _editorGetSegments(track = "video") {
    return track === "audio" ? _editor.audioSegments : _editor.videoSegments;
}

function _editorSetSegments(track, segments) {
    if (track === "audio") {
        _editor.audioSegments = segments;
    } else {
        _editor.videoSegments = segments;
    }
}

function _editorSortSegments(track = "video") {
    _editorGetSegments(track).sort((a, b) => (a.start - b.start) || (a.end - b.end));
}

function _editorFindSegment(track = "video", id) {
    return _editorGetSegments(track).find(seg => String(seg.id) === String(id));
}

function _editorSortVideoSegments() {
    _editorSortSegments("video");
}

function _editorFindVideoSegment(id) {
    return _editorFindSegment("video", id);
}

function _editorShouldShowAudioTrack() {
    return Boolean(_editor.musicUrl || _editor._musicFile || _editor._musicServerPath);
}

function _editorGetMusicPreviewAudio() {
    if (!_editorMusicPreviewAudio) {
        _editorMusicPreviewAudio = new Audio();
        _editorMusicPreviewAudio.preload = "auto";
        _editorMusicPreviewAudio.loop = true;
    }
    return _editorMusicPreviewAudio;
}

function _editorSetMusicPreviewSource(url) {
    const audio = _editorGetMusicPreviewAudio();
    if (!url) {
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
        _editorMusicPreviewWarned = false;
        return;
    }

    if (audio.src !== url) {
        audio.pause();
        audio.src = url;
        audio.load();
    }

    audio.volume = Math.max(0, Math.min(1, (_editor.musicVolume || 0) / 100));
    _editorMusicPreviewWarned = false;
}

function _editorSyncMusicPreviewPlayback(videoTime, shouldPlay) {
    if (!_editorShouldShowAudioTrack() || !_editor.musicUrl) {
        _editorSetMusicPreviewSource("");
        return;
    }

    const audio = _editorGetMusicPreviewAudio();
    if (!audio.src) {
        _editorSetMusicPreviewSource(_editor.musicUrl);
    }

    audio.volume = Math.max(0, Math.min(1, (_editor.musicVolume || 0) / 100));

    let targetTime = Math.max(0, Number(videoTime || 0));
    const duration = Number(audio.duration || 0);
    if (Number.isFinite(duration) && duration > 0.05) {
        targetTime = targetTime % duration;
    }

    if (Math.abs(Number(audio.currentTime || 0) - targetTime) > 0.25) {
        try {
            audio.currentTime = targetTime;
        } catch (_) {
            // Ignore seek errors while metadata is not ready.
        }
    }

    if (shouldPlay) {
        if (audio.paused) {
            const playPromise = audio.play();
            if (playPromise && typeof playPromise.catch === "function") {
                playPromise.catch(() => {
                    if (!_editorMusicPreviewWarned) {
                        _editorMusicPreviewWarned = true;
                        showToast("Não foi possível tocar a prévia do áudio externo. O áudio será aplicado na exportação.", "error");
                    }
                });
            }
        }
        return;
    }

    if (!audio.paused) {
        audio.pause();
    }
}

function _editorCloneVideoSegmentsForAudio() {
    return _editor.videoSegments.map((seg, idx) => ({
        id: `auto-audio-${idx + 1}`,
        start: Number(seg.start || 0),
        end: Number(seg.end || 0),
    }));
}

function _editorSyncAudioSegmentsWithVideoIfNoExternalAudio() {
    if (_editorShouldShowAudioTrack()) return;
    _editorSetMusicPreviewSource("");
    _editor.audioSegments = _editorCloneVideoSegmentsForAudio();
    _editor.selectedTracks = ["video"];
    if (_editor.selectedClip.track === "audio" || _editor.selectedClip.kind === "music") {
        _editor.selectedClip = { kind: "", id: "", track: "" };
    }
}

function _editorIsTrackSelectable(track) {
    if (track === "video") return true;
    if (track === "audio") return _editorShouldShowAudioTrack();
    return false;
}

function _editorGetSelectedSegmentTracks() {
    const tracks = (_editor.selectedTracks || []).filter(_editorIsTrackSelectable);
    return tracks.length ? tracks : ["video"];
}

function _editorIsTrackSelected(track) {
    return _editorGetSelectedSegmentTracks().includes(track);
}

function _editorToggleTrackSelection(track) {
    if (!_editorIsTrackSelectable(track)) return;

    const current = _editorGetSelectedSegmentTracks();
    let next = current;

    if (current.includes(track)) {
        // When multiple tracks are active, click isolates the chosen track.
        if (current.length > 1) {
            next = [track];
        }
    } else if (current.length === 1) {
        // With one active track, clicking the other track enables multi-select.
        next = [current[0], track];
    } else {
        next = [track];
    }

    _editor.selectedTracks = next;
    _editor.selectedClip = { kind: "", id: "", track: "" };
    _editorRefreshTrackSelectionUI();
    _editorRenderProps();
    _editorRenderTimeline();
}

function _editorRefreshTrackSelectionUI() {
    document.querySelectorAll(".editor-track").forEach(trackEl => {
        const track = trackEl.dataset.track || "";
        trackEl.classList.remove("track-targeted", "track-not-targeted");
        if (!_editorIsTrackSelectable(track)) return;
        if (_editorIsTrackSelected(track)) {
            trackEl.classList.add("track-targeted");
        } else {
            trackEl.classList.add("track-not-targeted");
        }
    });
}

function _editorRecomputeTrimBounds() {
    if (!_editor.videoSegments.length) {
        _editor.trimStart = 0;
        _editor.trimEnd = _editor.duration || 0;
        return;
    }
    _editorSortVideoSegments();
    _editor.trimStart = Math.max(0, _editor.videoSegments[0].start || 0);
    _editor.trimEnd = Math.max(_editor.trimStart, _editor.videoSegments[_editor.videoSegments.length - 1].end || _editor.trimStart);
}

function _editorInitVideoSegments() {
    const dur = Math.max(_editor.duration || 0, 0.1);
    _editor.videoSegments = [{ id: _editorGenId(), start: 0, end: dur }];
    _editor.audioSegments = _editorCloneVideoSegmentsForAudio();
    _editor.selectedTracks = ["video"];
    _editorRecomputeTrimBounds();
}

function _editorClampSegmentRange(track, segmentId, nextStart, span) {
    const dur = Math.max(_editor.duration || 0, 0.1);
    const sorted = [..._editorGetSegments(track)].sort((a, b) => a.start - b.start);
    const idx = sorted.findIndex(seg => String(seg.id) === String(segmentId));
    if (idx < 0) {
        const clampedStart = Math.max(0, Math.min(Math.max(0, dur - span), nextStart));
        return [clampedStart, clampedStart + span];
    }

    const prev = sorted[idx - 1] || null;
    const next = sorted[idx + 1] || null;
    const minStart = prev ? prev.end + 0.02 : 0;
    const maxStart = next ? Math.max(minStart, next.start - span - 0.02) : Math.max(minStart, dur - span);
    const clampedStart = Math.max(minStart, Math.min(maxStart, nextStart));
    return [clampedStart, clampedStart + span];
}

// ---------- Load completed videos for selection ----------
async function loadEditorVideosList() {
    const container = document.getElementById("editor-videos-list");
    if (!container) return;
    try {
        const data = await api("/video/projects");
        const completed = data.filter(p => p.status === "completed" && !p.video_expired);
        if (!completed.length) {
            container.innerHTML = "<p class='loading'>Nenhum video finalizado ainda. Use o botao + para enviar um video ou crie um video primeiro.</p>";
            return;
        }
        container.innerHTML = completed.map(p => {
            const thumb = p.thumbnail_url
                ? `<div style="position:relative"><img class="card-thumb" src="${p.thumbnail_url}" alt="" loading="lazy"><div class="editor-video-card-overlay"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg></div></div>`
                : `<div class="card-thumb card-thumb-placeholder" style="position:relative"><svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5 3 19 12 5 21 5 3"/></svg><div class="editor-video-card-overlay"><svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg></div></div>`;
            return `<div class="card" style="cursor:pointer" onclick="openEditor(${p.id})">
                ${thumb}
                <div class="card-body"><h4 class="card-title">${esc(p.title)}</h4></div>
            </div>`;
        }).join("");
    } catch (err) {
        container.innerHTML = `<p class='loading'>Erro: ${esc(err.message)}</p>`;
    }
}

async function _editorUploadVideo(input) {
    const file = input.files?.[0];
    if (!file) return;

    try {
        showToast("Enviando vídeo para o editor...");
        const formData = new FormData();
        formData.append("file", file);
        const payload = await apiForm("/video/editor/upload-video", formData, { method: "POST" });
        input.value = "";

        await loadEditorVideosList();
        if (payload?.project_id) {
            showToast("Vídeo enviado! Abrindo editor...", "success");
            await openEditor(payload.project_id);
        } else {
            showToast("Vídeo enviado com sucesso.", "success");
        }
    } catch (err) {
        showToast("Erro ao enviar vídeo: " + err.message, "error");
    }
}
window._editorUploadVideo = _editorUploadVideo;

function _editorGetMediaLayerById(id) {
    return _editor.mediaLayers.find(layer => String(layer.id) === String(id)) || null;
}

function _editorNormalizeMediaLayer(layer) {
    const aspect = Math.max(0.2, Number(layer.aspectRatio || 1));
    return {
        ...layer,
        width: Math.max(8, Math.min(100, Number(layer.width || 100))),
        x: Math.max(0, Math.min(100, Number(layer.x || 0))),
        y: Math.max(0, Math.min(100, Number(layer.y || 0))),
        startTime: Math.max(0, Number(layer.startTime || 0)),
        endTime: Math.max(0, Number(layer.endTime || 0)),
        duration: Math.max(0, Number(layer.duration || 0)),
        volume: Math.max(0, Math.min(200, Number(layer.volume ?? 100))),
        audioOnly: Boolean(layer.audioOnly),
        aspectRatio: aspect,
    };
}

function _editorGetMediaLayerLayout(layer, hostWidth, hostHeight) {
    const safeLayer = _editorNormalizeMediaLayer(layer);
    let widthPx = (hostWidth * safeLayer.width) / 100;
    let heightPx = widthPx / safeLayer.aspectRatio;
    if (heightPx > hostHeight) {
        heightPx = hostHeight;
        widthPx = heightPx * safeLayer.aspectRatio;
    }
    const maxLeft = Math.max(0, hostWidth - widthPx);
    const maxTop = Math.max(0, hostHeight - heightPx);
    const leftPx = (safeLayer.x / 100) * maxLeft;
    const topPx = (safeLayer.y / 100) * maxTop;
    return { widthPx, heightPx, leftPx, topPx, maxLeft, maxTop };
}

function _editorSyncMediaLayersWithTime(timeSec) {
    const host = document.getElementById("editor-media-layer-host");
    if (!host) return;
    const currentTime = Math.max(0, Number(timeSec || 0));
    const shouldPlay = Boolean(_editor.playing);

    host.querySelectorAll(".editor-media-layer-item").forEach((item) => {
        const layer = _editorGetMediaLayerById(item.dataset.id || "");
        if (!layer) {
            item.remove();
            return;
        }

        const normalizedLayer = _editorNormalizeMediaLayer(layer);
        const localTime = Math.max(0, currentTime - normalizedLayer.startTime);
        const reachedVideoEnd = normalizedLayer.kind === "video"
            && Number(normalizedLayer.duration || 0) > 0
            && localTime > Number(normalizedLayer.duration || 0);
        const inRange = currentTime >= normalizedLayer.startTime && currentTime <= normalizedLayer.endTime && !reachedVideoEnd;

        if (normalizedLayer.kind === "video" && normalizedLayer.audioOnly) {
            item.style.display = inRange ? "block" : "none";
            item.style.opacity = "0";
            item.style.pointerEvents = "none";
        } else {
            item.style.display = inRange ? "block" : "none";
            item.style.opacity = "1";
            item.style.pointerEvents = "";
        }

        if (normalizedLayer.kind !== "video") return;

        const videoEl = item.querySelector("video");
        if (!videoEl) return;
        const maxTime = normalizedLayer.duration > 0 ? Math.max(0, normalizedLayer.duration - 0.05) : localTime;
        const targetTime = Math.min(localTime, maxTime);
        if (Math.abs((videoEl.currentTime || 0) - targetTime) > 0.1) {
            try {
                videoEl.currentTime = targetTime;
            } catch {
                // Ignore seek errors while metadata is loading.
            }
        }
        videoEl.volume = Math.max(0, Math.min(1, normalizedLayer.volume / 100));
        videoEl.muted = normalizedLayer.volume <= 0;

        if (shouldPlay && inRange && !videoEl.ended) {
            if (videoEl.paused) {
                const playPromise = videoEl.play();
                if (playPromise?.catch) {
                    playPromise.catch(() => {
                        // Ignore autoplay and interruption errors in preview sync.
                    });
                }
            }
        } else if (!videoEl.paused) {
            videoEl.pause();
        }
    });
}

function _editorRenderMediaLayers() {
    const host = document.getElementById("editor-media-layer-host");
    if (!host) return;
    const hostRect = host.getBoundingClientRect();
    const hostWidth = Math.max(1, hostRect.width || host.offsetWidth || 1);
    const hostHeight = Math.max(1, hostRect.height || host.offsetHeight || 1);
    const selectedId = String(_editor.selectedClip.id || "");

    const visibleLayers = _editor.mediaLayers
        .map(rawLayer => _editorNormalizeMediaLayer(rawLayer));

    host.innerHTML = visibleLayers.map((layer, idx) => {
        const layout = _editorGetMediaLayerLayout(layer, hostWidth, hostHeight);
        const selectedClass = selectedId === String(layer.id) && _editor.selectedClip.kind === "media-layer" ? " selected" : "";
        const zIndex = (visibleLayers.length - idx) + 1;
        const isSelected = selectedId === String(layer.id) && _editor.selectedClip.kind === "media-layer";
        const mediaHtml = layer.kind === "video"
            ? `<video src="${esc(layer.url)}" playsinline preload="metadata"></video>`
            : `<img src="${esc(layer.url)}" alt="Camada" loading="lazy">`;
        return `
            <div
                class="editor-media-layer-item${selectedClass}"
                data-id="${layer.id}"
                style="left:${layout.leftPx}px;top:${layout.topPx}px;width:${layout.widthPx}px;height:${layout.heightPx}px;z-index:${zIndex}"
            >
                ${mediaHtml}
                ${isSelected ? '<div class="editor-media-layer-handle" data-role="resize"></div>' : ''}
            </div>
        `;
    }).join("");

    const video = document.getElementById("editor-video");
    _editorSyncMediaLayersWithTime(Number(video?.currentTime || 0));
}

function _editorSelectMediaLayer(id, renderProps = true) {
    const layer = _editorGetMediaLayerById(id);
    if (!layer) return;
    _editor.selectedClip = { kind: "media-layer", id: String(layer.id), track: `media-${layer.id}` };

    let switchedTool = false;
    if (renderProps && _editor.activeTool !== "layers") {
        _editorSelectTool("layers");
        switchedTool = true;
    }
    if (renderProps && !switchedTool) {
        _editorRenderProps();
    }

    _editorRenderTimeline();
    _editorRenderMediaLayers();
}
window._editorSelectMediaLayer = _editorSelectMediaLayer;

function _editorSetMediaLayerSize(id, val) {
    const layer = _editorGetMediaLayerById(id);
    if (!layer) return;
    layer.width = Math.max(8, Math.min(100, Number(val || layer.width || 100)));
    _editorRenderMediaLayers();
    _editorRenderProps();
}
window._editorSetMediaLayerSize = _editorSetMediaLayerSize;

function _editorSetMediaLayerX(id, val) {
    const layer = _editorGetMediaLayerById(id);
    if (!layer) return;
    layer.x = Math.max(0, Math.min(100, Number(val || layer.x || 0)));
    _editorRenderMediaLayers();
    _editorRenderProps();
}
window._editorSetMediaLayerX = _editorSetMediaLayerX;

function _editorSetMediaLayerY(id, val) {
    const layer = _editorGetMediaLayerById(id);
    if (!layer) return;
    layer.y = Math.max(0, Math.min(100, Number(val || layer.y || 0)));
    _editorRenderMediaLayers();
    _editorRenderProps();
}
window._editorSetMediaLayerY = _editorSetMediaLayerY;

function _editorSetMediaLayerVolume(id, val) {
    const layer = _editorGetMediaLayerById(id);
    if (!layer || layer.kind !== "video") return;
    layer.volume = Math.max(0, Math.min(200, Number(val || layer.volume || 100)));

    const label = document.getElementById("editor-layer-vol-label");
    if (label && _editor.selectedClip.kind === "media-layer" && String(_editor.selectedClip.id) === String(id)) {
        label.textContent = `${Math.round(layer.volume)}%`;
    }

    const video = document.getElementById("editor-video");
    _editorSyncMediaLayersWithTime(Number(video?.currentTime || 0));
}
window._editorSetMediaLayerVolume = _editorSetMediaLayerVolume;

function _editorToggleMediaLayerAudioOnly(id) {
    const layer = _editorGetMediaLayerById(id);
    if (!layer || layer.kind !== "video") return;
    _editorSaveState();
    layer.audioOnly = !layer.audioOnly;
    _editorRenderMediaLayers();
    _editorRenderTimeline();
    _editorRenderProps();
}
window._editorToggleMediaLayerAudioOnly = _editorToggleMediaLayerAudioOnly;

function _editorSetMediaLayerStart(id, val) {
    const layer = _editorGetMediaLayerById(id);
    if (!layer) return;
    const nextStart = Math.max(0, Math.min(_editor.duration || 0, Number(val || 0)));
    layer.startTime = nextStart;
    layer.endTime = Math.max(nextStart + 0.1, Number(layer.endTime || nextStart + 0.1));
    if (_editor.duration > 0) {
        layer.endTime = Math.min(_editor.duration, layer.endTime);
    }
    _editorRenderTimeline();
    _editorSyncMediaLayersWithTime(Number(document.getElementById("editor-video")?.currentTime || 0));
    _editorRenderProps();
}
window._editorSetMediaLayerStart = _editorSetMediaLayerStart;

function _editorSetMediaLayerEnd(id, val) {
    const layer = _editorGetMediaLayerById(id);
    if (!layer) return;
    const nextEnd = Math.max(0, Math.min(_editor.duration || 0, Number(val || 0)));
    layer.endTime = nextEnd;
    layer.startTime = Math.min(layer.startTime, Math.max(0, nextEnd - 0.1));
    _editorRenderTimeline();
    _editorSyncMediaLayersWithTime(Number(document.getElementById("editor-video")?.currentTime || 0));
    _editorRenderProps();
}
window._editorSetMediaLayerEnd = _editorSetMediaLayerEnd;

function _editorDeleteMediaLayer(id) {
    _editorSaveState();
    _editor.mediaLayers = _editor.mediaLayers.filter(layer => String(layer.id) !== String(id));
    if (_editor.selectedClip.kind === "media-layer" && String(_editor.selectedClip.id) === String(id)) {
        _editor.selectedClip = { kind: "", id: "", track: "" };
    }
    _editorRefreshQuickActions();
    _editorRenderTimeline();
    _editorRenderMediaLayers();
    _editorRenderProps();
}
window._editorDeleteMediaLayer = _editorDeleteMediaLayer;

function _editorPushMediaLayer(kind, payload) {
    const previewUrl = String(payload?.media_url || "").trim();
    const serverPath = String(payload?.path || "").trim();
    if (!previewUrl || !serverPath) {
        throw new Error("Resposta de upload invalida para camada.");
    }
    const width = Math.max(1, Number(payload?.width || 0) || 1);
    const height = Math.max(1, Number(payload?.height || 0) || 1);
    const aspectRatio = width / height;
    const baseDuration = Math.max(0.1, Number(_editor.duration || 0.1));
    const layerDuration = kind === "video" ? Math.max(0, Number(payload?.duration || 0)) : baseDuration;
    const initialEnd = kind === "video" && layerDuration > 0
        ? Math.min(baseDuration, layerDuration)
        : baseDuration;
    const layer = {
        id: _editorGenId(),
        kind,
        name: String(payload?.name || (kind === "video" ? "Camada de video" : "Camada de imagem")),
        url: previewUrl,
        path: serverPath,
        width: 100,
        x: 0,
        y: 0,
        startTime: 0,
        endTime: Math.max(0.1, initialEnd),
        duration: layerDuration,
        aspectRatio,
        volume: 100,
        audioOnly: false,
    };
    _editor.mediaLayers.push(layer);
    _editorSelectMediaLayer(layer.id, true);
}

async function _editorUploadLayerVideo(input) {
    const file = input.files?.[0];
    if (!file) return;
    if (!_editor.projectId) {
        input.value = "";
        showToast("Abra um projeto no editor antes de enviar vídeos em camada.", "error");
        return;
    }

    try {
        _editorSaveState();
        showToast("Enviando vídeo para camada...");
        const formData = new FormData();
        formData.append("file", file);
        const payload = await apiForm("/video/editor/upload-layer-video", formData, { method: "POST" });
        _editorPushMediaLayer("video", payload);
        _editorRenderTimeline();
        _editorRenderMediaLayers();
        showToast("Nova camada de vídeo adicionada.", "success");
    } catch (err) {
        showToast("Erro ao enviar vídeo da camada: " + err.message, "error");
    } finally {
        input.value = "";
    }
}
window._editorUploadLayerVideo = _editorUploadLayerVideo;

async function _editorUploadLayerImage(input) {
    const file = input.files?.[0];
    if (!file) return;
    if (!_editor.projectId) {
        input.value = "";
        showToast("Abra um projeto no editor antes de enviar imagens em camada.", "error");
        return;
    }

    try {
        _editorSaveState();
        showToast("Enviando imagem para camada...");
        const formData = new FormData();
        formData.append("file", file);
        const payload = await apiForm("/video/editor/upload-layer-image", formData, { method: "POST" });
        _editorPushMediaLayer("image", payload);
        _editorRenderTimeline();
        _editorRenderMediaLayers();
        showToast("Nova camada de imagem adicionada.", "success");
    } catch (err) {
        showToast("Erro ao enviar imagem da camada: " + err.message, "error");
    } finally {
        input.value = "";
    }
}
window._editorUploadLayerImage = _editorUploadLayerImage;

function _editorOnMediaLayerPointerDown(e) {
    const host = document.getElementById("editor-media-layer-host");
    const item = e.target.closest(".editor-media-layer-item");
    if (!host || !item || !host.contains(item)) return;

    const layerId = item.dataset.id || "";
    const layer = _editorGetMediaLayerById(layerId);
    if (!layer) return;

    _editorSelectMediaLayer(layerId, true);

    const hostRect = host.getBoundingClientRect();
    const hostWidth = Math.max(1, hostRect.width || host.offsetWidth || 1);
    const hostHeight = Math.max(1, hostRect.height || host.offsetHeight || 1);
    const layout = _editorGetMediaLayerLayout(layer, hostWidth, hostHeight);
    const onResizeHandle = Boolean(e.target.closest(".editor-media-layer-handle[data-role='resize']"));

    _editorMediaLayerDrag = {
        id: String(layer.id),
        mode: onResizeHandle ? "resize" : "move",
        startX: e.clientX,
        startY: e.clientY,
        startLeft: layout.leftPx,
        startTop: layout.topPx,
        startWidth: layout.widthPx,
        startHeight: layout.heightPx,
        hostWidth,
        hostHeight,
        aspectRatio: Math.max(0.2, Number(layer.aspectRatio || 1)),
        maxLeft: layout.maxLeft,
        maxTop: layout.maxTop,
        dirty: false,
    };

    item.setPointerCapture?.(e.pointerId);
    e.preventDefault();
    e.stopPropagation();
}

function _editorOnMediaLayerDragMove(e) {
    if (!_editorMediaLayerDrag) return;
    const drag = _editorMediaLayerDrag;
    const layer = _editorGetMediaLayerById(drag.id);
    if (!layer) {
        _editorMediaLayerDrag = null;
        return;
    }

    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;

    if (!drag.dirty && (Math.abs(dx) > 1 || Math.abs(dy) > 1)) {
        _editorSaveState();
        drag.dirty = true;
    }

    let nextLeft = drag.startLeft;
    let nextTop = drag.startTop;
    let nextWidth = drag.startWidth;
    let nextHeight = drag.startHeight;

    if (drag.mode === "resize") {
        const minWidth = Math.max(32, drag.hostWidth * 0.08);
        const maxByWidth = Math.max(minWidth, drag.hostWidth - drag.startLeft);
        const maxByHeight = Math.max(minWidth, (drag.hostHeight - drag.startTop) * drag.aspectRatio);
        nextWidth = Math.max(minWidth, Math.min(Math.min(maxByWidth, maxByHeight), drag.startWidth + dx));
        nextHeight = nextWidth / drag.aspectRatio;

        const nextMaxLeft = Math.max(0, drag.hostWidth - nextWidth);
        const nextMaxTop = Math.max(0, drag.hostHeight - nextHeight);
        nextLeft = Math.max(0, Math.min(nextMaxLeft, drag.startLeft));
        nextTop = Math.max(0, Math.min(nextMaxTop, drag.startTop));

        layer.width = Math.max(8, Math.min(100, (nextWidth / drag.hostWidth) * 100));
        layer.x = nextMaxLeft > 0 ? (nextLeft / nextMaxLeft) * 100 : 0;
        layer.y = nextMaxTop > 0 ? (nextTop / nextMaxTop) * 100 : 0;
    } else {
        nextLeft = Math.max(0, Math.min(drag.maxLeft, drag.startLeft + dx));
        nextTop = Math.max(0, Math.min(drag.maxTop, drag.startTop + dy));
        layer.x = drag.maxLeft > 0 ? (nextLeft / drag.maxLeft) * 100 : 0;
        layer.y = drag.maxTop > 0 ? (nextTop / drag.maxTop) * 100 : 0;
    }

    const host = document.getElementById("editor-media-layer-host");
    const safeId = String(layer.id).replace(/"/g, "\\\"");
    const item = host?.querySelector(`.editor-media-layer-item[data-id="${safeId}"]`);
    if (item) {
        item.style.left = `${nextLeft}px`;
        item.style.top = `${nextTop}px`;
        if (drag.mode === "resize") {
            item.style.width = `${nextWidth}px`;
            item.style.height = `${nextHeight}px`;
        }
    }

    _editorRenderProps();
    e.preventDefault();
}

function _editorOnMediaLayerDragEnd() {
    if (!_editorMediaLayerDrag) return;
    _editorMediaLayerDrag = null;
    _editorRenderMediaLayers();
    _editorRenderProps();
}

async function _editorUploadProjectImages(input) {
    const files = Array.from(input.files || []);
    if (!files.length) return;
    if (!_editor.projectId) {
        input.value = "";
        showToast("Abra um projeto no editor antes de enviar imagens.", "error");
        return;
    }

    try {
        const formData = new FormData();
        files.forEach((file) => formData.append("images", file));
        showToast(`Enviando ${files.length} imagem(ns) para o projeto...`);
        const payload = await apiForm(`/video/projects/${_editor.projectId}/images`, formData, { method: "POST" });
        const savedCount = Math.max(0, Number(payload?.saved_count || 0));
        if (savedCount > 0) {
            showToast(`${savedCount} imagem(ns) enviada(s) para este projeto.`, "success");
        } else {
            showToast("Nenhuma imagem valida foi enviada.", "error");
        }
    } catch (err) {
        showToast("Erro ao enviar imagens: " + err.message, "error");
    } finally {
        input.value = "";
    }
}
window._editorUploadProjectImages = _editorUploadProjectImages;

// ---------- Open editor for a project ----------
async function openEditor(projectId) {
    try {
        const detail = await api(`/video/projects/${projectId}`);
        const render = _pickLatestAvailableRender(detail.renders || []);
        if (!render || !render.video_url) {
            showToast("Este vídeo não tem arquivo disponível.", "error");
            return;
        }
        // Reset editor state
        _editor.projectId = projectId;
        _editor.videoUrl = render.video_url;
        _editor.sourceAspectRatio = ["9:16", "16:9", "1:1"].includes(detail.aspect_ratio) ? detail.aspect_ratio : "9:16";
        _editor.outputAspectRatio = "source";
        _editor.playing = false;
        _editor.activeTool = "text";
        _editor.subtitleListOpen = false;
        _editor.selectedClip = { kind: "", id: "", track: "" };
        _editor.texts = [];
        _editor.subtitles = [];
        _editor.videoSegments = [];
        _editor.audioSegments = [];
        _editor.selectedTracks = ["video"];
        _editor.trimStart = 0;
        _editor.trimEnd = 0;
        _editor.musicUrl = "";
        _editor._musicFile = null;
        _editor._musicServerPath = "";
        _editor._musicSource = "audio";
        _editorSetMusicPreviewSource("");
        _editor.musicVolume = 80;
        _editor.originalVolume = 100;
        _editor.filter = "none";
        _editor.stickers = [];
        _editor.mediaLayers = [];
        _editor.quality = "original";
        _editor.undoStack = [];
        _editor.redoStack = [];
        _editor._nextId = 1;

        document.getElementById("editor-select-view").hidden = true;
        document.getElementById("editor-workspace").hidden = false;
        document.getElementById("editor-project-name").textContent = detail.title || "Projeto";

        const video = document.getElementById("editor-video");
        video.src = _editor.videoUrl;
        video.load();
        video.onloadedmetadata = () => {
            _editor.duration = video.duration;
            _editorInitVideoSegments();
            if (!["9:16", "16:9", "1:1"].includes(detail.aspect_ratio)) {
                _editor.sourceAspectRatio = video.videoWidth >= video.videoHeight ? "16:9" : "9:16";
            }
            _editorApplyAspectRatio();
            _editorRenderMediaLayers();
            document.getElementById("editor-time-total").textContent = _fmtTime(video.duration);
            _editorRefreshQuickActions();
            _editorRenderTimeline();
            _editorSelectTool("text");
        };
        _updateUndoRedoBtns();
    } catch (err) {
        showToast("Erro ao abrir editor: " + err.message, "error");
    }
}
window.openEditor = openEditor;

// ---------- Close editor ----------
function closeEditor() {
    document.getElementById("editor-select-view").hidden = false;
    document.getElementById("editor-workspace").hidden = true;
    const video = document.getElementById("editor-video");
    video.pause();
    video.removeAttribute("src");
    _editorSetMusicPreviewSource("");
    const pp = document.getElementById("editor-props-panel");
    if (pp) {
        pp.classList.remove("open");
        pp.style.removeProperty("display");
    }
    const wrapper = document.getElementById("editor-canvas-wrapper");
    if (wrapper) wrapper.style.removeProperty("--editor-aspect-ratio");
    const layerHost = document.getElementById("editor-media-layer-host");
    if (layerHost) layerHost.innerHTML = "";
    _editor.selectedClip = { kind: "", id: "", track: "" };
    _editorRefreshQuickActions();
    _editorRefreshTrackSelectionUI();
    _editor.playing = false;
}

// ---------- Format time ----------
function _fmtTime(sec) {
    if (!sec || isNaN(sec)) return "00:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}

// ---------- Play/Pause ----------
function _editorTogglePlay() {
    const video = document.getElementById("editor-video");
    if (!video.src) return;
    if (video.paused) {
        const sorted = [..._editor.videoSegments].sort((a, b) => a.start - b.start);
        const first = sorted[0];
        if (first && video.currentTime < first.start) {
            video.currentTime = first.start;
        }
        if (sorted.length) {
            const inSegment = sorted.some(seg => video.currentTime >= seg.start && video.currentTime <= seg.end);
            if (!inSegment) {
                const next = sorted.find(seg => seg.start > video.currentTime);
                video.currentTime = (next || sorted[0]).start;
            }
        }
        _editorSetMusicPreviewSource(_editor.musicUrl || "");
        video.play();
        _editor.playing = true;
        _editorSyncMusicPreviewPlayback(video.currentTime, true);
        _editorSyncMediaLayersWithTime(video.currentTime);
    } else {
        video.pause();
        _editor.playing = false;
        _editorSyncMusicPreviewPlayback(video.currentTime, false);
        _editorSyncMediaLayersWithTime(video.currentTime);
    }
    _updatePlayIcon();
}

function _updatePlayIcon() {
    const icon = document.getElementById("editor-play-icon");
    if (_editor.playing) {
        icon.innerHTML = '<rect x="6" y="4" width="4" height="16" fill="currentColor"/><rect x="14" y="4" width="4" height="16" fill="currentColor"/>';
    } else {
        icon.innerHTML = '<polygon points="6 3 20 12 6 21 6 3"/>';
    }
}

function _editorResetPlaybackToStart() {
    const video = document.getElementById("editor-video");
    if (!video) return;

    video.pause();
    video.currentTime = 0;
    _editor.playing = false;
    _editorSyncMusicPreviewPlayback(0, false);
    _updatePlayIcon();

    document.getElementById("editor-time-current").textContent = _fmtTime(0);
    _editorMovePlayhead(0);
    _editorDrawOverlays(0);
    _editorSyncMediaLayersWithTime(0);
}

// ---------- Time update ----------
function _editorTimeUpdate() {
    const video = document.getElementById("editor-video");
    const t = video.currentTime;
    document.getElementById("editor-time-current").textContent = _fmtTime(t);

    // Enforce segment boundaries: skip removed gaps and stop after last segment.
    if (_editor.videoSegments.length) {
        const sorted = [..._editor.videoSegments].sort((a, b) => a.start - b.start);
        const last = sorted[sorted.length - 1];
        if (last && t >= (last.end - 0.02)) {
            _editorResetPlaybackToStart();
            return;
        }

        if (_editor.playing) {
            const inSegment = sorted.some(seg => t >= seg.start && t < seg.end);
            if (!inSegment) {
                const next = sorted.find(seg => seg.start > t);
                if (next) {
                    video.currentTime = next.start;
                    _editorSyncMusicPreviewPlayback(next.start, true);
                    return;
                }
            }
        }
    } else if (_editor.trimEnd > 0 && t >= _editor.trimEnd) {
        _editorResetPlaybackToStart();
        return;
    }
    // Move playhead
    _editorMovePlayhead(t);
    // Draw overlays
    _editorDrawOverlays(t);
    _editorSyncMusicPreviewPlayback(t, _editor.playing && !video.paused);
    _editorSyncMediaLayersWithTime(t);
}

function _editorMovePlayhead(t) {
    const playhead = document.getElementById("editor-timeline-playhead");
    if (!playhead || !_editor.duration) return;
    const trackWidth = document.getElementById("editor-track-video")?.offsetWidth || 600;
    const safeTime = Math.max(0, Math.min(_editor.duration, t || 0));
    const pct = _editor.duration > 0 ? (safeTime / _editor.duration) : 0;
    const x = Math.max(0, Math.min(trackWidth - 2, pct * trackWidth));
    playhead.style.left = (80 + x) + "px";
}

function _editorClampToVideoSegments(timeSec) {
    if (!_editor.videoSegments.length) {
        return Math.max(0, Math.min(_editor.duration || 0, timeSec));
    }
    const sorted = [..._editor.videoSegments].sort((a, b) => a.start - b.start);
    let t = Math.max(0, Math.min(_editor.duration || 0, timeSec));
    const inside = sorted.find(seg => t >= seg.start && t <= seg.end);
    if (inside) return t;
    const next = sorted.find(seg => seg.start > t);
    if (next) return next.start;
    return sorted[sorted.length - 1].end;
}

function _editorSeekByClientX(clientX) {
    const trackContent = document.getElementById("editor-track-video");
    const video = document.getElementById("editor-video");
    if (!trackContent || !video || !_editor.duration) return;

    const rect = trackContent.getBoundingClientRect();
    const x = Math.max(0, Math.min(clientX - rect.left, rect.width));
    const pct = rect.width > 0 ? (x / rect.width) : 0;
    const rawTime = pct * _editor.duration;
    const nextTime = _editorClampToVideoSegments(rawTime);

    video.currentTime = nextTime;
    document.getElementById("editor-time-current").textContent = _fmtTime(nextTime);
    _editorMovePlayhead(nextTime);
    _editorDrawOverlays(nextTime);
    _editorSyncMusicPreviewPlayback(nextTime, _editor.playing && !video.paused);
    _editorSyncMediaLayersWithTime(nextTime);
}

function _editorStartTimelineScrub(event) {
    if (!_editor.duration) return false;
    if (event.button !== undefined && event.button !== 0) return false;
    if (event.target.closest(".editor-track-clip")) return false;
    if (event.target.closest(".editor-track-label")) return false;

    _editorTimelineScrub = { active: true };
    _editorSeekByClientX(event.clientX);
    document.addEventListener("pointermove", _editorOnTimelineScrubMove);
    document.addEventListener("pointerup", _editorOnTimelineScrubEnd, { once: true });
    return true;
}

function _editorOnTimelineScrubMove(event) {
    if (!_editorTimelineScrub?.active) return;
    _editorSeekByClientX(event.clientX);
}

function _editorOnTimelineScrubEnd() {
    document.removeEventListener("pointermove", _editorOnTimelineScrubMove);
    _editorTimelineScrub = null;
}

// ---------- Draw canvas overlays (texts, stickers, subtitles) ----------
function _editorDrawOverlays(t) {
    const canvas = document.getElementById("editor-overlay-canvas");
    const wrapper = document.getElementById("editor-canvas-wrapper");
    if (!canvas || !wrapper) return;
    canvas.width = wrapper.offsetWidth;
    canvas.height = wrapper.offsetHeight;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Apply filter to video element
    const video = document.getElementById("editor-video");
    video.style.filter = _getCSSFilter(_editor.filter);

    // Draw texts
    for (const txt of _editor.texts) {
        if (t >= txt.startTime && t <= txt.endTime) {
            const fs = txt.fontSize * (canvas.height / 720);
            let fontStr = "";
            if (txt.italic) fontStr += "italic ";
            if (txt.bold) fontStr += "bold ";
            fontStr += fs + "px " + (txt.fontFamily || "Manrope, sans-serif");
            ctx.font = fontStr;
            ctx.fillStyle = txt.color || "#ffffff";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            // Shadow for readability
            ctx.shadowColor = "rgba(0,0,0,0.7)";
            ctx.shadowBlur = 4;
            ctx.shadowOffsetX = 1;
            ctx.shadowOffsetY = 1;
            const x = (txt.x / 100) * canvas.width;
            const y = (txt.y / 100) * canvas.height;
            ctx.fillText(txt.content, x, y);
            ctx.shadowColor = "transparent";
        }
    }

    // Draw subtitles
    for (const sub of _editor.subtitles) {
        if (t >= sub.startTime && t <= sub.endTime) {
            const scale = canvas.height / 720;
            const fs = (sub.fontSize || 28) * scale;
            let fontStr = "";
            if (sub.italic) fontStr += "italic ";
            if (sub.bold) fontStr += "bold ";
            fontStr += fs + "px " + (sub.fontFamily || "Arial, sans-serif");
            ctx.font = fontStr;
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            const sx = (sub.x / 100) * canvas.width;
            const sy = (sub.y / 100) * canvas.height;
            const textW = ctx.measureText(sub.text).width;
            // Background box
            if (sub.bgColor) {
                ctx.fillStyle = sub.bgColor;
                const pad = 8 * scale;
                const radius = 6 * scale;
                const bx = sx - textW / 2 - pad;
                const by = sy - fs / 2 - pad * 0.6;
                const bw = textW + pad * 2;
                const bh = fs + pad * 1.2;
                ctx.beginPath();
                ctx.roundRect(bx, by, bw, bh, radius);
                ctx.fill();
            }
            // Outline
            if (sub.outlineColor) {
                ctx.strokeStyle = sub.outlineColor;
                ctx.lineWidth = Math.max(2, fs * 0.08);
                ctx.lineJoin = "round";
                ctx.strokeText(sub.text, sx, sy);
            }
            // Fill text
            ctx.fillStyle = sub.fontColor || "#ffffff";
            ctx.shadowColor = "rgba(0,0,0,0.6)";
            ctx.shadowBlur = 4;
            ctx.shadowOffsetX = 1;
            ctx.shadowOffsetY = 1;
            ctx.fillText(sub.text, sx, sy);
            ctx.shadowColor = "transparent";
        }
    }

    // Draw stickers
    for (const st of _editor.stickers) {
        if (t >= st.startTime && t <= st.endTime) {
            const size = (st.size || 48) * (canvas.height / 720);
            const x = (st.x / 100) * canvas.width;
            const y = (st.y / 100) * canvas.height;
            ctx.font = size + "px serif";
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(st.emoji, x, y);
        }
    }
}

function _editorFindSubtitleAtCanvasPoint(clientX, clientY) {
    const canvas = document.getElementById("editor-overlay-canvas");
    if (!canvas || !_editor.subtitles.length) return null;

    const video = document.getElementById("editor-video");
    if (!video) return null;
    const currentTime = Number(video.currentTime || 0);

    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    if (x < 0 || y < 0 || x > rect.width || y > rect.height) return null;

    const ctx = canvas.getContext("2d");
    if (!ctx) return null;

    const activeSubs = _editor.subtitles.filter(sub => currentTime >= sub.startTime && currentTime <= sub.endTime);
    if (!activeSubs.length) return null;

    for (let i = activeSubs.length - 1; i >= 0; i -= 1) {
        const sub = activeSubs[i];
        const scale = canvas.height / 720;
        const fs = (sub.fontSize || 28) * scale;
        let fontStr = "";
        if (sub.italic) fontStr += "italic ";
        if (sub.bold) fontStr += "bold ";
        fontStr += fs + "px " + (sub.fontFamily || "Arial, sans-serif");
        ctx.font = fontStr;

        const sx = (sub.x / 100) * canvas.width;
        const sy = (sub.y / 100) * canvas.height;
        const textW = ctx.measureText(sub.text || "").width;
        const pad = sub.bgColor ? 8 * scale : 4 * scale;

        const left = sx - textW / 2 - pad;
        const right = sx + textW / 2 + pad;
        const top = sy - fs / 2 - pad * 0.7;
        const bottom = sy + fs / 2 + pad * 0.7;

        if (x >= left && x <= right && y >= top && y <= bottom) {
            return sub;
        }
    }

    return null;
}

function _editorHandleOverlayClick(event) {
    const hitSubtitle = _editorFindSubtitleAtCanvasPoint(event.clientX, event.clientY);
    if (!hitSubtitle) return;

    event.preventDefault();
    event.stopPropagation();
    _editorSelectSubtitle(hitSubtitle.id, true);
}

function _getCSSFilter(name) {
    const filters = {
        none: "none",
        grayscale: "grayscale(1)",
        sepia: "sepia(0.8)",
        warm: "saturate(1.3) brightness(1.05) hue-rotate(-10deg)",
        cool: "saturate(0.9) brightness(1.05) hue-rotate(15deg)",
        vintage: "sepia(0.4) contrast(1.1) brightness(0.95)",
        vivid: "saturate(1.6) contrast(1.1)",
        dramatic: "contrast(1.4) brightness(0.9) saturate(0.8)",
        fade: "brightness(1.1) saturate(0.7) contrast(0.9)",
        noir: "grayscale(1) contrast(1.3) brightness(0.85)",
        cinematic: "contrast(1.15) saturate(1.1) brightness(0.95) sepia(0.1)",
        retro: "sepia(0.5) hue-rotate(-15deg) saturate(1.2)",
    };
    return filters[name] || "none";
}

// ---------- Tool selection ----------
function _editorSelectTool(toolName) {
    _editor.activeTool = toolName;
    document.querySelectorAll(".editor-tool-btn").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.tool === toolName);
    });

    const overlayCanvas = document.getElementById("editor-overlay-canvas");
    if (overlayCanvas) {
        overlayCanvas.style.pointerEvents = toolName === "layers" ? "none" : "auto";
    }

    if (toolName === "layers") {
        _editorRenderMediaLayers();
    }

    _editorRenderProps();
    // On mobile, toggle props panel
    const pp = document.getElementById("editor-props-panel");
    if (pp && window.innerWidth <= 768) {
        pp.classList.toggle("open", true);
        pp.style.display = "block";
    } else if (pp) {
        pp.style.removeProperty("display");
    }
}

// ---------- Render properties panel based on tool ----------
function _editorRenderProps() {
    const container = document.getElementById("editor-props-content");
    if (!container) return;
    const tool = _editor.activeTool;

    if (tool === "text") {
        container.innerHTML = `
            <div class="editor-props-title">Textos</div>
            <button class="editor-add-btn" onclick="_editorAddText()">+ Adicionar texto</button>
            <div id="editor-text-list" class="editor-props-group">
                ${_editor.texts.map(t => `
                    <div class="editor-subtitle-item${t._selected ? ' active' : ''}" onclick="_editorSelectText(${t.id})">
                        <span class="sub-time">${_fmtTime(t.startTime)}-${_fmtTime(t.endTime)}</span>
                        <span class="sub-text">${esc(t.content)}</span>
                        <button class="sub-delete" onclick="event.stopPropagation();_editorDeleteText(${t.id})">✕</button>
                    </div>
                `).join("")}
            </div>
            ${_editorTextEditForm()}
        `;
    } else if (tool === "subtitles") {
        const isGenerating = _editor._subtitleGenerating;
        const hasSubs = _editor.subtitles.length > 0;
        const selectedSub = _editor.subtitles.find(s => s._selected) || _editor.subtitles[0] || null;
        const subtitleY = Math.round(selectedSub?.y ?? 82);
        const subtitleSize = Math.round(selectedSub?.fontSize ?? 28);
        container.innerHTML = `
            <div class="editor-props-title">Legendas</div>
            <button class="editor-add-btn" onclick="_editorAutoSubtitles()" ${isGenerating ? "disabled" : ""}>
                ${isGenerating
                    ? '<div class="spinner-small" style="width:14px;height:14px"></div> Gerando legendas...'
                    : '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"/></svg> Gerar legendas automaticas'}
            </button>
            <button class="editor-add-btn" onclick="_editorAddSubtitle()" style="margin-top:4px">+ Adicionar legenda manual</button>
            ${hasSubs ? `
                <button class="editor-add-btn" onclick="_editorClearSubtitles()" style="margin-top:4px;border-color:rgba(239,68,68,0.3);color:#ef4444">Limpar todas</button>
            ` : ""}
            ${hasSubs ? `
                <div class="editor-sub-toolbar" style="margin-top:8px">
                    <span class="editor-sub-count">${_editor.subtitles.length} trecho(s) gerado(s)</span>
                    <button
                        class="editor-sub-icon-btn${_editor.subtitleListOpen ? " active" : ""}"
                        onclick="_editorToggleSubtitleList()"
                        title="Editar trechos"
                        aria-label="Editar trechos"
                    >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M12 20h9"/>
                            <path d="M16.5 3.5a2.12 2.12 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/>
                        </svg>
                    </button>
                </div>
                <div class="editor-sub-quick-card">
                    <div class="editor-sub-quick-row">
                        <span class="editor-sub-quick-label">Posicao</span>
                        <div class="editor-sub-stepper">
                            <button type="button" onclick="_editorNudgeSubtitlesY(-2)" aria-label="Subir legenda">↑</button>
                            <input type="range" min="5" max="95" value="${subtitleY}" oninput="_editorSetSubtitlesY(this.value, true)">
                            <button type="button" onclick="_editorNudgeSubtitlesY(2)" aria-label="Descer legenda">↓</button>
                        </div>
                        <span class="editor-sub-quick-value" id="editor-sub-global-y-value">${subtitleY}%</span>
                    </div>
                    <div class="editor-sub-quick-row">
                        <span class="editor-sub-quick-label">Tamanho</span>
                        <div class="editor-sub-stepper">
                            <button type="button" onclick="_editorNudgeSubtitlesSize(-2)" aria-label="Reduzir legenda">-</button>
                            <input type="range" min="14" max="72" value="${subtitleSize}" oninput="_editorSetSubtitlesFontSize(this.value, true)">
                            <button type="button" onclick="_editorNudgeSubtitlesSize(2)" aria-label="Aumentar legenda">+</button>
                        </div>
                        <span class="editor-sub-quick-value" id="editor-sub-global-size-value">${subtitleSize}px</span>
                    </div>
                </div>
            ` : ""}
            ${hasSubs && _editor.subtitleListOpen ? `
                <div class="editor-props-group" id="editor-subtitle-list" style="margin-top:8px;max-height:200px;overflow-y:auto">
                    ${_editor.subtitles.map(s => `
                        <div class="editor-subtitle-item${s._selected ? ' active' : ''}" onclick="_editorSelectSubtitle(${s.id})">
                            <span class="sub-time">${_fmtTime(s.startTime)}-${_fmtTime(s.endTime)}</span>
                            <span class="sub-text">${esc(s.text)}</span>
                            <button class="sub-delete" onclick="event.stopPropagation();_editorDeleteSubtitle(${s.id})">✕</button>
                        </div>
                    `).join("")}
                </div>
            ` : ""}
            ${hasSubs ? `
                <div class="editor-props-title" style="margin-top:12px">Estilos</div>
                <div class="editor-subtitle-styles-grid" id="editor-sub-styles-grid">
                    ${SUBTITLE_STYLES.map(st => `
                        <div class="editor-sub-style-card${(_editor.subtitles.find(s=>s._selected)||{}).styleName === st.name ? ' active' : ''}" onclick="_editorApplySubStyle('${st.name}')">
                            <div class="editor-sub-style-preview" style="font-family:${st.fontFamily};color:${st.fontColor};font-size:11px;font-weight:${st.bold?'bold':'normal'};font-style:${st.italic?'italic':'normal'};${st.bgColor?'background:'+st.bgColor+';padding:2px 4px;border-radius:3px;':''}${st.outlineColor?'text-shadow:-1px -1px 0 '+st.outlineColor+',1px -1px 0 '+st.outlineColor+',-1px 1px 0 '+st.outlineColor+',1px 1px 0 '+st.outlineColor+';':''}">Abc</div>
                            <span>${st.label}</span>
                        </div>
                    `).join("")}
                </div>
            ` : ""}
            ${_editor.subtitleListOpen ? _editorSubtitleEditForm() : ""}
        `;
    } else if (tool === "trim") {
        const hasExternalAudio = _editorShouldShowAudioTrack();
        const selectedSeg = _editor.selectedClip.kind === "segment"
            ? _editorFindSegment(_editor.selectedClip.track || "video", _editor.selectedClip.id)
            : null;
        const segInfo = selectedSeg
            ? `${_fmtTime(selectedSeg.start)} - ${_fmtTime(selectedSeg.end)} (${_fmtTime(selectedSeg.end - selectedSeg.start)})`
            : "Nenhum trecho selecionado";
        const selectedTracksLabel = _editorGetSelectedSegmentTracks()
            .map(track => track === "video" ? "Video" : "Audio")
            .join(" + ");
        const tracksSummary = hasExternalAudio
            ? `Trechos: Video ${_editor.videoSegments.length} | Audio ${_editor.audioSegments.length}`
            : `Trechos: Video ${_editor.videoSegments.length}`;
        const trimHint = hasExternalAudio
            ? "Marque as faixas Video/Audio na timeline. O corte e ajuste serao aplicados somente nas faixas marcadas."
            : "Sem audio externo, o audio original acompanha os cortes do video automaticamente.";
        const selectedVolumeTracks = _editorGetSelectedSegmentTracks().filter(track => track === "video" || (track === "audio" && hasExternalAudio));
        const volumeControlsHtml = selectedVolumeTracks.map((track) => {
            const isVideo = track === "video";
            const volumePct = isVideo
                ? Math.max(0, Math.min(100, Number(_editor.originalVolume || 0)))
                : Math.max(0, Math.min(100, Number(_editor.musicVolume || 0)));
            const trackLabel = isVideo ? "Video original" : "Audio externo";
            return `
                <div class="editor-track-props-volume-item">
                    <div class="editor-track-props-volume-head">
                        <span class="editor-track-props-volume-name-wrap">
                            <span class="editor-track-props-volume-icon">${_editorTimelineVolumeIcon(track)}</span>
                            <span class="editor-track-props-volume-name">${trackLabel}</span>
                        </span>
                        <span class="editor-track-props-volume-value" id="editor-track-vol-label-${track}">${volumePct}%</span>
                    </div>
                    <input
                        id="editor-track-vol-input-${track}"
                        class="editor-track-props-volume-slider"
                        type="range"
                        min="0"
                        max="100"
                        value="${volumePct}"
                        oninput="_editorSetTrackVolumeFromTrim('${track}', this.value)"
                    >
                </div>
            `;
        }).join("");
        container.innerHTML = `
            <div class="editor-props-title">Cortar video</div>
            <p style="font-size:11px;color:var(--text-muted);margin-bottom:8px">${trimHint}</p>
            <div class="editor-trim-range" style="display:grid;gap:8px">
                <button class="editor-add-btn" type="button" onclick="_editorSplitAtCurrentTime()">Cortar no ponto atual</button>
                <button class="editor-add-btn" type="button" onclick="_editorResetVideoSegments()" style="background:rgba(255,255,255,0.04)">Restaurar video inteiro</button>
                <div class="editor-trim-values">
                    <span>${tracksSummary}</span>
                    <span style="margin-left:10px">Selecionado: ${segInfo}</span>
                </div>
                <div class="editor-trim-values">
                    <span>Faixas marcadas: ${selectedTracksLabel}</span>
                </div>
                ${selectedVolumeTracks.length ? `
                    <div class="editor-props-group editor-track-props-volume-group">
                        <label>Volume das faixas selecionadas</label>
                        <div class="editor-track-props-volume-list">${volumeControlsHtml}</div>
                    </div>
                ` : ""}
            </div>
        `;
    } else if (tool === "music") {
        container.innerHTML = `
            <div class="editor-props-title">Audio</div>
            <div class="editor-props-group" style="margin-top:12px">
                <button class="editor-add-btn" onclick="document.getElementById('editor-music-upload').click()">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                    Enviar arquivo de audio
                </button>
                <input type="file" id="editor-music-upload" accept="audio/*" hidden onchange="_editorUploadMusic(this)">
                <button class="editor-add-btn" style="margin-top:8px" onclick="document.getElementById('editor-music-video-upload').click()">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="5" width="15" height="14" rx="2"/><polygon points="22 7 16 12 22 17 22 7"/></svg>
                    Extrair audio de video
                </button>
                <input type="file" id="editor-music-video-upload" accept="video/*" hidden onchange="_editorUploadVideoForMusic(this)">
                ${_editor.musicUrl ? `
                    <div class="editor-music-current">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
                        <div class="editor-music-info">Audio adicionado<small>${_editor._musicSource === "video" ? "Extraido de video enviado" : "Arquivo de audio carregado"}</small></div>
                        <button class="sub-delete" onclick="_editorRemoveMusic()">✕</button>
                    </div>
                    <label>Volume do audio</label>
                    <div class="editor-volume-row">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
                        <input type="range" min="0" max="100" value="${_editor.musicVolume}" oninput="_editorSetMusicVolume(this.value)">
                        <span id="editor-music-vol-label">${_editor.musicVolume}%</span>
                    </div>
                ` : ""}
            </div>
        `;
    } else if (tool === "layers") {
        const selectedLayer = _editor.selectedClip.kind === "media-layer"
            ? _editorGetMediaLayerById(_editor.selectedClip.id)
            : null;
        const orderedLayers = [..._editor.mediaLayers].reverse();

        container.innerHTML = `
            <div class="editor-props-title">Camadas</div>
            <div class="editor-props-group" style="margin-top:12px">
                <div style="display:grid;gap:8px;grid-template-columns:repeat(2,minmax(0,1fr))">
                    <button class="editor-add-btn" type="button" onclick="document.getElementById('editor-layer-video-upload-input').click()">
                        + Video
                    </button>
                    <button class="editor-add-btn" type="button" onclick="document.getElementById('editor-layer-image-upload-input').click()">
                        + Imagem
                    </button>
                </div>

                ${orderedLayers.length ? `
                    <div class="editor-layer-list" style="margin-top:10px">
                        ${orderedLayers.map((layer, idx) => `
                            <div class="editor-layer-list-item${selectedLayer && String(selectedLayer.id) === String(layer.id) ? ' active' : ''}" onclick="_editorSelectMediaLayer('${layer.id}')">
                                <span>${esc(layer.kind === 'video' ? 'Video' : 'Imagem')} ${orderedLayers.length - idx}</span>
                                <button class="sub-delete" onclick="event.stopPropagation();_editorDeleteMediaLayer('${layer.id}')">✕</button>
                            </div>
                        `).join("")}
                    </div>
                ` : '<p style="margin-top:8px;font-size:11px;color:var(--text-muted)">Envie video/imagem para adicionar uma camada acima do video base.</p>'}
            </div>

            ${selectedLayer ? `
                <div class="editor-props-title" style="margin-top:12px">Editar camada</div>
                <div class="editor-props-group">
                    ${selectedLayer.kind === 'video' ? `
                        <label>Volume (<span id="editor-layer-vol-label">${Math.round(selectedLayer.volume ?? 100)}%</span>)</label>
                        <input type="range" min="0" max="200" value="${Math.round(selectedLayer.volume ?? 100)}" oninput="_editorSetMediaLayerVolume('${selectedLayer.id}', this.value)">
                        <button class="editor-add-btn" type="button" style="margin-top:8px" onclick="_editorToggleMediaLayerAudioOnly('${selectedLayer.id}')">
                            ${selectedLayer.audioOnly ? 'Mostrar video + audio' : 'Usar somente audio da camada'}
                        </button>
                    ` : '<p style="font-size:11px;color:var(--text-muted)">Camada de imagem não possui ajuste de volume.</p>'}

                    <button class="editor-add-btn" type="button" style="margin-top:10px;border-color:rgba(239,68,68,.35);color:#ef4444" onclick="_editorDeleteMediaLayer('${selectedLayer.id}')">
                        Remover camada
                    </button>
                </div>
            ` : ''}
        `;
    } else if (tool === "filters") {
        const filterNames = ["none","grayscale","sepia","warm","cool","vintage","vivid","dramatic","fade","noir","cinematic","retro"];
        const filterLabels = {"none":"Original","grayscale":"P&B","sepia":"Sepia","warm":"Quente","cool":"Frio","vintage":"Vintage","vivid":"Vivido","dramatic":"Dramatico","fade":"Desbotado","noir":"Noir","cinematic":"Cinema","retro":"Retro"};
        container.innerHTML = `
            <div class="editor-props-title">Filtros</div>
            <div class="editor-filter-grid">
                ${filterNames.map(f => `
                    <div class="editor-filter-card${_editor.filter === f ? ' active' : ''}" onclick="_editorSetFilter('${f}')">
                        <div class="editor-filter-preview" style="filter:${_getCSSFilter(f)}"></div>
                        <span>${filterLabels[f]}</span>
                    </div>
                `).join("")}
            </div>
        `;
    } else if (tool === "stickers") {
        const emojis = ["😀","😂","🥰","😎","🔥","⭐","❤️","👍","🎉","🎵","💯","👏","🤩","💪","✨","🌟","😍","🥳","💥","🎬","📸","🎶","💡","🚀","👑","🏆","💎","🌈","🎯","🙏","😱","🤯","💰","📢","🎭","🎨","🎸","🎤","🎧","👀","💬","🔔","⚡","🌺","🦋","🐾","🍕","☕","🎮","🎁"];
        container.innerHTML = `
            <div class="editor-props-title">Stickers & Emojis</div>
            <div class="editor-sticker-grid">
                ${emojis.map(e => `<div class="editor-sticker-item" onclick="_editorAddSticker('${e}')">${e}</div>`).join("")}
            </div>
            ${_editor.stickers.length ? `
                <div class="editor-props-title" style="margin-top:12px">Adicionados</div>
                <div class="editor-props-group">
                    ${_editor.stickers.map(s => `
                        <div class="editor-subtitle-item">
                            <span style="font-size:20px">${s.emoji}</span>
                            <span class="sub-time">${_fmtTime(s.startTime)}-${_fmtTime(s.endTime)}</span>
                            <button class="sub-delete" onclick="_editorDeleteSticker(${s.id})">✕</button>
                        </div>
                    `).join("")}
                </div>
            ` : ""}
        `;
    } else if (tool === "quality") {
        const qualities = [
            {val: "original", label: "Original", desc: "Manter qualidade atual do video"},
            {val: "enhance", label: "Melhorar", desc: "IA aprimora nitidez e cores"},
            {val: "hd", label: "HD 720p", desc: "Reescalar para 720p"},
            {val: "fullhd", label: "Full HD 1080p", desc: "Reescalar para 1080p"},
        ];
        container.innerHTML = `
            <div class="editor-props-title">Qualidade</div>
            <div class="editor-props-group">
                ${qualities.map(q => `
                    <div class="editor-quality-option${_editor.quality === q.val ? ' active' : ''}" onclick="_editorSetQuality('${q.val}')">
                        <div>
                            <div class="editor-quality-label">${q.label}</div>
                            <div class="editor-quality-desc">${q.desc}</div>
                        </div>
                    </div>
                `).join("")}
            </div>
        `;
    }
}

// Text edit form
function _editorTextEditForm() {
    const sel = _editor.texts.find(t => t._selected);
    if (!sel) return "";
    return `
        <div class="editor-props-group" style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border)">
            <label>Conteudo</label>
            <textarea rows="2" style="resize:vertical" oninput="_editorUpdateTextProp(${sel.id},'content',this.value)">${esc(sel.content)}</textarea>
            <label>Cor</label>
            <div class="editor-color-row">
                <input type="color" value="${sel.color}" oninput="_editorUpdateTextProp(${sel.id},'color',this.value)">
                <span style="font-size:11px;color:var(--text-muted)">${sel.color}</span>
            </div>
            <label>Tamanho da fonte</label>
            <div class="editor-font-size-row">
                <input type="range" min="12" max="120" value="${sel.fontSize}" oninput="_editorUpdateTextProp(${sel.id},'fontSize',parseInt(this.value))">
                <span>${sel.fontSize}px</span>
            </div>
            <label>Posicao vertical (%)</label>
            <div class="editor-font-size-row">
                <input type="range" min="5" max="95" value="${sel.y}" oninput="_editorUpdateTextProp(${sel.id},'y',parseInt(this.value))">
                <span>${sel.y}%</span>
            </div>
            <label>Tempo</label>
            <div class="editor-trim-values">
                <span>Inicio: ${_fmtTime(sel.startTime)}</span>
                <span>Fim: ${_fmtTime(sel.endTime)}</span>
            </div>
            <div class="editor-font-size-row">
                <input type="range" min="0" max="${_editor.duration}" step="0.1" value="${sel.startTime}" oninput="_editorUpdateTextProp(${sel.id},'startTime',parseFloat(this.value))">
            </div>
            <div class="editor-font-size-row">
                <input type="range" min="0" max="${_editor.duration}" step="0.1" value="${sel.endTime}" oninput="_editorUpdateTextProp(${sel.id},'endTime',parseFloat(this.value))">
            </div>
            <div style="display:flex;gap:8px">
                <label style="display:flex;align-items:center;gap:4px"><input type="checkbox" ${sel.bold ? "checked" : ""} onchange="_editorUpdateTextProp(${sel.id},'bold',this.checked)"> Negrito</label>
                <label style="display:flex;align-items:center;gap:4px"><input type="checkbox" ${sel.italic ? "checked" : ""} onchange="_editorUpdateTextProp(${sel.id},'italic',this.checked)"> Italico</label>
            </div>
        </div>
    `;
}

// Subtitle edit form
function _editorSubtitleEditForm() {
    const sel = _editor.subtitles.find(s => s._selected);
    if (!sel) return "";
    return `
        <div class="editor-props-group" style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border)">
            <label>Texto da legenda</label>
            <textarea rows="2" style="resize:vertical" oninput="_editorUpdateSubProp(${sel.id},'text',this.value)">${esc(sel.text)}</textarea>
            <label>Cor do texto</label>
            <div class="editor-color-row">
                <input type="color" value="${sel.fontColor || '#ffffff'}" oninput="_editorUpdateSubProp(${sel.id},'fontColor',this.value)">
                <span style="font-size:11px;color:var(--text-muted)">${sel.fontColor || '#ffffff'}</span>
            </div>
            <label>Cor de fundo</label>
            <div class="editor-color-row">
                <input type="color" value="${(sel.bgColor||'').startsWith('rgba') ? '#000000' : (sel.bgColor || '#000000')}" oninput="_editorUpdateSubProp(${sel.id},'bgColor',this.value)">
                <label style="display:flex;align-items:center;gap:4px;font-size:11px;margin:0"><input type="checkbox" ${sel.bgColor ? 'checked' : ''} onchange="_editorUpdateSubProp(${sel.id},'bgColor',this.checked?'rgba(0,0,0,0.6)':'')"> Ativado</label>
            </div>
            <label>Contorno</label>
            <div class="editor-color-row">
                <input type="color" value="${sel.outlineColor || '#000000'}" oninput="_editorUpdateSubProp(${sel.id},'outlineColor',this.value)">
                <label style="display:flex;align-items:center;gap:4px;font-size:11px;margin:0"><input type="checkbox" ${sel.outlineColor ? 'checked' : ''} onchange="_editorUpdateSubProp(${sel.id},'outlineColor',this.checked?'#000000':'')"> Ativado</label>
            </div>
            <label>Tamanho da fonte</label>
            <div class="editor-font-size-row">
                <input type="range" min="14" max="72" value="${sel.fontSize || 28}" oninput="_editorUpdateSubProp(${sel.id},'fontSize',parseInt(this.value))">
                <span>${sel.fontSize || 28}px</span>
            </div>
            <label>Posicao vertical</label>
            <div class="editor-font-size-row">
                <input type="range" min="5" max="95" value="${sel.y || 85}" oninput="_editorUpdateSubProp(${sel.id},'y',parseInt(this.value))">
                <span>${sel.y || 85}%</span>
            </div>
            <label>Posicao horizontal</label>
            <div class="editor-font-size-row">
                <input type="range" min="10" max="90" value="${sel.x || 50}" oninput="_editorUpdateSubProp(${sel.id},'x',parseInt(this.value))">
                <span>${sel.x || 50}%</span>
            </div>
            <label>Fonte</label>
            <select onchange="_editorUpdateSubProp(${sel.id},'fontFamily',this.value)" style="background:rgba(255,255,255,0.06);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:6px 8px;font-size:12px">
                <option value="Arial, sans-serif" ${sel.fontFamily==="Arial, sans-serif"?"selected":""}>Arial</option>
                <option value="Arial Black, sans-serif" ${sel.fontFamily==="Arial Black, sans-serif"?"selected":""}>Arial Black</option>
                <option value="Manrope, sans-serif" ${sel.fontFamily==="Manrope, sans-serif"?"selected":""}>Manrope</option>
                <option value="Outfit, sans-serif" ${sel.fontFamily==="Outfit, sans-serif"?"selected":""}>Outfit</option>
                <option value="Georgia, serif" ${sel.fontFamily==="Georgia, serif"?"selected":""}>Georgia</option>
                <option value="Courier New, monospace" ${sel.fontFamily==="Courier New, monospace"?"selected":""}>Courier New</option>
                <option value="Times New Roman, serif" ${sel.fontFamily==="Times New Roman, serif"?"selected":""}>Times New Roman</option>
            </select>
            <div style="display:flex;gap:8px">
                <label style="display:flex;align-items:center;gap:4px"><input type="checkbox" ${sel.bold ? "checked" : ""} onchange="_editorUpdateSubProp(${sel.id},'bold',this.checked)"> Negrito</label>
                <label style="display:flex;align-items:center;gap:4px"><input type="checkbox" ${sel.italic ? "checked" : ""} onchange="_editorUpdateSubProp(${sel.id},'italic',this.checked)"> Italico</label>
            </div>
            <label>Tempo</label>
            <div class="editor-trim-values">
                <span>Inicio: ${_fmtTime(sel.startTime)}</span>
                <span>Fim: ${_fmtTime(sel.endTime)}</span>
            </div>
            <div class="editor-font-size-row">
                <input type="range" min="0" max="${_editor.duration}" step="0.1" value="${sel.startTime}" oninput="_editorUpdateSubProp(${sel.id},'startTime',parseFloat(this.value))">
            </div>
            <div class="editor-font-size-row">
                <input type="range" min="0" max="${_editor.duration}" step="0.1" value="${sel.endTime}" oninput="_editorUpdateSubProp(${sel.id},'endTime',parseFloat(this.value))">
            </div>
        </div>
    `;
}

// ---------- Text actions ----------
function _editorAddText() {
    _editorSaveState();
    const video = document.getElementById("editor-video");
    const t = video?.currentTime || 0;
    _editor.texts.forEach(x => x._selected = false);
    const newText = {
        id: _editorGenId(), content: "Seu texto aqui", startTime: t, endTime: Math.min(t + 5, _editor.duration),
        x: 50, y: 50, fontSize: 36, color: "#ffffff", fontFamily: "Manrope, sans-serif", bold: true, italic: false, _selected: true,
    };
    _editor.texts.push(newText);
    _editor.selectedClip = { kind: "text", id: String(newText.id) };
    _editorRefreshQuickActions();
    _editorRenderProps();
    _editorRenderTimeline();
}
window._editorAddText = _editorAddText;

function _editorSelectText(id) {
    _editor.texts.forEach(t => t._selected = (t.id === id));
    _editor.selectedClip = { kind: "text", id: String(id) };
    _editorRefreshQuickActions();
    _editorRenderProps();
}
window._editorSelectText = _editorSelectText;

function _editorDeleteText(id) {
    _editorSaveState();
    _editor.texts = _editor.texts.filter(t => t.id !== id);
    if (_editor.selectedClip.kind === "text" && _editor.selectedClip.id === String(id)) {
        _editor.selectedClip = { kind: "", id: "" };
    }
    _editorRefreshQuickActions();
    _editorRenderProps();
    _editorRenderTimeline();
}
window._editorDeleteText = _editorDeleteText;

function _editorUpdateTextProp(id, prop, val) {
    const t = _editor.texts.find(x => x.id === id);
    if (!t) return;
    t[prop] = val;
    // Re-render the form parts without full rebuild to avoid losing focus
    const video = document.getElementById("editor-video");
    if (video) _editorDrawOverlays(video.currentTime);
    _editorRenderTimeline();
}
window._editorUpdateTextProp = _editorUpdateTextProp;

// ---------- Subtitle actions ----------
function _editorAddSubtitle() {
    _editorSaveState();
    const video = document.getElementById("editor-video");
    const t = video?.currentTime || 0;
    _editor.subtitles.forEach(x => x._selected = false);
    const defStyle = SUBTITLE_STYLES[0];
    const newSub = {
        id: _editorGenId(), text: "Legenda aqui", startTime: t, endTime: Math.min(t + 3, _editor.duration),
        styleName: defStyle.name, x: 50, y: 82, fontSize: defStyle.fontSize,
        fontColor: defStyle.fontColor, bgColor: defStyle.bgColor, outlineColor: defStyle.outlineColor,
        fontFamily: defStyle.fontFamily, bold: defStyle.bold, italic: defStyle.italic, _selected: true,
    };
    _editor.subtitles.push(newSub);
    _editor.selectedClip = { kind: "subtitle", id: String(newSub.id) };
    _editorRefreshQuickActions();
    _editor.subtitleListOpen = false;
    _editorRenderProps();
    _editorRenderTimeline();
}
window._editorAddSubtitle = _editorAddSubtitle;

function _editorApplySubStyle(styleName) {
    const sel = _editor.subtitles.find(s => s._selected);
    if (!sel) {
        // Apply to all
        _editorSaveState();
        const st = _getSubStyle(styleName);
        _editor.subtitles.forEach(s => {
            s.styleName = st.name; s.fontSize = st.fontSize; s.fontColor = st.fontColor;
            s.bgColor = st.bgColor; s.outlineColor = st.outlineColor; s.fontFamily = st.fontFamily;
            s.bold = st.bold; s.italic = st.italic;
        });
    } else {
        _editorSaveState();
        const st = _getSubStyle(styleName);
        sel.styleName = st.name; sel.fontSize = st.fontSize; sel.fontColor = st.fontColor;
        sel.bgColor = st.bgColor; sel.outlineColor = st.outlineColor; sel.fontFamily = st.fontFamily;
        sel.bold = st.bold; sel.italic = st.italic;
    }
    _editorRenderProps();
    const videoEl = document.getElementById("editor-video");
    if (videoEl) _editorDrawOverlays(videoEl.currentTime);
}
window._editorApplySubStyle = _editorApplySubStyle;

const _subtitleProperNames = ["Senhor", "Deus", "Pastor", "Jesus", "Cristo", "Pai"];

function _normalizeAutoSubtitleText(rawText) {
    let text = String(rawText || "").trim();
    if (!text) return "";

    // Keep auto subtitles in sentence case: mostly lowercase, first letter uppercase.
    text = text.replace(/\s+/g, " ").toLowerCase();

    // Religious proper names stay capitalized even when in the middle of a sentence.
    for (const name of _subtitleProperNames) {
        const matcher = new RegExp(`\\b${name.toLowerCase()}\\b`, "gi");
        text = text.replace(matcher, name);
    }

    text = text.replace(/^(["'([{«“]*)([a-zà-ÿ])/i, (_m, prefix, chr) => `${prefix}${chr.toUpperCase()}`);
    text = text.replace(/([.!?]\s+)([a-zà-ÿ])/gi, (_m, prev, chr) => `${prev}${chr.toUpperCase()}`);
    return text;
}

async function _editorAutoSubtitles() {
    if (_editor._subtitleGenerating) return;
    _editor._subtitleGenerating = true;
    _editorRenderProps();
    try {
        const res = await api(`/video/editor/transcribe/${_editor.projectId}`, { method: "POST" });
        if (!res.words || !res.words.length) {
            showToast("Não foi possível detectar fala no vídeo.", "error");
            _editor._subtitleGenerating = false;
            _editorRenderProps();
            return;
        }
        _editorSaveState();
        // Group words into subtitle lines (max ~6 words or 2s gap)
        const lines = [];
        let current = { words: [], start: 0, end: 0 };
        for (const w of res.words) {
            if (!current.words.length) {
                current = { words: [w.word], start: w.start, end: w.end };
            } else if (current.words.length >= 6 || (w.start - current.end) > 1.5) {
                lines.push(current);
                current = { words: [w.word], start: w.start, end: w.end };
            } else {
                current.words.push(w.word);
                current.end = w.end;
            }
        }
        if (current.words.length) lines.push(current);

        // Determine style: use currently selected style or default
        const selSub = _editor.subtitles.find(s => s._selected);
        const styleName = selSub ? selSub.styleName : "classico";
        const st = _getSubStyle(styleName);

        _editor.subtitles = lines.map(line => ({
            id: _editorGenId(),
            text: _normalizeAutoSubtitleText(line.words.join(" ")),
            startTime: line.start,
            endTime: line.end,
            styleName: st.name, x: 50, y: 82, fontSize: st.fontSize,
            fontColor: st.fontColor, bgColor: st.bgColor, outlineColor: st.outlineColor,
            fontFamily: st.fontFamily, bold: st.bold, italic: st.italic, _selected: false,
        }));
        _editor.subtitleListOpen = false;
        showToast(`${lines.length} legendas geradas automaticamente!`, "success");
    } catch (err) {
        showToast("Erro ao gerar legendas: " + err.message, "error");
    }
    _editor._subtitleGenerating = false;
    _editorRenderProps();
    _editorRenderTimeline();
}
window._editorAutoSubtitles = _editorAutoSubtitles;

function _editorClearSubtitles() {
    if (!_editor.subtitles.length) return;
    _editorSaveState();
    _editor.subtitles = [];
    if (_editor.selectedClip.kind === "subtitle") {
        _editor.selectedClip = { kind: "", id: "" };
    }
    _editorRefreshQuickActions();
    _editor.subtitleListOpen = false;
    _editorRenderProps();
    _editorRenderTimeline();
}
window._editorClearSubtitles = _editorClearSubtitles;

function _editorToggleSubtitleList() {
    _editor.subtitleListOpen = !_editor.subtitleListOpen;
    _editorRenderProps();
}
window._editorToggleSubtitleList = _editorToggleSubtitleList;

function _editorSelectSubtitle(id, openEditor = false) {
    _editor.subtitles.forEach(s => s._selected = (s.id === id));
    _editor.selectedClip = { kind: "subtitle", id: String(id), track: "text" };
    if (openEditor) {
        _editor.subtitleListOpen = true;
    }

    if (openEditor && _editor.activeTool !== "subtitles") {
        _editorSelectTool("subtitles");
    } else {
        _editorRenderProps();
    }

    _editorRefreshQuickActions();
    _editorRenderTimeline();

    if (openEditor) {
        requestAnimationFrame(() => {
            const textarea = document.querySelector("#editor-props-content textarea");
            if (textarea) {
                textarea.focus();
                textarea.select();
            }
        });
    }
}
window._editorSelectSubtitle = _editorSelectSubtitle;

function _editorDeleteSubtitle(id) {
    _editorSaveState();
    _editor.subtitles = _editor.subtitles.filter(s => s.id !== id);
    if (_editor.selectedClip.kind === "subtitle" && _editor.selectedClip.id === String(id)) {
        _editor.selectedClip = { kind: "", id: "" };
    }
    _editorRefreshQuickActions();
    _editorRenderProps();
    _editorRenderTimeline();
}
window._editorDeleteSubtitle = _editorDeleteSubtitle;

function _editorUpdateSubProp(id, prop, val) {
    const s = _editor.subtitles.find(x => x.id === id);
    if (!s) return;
    s[prop] = val;
    const video = document.getElementById("editor-video");
    if (video) _editorDrawOverlays(video.currentTime);
    _editorRenderTimeline();
}
window._editorUpdateSubProp = _editorUpdateSubProp;

function _editorSetSubtitlesY(val, noRender = false) {
    if (!_editor.subtitles.length) return;
    const targetY = Math.max(5, Math.min(95, parseInt(val, 10) || 82));
    _editor.subtitles.forEach(s => { s.y = targetY; });
    const yLabel = document.getElementById("editor-sub-global-y-value");
    if (yLabel) yLabel.textContent = `${targetY}%`;
    const video = document.getElementById("editor-video");
    if (video) _editorDrawOverlays(video.currentTime);
    _editorRenderTimeline();
    if (!noRender) _editorRenderProps();
}
window._editorSetSubtitlesY = _editorSetSubtitlesY;

function _editorNudgeSubtitlesY(delta) {
    if (!_editor.subtitles.length) return;
    const base = _editor.subtitles.find(s => s._selected)?.y ?? _editor.subtitles[0].y ?? 82;
    _editorSetSubtitlesY(base + delta);
}
window._editorNudgeSubtitlesY = _editorNudgeSubtitlesY;

function _editorSetSubtitlesFontSize(val, noRender = false) {
    if (!_editor.subtitles.length) return;
    const targetSize = Math.max(14, Math.min(72, parseInt(val, 10) || 28));
    _editor.subtitles.forEach(s => { s.fontSize = targetSize; });
    const sizeLabel = document.getElementById("editor-sub-global-size-value");
    if (sizeLabel) sizeLabel.textContent = `${targetSize}px`;
    const video = document.getElementById("editor-video");
    if (video) _editorDrawOverlays(video.currentTime);
    _editorRenderTimeline();
    if (!noRender) _editorRenderProps();
}
window._editorSetSubtitlesFontSize = _editorSetSubtitlesFontSize;

function _editorNudgeSubtitlesSize(delta) {
    if (!_editor.subtitles.length) return;
    const base = _editor.subtitles.find(s => s._selected)?.fontSize ?? _editor.subtitles[0].fontSize ?? 28;
    _editorSetSubtitlesFontSize(base + delta);
}
window._editorNudgeSubtitlesSize = _editorNudgeSubtitlesSize;

// ---------- Trim actions ----------
function _editorSplitAtCurrentTime() {
    const video = document.getElementById("editor-video");
    if (!video || !_editor.videoSegments.length) return;

    const t = Math.max(0, Math.min(video.currentTime || 0, _editor.duration || 0));
    const selectedTracks = _editorGetSelectedSegmentTracks();
    const splitTargets = selectedTracks.map(track => {
        const seg = _editorGetSegments(track).find(item => t > item.start + 0.08 && t < item.end - 0.08);
        return { track, seg };
    }).filter(item => item.seg);

    if (!splitTargets.length) {
        showToast("Posicione o playhead dentro de um trecho para cortar.", "error");
        return;
    }

    _editorSaveState();
    let selectedSplit = null;
    splitTargets.forEach(({ track, seg }) => {
        const first = { id: _editorGenId(), start: seg.start, end: t };
        const second = { id: _editorGenId(), start: t, end: seg.end };
        const nextSegments = _editorGetSegments(track)
            .filter(item => item !== seg)
            .concat([first, second]);
        _editorSetSegments(track, nextSegments);
        _editorSortSegments(track);
        selectedSplit = { kind: "segment", id: String(second.id), track };
    });

    _editorRecomputeTrimBounds();
    _editorSyncAudioSegmentsWithVideoIfNoExternalAudio();
    _editor.selectedClip = selectedSplit || { kind: "", id: "", track: "" };
    _editorRenderTimeline();
    _editorRenderProps();
    showToast(`Trecho dividido em ${splitTargets.length} faixa(s).`, "success");
}
window._editorSplitAtCurrentTime = _editorSplitAtCurrentTime;

function _editorResetVideoSegments() {
    if (!_editor.duration) return;
    _editorSaveState();
    _editorInitVideoSegments();
    _editor.selectedClip = { kind: "", id: "", track: "" };
    _editorRenderTimeline();
    _editorRenderProps();
    showToast("Cortes removidos. Vídeo restaurado.", "success");
}
window._editorResetVideoSegments = _editorResetVideoSegments;

function _editorSetTrimStart(val) {
    const parsed = parseFloat(val);
    if (isNaN(parsed) || !_editor.videoSegments.length) return;
    _editorSortVideoSegments();
    const first = _editor.videoSegments[0];
    first.start = Math.max(0, Math.min(first.end - 0.1, parsed));
    _editorRecomputeTrimBounds();
    _editorSyncAudioSegmentsWithVideoIfNoExternalAudio();
    const label = document.getElementById("trim-start-label");
    if (label) label.textContent = _fmtTime(_editor.trimStart);
    _editorRenderTimeline();
}
window._editorSetTrimStart = _editorSetTrimStart;

function _editorSetTrimEnd(val) {
    const parsed = parseFloat(val);
    if (isNaN(parsed) || !_editor.videoSegments.length) return;
    _editorSortVideoSegments();
    const last = _editor.videoSegments[_editor.videoSegments.length - 1];
    last.end = Math.max(last.start + 0.1, Math.min(_editor.duration || parsed, parsed));
    _editorRecomputeTrimBounds();
    _editorSyncAudioSegmentsWithVideoIfNoExternalAudio();
    const label = document.getElementById("trim-end-label");
    if (label) label.textContent = _fmtTime(_editor.trimEnd);
    _editorRenderTimeline();
}
window._editorSetTrimEnd = _editorSetTrimEnd;

// ---------- Music actions ----------
function _editorUploadMusic(input) {
    const file = input.files?.[0];
    if (!file) return;
    _editorSaveState();
    _editor.musicUrl = URL.createObjectURL(file);
    _editor._musicFile = file;
    _editor._musicServerPath = "";
    _editor._musicSource = "audio";
    _editorSetMusicPreviewSource(_editor.musicUrl);
    if (!_editor.audioSegments.length) {
        _editor.audioSegments = _editorCloneVideoSegmentsForAudio();
    }
    _editor.selectedTracks = ["video", "audio"];
    _editor.selectedClip = { kind: "music", id: "music" };
    _editorRefreshQuickActions();
    _editorRenderProps();
    _editorRenderTimeline();
}
window._editorUploadMusic = _editorUploadMusic;

async function _editorUploadVideoForMusic(input) {
    const file = input.files?.[0];
    if (!file) return;
    try {
        _editorSaveState();
        showToast("Extraindo áudio do vídeo...");

        const formData = new FormData();
        formData.append("file", file);
        const payload = await apiForm("/video/editor/upload-video-audio", formData, { method: "POST" });

        const serverPath = String(payload?.path || "").trim();
        const mediaUrlRaw = String(payload?.media_url || "").trim();
        const mediaUrl = mediaUrlRaw.startsWith("/")
            ? `${API.replace("/api", "")}${mediaUrlRaw}`
            : mediaUrlRaw;
        if (!serverPath) {
            throw new Error("Falha ao extrair audio do video");
        }

        _editor._musicSource = "video";
        _editor._musicFile = null;
        _editor._musicServerPath = serverPath;
        _editor.musicUrl = mediaUrl;
        _editorSetMusicPreviewSource(_editor.musicUrl || "");

        if (!_editor.audioSegments.length) {
            _editor.audioSegments = _editorCloneVideoSegmentsForAudio();
        }
        _editor.selectedTracks = ["video", "audio"];
        _editor.selectedClip = { kind: "music", id: "music" };
        _editorRefreshQuickActions();
        _editorRenderProps();
        _editorRenderTimeline();
        showToast("Áudio extraído do vídeo com sucesso!", "success");
    } catch (err) {
        showToast("Erro ao extrair áudio do vídeo: " + (err?.message || "erro desconhecido"), "error");
    } finally {
        if (input) input.value = "";
    }
}
window._editorUploadVideoForMusic = _editorUploadVideoForMusic;

function _editorRemoveMusic() {
    _editorSaveState();
    _editor.musicUrl = "";
    _editor._musicFile = null;
    _editor._musicServerPath = "";
    _editor._musicSource = "audio";
    _editorSetMusicPreviewSource("");
    _editorSyncAudioSegmentsWithVideoIfNoExternalAudio();
    if (_editor.selectedClip.kind === "music") {
        _editor.selectedClip = { kind: "", id: "" };
    }
    _editorRefreshQuickActions();
    _editorRenderProps();
    _editorRenderTimeline();
}
window._editorRemoveMusic = _editorRemoveMusic;

function _editorSetMusicVolume(val) {
    const next = Math.max(0, Math.min(100, parseInt(val, 10) || 0));
    _editor.musicVolume = next;
    const label = document.getElementById("editor-music-vol-label");
    if (label) label.textContent = _editor.musicVolume + "%";
    const trimLabel = document.getElementById("editor-track-vol-label-audio");
    if (trimLabel) trimLabel.textContent = _editor.musicVolume + "%";
    const video = document.getElementById("editor-video");
    _editorSyncMusicPreviewPlayback(Number(video?.currentTime || 0), _editor.playing && !!video && !video.paused);
}
window._editorSetMusicVolume = _editorSetMusicVolume;

function _editorSetOriginalVolume(val) {
    const next = Math.max(0, Math.min(100, parseInt(val, 10) || 0));
    _editor.originalVolume = next;
    const video = document.getElementById("editor-video");
    if (video) video.volume = _editor.originalVolume / 100;
    const label = document.getElementById("editor-orig-vol-label");
    if (label) label.textContent = _editor.originalVolume + "%";
    const trimLabel = document.getElementById("editor-track-vol-label-video");
    if (trimLabel) trimLabel.textContent = _editor.originalVolume + "%";
}
window._editorSetOriginalVolume = _editorSetOriginalVolume;

function _editorSetTrackVolumeFromTrim(track, val) {
    if (track === "video") {
        _editorSetOriginalVolume(val);
        return;
    }
    if (track === "audio") {
        _editorSetMusicVolume(val);
    }
}
window._editorSetTrackVolumeFromTrim = _editorSetTrackVolumeFromTrim;

// ---------- Filter ----------
function _editorSetFilter(name) {
    _editorSaveState();
    _editor.filter = name;
    const video = document.getElementById("editor-video");
    if (video) video.style.filter = _getCSSFilter(name);
    _editorRenderProps();
}
window._editorSetFilter = _editorSetFilter;

// ---------- Sticker actions ----------
function _editorAddSticker(emoji) {
    _editorSaveState();
    const video = document.getElementById("editor-video");
    const t = video?.currentTime || 0;
    const newSticker = {
        id: _editorGenId(), emoji, x: 50, y: 30, startTime: t, endTime: Math.min(t + 4, _editor.duration), size: 48,
    };
    _editor.stickers.push(newSticker);
    _editor.selectedClip = { kind: "sticker", id: String(newSticker.id) };
    _editorRefreshQuickActions();
    _editorRenderProps();
    _editorRenderTimeline();
}
window._editorAddSticker = _editorAddSticker;

function _editorDeleteSticker(id) {
    _editorSaveState();
    _editor.stickers = _editor.stickers.filter(s => s.id !== id);
    if (_editor.selectedClip.kind === "sticker" && _editor.selectedClip.id === String(id)) {
        _editor.selectedClip = { kind: "", id: "" };
    }
    _editorRefreshQuickActions();
    _editorRenderProps();
    _editorRenderTimeline();
}
window._editorDeleteSticker = _editorDeleteSticker;

// ---------- Quality ----------
function _editorSetQuality(val) {
    _editorSaveState();
    _editor.quality = val;
    _editorRenderProps();
}
window._editorSetQuality = _editorSetQuality;

// ---------- Timeline rendering ----------
function _editorTimelineTrackIcon(kind) {
    if (kind === "video") {
        return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>';
    }
    if (kind === "audio") {
        return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';
    }
    if (kind === "text") {
        return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7V4h16v3"/><line x1="12" y1="4" x2="12" y2="20"/></svg>';
    }
    if (kind === "subtitle") {
        return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M7 12h4"/><path d="M13 12h4"/><path d="M7 16h10"/></svg>';
    }
    if (kind === "media-layer") {
        return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/></svg>';
    }
    return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/></svg>';
}

function _editorTimelineVolumeIcon(track) {
    const muted = track === "video"
        ? Number(_editor.originalVolume || 0) <= 0
        : Number(_editor.musicVolume || 0) <= 0;

    if (muted) {
        return '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>';
    }
    return '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>';
}

function _editorRenderTimeline() {
    const dur = Math.max(_editor.duration || 0, 0.1);
    const ruler = document.getElementById("editor-timeline-ruler");
    const tracksWrap = document.getElementById("editor-timeline-tracks");
    const timelineEl = document.getElementById("editor-timeline");
    if (!ruler || !tracksWrap) return;

    const selectedKind = _editor.selectedClip.kind;
    const selectedId = String(_editor.selectedClip.id || "");
    const selectedTrack = _editor.selectedClip.track || "";
    const rows = [];

    _editorSortSegments("video");
    const videoClips = _editor.videoSegments.map((seg, idx) => {
        const segStart = Math.max(0, Math.min(Number(seg.start || 0), dur));
        const segEnd = Math.max(segStart + 0.05, Math.min(Number(seg.end || 0), dur));
        const startPct = (segStart / dur) * 100;
        const widthPct = Math.max(0.5, ((segEnd - segStart) / dur) * 100);
        const selectedClass = selectedKind === "segment" && selectedTrack === "video" && selectedId === String(seg.id) ? " selected" : "";
        return `<div class="editor-track-clip clip-video${selectedClass}" data-kind="segment" data-track="video" data-id="${seg.id}" style="left:${startPct}%;width:${widthPct}%">Video ${idx + 1}</div>`;
    }).join("");
    rows.push({
        track: "video",
        kind: "video",
        label: "Video",
        contentId: "editor-track-video",
        clipsHtml: videoClips,
    });

    if (_editorShouldShowAudioTrack()) {
        _editorSortSegments("audio");
        let audioClips = _editor.audioSegments.map((seg, idx) => {
            const segStart = Math.max(0, Math.min(Number(seg.start || 0), dur));
            const segEnd = Math.max(segStart + 0.05, Math.min(Number(seg.end || 0), dur));
            const startPct = (segStart / dur) * 100;
            const widthPct = Math.max(0.5, ((segEnd - segStart) / dur) * 100);
            const selectedClass = selectedKind === "segment" && selectedTrack === "audio" && selectedId === String(seg.id) ? " selected" : "";
            return `<div class="editor-track-clip clip-audio${selectedClass}" data-kind="segment" data-track="audio" data-id="${seg.id}" style="left:${startPct}%;width:${widthPct}%">Audio ${idx + 1}</div>`;
        }).join("");

        const musicSelected = selectedKind === "music" ? " selected" : "";
        audioClips += `<div class="editor-track-clip clip-audio${musicSelected}" data-kind="music" data-id="music" style="left:0;width:100%;top:1px;background:linear-gradient(135deg,#6b1a4a,#4a0e2e);border-color:rgba(107,26,74,0.6)">Audio</div>`;

        rows.push({
            track: "audio",
            kind: "audio",
            contentId: "editor-track-audio",
            label: "Audio",
            clipsHtml: audioClips,
        });
    }

    _editor.mediaLayers.forEach((layer, idx) => {
        const start = Math.max(0, Math.min(Number(layer.startTime || 0), dur));
        const maxLayerEnd = layer.kind === "video" && Number(layer.duration || 0) > 0
            ? start + Number(layer.duration || 0)
            : dur;
        const requestedEnd = Number(layer.endTime || dur);
        const end = Math.max(start + 0.05, Math.min(dur, Math.min(requestedEnd, maxLayerEnd)));
        const left = (start / dur) * 100;
        const width = Math.max(0.5, ((end - start) / dur) * 100);
        const selectedClass = selectedKind === "media-layer" && selectedId === String(layer.id) ? " selected" : "";
        const layerName = layer.kind === "video" ? `Camada Video ${idx + 1}` : `Camada Imagem ${idx + 1}`;
        rows.push({
            track: `media-${layer.id}`,
            kind: "media-layer",
            label: layerName,
            clipsHtml: `<div class="editor-track-clip clip-media${selectedClass}" data-kind="media-layer" data-track="media-${layer.id}" data-id="${layer.id}" style="left:${left}%;width:${width}%">${layer.kind === "video" ? "Video" : "Imagem"}</div>`,
        });
    });

    _editor.texts.forEach((item, idx) => {
        const start = Math.max(0, Math.min(Number(item.startTime || 0), dur));
        const end = Math.max(start + 0.05, Math.min(Number(item.endTime || 0), dur));
        const left = (start / dur) * 100;
        const width = Math.max(0.5, ((end - start) / dur) * 100);
        const selectedClass = selectedKind === "text" && selectedId === String(item.id) ? " selected" : "";
        const clipLabel = esc(String(item.content || "Texto").trim().substring(0, 20));
        rows.push({
            track: `text-${item.id}`,
            kind: "text",
            label: `Texto ${idx + 1}`,
            clipsHtml: `<div class="editor-track-clip clip-text${selectedClass}" data-kind="text" data-track="text-${item.id}" data-id="${item.id}" style="left:${left}%;width:${width}%">${clipLabel}</div>`,
        });
    });

    if (_editor.subtitles.length) {
        const subtitleClips = _editor.subtitles.map((item) => {
            const start = Math.max(0, Math.min(Number(item.startTime || 0), dur));
            const end = Math.max(start + 0.05, Math.min(Number(item.endTime || 0), dur));
            const left = (start / dur) * 100;
            const width = Math.max(0.5, ((end - start) / dur) * 100);
            const selectedClass = selectedKind === "subtitle" && selectedId === String(item.id) ? " selected" : "";
            const clipLabel = esc(String(item.text || "Legenda").trim().substring(0, 20));
            return `<div class="editor-track-clip clip-text${selectedClass}" data-kind="subtitle" data-track="subtitle" data-id="${item.id}" style="left:${left}%;width:${width}%">${clipLabel}</div>`;
        }).join("");

        rows.push({
            track: "subtitle",
            kind: "subtitle",
            label: "Legendas",
            clipsHtml: subtitleClips,
        });
    }

    _editor.stickers.forEach((item, idx) => {
        const start = Math.max(0, Math.min(Number(item.startTime || 0), dur));
        const end = Math.max(start + 0.05, Math.min(Number(item.endTime || 0), dur));
        const left = (start / dur) * 100;
        const width = Math.max(0.5, ((end - start) / dur) * 100);
        const selectedClass = selectedKind === "sticker" && selectedId === String(item.id) ? " selected" : "";
        rows.push({
            track: `sticker-${item.id}`,
            kind: "sticker",
            label: `Sticker ${idx + 1}`,
            clipsHtml: `<div class="editor-track-clip clip-sticker${selectedClass}" data-kind="sticker" data-track="sticker-${item.id}" data-id="${item.id}" style="left:${left}%;width:${width}%">${esc(String(item.emoji || "Sticker"))}</div>`,
        });
    });

    tracksWrap.innerHTML = rows.map((row) => {
        return `
            <div class="editor-track" data-track="${row.track}">
                <div class="editor-track-label">
                    <span class="editor-track-label-main">${_editorTimelineTrackIcon(row.kind)}<span class="editor-track-label-text">${row.label}</span></span>
                </div>
                <div class="editor-track-content"${row.contentId ? ` id="${row.contentId}"` : ""}>${row.clipsHtml || ""}</div>
            </div>
        `;
    }).join("");

    _editorRefreshTrackSelectionUI();

    const isMobile = window.innerWidth <= 768;
    const rowHeight = 32;
    const trackCount = Math.max(rows.length, 1);
    const idealHeight = 24 + (trackCount * rowHeight) + 8;
    const minHeight = isMobile ? 116 : 160;
    const maxHeight = isMobile ? 300 : 380;
    const finalHeight = Math.max(minHeight, Math.min(maxHeight, idealHeight));
    if (timelineEl) {
        timelineEl.style.height = `${finalHeight}px`;
        timelineEl.style.overflowY = idealHeight > maxHeight ? "auto" : "hidden";
    }

    // Ruler marks
    const step = dur > 120 ? 30 : dur > 60 ? 10 : 5;
    const trackW = document.getElementById("editor-track-video")?.offsetWidth || 600;
    let rulerHtml = "";
    for (let t = 0; t <= dur; t += step) {
        const pct = (t / dur) * trackW;
        rulerHtml += `<span class="editor-ruler-mark" style="left:${80 + pct}px">${_fmtTime(t)}</span>`;
        rulerHtml += `<span class="editor-ruler-tick major" style="left:${80 + pct}px"></span>`;
    }
    ruler.innerHTML = rulerHtml;

    _editorRefreshQuickActions();
}

function _editorSelectionCanDelete() {
    return ["segment", "text", "subtitle", "sticker", "music", "audio", "media-layer"].includes(_editor.selectedClip.kind);
}

function _editorSelectionCanDuplicate() {
    return ["text", "subtitle", "sticker"].includes(_editor.selectedClip.kind);
}

function _editorGetLayerOrderCount() {
    if (Array.isArray(_editor.mediaLayers) && _editor.mediaLayers.length) {
        return _editor.mediaLayers.length;
    }
    const host = document.getElementById("editor-media-layer-host");
    if (!host) return 0;
    const layeredEls = host.querySelectorAll("[data-layer-id], .editor-media-layer-item, .editor-media-layer");
    if (layeredEls.length) return layeredEls.length;
    return host.children.length;
}

function _editorCycleMediaLayerOrder() {
    const host = document.getElementById("editor-media-layer-host");
    const stateLayers = Array.isArray(_editor.mediaLayers) ? _editor.mediaLayers : [];
    const domLayers = host
        ? Array.from(host.querySelectorAll("[data-layer-id], .editor-media-layer-item, .editor-media-layer"))
        : [];
    const domCount = domLayers.length || (host ? host.children.length : 0);
    const hasStateLayers = stateLayers.length >= 2;
    const hasDomLayers = domCount >= 2;

    if (!hasStateLayers && !hasDomLayers) {
        showToast("Adicione pelo menos 2 camadas para alternar a ordem.", "info");
        _editorRefreshQuickActions();
        return;
    }

    _editorSaveState();

    if (hasStateLayers) {
        const first = stateLayers.shift();
        stateLayers.push(first);
    }

    if (hasDomLayers && host) {
        const firstDom = domLayers.length ? domLayers[0] : host.children[0];
        if (firstDom) host.appendChild(firstDom);
    }

    if (typeof _editorRenderMediaLayers === "function") {
        _editorRenderMediaLayers();
    }
    if (typeof _editorDrawOverlays === "function") {
        const video = document.getElementById("editor-video");
        _editorDrawOverlays(video ? Number(video.currentTime || 0) : 0);
    }

    _editorRenderTimeline();
    _editorRefreshQuickActions();
    showToast("Ordem das camadas alternada.", "success");
}
window._editorCycleMediaLayerOrder = _editorCycleMediaLayerOrder;

function _editorRefreshQuickActions() {
    const delBtn = document.getElementById("editor-quick-delete");
    const dupBtn = document.getElementById("editor-quick-duplicate");
    const cutBtn = document.getElementById("editor-quick-cut");
    const layerOrderBtn = document.getElementById("editor-quick-layer-order");
    if (delBtn) delBtn.disabled = !_editorSelectionCanDelete();
    if (dupBtn) dupBtn.disabled = !_editorSelectionCanDuplicate();
    if (cutBtn) cutBtn.disabled = !_editor.duration || !_editorGetSelectedSegmentTracks().length;
    if (layerOrderBtn) layerOrderBtn.disabled = _editorGetLayerOrderCount() < 2;
}

function _editorSelectTimelineClip(kind, id, renderProps = true, track = "") {
    const normalizedId = String(id ?? "");
    _editor.selectedClip = { kind: kind || "", id: normalizedId, track: track || "" };

    let switchedTool = false;
    if (kind === "segment") {
        if (renderProps && _editor.activeTool !== "trim") {
            _editorSelectTool("trim");
            switchedTool = true;
        }
    } else if (kind === "text") {
        _editor.texts.forEach(t => t._selected = String(t.id) === normalizedId);
        _editor.subtitles.forEach(s => s._selected = false);
        if (renderProps && _editor.activeTool !== "text") {
            _editorSelectTool("text");
            switchedTool = true;
        }
    } else if (kind === "subtitle") {
        _editor.subtitles.forEach(s => s._selected = String(s.id) === normalizedId);
        _editor.texts.forEach(t => t._selected = false);
        if (renderProps && _editor.activeTool !== "subtitles") {
            _editorSelectTool("subtitles");
            switchedTool = true;
        }
    } else if (kind === "media-layer") {
        if (renderProps && _editor.activeTool !== "layers") {
            _editorSelectTool("layers");
            switchedTool = true;
        }
        _editorRenderMediaLayers();
    } else if (kind === "sticker" && renderProps && _editor.activeTool !== "stickers") {
        _editorSelectTool("stickers");
        switchedTool = true;
    }

    if (renderProps && !switchedTool) {
        _editorRenderProps();
    }

    _editorRenderTimeline();
}

function _editorDeleteSelectedClip() {
    if (!_editor.selectedClip.kind) return;
    if (!_editorSelectionCanDelete()) {
        showToast("Selecione um trecho, texto, legenda, sticker ou áudio/música para excluir.", "error");
        return;
    }

    const selKind = _editor.selectedClip.kind;
    const selId = _editor.selectedClip.id;
    const selTrack = _editor.selectedClip.track || "video";
    if (selKind === "segment") {
        const trackSegments = _editorGetSegments(selTrack);
        if (trackSegments.length <= 1) {
            const trackLabel = selTrack === "audio" ? "audio" : "video";
            showToast(`Não é possível remover o último trecho do ${trackLabel}.`, "error");
            return;
        }
    }

    _editorSaveState();

    if (selKind === "segment") {
        const next = _editorGetSegments(selTrack).filter(seg => String(seg.id) !== selId);
        _editorSetSegments(selTrack, next);
        _editorSortSegments(selTrack);
        if (selTrack === "video") {
            _editorRecomputeTrimBounds();
            _editorSyncAudioSegmentsWithVideoIfNoExternalAudio();
        }
    } else if (selKind === "text") {
        _editor.texts = _editor.texts.filter(t => String(t.id) !== selId);
    } else if (selKind === "subtitle") {
        _editor.subtitles = _editor.subtitles.filter(s => String(s.id) !== selId);
    } else if (selKind === "sticker") {
        _editor.stickers = _editor.stickers.filter(s => String(s.id) !== selId);
    } else if (selKind === "media-layer") {
        _editor.mediaLayers = _editor.mediaLayers.filter(layer => String(layer.id) !== selId);
    } else if (selKind === "music") {
        _editor.musicUrl = "";
        _editor._musicFile = null;
        _editor._musicServerPath = "";
        _editor._musicSource = "audio";
        _editorSetMusicPreviewSource("");
        _editorSyncAudioSegmentsWithVideoIfNoExternalAudio();
    } else if (selKind === "audio") {
        _editorSetOriginalVolume(0);
        showToast("Áudio original silenciado.", "success");
    }

    _editor.selectedClip = { kind: "", id: "", track: "" };
    _editorRenderProps();
    _editorRenderTimeline();
    _editorRenderMediaLayers();
    const video = document.getElementById("editor-video");
    if (video) _editorDrawOverlays(video.currentTime);
}
window._editorDeleteSelectedClip = _editorDeleteSelectedClip;

function _editorDuplicateSelectedClip() {
    if (!_editorSelectionCanDuplicate()) return;

    const selKind = _editor.selectedClip.kind;
    const selId = _editor.selectedClip.id;
    const shift = 0.35;
    _editorSaveState();

    const cloneTimed = (item) => {
        const span = Math.max(0.1, (item.endTime || 0) - (item.startTime || 0));
        const maxStart = Math.max(0, (_editor.duration || span) - span);
        const startTime = Math.max(0, Math.min(maxStart, (item.startTime || 0) + shift));
        return { ...item, id: _editorGenId(), startTime, endTime: startTime + span };
    };

    if (selKind === "text") {
        const source = _editor.texts.find(t => String(t.id) === selId);
        if (!source) return;
        _editor.texts.forEach(t => t._selected = false);
        const copy = cloneTimed(source);
        copy._selected = true;
        _editor.texts.push(copy);
        _editor.selectedClip = { kind: "text", id: String(copy.id) };
    } else if (selKind === "subtitle") {
        const source = _editor.subtitles.find(s => String(s.id) === selId);
        if (!source) return;
        _editor.subtitles.forEach(s => s._selected = false);
        const copy = cloneTimed(source);
        copy._selected = true;
        _editor.subtitles.push(copy);
        _editor.selectedClip = { kind: "subtitle", id: String(copy.id) };
    } else if (selKind === "sticker") {
        const source = _editor.stickers.find(s => String(s.id) === selId);
        if (!source) return;
        const copy = cloneTimed(source);
        _editor.stickers.push(copy);
        _editor.selectedClip = { kind: "sticker", id: String(copy.id) };
    }

    _editorRenderProps();
    _editorRenderTimeline();
}
window._editorDuplicateSelectedClip = _editorDuplicateSelectedClip;

function _editorTimelineCanDrag(kind) {
    return ["segment", "text", "subtitle", "sticker", "media-layer"].includes(kind);
}

function _editorTimelineCanResize(kind) {
    return ["text", "subtitle", "media-layer"].includes(kind);
}

function _editorGetTimelineRange(kind, id, track = "") {
    if (kind === "segment") {
        const item = _editorFindSegment(track || "video", id);
        return item ? { start: item.start, end: item.end } : null;
    }
    if (kind === "text") {
        const item = _editor.texts.find(t => String(t.id) === String(id));
        return item ? { start: item.startTime, end: item.endTime } : null;
    }
    if (kind === "subtitle") {
        const item = _editor.subtitles.find(s => String(s.id) === String(id));
        return item ? { start: item.startTime, end: item.endTime } : null;
    }
    if (kind === "sticker") {
        const item = _editor.stickers.find(s => String(s.id) === String(id));
        return item ? { start: item.startTime, end: item.endTime } : null;
    }
    if (kind === "media-layer") {
        const item = _editorGetMediaLayerById(id);
        return item ? { start: item.startTime, end: item.endTime } : null;
    }
    return null;
}

function _editorApplyDraggedRange(kind, id, start, end, track = "") {
    if (kind === "segment") {
        const targetTrack = track || "video";
        const item = _editorFindSegment(targetTrack, id);
        if (!item) return;
        const span = Math.max(0.1, end - start);
        const [clampedStart, clampedEnd] = _editorClampSegmentRange(targetTrack, id, start, span);
        item.start = clampedStart;
        item.end = clampedEnd;
        if (targetTrack === "video") {
            _editorRecomputeTrimBounds();
            _editorSyncAudioSegmentsWithVideoIfNoExternalAudio();
        }
        return;
    }

    const duration = Math.max(_editor.duration || 0.1, 0.1);
    const safeStart = Math.max(0, Math.min(duration - 0.1, start));
    const safeEnd = Math.max(safeStart + 0.1, Math.min(duration, end));

    if (kind === "text") {
        const item = _editor.texts.find(t => String(t.id) === String(id));
        if (item) {
            item.startTime = safeStart;
            item.endTime = safeEnd;
        }
        return;
    }
    if (kind === "subtitle") {
        const item = _editor.subtitles.find(s => String(s.id) === String(id));
        if (item) {
            item.startTime = safeStart;
            item.endTime = safeEnd;
        }
        return;
    }
    if (kind === "sticker") {
        const item = _editor.stickers.find(s => String(s.id) === String(id));
        if (item) {
            item.startTime = safeStart;
            item.endTime = safeEnd;
        }
        return;
    }
    if (kind === "media-layer") {
        const item = _editorGetMediaLayerById(id);
        if (item) {
            const maxByDuration = item.kind === "video" && Number(item.duration || 0) > 0
                ? safeStart + Number(item.duration || 0)
                : safeEnd;
            item.startTime = safeStart;
            item.endTime = Math.max(safeStart + 0.1, Math.min(safeEnd, maxByDuration));
        }
    }
}

function _editorStartTimelineDrag(kind, id, track, event, trackEl, clipEl) {
    const frozenTrackWidth = Math.max(trackEl?.getBoundingClientRect?.().width || 0, 1);
    const fallbackTrackWidth = Math.max(
        document.getElementById("editor-track-video")?.getBoundingClientRect?.().width || 0,
        document.getElementById("editor-timeline")?.getBoundingClientRect?.().width || 0,
        1
    );
    const stableTrackWidth = Math.max(frozenTrackWidth, fallbackTrackWidth);
    const frozenClipRect = clipEl?.getBoundingClientRect?.() || null;

    _editorSelectTimelineClip(kind, id, false, track);
    if (!_editorTimelineCanDrag(kind) || !_editor.duration || !trackEl) return false;
    const range = _editorGetTimelineRange(kind, id, track);
    if (!range) return false;

    let mode = "move";
    if (_editorTimelineCanResize(kind) && frozenClipRect) {
        const localX = event.clientX - frozenClipRect.left;
        const edgeSize = Math.max(6, Math.min(14, frozenClipRect.width * 0.2));
        if (localX <= edgeSize) {
            mode = "resize-start";
        } else if (localX >= frozenClipRect.width - edgeSize) {
            mode = "resize-end";
        }
    }

    _editorTimelineDrag = {
        kind,
        id,
        track,
        mode,
        startX: event.clientX,
        trackWidth: stableTrackWidth,
        duration: Math.max(_editor.duration, 0.1),
        baseStart: range.start,
        baseEnd: range.end,
        minDuration: kind === "sticker" ? 0.5 : 0.2,
        moved: false,
        saved: false,
    };

    document.addEventListener("pointermove", _editorOnTimelineDragMove);
    document.addEventListener("pointerup", _editorOnTimelineDragEnd, { once: true });
    return true;
}

function _editorOnTimelineDragMove(event) {
    if (!_editorTimelineDrag) return;

    const drag = _editorTimelineDrag;
    const dx = event.clientX - drag.startX;
    const deltaSec = (dx / drag.trackWidth) * drag.duration;
    const baseSpan = Math.max(0.1, drag.baseEnd - drag.baseStart);
    let nextStart = drag.baseStart;
    let nextEnd = drag.baseEnd;

    if (drag.mode === "resize-start") {
        const maxStart = drag.baseEnd - drag.minDuration;
        nextStart = Math.max(0, Math.min(maxStart, drag.baseStart + deltaSec));
    } else if (drag.mode === "resize-end") {
        const minEnd = drag.baseStart + drag.minDuration;
        nextEnd = Math.max(minEnd, Math.min(drag.duration, drag.baseEnd + deltaSec));
    } else {
        const maxStart = Math.max(0, drag.duration - baseSpan);
        nextStart = Math.max(0, Math.min(maxStart, drag.baseStart + deltaSec));
        nextEnd = nextStart + baseSpan;
    }

    if (Math.abs(dx) > 1) {
        drag.moved = true;
    }
    if (drag.moved && !drag.saved) {
        _editorSaveState();
        drag.saved = true;
    }

    _editorApplyDraggedRange(drag.kind, drag.id, nextStart, nextEnd, drag.track);
    _editorRenderTimeline();
    const video = document.getElementById("editor-video");
    if (video) {
        _editorDrawOverlays(video.currentTime);
        if (drag.kind === "media-layer") {
            _editorSyncMediaLayersWithTime(video.currentTime);
        }
    }
}

function _editorOnTimelineDragEnd() {
    document.removeEventListener("pointermove", _editorOnTimelineDragMove);
    if (!_editorTimelineDrag) return;

    const moved = _editorTimelineDrag.moved;
    const kind = _editorTimelineDrag.kind;
    const id = _editorTimelineDrag.id;
    const track = _editorTimelineDrag.track;
    _editorTimelineDrag = null;

    if (moved) {
        _editorRenderTimeline();
        if (kind === "media-layer") {
            _editorRenderMediaLayers();
        }
        _editorRenderProps();
    } else {
        _editorSelectTimelineClip(kind, id, true, track);
    }
}

function _editorHandleDeleteKey(event) {
    const workspace = document.getElementById("editor-workspace");
    if (!workspace || workspace.hidden) return;
    if (event.key !== "Delete" && event.key !== "Backspace") return;

    const active = document.activeElement;
    const tag = (active?.tagName || "").toUpperCase();
    if (active?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(tag)) {
        return;
    }
    if (!_editor.selectedClip.kind) return;

    event.preventDefault();
    _editorDeleteSelectedClip();
}

// ---------- Export ----------
async function _editorExport() {
    if (!_editor.projectId || !_editor.videoUrl) return;

    // Build edit specification
    const edits = {
        project_id: _editor.projectId,
        aspect_ratio: _resolveAspectRatio(),
        trim_start: _editor.trimStart,
        trim_end: _editor.trimEnd,
        trim_video_segments: _editor.videoSegments
            .map(seg => ({ start: Number(seg.start || 0), end: Number(seg.end || 0) }))
            .filter(seg => seg.end > seg.start + 0.05),
        trim_audio_segments: _editor.audioSegments
            .map(seg => ({ start: Number(seg.start || 0), end: Number(seg.end || 0) }))
            .filter(seg => seg.end > seg.start + 0.05),
        trim_segments: _editor.videoSegments
            .map(seg => ({ start: Number(seg.start || 0), end: Number(seg.end || 0) }))
            .filter(seg => seg.end > seg.start + 0.05),
        filter: _editor.filter,
        quality: _editor.quality,
        original_volume: _editor.originalVolume,
        music_volume: _editor.musicVolume,
        texts: _editor.texts.map(t => ({
            content: t.content, start_time: t.startTime, end_time: t.endTime,
            x: t.x, y: t.y, font_size: t.fontSize, color: t.color,
            bold: t.bold, italic: t.italic,
        })),
        subtitles: _editor.subtitles.map(s => ({
            text: s.text, start_time: s.startTime, end_time: s.endTime,
            x: s.x, y: s.y, font_size: s.fontSize, font_color: s.fontColor,
            bg_color: s.bgColor, outline_color: s.outlineColor,
            font_family: s.fontFamily, bold: s.bold, italic: s.italic,
        })),
        stickers: _editor.stickers.map(s => ({
            emoji: s.emoji, x: s.x, y: s.y, start_time: s.startTime, end_time: s.endTime, size: s.size,
        })),
        media_layers: _editor.mediaLayers.map(layer => ({
            path: layer.path,
            kind: layer.kind,
            media_type: layer.kind,
            x: Number(layer.x || 0),
            y: Number(layer.y || 0),
            width: Number(layer.width || 100),
            start_time: Number(layer.startTime || 0),
            end_time: Number(layer.endTime || 0),
            duration: Number(layer.duration || 0),
            volume: Number(layer.volume ?? 100),
            audio_only: Boolean(layer.audioOnly),
        })),
    };

    // Show export overlay
    const overlay = document.createElement("div");
    overlay.className = "editor-export-overlay";
    overlay.innerHTML = `
        <div class="editor-export-card">
            <h3>Exportando video</h3>
            <div class="editor-export-progress"><div class="editor-export-progress-fill" id="editor-export-fill"></div></div>
            <p class="editor-export-status" id="editor-export-status">Enviando edicoes ao servidor...</p>
        </div>
    `;
    document.body.appendChild(overlay);

    try {
        // Upload music file if any
        let musicPath = String(_editor._musicServerPath || "").trim();
        if (!musicPath && _editor._musicFile) {
            const formData = new FormData();
            formData.append("file", _editor._musicFile);
            const musicUploadEndpoint = _editor._musicSource === "video"
                ? "/api/video/editor/upload-video-audio"
                : "/api/video/editor/upload-music";
            if (status) {
                status.textContent = _editor._musicSource === "video"
                    ? "Extraindo audio do video enviado..."
                    : "Enviando audio para o servidor...";
            }
            const uploadRes = await fetch(API.replace("/api", "") + musicUploadEndpoint, {
                method: "POST",
                headers: { Authorization: "Bearer " + token },
                body: formData,
            });
            if (!uploadRes.ok) {
                const errPayload = await uploadRes.json().catch(() => ({}));
                throw new Error(errPayload.detail || "Falha ao enviar audio");
            }
            const uploadData = await uploadRes.json();
            musicPath = String(uploadData?.path || "").trim();
            _editor._musicServerPath = musicPath;
            if (!_editor.musicUrl && uploadData?.media_url) {
                const mediaUrlRaw = String(uploadData.media_url);
                _editor.musicUrl = mediaUrlRaw.startsWith("/")
                    ? `${API.replace("/api", "")}${mediaUrlRaw}`
                    : mediaUrlRaw;
                _editorSetMusicPreviewSource(_editor.musicUrl);
            }
        }
        edits.music_path = musicPath;

        // Submit export job
        const res = await api("/video/editor/export", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(edits),
        });
        const jobId = res.job_id;

        // Poll progress
        const fill = document.getElementById("editor-export-fill");
        const status = document.getElementById("editor-export-status");
        let done = false;
        while (!done) {
            await new Promise(r => setTimeout(r, 2000));
            try {
                const poll = await api(`/video/editor/export/${jobId}/status`);
                if (fill) fill.style.width = (poll.progress || 0) + "%";
                if (status) status.textContent = poll.message || "Processando...";
                if (poll.status === "completed") {
                    done = true;
                    if (status) status.textContent = "Vídeo exportado com sucesso!";
                    if (fill) fill.style.width = "100%";
                    await new Promise(r => setTimeout(r, 1500));
                    overlay.remove();
                    showToast("Vídeo editado exportado com sucesso!", "success");

                    if (poll.output_url) {
                        const link = document.createElement("a");
                        link.href = poll.output_url;
                        link.download = "video-editado.mp4";
                        link.style.display = "none";
                        document.body.appendChild(link);
                        link.click();
                        document.body.removeChild(link);
                    }

                    closeEditor();
                    loadEditorVideosList();
                } else if (poll.status === "failed") {
                    done = true;
                    overlay.remove();
                    showToast("Erro ao exportar: " + (poll.error || "erro desconhecido"), "error");
                }
            } catch (e) {
                done = true;
                overlay.remove();
                showToast("Erro ao verificar status: " + e.message, "error");
            }
        }
    } catch (err) {
        overlay.remove();
        showToast("Erro ao exportar: " + err.message, "error");
    }
}

// ---------- Bind editor events ----------
function _bindEditorEvents() {
    const video = document.getElementById("editor-video");
    if (video) {
        video.addEventListener("timeupdate", _editorTimeUpdate);
        video.addEventListener("ended", () => {
            _editorResetPlaybackToStart();
        });
    }
    document.getElementById("editor-overlay-canvas")?.addEventListener("click", _editorHandleOverlayClick);
    document.getElementById("editor-play-btn")?.addEventListener("click", _editorTogglePlay);
    document.getElementById("editor-back-btn")?.addEventListener("click", closeEditor);
    document.getElementById("editor-undo-btn")?.addEventListener("click", _editorUndo);
    document.getElementById("editor-redo-btn")?.addEventListener("click", _editorRedo);
    document.getElementById("editor-export-btn")?.addEventListener("click", _editorExport);
    document.getElementById("editor-upload-btn")?.addEventListener("click", () => {
        document.getElementById("editor-video-upload-input")?.click();
    });
    document.getElementById("editor-side-upload-video-btn")?.addEventListener("click", () => {
        const layerVideoInput = document.getElementById("editor-layer-video-upload-input");
        if (!layerVideoInput) return;
        if (!_editor.projectId) {
            showToast("Abra um projeto no editor antes de enviar vídeos em camada.", "error");
            return;
        }
        layerVideoInput.click();
    });
    document.getElementById("editor-side-upload-images-btn")?.addEventListener("click", () => {
        const imageInput = document.getElementById("editor-layer-image-upload-input");
        if (!imageInput) return;
        if (!_editor.projectId) {
            showToast("Abra um projeto no editor antes de enviar imagens em camada.", "error");
            return;
        }
        imageInput.click();
    });

    document.getElementById("editor-media-layer-host")?.addEventListener("pointerdown", _editorOnMediaLayerPointerDown);
    document.addEventListener("pointermove", _editorOnMediaLayerDragMove);
    document.addEventListener("pointerup", _editorOnMediaLayerDragEnd);
    window.addEventListener("resize", _editorRenderMediaLayers);
    document.getElementById("editor-quick-add-text")?.addEventListener("click", _editorAddText);
    document.getElementById("editor-quick-add-subtitle")?.addEventListener("click", _editorAddSubtitle);
    document.getElementById("editor-quick-cut")?.addEventListener("click", _editorSplitAtCurrentTime);
    document.getElementById("editor-quick-layer-order")?.addEventListener("click", _editorCycleMediaLayerOrder);
    document.getElementById("editor-quick-delete")?.addEventListener("click", _editorDeleteSelectedClip);
    document.getElementById("editor-quick-duplicate")?.addEventListener("click", _editorDuplicateSelectedClip);
    document.getElementById("editor-aspect-select")?.addEventListener("change", (e) => {
        _editorSaveState();
        _editorSetOutputAspectRatio(e.target.value);
    });

    // Tool buttons
    document.querySelectorAll(".editor-tool-btn").forEach(btn => {
        const tool = btn.dataset.tool;
        if (!tool) return;
        btn.addEventListener("click", () => _editorSelectTool(tool));
    });

    document.getElementById("editor-timeline-tracks")?.addEventListener("pointerdown", (e) => {
        const clip = e.target.closest(".editor-track-clip");
        if (!clip) return;
        const kind = clip.dataset.kind || "";
        const id = clip.dataset.id || "";
        const track = clip.dataset.track || "";
        const trackEl = clip.parentElement;
        const dragStarted = _editorStartTimelineDrag(kind, id, track, e, trackEl, clip);
        if (dragStarted) {
            e.stopPropagation();
            e.preventDefault();
        }
    });

    document.getElementById("editor-timeline-tracks")?.addEventListener("click", (e) => {
        const label = e.target.closest(".editor-track-label");
        if (label) {
            const track = label.closest(".editor-track")?.dataset.track || "";
            if (_editorIsTrackSelectable(track)) {
                _editorToggleTrackSelection(track);
            }
            e.stopPropagation();
            return;
        }

        const clip = e.target.closest(".editor-track-clip");
        if (!clip) return;
        _editorSelectTimelineClip(
            clip.dataset.kind || "",
            clip.dataset.id || "",
            true,
            clip.dataset.track || ""
        );
        e.stopPropagation();
    });

    document.addEventListener("keydown", _editorHandleDeleteKey);

    // Timeline click-and-hold scrub
    document.getElementById("editor-timeline")?.addEventListener("pointerdown", (e) => {
        const started = _editorStartTimelineScrub(e);
        if (started) {
            e.preventDefault();
        }
    });

    _editorRefreshQuickActions();
    _editorRefreshTrackSelectionUI();
}

// Init editor bindings when DOM ready
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _bindEditorEvents);
} else {
    _bindEditorEvents();
}

bootstrap();




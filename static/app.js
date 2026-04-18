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
                throw new Error("Falha de conexao ao enviar arquivos. Verifique a internet e tente novamente.");
            }
            await new Promise((resolve) => setTimeout(resolve, 800));
        }
    }
    if (!response) {
        throw new Error(lastError?.message || "Falha ao enviar requisicao.");
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
    const subtitle = document.getElementById("auth-subtitle");
    if (subtitle) subtitle.textContent = isLogin
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
    alert(`Nao foi possivel conectar ${platformName}.${reasonText}`);
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
            const render = (detail.renders || []).find(r => r.video_url);
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
    zoomImages: true,
    imageDisplaySeconds: 0,
    promptOptimized: false,
};
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
        alert("Roteiro nao disponivel para este projeto.");
        return;
    }

    // 1. Reset wizard state
    resetCreateWizard();

    // 2. Prepare content BEFORE opening modal
    const modeSelection = document.getElementById("create-mode-selection");
    if (modeSelection) modeSelection.hidden = true;

    document.querySelectorAll(".create-panel").forEach(p => { p.hidden = true; });

    const scriptPanel = document.getElementById("create-panel-script");
    if (scriptPanel) {
        scriptPanel.hidden = false;
        scriptPanel.style.display = "block";
        const steps = scriptPanel.querySelectorAll(".wizard-step");
        steps.forEach(s => {
            const stepNum = parseInt(s.dataset.step);
            if (stepNum === 1) {
                s.hidden = false;
                s.style.animation = "none";
                s.style.opacity = "1";
            } else {
                s.hidden = true;
            }
        });
    }

    const backBtn = document.getElementById("script-back");
    if (backBtn) backBtn.hidden = false;
    const createBtn = document.getElementById("script-create-btn");
    if (createBtn) createBtn.hidden = true;

    const textEl = document.getElementById("script-text");
    if (textEl) textEl.value = project.lyrics_text;
    const countEl = document.getElementById("script-char-count");
    if (countEl) countEl.textContent = project.lyrics_text.length.toLocaleString("pt-BR");
    const titleEl = document.getElementById("script-title");
    if (titleEl) titleEl.value = project.title || "";
    if (project.style_prompt) setSelectedStyles("script-style-tags", project.style_prompt);
    const aspectEl = document.getElementById("script-aspect");
    if (aspectEl && project.aspect_ratio) aspectEl.value = project.aspect_ratio;
    if (project.video_type) scriptData.videoType = project.video_type;
    scriptStep = 2;

    // 3. Open modal
    openModal("modal-new-project");
}

function openCopyFormatModal(projectId) {
    if (!projectId) {
        projectId = _copyFormatSourceProjectId;
    }
    const project = _projectsCache.find(p => p.id === projectId);
    if (!project || project.status !== "completed") {
        alert("Somente videos concluidos podem ser copiados de formato.");
        return;
    }
    _copyFormatSourceProjectId = projectId;
    const sourceEl = document.getElementById("copy-format-source");
    if (sourceEl) {
        sourceEl.textContent = `Origem: ${project.title || "Video"} (${project.aspect_ratio || "16:9"})`;
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
        alert("Somente videos concluidos podem ser copiados.");
        return;
    }
    _copyFormatSourceProjectId = projectId;
    const sourceEl = document.getElementById("copy-choice-source");
    if (sourceEl) {
        sourceEl.textContent = `Origem: ${project.title || "Video"} (${project.aspect_ratio || "16:9"})`;
    }
    openModal("modal-copy-choice");
}

function chooseCopyScript() {
    const projectId = _copyFormatSourceProjectId;
    if (!projectId) {
        alert("Nenhum video selecionado para copia.");
        return;
    }
    closeModal("modal-copy-choice");
    _copyFormatSourceProjectId = 0;
    createSimilar(projectId);
}

function chooseCopyFormat() {
    if (!_copyFormatSourceProjectId) {
        alert("Nenhum video selecionado para copia.");
        return;
    }
    closeModal("modal-copy-choice");
    openCopyFormatModal();
}

async function createFormatCopy() {
    if (!_copyFormatSourceProjectId) {
        alert("Nenhum video selecionado para copia.");
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
        alert("Projeto nao encontrado.");
        return;
    }

    _renameProjectId = project.id;
    _editThumbFile = null;
    const sourceEl = document.getElementById("edit-project-source");
    if (sourceEl) {
        sourceEl.textContent = `Projeto atual: ${project.title || "Video"}`;
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
                const render = renders.find((item) => item && item.video_url) || renders[0] || null;
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
        alert("Digite um nome para o video.");
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
            const area = document.getElementById("script-bgm-upload-area");
            if (area) area.hidden = !bgmToggle.checked;
            if (!bgmToggle.checked) {
                const fi = document.getElementById("script-bgm-file");
                if (fi) fi.value = "";
            }
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
            // Show/hide Grok-only duration buttons (12s, 15s)
            const isGrok = engineVal === "grok";
            const container = eng.closest(".form-group")?.parentElement;
            if (container) {
                container.querySelectorAll(".grok-only").forEach((btn) => {
                    btn.hidden = !isGrok;
                });
                // If a hidden button was selected, reset to 7s
                if (!isGrok) {
                    container.querySelectorAll(".duration-option.grok-only.selected").forEach((btn) => {
                        btn.classList.remove("selected");
                        const def7 = btn.closest(".duration-options")?.querySelector('[data-value="7"]');
                        if (def7) def7.classList.add("selected");
                    });
                }
                // Auto-toggle music checkbox: engines with native audio → uncheck
                const hasNativeAudio = (engineVal === "grok" || engineVal === "seedance");
                const musicCb = container.querySelector("[id$='-realistic-music']");
                if (musicCb) musicCb.checked = !hasNativeAudio;
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
    createMode = mode;
    document.querySelectorAll(".create-tab").forEach((t) => {
        t.classList.toggle("active", t.dataset.createMode === mode);
    });
    document.getElementById("create-mode-selection").hidden = true;
    document.querySelectorAll(".create-panel").forEach((p) => (p.hidden = true));
    const panel = document.getElementById(`create-panel-${mode}`);
    if (panel) panel.hidden = false;
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
        s.hidden = parseInt(s.dataset.step) !== currentDataStep;
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

    if (!prompt) {
        alert("Descreva a cena que voce quer ver no video.");
        return;
    }

    const durBtn = document.querySelector(`#${durationSelectorId} .duration-option.selected`);
    const duration = durBtn ? parseInt(durBtn.dataset.value) : 7;
    const aspectEl = document.getElementById(aspectSelectorId);
    const aspect = aspectEl ? aspectEl.value : "16:9";
    const musicEl = document.getElementById(musicCheckboxId);
    const addMusic = musicEl ? musicEl.checked : true;
    const engineBtn = document.querySelector(`#${engineSelectorId} .engine-option.selected`);
    const engine = engineBtn ? engineBtn.dataset.value : "minimax";
    const engineLabel = engine === "minimax" ? "MiniMax Hailuo" : engine === "wan2" ? "Wan 2.2" : engine === "grok" ? "Grok" : "Seedance 2.0";

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
    setCreateProgress(CREATE_PROGRESS_BASE, "Gerando video realista...", "Preparando...");
    _smoothProgressTarget = 10;
    _startSmoothProgress();

    try {
        // Upload reference image if available
        let imageUploadId = "";
        if (scriptPhotos.length > 0) {
            setCreateProgress(5, "Gerando video realista...", "Enviando imagem de referencia...");
            const uploaded = await uploadTempFileWithRetry(scriptPhotos[0], "image", "imagem de referencia");
            imageUploadId = uploaded.upload_id;
            _smoothProgressTarget = 15;
        }

        setCreateProgress(10, "Gerando video realista...", "Otimizando prompt com IA...");
        _smoothProgressTarget = 15;

        const resp = await api("/video/generate-realistic", {
            method: "POST",
            body: JSON.stringify({
                prompt,
                duration,
                aspect_ratio: aspect,
                generate_audio: addMusic || addNarration,
                add_music: addMusic,
                add_narration: addNarration,
                narration_text: narrationText,
                narration_voice: narrationVoice,
                title: title || "",
                image_upload_id: imageUploadId,
                engine: engine,
                prompt_optimized: scriptData.promptOptimized || false,
                realistic_style: realisticStyle || "",
            }),
        });

        const projectId = resp.id;

        _smoothProgressTarget = 25;
        setCreateProgress(25, "Gerando video realista...", `${engineLabel} esta criando seu video...`);

        await pollRealisticProgress(projectId, engineLabel);

        _stopSmoothProgress();
        setCreateProgress(100, "Concluido!", "Video realista gerado com sucesso!");

        setTimeout(() => {
            closeModal("modal-new-project");
            resetCreateWizard();
            loadProjects();
        }, 1200);

    } catch (e) {
        _stopSmoothProgress();
        let msg = e.message || "Erro ao gerar video realista.";
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
            setCreateProgress(progress, "Gerando video realista...",
                progress < 15 ? "Otimizando prompt com IA..." :
                progress < 80 ? `${label} esta criando seu video...` :
                progress < 90 ? "Baixando video gerado..." :
                progress < 95 ? "Gerando thumbnail..." :
                "Finalizando..."
            );

            if (status === "completed") return;
            if (status === "failed") {
                throw new Error(data.error_message || "Falha na geracao do video realista.");
            }
        } catch (e) {
            if (e.message && !e.message.includes("fetch")) throw e;
        }
    }
    throw new Error("Tempo limite excedido. O video pode ainda estar sendo gerado — verifique seus projetos.");
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
    if (bgmToggle) bgmToggle.checked = true;
    const bgmUploadArea = document.getElementById("script-bgm-upload-area");
    if (bgmUploadArea) bgmUploadArea.hidden = false;

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
    const imageSecondsInput = document.getElementById("script-image-seconds");
    if (imageSecondsInput) imageSecondsInput.value = "";
    toggleScriptPhotoDependentFields();
    toggleAudioMusicOptions();

    // Reset selections
    document.querySelectorAll(".wizard-option.selected").forEach((o) => o.classList.remove("selected"));
    document.querySelectorAll(".duration-option").forEach((d) => {
        d.classList.toggle("selected", d.dataset.value === "60");
    });
    // Reset style tags
    document.querySelectorAll(".style-tag.selected").forEach((t) => t.classList.remove("selected"));

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
            d.classList.toggle("selected", d.dataset.value === "7");
        });
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
        if (!sel) { alert("Escolha o tipo de video."); return; }
        wizardData.videoType = sel.dataset.type;
        // Show/hide style buttons on topic step for realistic mode
        const topicInspirationEl = document.getElementById("wizard-topic-inspiration");
        if (topicInspirationEl) topicInspirationEl.hidden = wizardData.videoType !== "realista";
    }
    if (currentDataStep === 1) {
        const topic = document.getElementById("wizard-topic").value.trim();
        if (!topic) { alert("Digite o tema do video."); return; }
        wizardData.topic = topic;
    }
    if (currentDataStep === 3) {
        const sel = document.querySelector("#create-panel-wizard .wizard-step[data-step='3'] .wizard-option.selected");
        if (!sel) { alert("Escolha o tom da narracao."); return; }
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

        showCreateProgress("Gerando narracao com voz IA...");

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
        if (!selectedCard) { alert("Escolha o tipo de video."); return; }
        scriptData.videoType = selectedCard.dataset.type;
        // Adapt next step UI for video type
        adaptScriptStepForVideoType(scriptData.videoType);
    }

    if (currentDataStep === 1) {
        const title = document.getElementById("script-title").value.trim();
        const text = document.getElementById("script-text").value.trim();

        // Realistic mode: only need prompt text, optionally photos/audio
        if (scriptData.videoType === "realista") {
            if (!title && !text) { alert("Escreva um titulo ou um prompt para o video."); return; }
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
        if (!title) { alert("Digite o titulo do projeto."); return; }

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
                alert("Escreva um roteiro com pelo menos 20 caracteres para a narracao.");
                return;
            }
        } else {
            if (useUserAudioToggle && !hasUserAudio) {
                alert("Envie um audio para usar no video.");
                return;
            }
            if (createNarration && !hasUserAudio && (!text || text.length < 20)) {
                alert("Escreva um roteiro com pelo menos 20 caracteres.");
                return;
            }
            if (!createNarration && scriptPhotos.length === 0 && !hasUserAudio) {
                alert("Envie fotos para criar o video sem narracao.");
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
            if (!sel) { alert("Escolha o tom da narracao."); return; }
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
        alert("Selecione um arquivo de audio para usar no video.");
        return;
    }

    if (useVideoSelected && !scriptData.useCustomVideo) {
        alert("Selecione um video para enviar.");
        return;
    }

    if (!scriptData.text && !scriptData.useCustomImages && !scriptData.useCustomAudio && !scriptData.useCustomVideo) {
        alert("Sem narracao, envie fotos, video ou audio para criar um video personalizado.");
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
        ? "Preparando video com legendas..."
        : scriptData.useCustomAudio
        ? "Preparando video a partir do seu audio..."
        : (scriptData.text ? "Gerando narracao com voz IA..." : "Preparando video com fotos (musica automatica se nao enviar)...");
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
            showCreateProgress("Enviando video...", { progress: 15, stage: "Enviando arquivos..." });
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
            showCreateProgress("Enviando audio principal...", { progress: 48, stage: "Enviando arquivos..." });
            const uploadedMainAudio = await uploadTempFileWithRetry(scriptUserAudioFile, "audio", "audio principal");
            uploadedMainAudioId = uploadedMainAudio.upload_id || "";
        }

        if (scriptData.removeVocals) {
            karaokeOperationId = createKaraokeOperationId();
            showCreateProgress("Removendo voz do audio...", { progress: 52, stage: "Removendo voz..." });
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
        formData.append("title", scriptData.title || "Video com roteiro");
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
        setCreateProgress(100, "Concluido", "Audio processado com sucesso.");

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
        alert("Formato nao suportado. Envie um video MP4, MOV, AVI ou WEBM.");
        event.target.value = "";
        return;
    }
    if (file.size > MAX_VIDEO_SIZE) {
        alert("Video excede 500MB. Reduza o tamanho e tente novamente.");
        event.target.value = "";
        return;
    }

    scriptUserVideoFile = file;
    const nameEl = document.getElementById("script-video-name");
    if (nameEl) {
        nameEl.hidden = false;
        nameEl.textContent = "Video selecionado: " + file.name + " (" + (file.size / 1024 / 1024).toFixed(1) + "MB)";
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
        alert("Formato nao suportado. Envie um arquivo de audio valido.");
        event.target.value = "";
        return;
    }
    if (file.size > MAX_AUDIO_SIZE) {
        alert("Audio excede 80MB. Reduza o tamanho e tente novamente.");
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
        textarea.placeholder = "Narracao desativada. O video sera criado com fotos + fundo musical.";
    } else {
        textarea.placeholder = "Cole ou escreva o roteiro completo da narracao aqui...";
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
            alert(`Formato nao suportado: ${file.name}. Use JPG, PNG ou WebP.`);
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
});

function adaptScriptStepForVideoType(videoType) {
    const isRealistic = videoType === "realista";
    const videoSection = document.getElementById("script-video-upload-section");
    const textarea = document.getElementById("script-text");
    if (videoSection) videoSection.hidden = isRealistic;
    if (textarea) {
        textarea.placeholder = isRealistic
            ? "Cole ou escreva seu prompt aqui..."
            : "Cole ou escreva o roteiro completo da narracao aqui...";
    }
    // Reset video toggle if switching to realistic
    if (isRealistic) {
        const videoCb = document.getElementById("script-use-video");
        if (videoCb && videoCb.checked) {
            videoCb.checked = false;
            toggleVideoUpload();
        }
    }
}

function showAiSuggestPanel() {
    const isRealistic = scriptData.videoType === "realista";
    // Adapt AI suggest panel for mode
    document.getElementById("ai-suggest-title").textContent = isRealistic ? "Gerar prompt com IA" : "Gerar roteiro com IA";
    document.getElementById("ai-suggest-hint").textContent = isRealistic
        ? "Descreva a cena e a IA criara um prompt cinematografico profissional"
        : "Descreva o tema e a IA criara um roteiro completo";
    document.getElementById("ai-suggest-topic").placeholder = isRealistic
        ? "Ex: uma cachorra adotou um gatinho, produto girando..."
        : "Ex: beneficios da meditacao, como fazer pao caseiro...";
    document.getElementById("ai-suggest-tone-group").hidden = isRealistic;
    document.getElementById("ai-suggest-style-group").hidden = !isRealistic;
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
    const topic = document.getElementById("ai-suggest-topic").value.trim();
    if (!topic) { alert("Digite o tema do video."); return; }
    const isRealistic = scriptData.videoType === "realista";

    if (isRealistic) {
        // Generate optimized prompt for the selected engine
        const style = document.getElementById("ai-suggest-style").value;
        const engineBtn = document.querySelector("#wizard-realistic-engine .engine-option.selected") || document.querySelector("#script-realistic-engine .engine-option.selected");
        const engine = engineBtn ? engineBtn.dataset.value : "minimax";
        const engineLabel = engine === "grok" ? "Grok" : engine === "minimax" ? "MiniMax" : engine === "wan2" ? "Wan 2.2" : "Seedance";
        showCreateProgress("Gerando prompt cinematografico com IA...", {
            progress: 30,
            stage: `Otimizando prompt ${engineLabel}...`,
        });
        try {
            const result = await api("/video/generate-realistic-prompt", {
                method: "POST",
                body: JSON.stringify({ topic, style, engine }),
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
                duration_seconds: parseInt(document.getElementById("ai-suggest-duration").value),
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

async function watchVideo(projectId) {
    try {
        const project = await api(`/video/projects/${projectId}`);
        if (!project.renders || !project.renders.length) {
            alert("Nenhum video renderizado encontrado.");
            return;
        }
        const render = project.renders.find((item) => item && item.video_url);
        if (!render) {
            alert("Este video nao esta mais disponivel para reproducao.");
            return;
        }
        const playerModal = document.getElementById("modal-player");
        const video = document.getElementById("player-video");
        if (!playerModal || !video) {
            window.open(render.video_url, "_blank");
            return;
        }
        document.getElementById("player-title").textContent = project.title || "Video";
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
                for (const render of detail.renders || []) {
                    if (!render.video_url) {
                        continue;
                    }
                    const duration = render.duration != null
                        ? `${Math.floor(render.duration / 60)}:${String(Math.round(render.duration % 60)).padStart(2, "0")}`
                        : "?";
                    const optionLabel = `[${project.title || "Sem titulo"}] ${render.format} - ${duration}`;
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
        alert("Selecione um video");
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
                const title = draft.title.trim() || "Sem titulo";
                const description = draft.description.trim();
                const descriptionPreview = description
                    ? (description.length > 140 ? `${description.slice(0, 140).trim()}...` : description)
                    : "Sem descricao.";
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
        alert("Selecione um video para salvar rascunho.");
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
        alert("Rascunho invalido.");
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
        alert("Este video nao esta mais disponivel para abrir o rascunho.");
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
        alert("Rascunho invalido.");
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
        alert("Rascunho invalido.");
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
        alert("Publicacao iniciada.");
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
        alert("Selecione um video antes de agendar.");
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
        alert("Escolha data e horario para agendar.");
        if (dtInput) dtInput.focus();
        return;
    }

    const scheduledDate = new Date(rawValue);
    if (Number.isNaN(scheduledDate.getTime())) {
        alert("Data/hora invalida.");
        return;
    }
    if (scheduledDate.getTime() <= Date.now() + 30000) {
        alert("Escolha um horario futuro para o agendamento.");
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
        alert("Publicacao agendada com sucesso.");
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
        return "A API do YouTube nao esta ativada no projeto Google Cloud.\n\nPasso a passo:\n1. Acesse console.cloud.google.com\n2. Selecione o projeto do CriaVideo\n3. Va em APIs e Servicos > Biblioteca\n4. Busque 'YouTube Data API v3' e clique em Ativar\n5. Aguarde alguns minutos e tente publicar novamente.";
    }
    if (lower.includes("accessnotconfigured") || lower.includes("api has not been enabled")) {
        return "Uma API necessaria nao esta ativada no Google Cloud. Acesse console.cloud.google.com, ative a API indicada e tente novamente.";
    }
    if (lower.includes("invalid_grant") || lower.includes("token has been expired") || lower.includes("token has been revoked")) {
        return "Sua conexao com a plataforma expirou.\n\nPasso a passo:\n1. Va na aba 'Contas' na pagina de publicacao\n2. Desconecte a conta afetada\n3. Conecte novamente\n4. Tente publicar de novo.";
    }
    if (lower.includes("custom video thumbnails") || lower.includes("thumbnails/set")) {
        return "O video foi publicado, mas o YouTube bloqueou a thumbnail personalizada desta conta/canal.\n\nComo resolver:\n1. No YouTube Studio, confirme se o canal esta verificado (telefone)\n2. Ative recursos avancados/intermediarios da conta\n3. Aguarde alguns minutos apos a verificacao\n4. Publique novamente para aplicar a thumbnail";
    }
    if (lower.includes("quota") || lower.includes("rate limit") || lower.includes("too many requests")) {
        return "Limite de uso da API atingido. Aguarde algumas horas e tente novamente, ou verifique sua cota no painel do Google Cloud.";
    }
    if (lower.includes("forbidden") || lower.includes("403")) {
        return "Acesso negado pela plataforma. Verifique se a conta conectada tem permissao para publicar videos e se todas as APIs necessarias estao ativadas.";
    }
    if (lower.includes("unauthorized") || lower.includes("401")) {
        return "Autenticacao falhou.\n\nPasso a passo:\n1. Va na aba 'Contas'\n2. Desconecte e reconecte a conta\n3. Tente publicar novamente.";
    }
    if (lower.includes("not found") || lower.includes("file not found") || lower.includes("render file")) {
        return "O arquivo de video nao foi encontrado no servidor. Tente renderizar o video novamente antes de publicar.";
    }
    if (lower.includes("social account not found")) {
        return "A conta social nao foi encontrada. Reconecte sua conta na aba 'Contas' e tente novamente.";
    }
    if (lower.includes("network") || lower.includes("timeout") || lower.includes("connection")) {
        return "Erro de conexao com a plataforma. Verifique sua internet e tente novamente em alguns minutos.";
    }
    return "Erro ao publicar: " + raw + "\n\nSe o problema persistir, entre em contato com o suporte.";
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
        title.textContent = job.status === "failed" ? "Motivo da falha" : "Aviso da publicacao";
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
                ? "Escolha qual conta desta plataforma sera usada na publicacao."
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

async function loadAutoSchedules() {
    const container = document.getElementById("auto-schedules-list");
    if (!container) return;
    try {
        const data = await api("/automation/schedules");
        if (!data.length) {
            container.innerHTML = "<p class='loading'>Nenhuma automacao criada.</p>";
            return;
        }
        container.innerHTML = data.map(renderAutoCard).join("");
    } catch (error) {
        container.innerHTML = `<p class="loading">Erro: ${esc(error.message)}</p>`;
    }
}

function renderAutoCard(s) {
    const typeBadge = s.video_type === "music"
        ? '<span class="badge badge-processing">Musical</span>'
        : '<span class="badge badge-completed">Narrado</span>';
    const modeBadge = s.creation_mode === "manual"
        ? '<span class="badge">Manual</span>'
        : '<span class="badge badge-queued">Auto</span>';
    const statusBadge = s.is_active
        ? '<span class="badge badge-completed">Ativo</span>'
        : '<span class="badge badge-failed">Pausado</span>';

    const themes = (s.themes || []);
    const pendingCount = themes.filter(t => t.status === "pending").length;
    const doneCount = themes.filter(t => t.status === "done").length;

    const themeListHtml = themes.map(t => {
        let icon, statusClass, statusLabel;
        if (t.status === "done" || t.status === "completed") {
            icon = "✅"; statusClass = "theme-done"; statusLabel = "Publicado";
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

    return `<div class="auto-card" id="auto-card-${s.id}">
        <div class="auto-card-header">
            <h4>${esc(s.name || "Automacao")}</h4>
            ${statusBadge}
        </div>
        <div class="auto-card-badges">${typeBadge} ${modeBadge}</div>
        <div class="auto-card-meta">
            <span>${freq} as ${esc(s.time_local || s.time_utc)}</span>
            <span>${pendingCount} pendentes / ${doneCount} feitos</span>
        </div>
        <div class="auto-card-detail">
            <strong>Temas:</strong>
            <ul class="auto-theme-list">${themeListHtml || "<li class='loading'>Sem temas</li>"}</ul>
            <div class="auto-theme-add" style="margin-top:0.5rem">
                <input type="text" class="input" placeholder="Novo tema..." id="add-theme-input-${s.id}" maxlength="200">
                <button class="btn btn-primary btn-sm" type="button" onclick="addAutoThemeToSchedule(${s.id})">+</button>
            </div>
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

    // reset selections
    document.querySelectorAll("#modal-new-automation .auto-type-card").forEach(c => c.classList.remove("active"));
    const narrationBtn = document.querySelector('#modal-new-automation [data-video-type="narration"]');
    if (narrationBtn) narrationBtn.classList.add("active");
    const autoBtn = document.querySelector('#modal-new-automation [data-creation-mode="auto"]');
    if (autoBtn) autoBtn.classList.add("active");

    const manual = document.getElementById("auto-manual-settings");
    if (manual) manual.hidden = true;
    const musicPanel = document.getElementById("auto-music-settings");
    if (musicPanel) musicPanel.hidden = true;

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

    showAutoStep(1);
    openModal("modal-new-automation");
}

function showAutoStep(step) {
    _autoWizardStep = step;
    document.querySelectorAll("#modal-new-automation .auto-step").forEach(el => {
        el.classList.toggle("active", parseInt(el.dataset.autoStep) === step);
    });
    document.querySelectorAll("#modal-new-automation .auto-dot").forEach(el => {
        el.classList.toggle("active", parseInt(el.dataset.autoStep) <= step);
    });
    const btnBack = document.getElementById("auto-btn-back");
    const btnNext = document.getElementById("auto-btn-next");
    const btnCreate = document.getElementById("auto-btn-create");
    if (btnBack) btnBack.hidden = step === 1;
    if (btnNext) btnNext.hidden = step === 4;
    if (btnCreate) btnCreate.hidden = step !== 4;
}

function autoStepNext() {
    if (_autoWizardStep === 3 && _autoWizardThemes.length === 0) {
        alert("Digite o tema e aperte no botão + para adicionar.");
        const addBtn = document.getElementById("auto-add-theme-btn");
        if (addBtn) { addBtn.classList.add("btn-error-pulse"); setTimeout(() => addBtn.classList.remove("btn-error-pulse"), 2000); }
        return;
    }
    if (_autoWizardStep < 4) showAutoStep(_autoWizardStep + 1);
}

function autoStepBack() {
    if (_autoWizardStep > 1) showAutoStep(_autoWizardStep - 1);
}

function selectAutoVideoType(type) {
    document.querySelectorAll('#modal-new-automation [data-auto-step="1"] .auto-type-card').forEach(c => {
        c.classList.toggle("active", c.dataset.videoType === type);
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
    const musicPanel = document.getElementById("auto-music-settings");
    if (narrationPanel) narrationPanel.hidden = !(mode === "manual" && videoType === "narration");
    if (musicPanel) musicPanel.hidden = !(mode === "manual" && videoType === "music");
    // hide vocalist when instrumental
    const vocalistGroup = document.getElementById("auto-music-vocalist-group");
    if (vocalistGroup) {
        const musicMode = document.getElementById("auto-music-mode")?.value;
        vocalistGroup.hidden = musicMode === "instrumental";
    }
}

function toggleAutoMusicLyrics() {
    const mode = document.getElementById("auto-music-mode")?.value;
    const lyricsGroup = document.getElementById("auto-music-lyrics-group");
    const vocalistGroup = document.getElementById("auto-music-vocalist-group");
    if (lyricsGroup) lyricsGroup.hidden = mode !== "lyrics";
    if (vocalistGroup) vocalistGroup.hidden = mode === "instrumental";
}

function getSelectedAutoVideoType() {
    const activeCard = document.querySelector('#modal-new-automation [data-auto-step="1"] .auto-type-card.active');
    return activeCard ? activeCard.dataset.videoType : "narration";
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
    ul.innerHTML = _autoWizardThemes.map((t, i) => `
        <li class="auto-theme-item">
            <span class="theme-status">${i + 1}.</span>
            <span class="theme-text">${esc(t)}</span>
            <button class="theme-remove" onclick="removeAutoWizardTheme(${i})" type="button">&times;</button>
        </li>
    `).join("");
}

async function loadAutoAccountOptions() {
    const select = document.getElementById("auto-account");
    if (!select) return;
    try {
        const accounts = await api("/social/accounts");
        const platform = document.getElementById("auto-platform")?.value || "youtube";
        const filtered = accounts.filter(a => a.platform === platform);
        if (!filtered.length) {
            select.innerHTML = "<option value=''>Conecte uma conta desta plataforma</option>";
            return;
        }
        select.innerHTML = filtered.map(a => {
            const label = socialAccountDisplayName(a);
            return `<option value="${a.id}">${esc(label)}</option>`;
        }).join("");
    } catch {
        select.innerHTML = "<option value=''>Nenhuma conta</option>";
    }
}

async function createAutoSchedule() {
    const name = (document.getElementById("auto-name")?.value || "").trim();
    if (!name) {
        alert("Informe um nome para a automacao.");
        return;
    }
    if (_autoWizardThemes.length === 0) {
        alert("Digite o tema e aperte no botão + para adicionar.");
        showAutoStep(3);
        const addBtn = document.getElementById("auto-add-theme-btn");
        if (addBtn) { addBtn.classList.add("btn-error-pulse"); setTimeout(() => addBtn.classList.remove("btn-error-pulse"), 2000); }
        return;
    }
    const accountId = parseInt(document.getElementById("auto-account")?.value || "0", 10);
    if (!accountId) {
        alert("Selecione uma conta social.");
        return;
    }

    const videoType = getSelectedAutoVideoType();
    const creationMode = getSelectedAutoCreationMode();
    const platform = document.getElementById("auto-platform")?.value || "youtube";
    const frequency = document.getElementById("auto-frequency")?.value || "daily";
    const timeUtc = document.getElementById("auto-time")?.value || "14:00";
    const userTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const dayOfWeek = frequency === "weekly" ? parseInt(document.getElementById("auto-dow")?.value || "0", 10) : null;

    let defaultSettings = null;
    if (creationMode === "manual" && videoType === "narration") {
        defaultSettings = {
            tone: document.getElementById("auto-tone")?.value || "informativo",
            voice: document.getElementById("auto-voice")?.value || "onyx",
            style: document.getElementById("auto-style")?.value || "cinematic, vibrant colors, dynamic lighting",
            duration: parseInt(document.getElementById("auto-duration")?.value || "120", 10),
            aspect_ratio: document.getElementById("auto-aspect")?.value || "16:9",
        };
    } else if (creationMode === "manual" && videoType === "music") {
        const musicMode = document.getElementById("auto-music-mode")?.value || "generate";
        defaultSettings = {
            music_mode: musicMode,
            music_mood: document.getElementById("auto-music-mood")?.value || "alegre",
            music_genre: document.getElementById("auto-music-genre")?.value || "gospel",
            music_vocalist: musicMode === "instrumental" ? "" : (document.getElementById("auto-music-vocalist")?.value || "female"),
            music_duration: parseInt(document.getElementById("auto-music-duration")?.value || "0", 10) || null,
            music_language: document.getElementById("auto-music-language")?.value || "pt-BR",
            music_lyrics: musicMode === "lyrics" ? (document.getElementById("auto-music-lyrics")?.value || "") : "",
            style: document.getElementById("auto-music-style")?.value || "cinematic, vibrant colors, dynamic lighting",
            aspect_ratio: document.getElementById("auto-music-aspect")?.value || "16:9",
        };
    }

    const btn = document.getElementById("auto-btn-create");
    if (btn) { btn.disabled = true; btn.textContent = "Criando..."; }

    try {
        await api("/automation/schedules", {
            method: "POST",
            body: JSON.stringify({
                name,
                video_type: videoType,
                creation_mode: creationMode,
                platform,
                social_account_id: accountId,
                frequency,
                time_local: timeUtc,
                timezone: userTimezone,
                day_of_week: dayOfWeek,
                default_settings: defaultSettings,
                themes: _autoWizardThemes,
            }),
        });
        closeModal("modal-new-automation");
        loadAutoSchedules();
    } catch (error) {
        alert(`Erro: ${error.message}`);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = "Ativar Automacao"; }
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
            throw new Error("A plataforma nao retornou URL de autorizacao");
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
    if (platform === "instagram" && (lower.includes("facebook_app_id") || lower.includes("facebook app_id") || lower.includes("instagram oauth nao configurado"))) {
        return [
            "Erro ao conectar Instagram: faltam configuracoes no servidor.",
            "",
            "Como resolver:",
            "1. Criar/abrir um app no Meta for Developers",
            "2. Habilitar Facebook Login e permissoes do Instagram",
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
        alert("Conta nao encontrada.");
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
        alert("Nao foi possivel acessar o microfone. Verifique as permissoes do navegador.");
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
    if (hint) hint.textContent = "Processando audio...";
    const result = await trimAudioTo30s(file);
    if (result.tooLarge) {
        alert("Audio muito grande. Grave pelo microfone ou envie um arquivo menor (max 10MB).");
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
    if (!blob) { alert("Grave ou envie um audio primeiro."); return; }

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

bootstrap();

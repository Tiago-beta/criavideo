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
    updateCreditsDisplay();
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
    if (id === "modal-player") {
        document.getElementById("app").classList.add("sidebar-collapsed");
    }
}

function closeModal(id) {
    document.getElementById(id).classList.remove("open");
    if (id === "modal-player") {
        const video = document.getElementById("player-video");
        if (video) {
            video.pause();
            video.src = "";
        }
        document.getElementById("app").classList.remove("sidebar-collapsed");
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

async function loadProjects() {
    const container = document.getElementById("projects-list");
    try {
        const data = await api("/video/projects");
        _projectsCache = data;
        if (!data.length) {
            container.innerHTML = "<p class='loading'>Nenhum projeto ainda. Crie o primeiro.</p>";
            return;
        }
        container.innerHTML = data.map((project) => {
            const dt = project.created_at ? new Date(project.created_at) : null;
            const dateStr = dt ? `${String(dt.getHours()).padStart(2,"0")}:${String(dt.getMinutes()).padStart(2,"0")} · ${dt.toLocaleDateString("pt-BR")}` : "-";
            const statusPt = _statusPt(project.status);
            const thumbClick = project.status === "completed" ? `onclick="watchVideo(${project.id})" style="cursor:pointer"` : "";
            const thumb = project.thumbnail_url
                ? `<img class="card-thumb" src="${project.thumbnail_url}" alt="" loading="lazy" ${thumbClick}>`
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
                            ${project.status === "completed" ? `<button class="card-btn card-btn-watch" onclick="watchVideo(${project.id})" type="button" title="Assistir"><svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>` : ""}
                            ${(project.status === "pending" || project.status === "failed") ? `<button class="card-btn card-btn-generate" onclick="generateVideo(${project.id})" type="button" title="Gerar vídeo"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>` : ""}
                            ${project.status === "completed" ? `<button class="card-btn card-btn-similar" onclick="openCopyChoiceModal(${project.id})" type="button" title="Criar copia"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>` : (project.lyrics_text ? `<button class="card-btn card-btn-similar" onclick="createSimilar(${project.id})" type="button" title="Criar Semelhante"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>` : "")}
                            <button class="card-btn card-btn-delete" onclick="deleteProject(${project.id})" type="button" title="Excluir"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg></button>
                        </div>
                        <span class="card-date">${dateStr}</span>
                    </div>
                </div>
            `;
        }).join("");
        // Start polling for in-progress projects
        _pollInProgress(data);
    } catch (error) {
        container.innerHTML = `<p class="loading">Erro: ${esc(error.message)}</p>`;
    }
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

let _pollTimer = null;
function _pollInProgress(projects) {
    if (_pollTimer) clearInterval(_pollTimer);
    const active = projects.filter(p =>
        p.status !== "completed" && p.status !== "failed" && p.status !== "pending"
    );
    if (!active.length) return;
    _pollTimer = setInterval(async () => {
        try {
            const data = await api("/video/projects");
            _projectsCache = data;
            const stillActive = data.filter(p =>
                p.status !== "completed" && p.status !== "failed" && p.status !== "pending"
            );
            // Update cards in-place instead of full re-render
            for (const p of data) {
                _updateCardInPlace(p);
            }
            if (!stillActive.length) {
                clearInterval(_pollTimer);
                _pollTimer = null;
                loadProjects(); // Full refresh to get thumbnails
            }
        } catch (_) {
            clearInterval(_pollTimer);
            _pollTimer = null;
        }
    }, 3000);
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
let wizardData = { topic: "", tone: "", voice: "", duration: 60, aspect: "16:9", style: "" };
let scriptStep = 1;
let scriptData = { text: "", tone: "", voice: "", title: "", aspect: "16:9", style: "", useCustomImages: false, createNarration: true, enableSubtitles: true, zoomImages: true, imageDisplaySeconds: 0 };

async function createSimilar(projectId) {
    const project = _projectsCache.find(p => p.id === projectId);
    if (!project || !project.lyrics_text) {
        alert("Roteiro nao disponivel para este projeto.");
        return;
    }
    resetCreateWizard();
    openModal("modal-new-project");
    switchCreateMode("script");
    document.getElementById("script-text").value = project.lyrics_text;
    document.getElementById("script-char-count").textContent = project.lyrics_text.length.toLocaleString("pt-BR");
    document.getElementById("script-title").value = project.title || "";
    if (project.style_prompt) {
        setSelectedStyles("script-style-tags", project.style_prompt);
    }
    if (project.aspect_ratio) {
        document.getElementById("script-aspect").value = project.aspect_ratio;
    }
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

    // Wizard option clicks (event delegation)
    document.getElementById("modal-new-project").addEventListener("click", (e) => {
        const opt = e.target.closest(".wizard-option");
        if (opt) {
            const grid = opt.closest(".wizard-grid");
            grid.querySelectorAll(".wizard-option").forEach((o) => o.classList.remove("selected"));
            opt.classList.add("selected");
            // When selecting a builtin voice, deselect any persona selection
            const voiceSelector = opt.closest(".voice-selector");
            if (voiceSelector) {
                voiceSelector.querySelectorAll(".persona-item.selected").forEach(o => o.classList.remove("selected"));
            }
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
    wizardData = { topic: "", tone: "", voice: "", voiceProfileId: 0, duration: 60, aspect: "16:9", style: "" };
    scriptStep = 1;
    scriptData = { text: "", tone: "", voice: "", voiceProfileId: 0, title: "", aspect: "16:9", style: "", useCustomImages: false, createNarration: true, enableSubtitles: true, zoomImages: true, imageDisplaySeconds: 0 };

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
    updateWizardUI("create-panel-wizard", wizardStep, 5, "wizard");
    updateWizardUI("create-panel-script", scriptStep, 5, "script");
    document.getElementById("wizard-topic").value = "";
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

    // Reset subtitle toggle
    const subCb = document.getElementById("script-enable-subtitles");
    if (subCb) subCb.checked = true;
    const imageSecondsInput = document.getElementById("script-image-seconds");
    if (imageSecondsInput) imageSecondsInput.value = "";
    toggleScriptPhotoDependentFields();

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
        const personaSel = document.querySelector("#wizard-persona-list .persona-item.selected");
        const builtinSel = document.querySelector("#create-panel-wizard .wizard-step[data-step='3'] .wizard-option.selected");
        if (personaSel) {
            wizardData.voice = personaSel.dataset.value;
            wizardData.voiceProfileId = parseInt(personaSel.dataset.profileId || "0");
        } else if (builtinSel) {
            wizardData.voice = builtinSel.dataset.value;
            wizardData.voiceProfileId = 0;
        } else {
            alert("Escolha a voz."); return;
        }
    }
    wizardStep = Math.min(wizardStep + 1, 5);
    updateWizardUI("create-panel-wizard", wizardStep, 5, "wizard");
}

function wizardBack() {
    wizardStep = Math.max(wizardStep - 1, 1);
    updateWizardUI("create-panel-wizard", wizardStep, 5, "wizard");
}

async function handleWizardCreate() {
    // Collect step 4 (style) + step 5 (duration/format) data
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
                title: wizardData.topic,
                aspect_ratio: wizardData.aspect,
                style_prompt: wizardData.style,
                pause_level: wizardData.pauseLevel,
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
        const title = document.getElementById("script-title").value.trim();
        const text = document.getElementById("script-text").value.trim();
        const usePhotos = document.getElementById("script-use-photos").checked;
        const createNarration = !usePhotos || document.getElementById("script-create-narration").checked;
        if (!title) { alert("Digite o titulo do projeto."); return; }
        if (createNarration && (!text || text.length < 20)) {
            alert("Escreva um roteiro com pelo menos 20 caracteres.");
            return;
        }
        if (!createNarration && scriptPhotos.length === 0) {
            alert("Envie fotos para criar o video sem narracao.");
            return;
        }
        scriptData.title = title;
        scriptData.useCustomImages = usePhotos && scriptPhotos.length > 0;
        scriptData.createNarration = createNarration;
        scriptData.text = createNarration ? text : "";
        if (!createNarration) {
            scriptStep = 4;
            updateWizardUI("create-panel-script", scriptStep, 5, "script");
            return;
        }
    }
    if (scriptStep === 2) {
        if (!scriptData.text) {
            scriptData.tone = "informativo";
        } else {
        const sel = document.querySelector("#create-panel-script .wizard-step[data-step='2'] .wizard-option.selected");
        if (!sel) { alert("Escolha o tom da narracao."); return; }
        scriptData.tone = sel.dataset.value;
        }
    }
    if (scriptStep === 3) {
        if (!scriptData.text) {
            scriptData.voice = "onyx";
            scriptData.voiceProfileId = 0;
        } else {
        const personaSel = document.querySelector("#script-persona-list .persona-item.selected");
        const builtinSel = document.querySelector("#create-panel-script .wizard-step[data-step='3'] .wizard-option.selected");
        if (personaSel) {
            scriptData.voice = personaSel.dataset.value;
            scriptData.voiceProfileId = parseInt(personaSel.dataset.profileId || "0");
        } else if (builtinSel) {
            scriptData.voice = builtinSel.dataset.value;
            scriptData.voiceProfileId = 0;
        } else {
            alert("Escolha a voz."); return;
        }
        }
    }
    scriptStep = Math.min(scriptStep + 1, 5);
    updateWizardUI("create-panel-script", scriptStep, 5, "script");
}

function scriptBack() {
    if (scriptStep === 4 && scriptData.useCustomImages && !scriptData.createNarration) {
        scriptStep = 1;
    } else {
        scriptStep = Math.max(scriptStep - 1, 1);
    }
    updateWizardUI("create-panel-script", scriptStep, 5, "script");
}

async function handleScriptCreate() {
    scriptData.title = document.getElementById("script-title").value.trim();
    scriptData.aspect = document.getElementById("script-aspect").value;
    scriptData.style = getSelectedStyles("script-style-tags");
    scriptData.pauseLevel = getSelectedPause("script-pause-options");
    const usePhotosSelected = document.getElementById("script-use-photos").checked;
    scriptData.zoomImages = true;
    scriptData.imageDisplaySeconds = usePhotosSelected
        ? (parseFloat(document.getElementById("script-image-seconds").value || "0") || 0)
        : 0;
    scriptData.useCustomImages = usePhotosSelected && scriptPhotos.length > 0;
    scriptData.createNarration = !scriptData.useCustomImages || document.getElementById("script-create-narration").checked;
    if (!scriptData.createNarration) {
        scriptData.text = "";
        scriptData.voice = "";
        scriptData.voiceProfileId = 0;
    }
    scriptData.enableSubtitles = usePhotosSelected ? document.getElementById("script-enable-subtitles").checked : true;
    const bgmEnabled = document.getElementById("script-enable-bgm") ? document.getElementById("script-enable-bgm").checked : true;
    const bgmFileInput = document.getElementById("script-bgm-file");
    const bgmFile = bgmEnabled && bgmFileInput && bgmFileInput.files ? bgmFileInput.files[0] : null;

    if (!scriptData.text && !scriptData.useCustomImages) {
        alert("Sem narracao, envie fotos para criar um video personalizado.");
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

    showCreateProgress(scriptData.text ? "Gerando narracao com voz IA..." : "Preparando video com fotos (musica automatica se nao enviar)...");

    try {
        const uploadedImageIds = [];
        let uploadedMusicId = "";

        if (scriptData.useCustomImages) {
            for (let i = 0; i < scriptPhotos.length; i++) {
                showCreateProgress(`Enviando foto ${i + 1}/${scriptPhotos.length}...`);
                const uploaded = await uploadTempFileWithRetry(scriptPhotos[i], "image", `foto ${i + 1}`);
                uploadedImageIds.push(uploaded.upload_id);
            }
        }

        if (bgmFile) {
            showCreateProgress("Enviando fundo musical...");
            const uploadedAudio = await uploadTempFileWithRetry(bgmFile, "audio", "audio");
            uploadedMusicId = uploadedAudio.upload_id || "";
        }

        showCreateProgress(scriptData.text ? "Gerando narracao com voz IA..." : "Preparando video com fotos (musica automatica se nao enviar)...");

        const formData = new FormData();
        formData.append("script", scriptData.text);
        formData.append("voice", scriptData.voice || "");
        formData.append("voice_profile_id", String(scriptData.voiceProfileId || 0));
        formData.append("title", scriptData.title || "Video com roteiro");
        formData.append("aspect_ratio", scriptData.aspect);
        formData.append("style_prompt", scriptData.style);
        formData.append("pause_level", scriptData.pauseLevel || "normal");
        formData.append("enable_subtitles", scriptData.enableSubtitles ? "true" : "false");
        formData.append("zoom_images", scriptData.zoomImages ? "true" : "false");
        formData.append("image_display_seconds", String(scriptData.imageDisplaySeconds > 0 ? scriptData.imageDisplaySeconds : 0));
        formData.append("no_background_music", bgmEnabled ? "false" : "true");
        if (uploadedMusicId) {
            formData.append("background_music_id", uploadedMusicId);
        }
        if (uploadedImageIds.length > 0) {
            for (const uploadId of uploadedImageIds) {
                formData.append("custom_image_ids", uploadId);
            }
        }

        const result = await apiForm("/video/generate-audio", formData);

        closeModal("modal-new-project");
        updateCreditsDisplay();
        pollProject(result.id);
        loadProjects();
    } catch (error) {
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
    const endpoint = kind === "audio" ? "/video/upload-temp-audio" : "/video/upload-temp-image";
    const maxRetries = 5;

    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
            const fd = new FormData();
            fd.append("file", file);
            showCreateProgress(`Enviando ${label}...`);
            const result = await apiForm(endpoint, fd);
            return result;
        } catch (error) {
            if (attempt === maxRetries) {
                throw new Error(`Falha ao enviar ${label} apos ${maxRetries} tentativas. Verifique sua conexao.`);
            }
            const delay = Math.min(5000, 500 * Math.pow(2, attempt - 1));
            showCreateProgress(`Reenviando ${label} (${attempt}/${maxRetries})...`);
            await new Promise((resolve) => setTimeout(resolve, delay));
        }
    }
}

// ── AI Script Suggestion ──

// ── Photo Upload (Meu Roteiro) ──
let scriptPhotos = []; // array of File objects
const MAX_PHOTOS = 20;
const MAX_PHOTO_SIZE = 10 * 1024 * 1024; // 10MB

function togglePhotoUpload() {
    const checked = document.getElementById("script-use-photos").checked;
    document.getElementById("script-photo-area").hidden = !checked;
    toggleScriptPhotoDependentFields();
    if (!checked) {
        scriptData.createNarration = true;
        const narCb = document.getElementById("script-create-narration");
        if (narCb) narCb.checked = true;
    }
    updateNarrationChoiceVisibility();
}

function toggleScriptPhotoDependentFields() {
    const usePhotos = document.getElementById("script-use-photos").checked;
    const imageSecondsGroup = document.getElementById("script-photo-seconds-group");
    const subtitlesGroup = document.getElementById("script-subtitles-group");
    if (imageSecondsGroup) imageSecondsGroup.hidden = !usePhotos;
    if (subtitlesGroup) subtitlesGroup.hidden = !usePhotos;
}

function toggleScriptNarration() {
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
    if (!dz) return;
    dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
    dz.addEventListener("dragleave", () => dz.classList.remove("dragover"));
    dz.addEventListener("drop", (e) => {
        e.preventDefault();
        dz.classList.remove("dragover");
        const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith("image/"));
        addPhotos(files);
    });
});

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
        const render = project.renders[0];
        document.getElementById("player-title").textContent = project.title || "Video";
        const video = document.getElementById("player-video");
        video.src = render.video_url;
        video.load();
        video.play().catch(() => {});
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

window.closeModal = closeModal;
window.createProject = createProjectFromLibrary;
window.generateVideo = generateVideo;
window.deleteProject = deleteProject;
window.watchVideo = watchVideo;
window.createSimilar = createSimilar;
window.openCopyChoiceModal = openCopyChoiceModal;
window.chooseCopyScript = chooseCopyScript;
window.chooseCopyFormat = chooseCopyFormat;
window.openCopyFormatModal = openCopyFormatModal;
window.createFormatCopy = createFormatCopy;
window.createSchedule = createSchedule;
window.toggleSchedule = toggleSchedule;
window.deleteSchedule = deleteSchedule;
window.connectPlatform = connectPlatform;
window.disconnectAccount = disconnectAccount;
window.loadProjects = loadProjects;

// ── Style Tags System ──
function initStyleTags() {
    document.querySelectorAll(".style-tag").forEach((tag) => {
        tag.addEventListener("click", () => {
            const container = tag.closest(".style-tags");
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

const PREVIEW_TABS = {
    create: {
        title: "Criar",
        chip: "Exemplos fixos",
    },
    videos: {
        title: "Meus Videos",
        chip: "Fluxo simplificado",
    },
    profile: {
        title: "Perfil",
        chip: "Conta e plano",
    },
};

const IS_CAPACITOR_APP = typeof window !== "undefined" && !!window.Capacitor;
const CRIAVIDEO_DEFAULT_API = "https://criavideo.pro/api";
const CRIAVIDEO_STAGING_API = "https://staging.criavideo.pro/api";
const APP_TOKEN_KEY = "criavideo_token";

function resolveApiBase() {
    if (!IS_CAPACITOR_APP) return "/api";
    const configuredApi = String(window.CRIAVIDEO_API_BASE || localStorage.getItem("criavideo_api_base") || "").trim();
    if (configuredApi) return configuredApi.replace(/\/+$/, "");
    const host = String(window.location?.hostname || "").toLowerCase();
    return host === "staging.criavideo.pro" ? CRIAVIDEO_STAGING_API : CRIAVIDEO_DEFAULT_API;
}

const API = resolveApiBase();

const PREVIEW_EXAMPLES = [
    {
        id: "plant-care",
        title: "Lirio: antes, cuidado e resultado",
        description: "Exemplo vertical com abertura forte, prova visual e CTA curto para gerar inspiracao sem expirar.",
        badge: "Nao expira",
        format: "9:16 · 18s",
        gradient: "linear-gradient(135deg, #7b5a37 0%, #355f7a 55%, #0d1622 100%)",
        tags: ["antes e depois", "plantas", "caseiro"],
        prompt: "Mostre o vaso seco, corte para folhas novas e feche com enquadramento limpo da planta recuperada.",
        structure: ["Gancho visual imediato", "Transformacao em 3 passos", "Fechamento com CTA leve"],
    },
    {
        id: "beauty-demo",
        title: "Produto em maos + prova rapida",
        description: "Layout pensado para pequenos negocios com foco em demonstracao de produto e narrativa curta.",
        badge: "Curadoria",
        format: "9:16 · 22s",
        gradient: "linear-gradient(135deg, #6d385d 0%, #dc8ea2 48%, #141d2f 100%)",
        tags: ["produto", "social proof", "vendas"],
        prompt: "Abra com rosto e produto no mesmo frame, entre com beneficio central e finalize com prova de uso.",
        structure: ["Rosto no primeiro segundo", "Beneficio em texto curto", "Oferta final"],
    },
    {
        id: "real-estate",
        title: "Tour rapido de ambiente",
        description: "Modelo para video de espaco ou servico local, com cortes fluidos e legenda curta.",
        badge: "Favorito do time",
        format: "16:9 · 25s",
        gradient: "linear-gradient(135deg, #104868 0%, #7ec2d8 46%, #132438 100%)",
        tags: ["tour", "imovel", "servico local"],
        prompt: "Comece na entrada, marque 3 pontos fortes do ambiente e termine com chamada para contato.",
        structure: ["Cena de abertura ampla", "3 beneficios em sequencia", "CTA final"],
    },
];

const PREVIEW_VIDEOS = [
    {
        title: "Lirio - Reels principal",
        status: "Concluido",
        statusClass: "complete",
        updated: "Atualizado ha 12 min",
        detail: "Versao vertical pronta para copiar, baixar ou reutilizar como base.",
        progress: 100,
    },
    {
        title: "Promo vaso premium",
        status: "Renderizando",
        statusClass: "progress",
        updated: "Atualizado ha 3 min",
        detail: "Geracao em andamento com trilha curta e CTA final.",
        progress: 72,
    },
    {
        title: "Tour da loja - teaser",
        status: "Na fila",
        statusClass: "queued",
        updated: "Entrou na fila agora",
        detail: "Projeto aguardando slot de render no fluxo simplificado do app.",
        progress: 18,
    },
];

let activeTab = "create";
let toastTimer = null;
let appToken = localStorage.getItem(APP_TOKEN_KEY) || "";
let profileState = {
    name: "Conta principal",
    email: "voce@criavideo.pro",
    role: "Membro Play Store",
    plan: "Profissional mensal",
    credits: "1.014",
    renewal: "Renovacao em 12 dias",
    live: false,
};
let profileHistoryState = [
    { title: "Plano Profissional", when: "15 mai 2026", value: "Google Play" },
    { title: "Pacote extra 500", when: "02 mai 2026", value: "Google Play" },
    { title: "Restore de assinatura", when: "28 abr 2026", value: "Sincronizado" },
];
let videoState = [...PREVIEW_VIDEOS];
let exampleState = [...PREVIEW_EXAMPLES];

async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (!headers.has("Accept")) headers.set("Accept", "application/json");
    if (appToken && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${appToken}`);
    if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(`${API}${path}`, {
        ...options,
        headers,
    });
    if (!response.ok) {
        const message = await response.text();
        throw new Error(message || `HTTP ${response.status}`);
    }
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) return response.json();
    return response.text();
}

function getInitialTab() {
    const params = new URLSearchParams(window.location.search);
    const requested = String(params.get("tab") || "").trim().toLowerCase();
    if (PREVIEW_TABS[requested]) return requested;
    return "create";
}

function syncTabLink(tab) {
    const url = new URL(window.location.href);
    url.searchParams.set("tab", tab);
    window.history.replaceState({}, "", url);
}

function showToast(message) {
    const el = document.getElementById("preview-toast");
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
        el.hidden = true;
    }, 2400);
}

function setActiveTab(tab) {
    if (!PREVIEW_TABS[tab]) return;
    activeTab = tab;
    syncTabLink(tab);

    document.querySelectorAll(".nav-tab").forEach((button) => {
        button.classList.toggle("is-active", button.dataset.tab === tab);
    });

    document.querySelectorAll(".screen-panel").forEach((panel) => {
        const isActive = panel.dataset.screen === tab;
        panel.classList.toggle("is-active", isActive);
        panel.hidden = !isActive;
    });

    const title = document.getElementById("tab-title");
    const chip = document.getElementById("tab-chip");
    if (title) title.textContent = PREVIEW_TABS[tab].title;
    if (chip) chip.textContent = PREVIEW_TABS[tab].chip;
}

function renderExamples() {
    const container = document.getElementById("example-list");
    if (!container) return;
    container.innerHTML = exampleState.map((item) => {
        return `
            <article class="example-card">
                <div class="card-media" style="--card-bg:${item.gradient}">
                    <span class="card-badge">${item.badge}</span>
                    <span class="card-format">${item.format}</span>
                </div>
                <div class="card-body">
                    <h3>${item.title}</h3>
                    <p>${item.description}</p>
                    <div class="card-tags">
                        ${item.tags.map((tag) => `<span class="card-tag">${tag}</span>`).join("")}
                    </div>
                    <div class="card-actions">
                        <button class="card-action card-action-primary" type="button" data-example-action="watch" data-example-id="${item.id}">Assistir</button>
                        <button class="card-action" type="button" data-example-action="copy" data-example-id="${item.id}">Copiar estrutura</button>
                        <button class="card-action" type="button" data-example-action="inspect" data-example-id="${item.id}">Ver prompt</button>
                        <button class="card-action" type="button" data-example-action="favorite" data-example-id="${item.id}">Favoritar</button>
                    </div>
                </div>
            </article>
        `;
    }).join("");
}

function renderVideos() {
    const container = document.getElementById("videos-list");
    const note = document.getElementById("videos-note");
    const chip = document.getElementById("videos-chip");
    if (!container) return;
    if (!videoState.length) {
        container.innerHTML = `
            <article class="empty-card">
                <strong>Nenhum video encontrado</strong>
                <p>Quando a sessao estiver ativa, os projetos reais do usuario aparecem aqui. Se nao houver projetos, esta tela mostra o primeiro CTA de criacao.</p>
            </article>
        `;
        if (note) note.textContent = "Sessao conectada, mas ainda sem projetos para listar.";
        if (chip) chip.textContent = "Lista pessoal";
        return;
    }
    container.innerHTML = videoState.map((item) => {
        return `
            <article class="video-card">
                <div class="video-top">
                    <div>
                        <h3>${item.title}</h3>
                        <p>${item.detail}</p>
                    </div>
                    <span class="status-pill status-${item.statusClass}">${item.status}</span>
                </div>
                <div class="progress-track">
                    <div class="progress-bar" style="width:${item.progress}%"></div>
                </div>
                <div class="card-actions">
                    <button class="quick-action quick-action-primary" type="button" data-video-action="play">Abrir</button>
                    <button class="quick-action" type="button" data-video-action="duplicate">Duplicar</button>
                    <button class="quick-action" type="button" data-video-action="download">Baixar</button>
                    <button class="quick-action" type="button" data-video-action="delete">Excluir</button>
                </div>
                <p>${item.updated}</p>
            </article>
        `;
    }).join("");
    if (note) note.textContent = appToken ? "Projetos carregados de /api/video/projects com fallback visual simplificado." : "Conecte sua conta para ver os projetos reais aqui.";
    if (chip) chip.textContent = appToken ? "Projetos reais" : "Lista pessoal";
}

function renderProfile() {
    const container = document.getElementById("profile-stack");
    const note = document.getElementById("profile-note");
    const chip = document.getElementById("profile-chip");
    if (!container) return;
    container.innerHTML = `
        <article class="profile-card">
            <div class="profile-top">
                <div class="profile-avatar">CV</div>
                <div>
                    <h3>${profileState.name}</h3>
                    <p>${profileState.email}</p>
                    <div class="profile-meta">
                        <span class="plan-chip">${profileState.role}</span>
                        <span class="plan-chip">${profileState.plan}</span>
                    </div>
                </div>
            </div>
            <div class="profile-balance">
                <div class="balance-block">
                    <div class="balance-label">Creditos disponiveis</div>
                    <div class="balance-value">${profileState.credits}</div>
                </div>
                <div class="balance-block">
                    <div class="balance-label">Assinatura</div>
                    <div class="balance-value">${profileState.live ? "API ativa" : "Preview"}</div>
                </div>
            </div>
            <p>${profileState.renewal}</p>
            <div class="card-actions">
                <button class="quick-action quick-action-primary" type="button" data-profile-action="plan">Gerenciar plano</button>
                <button class="quick-action" type="button" data-profile-action="credits">Comprar creditos</button>
            </div>
        </article>
        <article class="profile-card">
            <h3>Historico financeiro</h3>
            <div class="history-list">
                ${profileHistoryState.map((item) => `
                    <div class="history-item">
                        <div>
                            <strong>${item.title}</strong>
                            <span>${item.when}</span>
                        </div>
                        <span class="history-value">${item.value}</span>
                    </div>
                `).join("")}
            </div>
        </article>
        <article class="profile-card">
            <h3>Configuracoes basicas</h3>
            <p>Links legais, suporte, versao do app e acoes de sessao ficam juntos nesta aba.</p>
            <div class="settings-list">
                <button class="settings-pill" type="button" data-profile-action="support">Suporte</button>
                <button class="settings-pill" type="button" data-profile-action="terms">Termos</button>
                <button class="settings-pill" type="button" data-profile-action="privacy">Privacidade</button>
                <button class="settings-pill" type="button" data-profile-action="logout">Sair</button>
            </div>
        </article>
    `;
    if (note) note.textContent = profileState.live ? "Conta, saldo e historico usam /api/auth/me, /api/credits e /api/credits/history quando a sessao esta ativa." : "Conta, saldo e plano vao trocar do mock para API real quando houver sessao ativa.";
    if (chip) chip.textContent = profileState.live ? "Conta conectada" : "Google Play";
}

function openExampleModal(example, action) {
    const modal = document.getElementById("example-modal");
    const kicker = document.getElementById("example-modal-kicker");
    const title = document.getElementById("example-modal-title");
    const body = document.getElementById("example-modal-body");
    if (!modal || !kicker || !title || !body) return;
    kicker.textContent = action === "watch" ? "Preview do exemplo" : "Prompt e estrutura";
    title.textContent = example.title;
    body.innerHTML = `
        <div class="modal-block">
            <strong>Resumo</strong>
            <p>${example.description}</p>
        </div>
        <div class="modal-block">
            <strong>Prompt base</strong>
            <p>${example.prompt}</p>
        </div>
        <div class="modal-block">
            <strong>Estrutura sugerida</strong>
            <ul>
                ${example.structure.map((step) => `<li>${step}</li>`).join("")}
            </ul>
        </div>
    `;
    modal.hidden = false;
}

function closeExampleModal() {
    const modal = document.getElementById("example-modal");
    if (modal) modal.hidden = true;
}

function bindEvents() {
    document.querySelectorAll(".nav-tab").forEach((button) => {
        button.addEventListener("click", () => setActiveTab(button.dataset.tab || "create"));
    });

    document.addEventListener("click", (event) => {
        const exampleButton = event.target.closest("[data-example-action]");
        if (exampleButton) {
            const example = exampleState.find((item) => item.id === exampleButton.dataset.exampleId);
            const action = exampleButton.dataset.exampleAction;
            if (!example) return;
            if (action === "copy") {
                showToast("Clone real entra na proxima etapa com endpoint dedicado.");
                return;
            }
            if (action === "favorite") {
                showToast("Favorito salvo apenas no preview desta shell.");
                return;
            }
            openExampleModal(example, action);
            return;
        }

        const videoButton = event.target.closest("[data-video-action]");
        if (videoButton) {
            showToast(`Acao \"${videoButton.dataset.videoAction}\" conecta no proximo passo aos projetos reais.`);
            return;
        }

        const profileButton = event.target.closest("[data-profile-action]");
        if (profileButton) {
            showToast(`Fluxo \"${profileButton.dataset.profileAction}\" depende da integracao real com API e Google Play.`);
            return;
        }

        if (event.target.id === "example-modal" || event.target.id === "example-modal-close") {
            closeExampleModal();
        }
    });
}

function normalizeProjectStatus(status, progress) {
    const normalized = String(status || "").toLowerCase();
    if (normalized === "completed") return { label: "Concluido", className: "complete", progress: 100 };
    if (normalized === "failed") return { label: "Falhou", className: "queued", progress: Math.max(0, Number(progress || 0)) };
    if (normalized) return { label: "Renderizando", className: "progress", progress: Math.max(6, Number(progress || 0)) };
    return { label: "Na fila", className: "queued", progress: Math.max(6, Number(progress || 0)) };
}

function formatPlanLabel(planCode) {
    const raw = String(planCode || "free").trim().toLowerCase();
    const labels = {
        free: "Plano gratuito",
        starter: "Plano Starter",
        basic: "Plano Basic",
        professional: "Plano Professional",
        supreme: "Plano Supreme",
    };
    return labels[raw] || `Plano ${raw}`;
}

function formatRenewal(planExpiresAt) {
    if (!planExpiresAt) return "Sem renovacao ativa";
    const date = new Date(planExpiresAt);
    if (Number.isNaN(date.getTime())) return "Renovacao disponivel na API";
    return `Renovacao em ${date.toLocaleDateString("pt-BR")}`;
}

function formatHistoryStatus(status) {
    const normalized = String(status || "").trim().toLowerCase();
    if (normalized === "confirmed") return "Confirmado";
    if (normalized === "pending") return "Pendente";
    if (normalized === "failed") return "Falhou";
    return normalized || "Registrado";
}

function formatHistoryDate(value) {
    if (!value) return "Sem data";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "Sem data";
    return date.toLocaleDateString("pt-BR");
}

function mapProjectsToPreview(projects) {
    return (projects || []).slice(0, 6).map((project) => {
        const status = normalizeProjectStatus(project.status, project.progress);
        const duration = Number(project.duration || 0);
        const durationText = duration > 0 ? `${Math.round(duration)}s` : "duracao em processamento";
        return {
            title: project.title || project.track_title || "Projeto sem titulo",
            status: status.label,
            statusClass: status.className,
            updated: project.render_created_at || project.created_at ? `Atualizado ${new Date(project.render_created_at || project.created_at).toLocaleDateString("pt-BR")}` : "Atualizado agora",
            detail: `${project.aspect_ratio || "9:16"} · ${durationText}`,
            progress: status.progress,
        };
    });
}

async function hydratePreviewData() {
    try {
        const examplePayload = await api("/playstore/examples");
        if (Array.isArray(examplePayload?.items) && examplePayload.items.length) {
            exampleState = examplePayload.items;
            renderExamples();
        }
    } catch (error) {
        console.warn("[Playstore Preview] examples fallback", error);
    }

    if (!appToken) {
        renderVideos();
        renderProfile();
        return;
    }

    try {
        const [mePayload, creditsPayload, projectsPayload] = await Promise.all([
            api("/auth/me"),
            api("/credits"),
            api("/video/projects"),
        ]);

        const user = mePayload?.user || {};
        profileState = {
            name: user.name || "Conta principal",
            email: user.email || "voce@criavideo.pro",
            role: user.role || "Membro",
            plan: formatPlanLabel(creditsPayload?.currentPlan),
            credits: Number(creditsPayload?.credits || 0).toLocaleString("pt-BR"),
            renewal: formatRenewal(creditsPayload?.planExpiresAt),
            live: true,
        };
        videoState = mapProjectsToPreview(projectsPayload);

        try {
            const historyPayload = await api("/credits/history");
            if (Array.isArray(historyPayload?.items) && historyPayload.items.length) {
                profileHistoryState = historyPayload.items.map((item) => ({
                    title: item.title || "Compra",
                    when: formatHistoryDate(item.createdAt),
                    value: `${formatHistoryStatus(item.status)}${item.type ? ` · ${String(item.type).toUpperCase()}` : ""}`,
                }));
            }
        } catch (historyError) {
            console.warn("[Playstore Preview] history fallback", historyError);
        }
    } catch (error) {
        console.warn("[Playstore Preview] auth data fallback", error);
        showToast("Preview carregado com fallback porque a sessao/API nao respondeu.");
    }

    renderVideos();
    renderProfile();
}

function initPlaystorePreview() {
    renderExamples();
    renderVideos();
    renderProfile();
    bindEvents();
    setActiveTab(getInitialTab());
    hydratePreviewData();
}

initPlaystorePreview();
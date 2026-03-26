/* ═══════════════════════════════════════════════
   CriaVideo — Dashboard App
   ═══════════════════════════════════════════════ */

const API = '/api';
const LEVITA_URL = 'https://levita.pro';
let token = localStorage.getItem('token') || '';
let levitaSongs = [];

// ── Auth: auto-login via URL ?token=xxx from Levita ──
(function autoLogin() {
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get('token');
    if (urlToken) {
        token = urlToken.trim();
        localStorage.setItem('token', token);
        // Clean URL without reload
        window.history.replaceState({}, '', window.location.pathname);
    }
})();

function getHeaders() {
    return {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
}

async function api(path, options = {}) {
    const resp = await fetch(`${API}${path}`, {
        headers: getHeaders(),
        ...options,
    });
    if (resp.status === 401) {
        redirectToLevita();
        throw new Error('Unauthorized');
    }
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || resp.statusText);
    }
    return resp.json();
}

function redirectToLevita() {
    // Redireciona para o Levita para login, que retorna com ?token=
    window.location.href = `${LEVITA_URL}/login?redirect=${encodeURIComponent(window.location.origin + '/video')}`;
}

// ── Levita Songs API ──
async function loadLevitaSongs() {
    try {
        const resp = await fetch(`${LEVITA_URL}/api/feed/my-created-music`, {
            headers: { Authorization: `Bearer ${token}` }
        });
        if (!resp.ok) return [];
        const data = await resp.json();
        levitaSongs = data.songs || [];
        return levitaSongs;
    } catch (e) {
        console.warn('Erro ao carregar músicas do Levita:', e);
        return [];
    }
}

// ── Navigation ──
document.querySelectorAll('.nav-links a').forEach(link => {
    link.addEventListener('click', e => {
        e.preventDefault();
        const page = link.dataset.page;
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        document.querySelectorAll('.nav-links a').forEach(a => a.classList.remove('active'));
        document.getElementById(`page-${page}`).classList.add('active');
        link.classList.add('active');
        loadPageData(page);
    });
});

function loadPageData(page) {
    if (page === 'projects') loadProjects();
    else if (page === 'publish') { loadRenders(); loadPublishJobs(); }
    else if (page === 'schedule') loadSchedules();
    else if (page === 'accounts') loadAccounts();
}

// ── Modal helpers ──
function openModal(id) {
    document.getElementById(id).classList.add('open');
}
function closeModal(id) {
    document.getElementById(id).classList.remove('open');
}

// ═══ PROJECTS ═══
document.getElementById('btn-new-project').addEventListener('click', async () => {
    await populateSongSelector();
    openModal('modal-new-project');
});

async function populateSongSelector() {
    const select = document.getElementById('np-song-select');
    const detailsDiv = document.getElementById('np-song-details');
    select.innerHTML = '<option value="">Carregando músicas...</option>';
    detailsDiv.style.display = 'none';

    const songs = await loadLevitaSongs();
    select.innerHTML = '<option value="">Selecione uma música do Levita</option>' +
        '<option value="manual">✏️ Inserir manualmente</option>' +
        songs.map((s, i) => `<option value="${i}">${esc(s.title || 'Sem título')}${s.artist ? ' — ' + esc(s.artist) : ''}</option>`).join('');
}

document.addEventListener('change', (e) => {
    if (e.target.id !== 'np-song-select') return;
    const val = e.target.value;
    const manualFields = document.getElementById('np-manual-fields');
    const detailsDiv = document.getElementById('np-song-details');

    if (val === 'manual') {
        manualFields.style.display = 'block';
        detailsDiv.style.display = 'none';
    } else if (val !== '' && levitaSongs[parseInt(val)]) {
        const song = levitaSongs[parseInt(val)];
        manualFields.style.display = 'none';
        detailsDiv.style.display = 'block';
        detailsDiv.innerHTML = `
            <p>🎵 <strong>${esc(song.title || 'Sem título')}</strong></p>
            ${song.artist ? `<p>🎤 ${esc(song.artist)}</p>` : ''}
            ${song.duration ? `<p>⏱ ${Math.round(song.duration)}s</p>` : ''}
            ${song.lyrics ? `<p style="max-height:100px;overflow-y:auto;font-size:.8rem;color:var(--text-muted)">${esc(song.lyrics).substring(0, 300)}...</p>` : ''}
        `;
    } else {
        manualFields.style.display = 'none';
        detailsDiv.style.display = 'none';
    }
});

async function loadProjects() {
    const el = document.getElementById('projects-list');
    try {
        const data = await api('/video/projects');
        if (!data.length) {
            el.innerHTML = '<p class="loading">Nenhum projeto ainda. Crie um novo!</p>';
            return;
        }
        el.innerHTML = data.map(p => `
            <div class="card">
                <h4>${esc(p.title)}</h4>
                <p>🎵 ${esc(p.track_title || '')} — ${esc(p.track_artist || '')}</p>
                <p>📐 ${p.aspect_ratio} &nbsp; ⏱ ${p.track_duration || '?'}s</p>
                <p><span class="badge badge-${badgeClass(p.status)}">${p.status}</span></p>
                ${p.progress != null ? `<div class="progress-bar"><div class="progress-bar-fill" style="width:${p.progress}%"></div></div>` : ''}
                <div class="card-actions">
                    ${p.status === 'pending' ? `<button class="btn btn-primary btn-sm" onclick="generateVideo(${p.id})">▶ Gerar Vídeo</button>` : ''}
                    <button class="btn btn-danger btn-sm" onclick="deleteProject(${p.id})">🗑</button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        el.innerHTML = `<p class="loading">Erro: ${esc(e.message)}</p>`;
    }
}

async function createProject() {
    const songVal = document.getElementById('np-song-select').value;
    let trackTitle, trackArtist, audioPath, lyricsText, trackDuration;

    if (songVal === 'manual') {
        trackTitle = document.getElementById('np-track-title').value;
        trackArtist = document.getElementById('np-artist').value;
        audioPath = document.getElementById('np-audio').value;
        lyricsText = document.getElementById('np-lyrics').value;
        trackDuration = parseInt(document.getElementById('np-duration').value) || 180;
    } else if (songVal !== '' && levitaSongs[parseInt(songVal)]) {
        const song = levitaSongs[parseInt(songVal)];
        trackTitle = song.title || '';
        trackArtist = song.artist || '';
        audioPath = `${LEVITA_URL}${song.audio_url}`;
        lyricsText = song.lyrics || '';
        trackDuration = Math.round(song.duration) || 180;
    } else {
        alert('Selecione uma música');
        return;
    }

    try {
        await api('/video/projects', {
            method: 'POST',
            body: JSON.stringify({
                title: document.getElementById('np-title').value || trackTitle,
                track_title: trackTitle,
                track_artist: trackArtist,
                audio_path: audioPath,
                lyrics_text: lyricsText,
                track_duration: trackDuration,
                aspect_ratio: document.getElementById('np-aspect').value,
                style_prompt: document.getElementById('np-style').value,
            }),
        });
        closeModal('modal-new-project');
        loadProjects();
    } catch (e) {
        alert('Erro ao criar projeto: ' + e.message);
    }
}

async function generateVideo(id) {
    try {
        await api(`/video/projects/${id}/generate`, { method: 'POST' });
        loadProjects();
        // Poll for updates
        const poll = setInterval(async () => {
            try {
                const p = await api(`/video/projects/${id}`);
                loadProjects();
                if (p.status === 'completed' || p.status === 'failed') clearInterval(poll);
            } catch { clearInterval(poll); }
        }, 5000);
    } catch (e) {
        alert('Erro: ' + e.message);
    }
}

async function deleteProject(id) {
    if (!confirm('Excluir este projeto?')) return;
    try {
        await api(`/video/projects/${id}`, { method: 'DELETE' });
        loadProjects();
    } catch (e) {
        alert('Erro: ' + e.message);
    }
}

// ═══ PUBLISH ═══
async function loadRenders() {
    try {
        const projects = await api('/video/projects');
        const select = document.getElementById('pub-render-select');
        select.innerHTML = '<option value="">Selecione...</option>';
        for (const p of projects) {
            if (p.status !== 'completed') continue;
            try {
                const detail = await api(`/video/projects/${p.id}`);
                if (detail.renders) {
                    for (const r of detail.renders) {
                        select.innerHTML += `<option value="${r.id}">[${p.title}] ${r.format} — ${r.duration}s</option>`;
                    }
                }
            } catch {}
        }
    } catch {}
}

document.getElementById('btn-publish').addEventListener('click', async () => {
    const renderId = document.getElementById('pub-render-select').value;
    if (!renderId) { alert('Selecione um vídeo'); return; }

    const platforms = [];
    document.querySelectorAll('#publish-form-area .checkbox-group input:checked').forEach(cb => {
        platforms.push(cb.value);
    });
    if (!platforms.length) { alert('Selecione pelo menos uma plataforma'); return; }

    try {
        await api('/publish/', {
            method: 'POST',
            body: JSON.stringify({
                render_id: parseInt(renderId),
                platforms: platforms,
                title: document.getElementById('pub-title').value,
                description: document.getElementById('pub-description').value,
            }),
        });
        alert('Publicação iniciada!');
        loadPublishJobs();
    } catch (e) {
        alert('Erro: ' + e.message);
    }
});

async function loadPublishJobs() {
    const el = document.getElementById('publish-jobs-list');
    try {
        const data = await api('/publish/jobs');
        if (!data.length) {
            el.innerHTML = '<p class="loading">Nenhuma publicação ainda.</p>';
            return;
        }
        el.innerHTML = `<table>
            <tr><th>ID</th><th>Plataforma</th><th>Status</th><th>URL</th><th>Data</th></tr>
            ${data.map(j => `
                <tr>
                    <td>${j.id}</td>
                    <td>${j.platform}</td>
                    <td><span class="badge badge-${badgeClass(j.status)}">${j.status}</span></td>
                    <td>${j.platform_url ? `<a href="${esc(j.platform_url)}" target="_blank" style="color:var(--accent)">Ver</a>` : '-'}</td>
                    <td>${j.published_at ? new Date(j.published_at).toLocaleString() : '-'}</td>
                </tr>
            `).join('')}
        </table>`;
    } catch (e) {
        el.innerHTML = `<p class="loading">Erro: ${esc(e.message)}</p>`;
    }
}

// ═══ SCHEDULE ═══
document.getElementById('btn-new-schedule').addEventListener('click', async () => {
    await loadAccountsForSelect();
    openModal('modal-new-schedule');
});

async function loadAccountsForSelect() {
    try {
        const data = await api('/social/accounts');
        const sel = document.getElementById('ns-account');
        sel.innerHTML = data.map(a => `<option value="${a.id}">${a.platform} — ${a.platform_username}</option>`).join('');
    } catch {}
}

async function loadSchedules() {
    const el = document.getElementById('schedules-list');
    try {
        const data = await api('/schedule/');
        if (!data.length) {
            el.innerHTML = '<p class="loading">Nenhum agendamento.</p>';
            return;
        }
        el.innerHTML = data.map(s => `
            <div class="card">
                <h4>${s.platform} — ${s.frequency}</h4>
                <p>⏰ ${s.time_utc} UTC</p>
                <p>📋 Fila: ${s.queue?.length || 0} vídeos</p>
                <p>Status: ${s.is_active ? '<span class="badge badge-completed">Ativo</span>' : '<span class="badge badge-failed">Pausado</span>'}</p>
                <div class="card-actions">
                    <button class="btn btn-sm ${s.is_active ? 'btn-secondary' : 'btn-primary'}" onclick="toggleSchedule(${s.id}, ${!s.is_active})">${s.is_active ? 'Pausar' : 'Ativar'}</button>
                    <button class="btn btn-danger btn-sm" onclick="deleteSchedule(${s.id})">🗑</button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        el.innerHTML = `<p class="loading">Erro: ${esc(e.message)}</p>`;
    }
}

async function createSchedule() {
    try {
        await api('/schedule/', {
            method: 'POST',
            body: JSON.stringify({
                platform: document.getElementById('ns-platform').value,
                social_account_id: parseInt(document.getElementById('ns-account').value),
                frequency: document.getElementById('ns-frequency').value,
                time_utc: document.getElementById('ns-time').value,
            }),
        });
        closeModal('modal-new-schedule');
        loadSchedules();
    } catch (e) {
        alert('Erro: ' + e.message);
    }
}

async function toggleSchedule(id, active) {
    try {
        await api(`/schedule/${id}`, {
            method: 'PATCH',
            body: JSON.stringify({ is_active: active }),
        });
        loadSchedules();
    } catch (e) {
        alert('Erro: ' + e.message);
    }
}

async function deleteSchedule(id) {
    if (!confirm('Excluir agendamento?')) return;
    try {
        await api(`/schedule/${id}`, { method: 'DELETE' });
        loadSchedules();
    } catch (e) {
        alert('Erro: ' + e.message);
    }
}

// ═══ ACCOUNTS ═══
function connectPlatform(platform) {
    window.location.href = `/api/social/connect/${platform}`;
}

async function loadAccounts() {
    const el = document.getElementById('accounts-list');
    try {
        const data = await api('/social/accounts');
        if (!data.length) {
            el.innerHTML = '<p class="loading">Nenhuma conta conectada.</p>';
            return;
        }
        el.innerHTML = data.map(a => `
            <div class="card">
                <h4>${a.platform}</h4>
                <p>👤 ${esc(a.platform_username)}</p>
                <div class="card-actions">
                    <button class="btn btn-danger btn-sm" onclick="disconnectAccount(${a.id})">Desconectar</button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        el.innerHTML = `<p class="loading">Erro: ${esc(e.message)}</p>`;
    }
}

async function disconnectAccount(id) {
    if (!confirm('Desconectar esta conta?')) return;
    try {
        await api(`/social/accounts/${id}`, { method: 'DELETE' });
        loadAccounts();
    } catch (e) {
        alert('Erro: ' + e.message);
    }
}

// ── Helpers ──
function esc(s) {
    const div = document.createElement('div');
    div.textContent = s || '';
    return div.innerHTML;
}

function badgeClass(status) {
    if (status === 'pending') return 'pending';
    if (status?.includes('generat') || status?.includes('render')) return 'rendering';
    if (status === 'completed' || status === 'published') return 'completed';
    if (status === 'failed') return 'failed';
    return 'pending';
}

// ── Init ──
if (!token) {
    redirectToLevita();
} else {
    loadProjects();
}

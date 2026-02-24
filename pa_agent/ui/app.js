const API = 'http://localhost:8765';
let currentClip = null;

// ---- Utilities ----
function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2500);
}

function fmtDuration(secs) {
  if (!secs) return '—';
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
    : `${m}:${String(s).padStart(2,'0')}`;
}

function fmtFps(fps) {
  if (!fps) return '—';
  // Show common fractions
  if (Math.abs(fps - 23.976) < 0.01) return '23.976';
  if (Math.abs(fps - 29.97) < 0.01) return '29.97';
  return fps.toFixed(2);
}

// ---- Tab switching ----
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
    if (tab.dataset.tab === 'queue') loadQueue();
    if (tab.dataset.tab === 'faces') loadFaces();
    if (tab.dataset.tab === 'settings') { loadSettings(); loadUsage(); }
  });
});

// ---- Status polling ----
async function pollStatus() {
  try {
    const r = await fetch(`${API}/api/status`);
    if (!r.ok) throw new Error();
    const d = await r.json();
    document.getElementById('stat-indexed').textContent = d.indexed ?? '—';
    document.getElementById('stat-pending').textContent = d.queue_depth ?? '—';
    document.getElementById('stat-today').textContent = d.processed_today ?? '—';
    document.getElementById('stat-failed').textContent = d.failed ?? '—';
    document.getElementById('service-dot').style.background = 'var(--success)';
    document.getElementById('service-label').textContent = d.scanner_running ? 'Running' : 'Idle';
  } catch {
    document.getElementById('service-dot').style.background = 'var(--fail)';
    document.getElementById('service-label').textContent = 'Offline';
  }
}
pollStatus();
setInterval(pollStatus, 5000);

// ---- Search ----
document.getElementById('search-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') doSearch();
});

async function doSearch() {
  const q = document.getElementById('search-input').value.trim();
  const res = document.getElementById('filter-res').value;
  const camera = document.getElementById('filter-camera').value.trim();
  const fps = document.getElementById('filter-fps').value;
  const durMin = document.getElementById('filter-dur-min').value;
  const durMax = document.getElementById('filter-dur-max').value;
  const dateFrom = document.getElementById('filter-date-from').value;
  const dateTo = document.getElementById('filter-date-to').value;

  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (res) params.set('resolution', res);
  if (camera) params.set('camera', camera);
  if (fps) params.set('fps', fps);
  if (durMin) params.set('duration_min', durMin);
  if (durMax) params.set('duration_max', durMax);
  if (dateFrom) params.set('date_from', dateFrom);
  if (dateTo) params.set('date_to', dateTo);
  params.set('n', '50');

  document.getElementById('results-info').textContent = 'Searching…';
  document.getElementById('results-grid').innerHTML = '';

  try {
    const r = await fetch(`${API}/api/search?${params}`);
    const data = await r.json();
    renderResults(data.results || []);
  } catch (e) {
    document.getElementById('results-info').textContent = 'Error: cannot reach service';
  }
}

function renderResults(results) {
  const grid = document.getElementById('results-grid');
  const info = document.getElementById('results-info');
  info.textContent = results.length ? `${results.length} result${results.length === 1 ? '' : 's'}` : 'No results found';
  grid.innerHTML = '';

  results.forEach(clip => {
    const card = document.createElement('div');
    card.className = 'clip-card';
    card.innerHTML = `
      <img class="clip-thumb"
           src="${API}/api/thumbnail/${clip.id}"
           onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"
           alt="" />
      <div class="clip-thumb-placeholder" style="display:none">🎬</div>
      <div class="clip-info">
        <div class="clip-name" title="${clip.filename}">${clip.filename || '—'}</div>
        <div class="clip-meta">
          ${clip.duration_sec ? `<span>${fmtDuration(clip.duration_sec)}</span>` : ''}
          ${clip.resolution ? `<span>${clip.resolution}</span>` : ''}
          ${clip.fps ? `<span>${fmtFps(clip.fps)} fps</span>` : ''}
          ${clip.camera_model ? `<span>${clip.camera_model}</span>` : ''}
        </div>
        ${clip.faces && clip.faces.length ? `
          <div class="clip-faces">
            ${clip.faces.slice(0, 3).map(f => `<span class="face-tag">${f}</span>`).join('')}
          </div>` : ''}
      </div>
    `;
    card.addEventListener('click', () => openDetail(clip));
    grid.appendChild(card);
  });
}

// ---- Detail panel ----
function openDetail(clip) {
  currentClip = clip;

  document.getElementById('d-title').textContent = clip.filename || '—';
  document.getElementById('d-path').textContent = clip.filepath || '—';

  const metaFields = [
    { k: 'Duration', v: fmtDuration(clip.duration_sec) },
    { k: 'Resolution', v: clip.resolution || '—' },
    { k: 'FPS', v: clip.fps ? fmtFps(clip.fps) : '—' },
    { k: 'Video Codec', v: clip.video_codec || '—' },
    { k: 'Audio Codec', v: clip.audio_codec || '—' },
    { k: 'Camera', v: [clip.camera_make, clip.camera_model].filter(Boolean).join(' ') || '—' },
  ];
  document.getElementById('d-meta').innerHTML = metaFields
    .map(f => `<div class="detail-meta-item"><div class="k">${f.k}</div><div class="v">${f.v}</div></div>`)
    .join('');

  document.getElementById('d-desc').textContent = clip.description || 'No description';

  const transcriptSection = document.getElementById('d-transcript-section');
  const transcriptEl = document.getElementById('d-transcript');
  if (clip.transcript) {
    transcriptSection.style.display = '';
    transcriptEl.textContent = clip.transcript;
  } else {
    transcriptSection.style.display = 'none';
  }

  const facesSection = document.getElementById('d-faces-section');
  const facesEl = document.getElementById('d-faces');
  if (clip.faces && clip.faces.length) {
    facesSection.style.display = '';
    facesEl.innerHTML = clip.faces.map(f => `<span class="face-tag">${f}</span>`).join(' ');
  } else {
    facesSection.style.display = 'none';
  }

  document.getElementById('detail-overlay').classList.add('open');
}

function closeDetail() {
  document.getElementById('detail-overlay').classList.remove('open');
  currentClip = null;
}

document.getElementById('detail-overlay').addEventListener('click', e => {
  if (e.target === document.getElementById('detail-overlay')) closeDetail();
});

// ---- NLE import stubs ----
function importPremiere() {
  if (!currentClip) return;
  // CSInterface would be available when running inside the CEP panel
  if (typeof CSInterface !== 'undefined') {
    const cs = new CSInterface();
    cs.evalScript(`
      var project = app.project;
      project.importFiles(["${currentClip.filepath}"], true, project.rootItem, false);
    `);
    toast('Imported to Premiere Pro');
  } else {
    // Copy path to clipboard as fallback
    navigator.clipboard.writeText(currentClip.filepath).then(() => {
      toast('Path copied to clipboard (open in Premiere manually)');
    });
  }
}

function importResolve() {
  if (!currentClip) return;
  navigator.clipboard.writeText(currentClip.filepath).then(() => {
    toast('Path copied — paste in DaVinci Resolve Media Pool');
  });
}

// ---- Queue tab ----
async function loadQueue() {
  try {
    const r = await fetch(`${API}/api/queue`);
    const data = await r.json();
    const tbody = document.getElementById('queue-tbody');
    tbody.innerHTML = '';
    (data.items || []).forEach(item => {
      const badgeClass = {
        INDEXED: 'badge-indexed', PENDING: 'badge-pending',
        FAILED: 'badge-failed', PROCESSING: 'badge-processing'
      }[item.status] || '';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${item.filename}</td>
        <td><span class="badge ${badgeClass}">${item.status}</span></td>
        <td>${item.retry_count}</td>
        <td style="font-size:11px;color:var(--text-dim);max-width:200px;overflow:hidden;text-overflow:ellipsis">${item.error_log || ''}</td>
        <td>${item.status === 'FAILED' ? `<button class="btn btn-outline btn-sm" onclick="retryVideo('${item.id}')">Retry</button>` : ''}</td>
      `;
      tbody.appendChild(tr);
    });
    if (!data.items || data.items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="color:var(--text-dim);padding:20px;text-align:center">Queue is empty</td></tr>';
    }
  } catch {
    document.getElementById('queue-tbody').innerHTML =
      '<tr><td colspan="5" style="color:var(--fail)">Cannot reach service</td></tr>';
  }
}

async function retryVideo(id) {
  await fetch(`${API}/api/retry`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ video_id: id }) });
  toast('Queued for retry');
  loadQueue();
}

// ---- Faces tab ----
async function loadFaces() {
  try {
    const r = await fetch(`${API}/api/faces`);
    const data = await r.json();
    const grid = document.getElementById('faces-grid');
    grid.innerHTML = '';
    (data.clusters || []).forEach(cluster => {
      const card = document.createElement('div');
      card.className = 'face-card';
      const thumbSrc = cluster.thumbnail_path
        ? `${API}/api/thumbnail_face/${cluster.cluster_id}`
        : '';
      card.innerHTML = `
        ${thumbSrc
          ? `<img class="face-avatar" src="${thumbSrc}" onerror="this.style.display='none'" alt="" />`
          : `<div class="face-avatar" style="display:flex;align-items:center;justify-content:center;font-size:30px;background:var(--surface)">👤</div>`
        }
        <div style="font-size:12px;font-weight:600">${cluster.identity_label || 'Unknown'}</div>
        <div class="face-count">${cluster.appearance_count} clips</div>
        <input class="face-label-input" type="text" value="${cluster.identity_label || ''}"
               placeholder="Add name…"
               data-cluster="${cluster.cluster_id}"
               onblur="saveFaceLabel(this)" />
      `;
      grid.appendChild(card);
    });
    if (!data.clusters || data.clusters.length === 0) {
      grid.innerHTML = '<p style="color:var(--text-dim);font-size:13px">No faces detected yet. Faces appear after videos are indexed.</p>';
    }
  } catch {
    document.getElementById('faces-grid').innerHTML = '<p style="color:var(--fail)">Cannot reach service</p>';
  }
}

async function saveFaceLabel(input) {
  const clusterId = input.dataset.cluster;
  const name = input.value.trim();
  if (!name) return;
  await fetch(`${API}/api/face/label`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cluster_id: clusterId, name }),
  });
  toast(`Labeled face as "${name}"`);
}

// ---- Settings tab ----
async function loadSettings() {
  try {
    const r = await fetch(`${API}/api/settings`);
    const cfg = await r.json();
    if (cfg.watch_paths) {
      document.getElementById('s-watch-paths').value = cfg.watch_paths.join('\n');
    }
    if (cfg.whisper_model) {
      document.getElementById('s-whisper').value = cfg.whisper_model;
    }
    if (cfg.license_key) {
      document.getElementById('s-license-key').value = cfg.license_key;
    }
    if (cfg.proxy_url) {
      document.getElementById('s-proxy-url').value = cfg.proxy_url;
    }
  } catch {}
}

async function saveSettings(e) {
  e.preventDefault();
  const watchRaw = document.getElementById('s-watch-paths').value;
  const watchPaths = watchRaw.split('\n').map(s => s.trim()).filter(Boolean);
  const whisperModel = document.getElementById('s-whisper').value;
  const licenseKey = document.getElementById('s-license-key').value.trim();
  const proxyUrl = document.getElementById('s-proxy-url').value.trim();

  const payload = { watch_paths: watchPaths, whisper_model: whisperModel };
  if (licenseKey) payload.license_key = licenseKey;
  if (proxyUrl) payload.proxy_url = proxyUrl;

  try {
    const r = await fetch(`${API}/api/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (r.ok) {
      toast('Settings saved. Restart service to apply watch paths.');
      loadUsage();
    } else {
      const err = await r.json();
      toast('Error: ' + (err.detail || 'unknown'));
    }
  } catch {
    toast('Cannot reach service');
  }
}

// ---- Usage panel ----
function fmtHours(secs) {
  if (secs === null || secs === undefined) return '—';
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

async function loadUsage() {
  const loadingEl = document.getElementById('usage-loading');
  const contentEl = document.getElementById('usage-content');
  const errorEl = document.getElementById('usage-error');

  loadingEl.style.display = '';
  contentEl.style.display = 'none';
  errorEl.style.display = 'none';

  try {
    const r = await fetch(`${API}/api/status`);
    if (!r.ok) throw new Error('status failed');
    const d = await r.json();

    loadingEl.style.display = 'none';

    if (d.tier_name) {
      document.getElementById('usage-tier').textContent = d.tier_name;
      const used = d.quota_remaining_sec !== null && d.quota_remaining_sec !== undefined
        ? null  // we have remaining, compute used from status
        : null;

      // Fetch usage details directly from proxy via local service status
      const limitSec = d.quota_remaining_sec !== null ? null : null; // determined below
      const remaining = d.quota_remaining_sec;

      document.getElementById('usage-used').textContent = remaining !== null
        ? fmtHours(remaining >= 0 ? (/* limit */ null) : 0)
        : '—';

      // Re-fetch from /api/status which now returns quota_remaining_sec
      const tierLimits = { starter: 7200, pro: 36000, studio: null };
      const limit = tierLimits[d.tier_name] ?? null;

      if (limit !== null) {
        const usedSec = limit - (remaining ?? 0);
        document.getElementById('usage-used').textContent = fmtHours(Math.max(0, usedSec));
        document.getElementById('usage-limit').textContent = fmtHours(limit);
        const pct = Math.min(100, Math.max(0, (usedSec / limit) * 100));
        document.getElementById('usage-bar').style.width = pct + '%';
        document.getElementById('usage-bar').style.background = pct > 90 ? 'var(--fail)' : pct > 70 ? 'var(--warn)' : 'var(--accent)';
        document.getElementById('usage-remaining').textContent =
          remaining !== null ? `${fmtHours(remaining)} remaining` : '';
        document.getElementById('usage-limit-row').style.display = '';
        document.getElementById('usage-bar-wrap').style.display = '';
      } else {
        document.getElementById('usage-used').textContent = 'Unlimited';
        document.getElementById('usage-limit-row').style.display = 'none';
        document.getElementById('usage-bar-wrap').style.display = 'none';
        document.getElementById('usage-remaining').textContent = '';
      }

      contentEl.style.display = '';
    } else {
      errorEl.textContent = 'No license key set — add a license key to enable AI analysis.';
      errorEl.style.display = '';
    }
  } catch {
    loadingEl.style.display = 'none';
    errorEl.textContent = 'Cannot reach service.';
    errorEl.style.display = '';
  }
}

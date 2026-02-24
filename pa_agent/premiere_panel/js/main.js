// ClipButler Premiere Panel — main UI logic
// Requires CSInterface.js (bundled with CEP SDK) in production builds.

let cs = null;
try { cs = new CSInterface(); } catch(e) { /* not inside Premiere */ }

// Poll service status
async function checkStatus() {
  try {
    const s = await cbGetStatus();
    document.getElementById('dot').style.background = '#34c759';
    document.getElementById('svc-status').textContent =
      `${s.indexed} clips indexed`;
  } catch {
    document.getElementById('dot').style.background = '#ff3b30';
    document.getElementById('svc-status').textContent = 'Service offline';
  }
}
checkStatus();
setInterval(checkStatus, 10000);

// Handle Enter key in search box
document.getElementById('q').addEventListener('keydown', e => {
  if (e.key === 'Enter') search();
});

async function search() {
  const q = document.getElementById('q').value.trim();
  const res = document.getElementById('f-res').value;
  const cam = document.getElementById('f-cam').value.trim();
  const fps = document.getElementById('f-fps').value;

  setStatus('Searching…');
  document.getElementById('results').innerHTML = '';

  try {
    const data = await cbSearch({ q, resolution: res, camera: cam, fps, n: 30 });
    renderResults(data.results || []);
    setStatus(`${(data.results || []).length} results`);
  } catch (e) {
    setStatus('Error: ' + e.message);
  }
}

function renderResults(results) {
  const container = document.getElementById('results');
  container.innerHTML = '';

  if (!results.length) {
    container.innerHTML = '<div style="padding:20px;color:#8e8e93;text-align:center">No results</div>';
    return;
  }

  results.forEach(clip => {
    const item = document.createElement('div');
    item.className = 'result-item';

    const fps = clip.fps ? (Math.abs(clip.fps - 23.976) < 0.01 ? '23.976' : clip.fps.toFixed(2)) : '';
    const meta = [clip.resolution, fps ? fps + ' fps' : '', clip.camera_model]
      .filter(Boolean).join(' · ');

    item.innerHTML = `
      <img class="thumb" src="${cbThumbnailUrl(clip.id)}"
           onerror="this.style.display='none'"
           alt="" />
      <div class="info">
        <div class="fname" title="${clip.filename}">${clip.filename}</div>
        <div class="meta">${meta || '—'}</div>
        <div class="desc">${clip.description || ''}</div>
      </div>
      <button class="import-btn" onclick="importClip('${clip.filepath.replace(/'/g, "\\'")}')">
        Import
      </button>
    `;
    container.appendChild(item);
  });
}

function importClip(filepath) {
  if (cs) {
    // Run ExtendScript inside Premiere
    const script = `
      (function() {
        try {
          var project = app.project;
          var paths = ["${filepath.replace(/\\/g, '\\\\')}"];
          project.importFiles(paths, true, project.rootItem, false);
          "ok";
        } catch(e) { e.toString(); }
      })()
    `;
    cs.evalScript(script, function(result) {
      setStatus(result === 'ok' ? 'Imported!' : 'Import error: ' + result);
    });
  } else {
    // Fallback: copy path to clipboard
    navigator.clipboard.writeText(filepath);
    setStatus('Path copied to clipboard');
  }
}

function setStatus(msg) {
  document.getElementById('status-bar').textContent = msg;
}

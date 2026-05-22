const API = '';  // same-origin; change to 'http://localhost:8000' for dev

// ── Helpers ───────────────────────────────────────────────────────────────────

function $(id) { return document.getElementById(id); }
function showEl(el) { el.classList.remove('hidden'); }
function hideEl(el) { el.classList.add('hidden'); }

function setProgress(pct, text) {
  $('progress-fill').style.width = pct + '%';
  $('progress-text').textContent = text;
}

// ── State ─────────────────────────────────────────────────────────────────────

let searchFile          = null;
let currentQueryFile    = null;  // original filename for feedback
let cropperInstance     = null;

// ── Stats ─────────────────────────────────────────────────────────────────────

async function loadStats() {
  try {
    const data = await fetch(`${API}/stats`).then(r => r.json());
    renderStats(data);
  } catch (e) {
    console.warn('Stats load failed:', e);
  }
}


function renderStats(data) {
  $('stat-total').textContent   = data.total   ?? '--';
  $('stat-cats').textContent    = (data.categories?.length) ?? '--';
  $('stat-vectors').textContent = data.qdrant_vectors ?? '--';

  $('s-total').textContent   = data.total   ?? '--';
  $('s-vectors').textContent = data.qdrant_vectors ?? '--';
  $('s-cats').textContent    = (data.categories?.length) ?? '--';

  const breakdown = $('category-breakdown');
  breakdown.innerHTML = '';
  (data.categories || []).forEach(c => {
    const chip = document.createElement('div');
    chip.className = 'cat-chip';
    chip.innerHTML = `<span class="cat-name">${c.name}</span><span class="cat-count">${c.count}</span>`;
    breakdown.appendChild(chip);
  });
}

// ── Accuracy ──────────────────────────────────────────────────────────────────

async function loadAccuracy() {
  try {
    const data = await fetch(`${API}/accuracy`).then(r => r.json());
    renderAccuracy(data);
  } catch (e) {
    console.warn('Accuracy load failed:', e);
  }
}

function renderAccuracy(data) {
  const pct   = (data.precision_pct !== null && data.precision_pct !== undefined)
                  ? data.precision_pct.toFixed(1) + '%'
                  : '--';
  const total = data.total_ratings ?? 0;

  $('s-precision').textContent = pct;
  $('s-feedback').textContent  = total;

  const byCat = data.by_category || [];
  if (byCat.length > 0) {
    const grid = $('accuracy-grid');
    grid.innerHTML = '';
    byCat.forEach(c => {
      const chip = document.createElement('div');
      chip.className = 'acc-chip';
      chip.innerHTML = `
        <div class="acc-chip-top">
          <span class="acc-cat">${c.category}</span>
          <span class="acc-pct">${c.precision.toFixed(1)}%</span>
        </div>
        <div class="acc-bar">
          <div class="acc-bar-fill" style="width:${c.precision}%"></div>
        </div>
        <div style="font-size:0.74rem;color:var(--text-dim);margin-top:3px">${c.relevant}/${c.total} relevant</div>
      `;
      grid.appendChild(chip);
    });
    showEl($('accuracy-breakdown'));
  }
}

// ── rembg status badge ────────────────────────────────────────────────────────

async function checkRembgStatus() {
  const badge = $('rembg-badge');
  try {
    const data = await fetch(`${API}/rembg-status`).then(r => r.json());
    if (data.preprocessing) {
      badge.textContent = '● BG Removal: active';
      badge.className   = 'rembg-badge active';
    } else if (data.available) {
      badge.textContent = '● BG Removal: disabled (set REMBG_PREPROCESSING=true)';
      badge.className   = 'rembg-badge inactive';
    } else {
      badge.textContent = '● BG Removal: not installed';
      badge.className   = 'rembg-badge inactive';
    }
  } catch (e) {
    badge.textContent = '● BG Removal: unknown';
    badge.className   = 'rembg-badge inactive';
  }
}

// ── Cropper helpers ───────────────────────────────────────────────────────────

function initCropper() {
  if (cropperInstance) {
    cropperInstance.destroy();
    cropperInstance = null;
  }
  // Reset aspect-ratio button state
  document.querySelectorAll('[data-ratio]').forEach(b => b.classList.remove('active'));
  document.querySelector('[data-ratio="free"]')?.classList.add('active');

  const cropImg = $('crop-img');
  cropImg.onload = null;
  cropImg.src = '';

  cropImg.onload = () => {
    cropImg.onload = null;
    cropperInstance = new Cropper(cropImg, {
      viewMode: 1,
      dragMode: 'move',
      autoCropArea: 0.85,
      responsive: true,
      restore: false,
      guides: true,
      center: true,
      highlight: false,
      cropBoxMovable: true,
      cropBoxResizable: true,
      toggleDragModeOnDblclick: false,
      ready() {
        updateSearchBtnHint();
      },
    });
  };
  cropImg.src = $('search-preview-img').src;
}

function destroyCropper() {
  if (cropperInstance) {
    cropperInstance.destroy();
    cropperInstance = null;
  }
}

// ── Search button hint (shows "cropped" indicator) ────────────────────────────

function updateSearchBtnHint() {
  const btn  = $('search-btn');
  const crop = $('crop-toggle').checked && cropperInstance;
  const hint = crop ? ' (cropped region)' : '';
  btn.innerHTML = `<span class="btn-icon">&#9889;</span> Search Similar${
    hint ? `<span class="search-btn-crop-hint">${hint}</span>` : ''}`;
}

// ── Crop toggle ───────────────────────────────────────────────────────────────

$('crop-toggle').addEventListener('change', function () {
  if (this.checked) {
    hideEl($('normal-preview-wrap'));
    showEl($('crop-preview-wrap'));
    initCropper();
  } else {
    destroyCropper();
    hideEl($('crop-preview-wrap'));
    showEl($('normal-preview-wrap'));
    updateSearchBtnHint();
  }
});

// ── Crop toolbar ──────────────────────────────────────────────────────────────

const ratioMap = { 'free': NaN, '1': 1, '4/3': 4/3, '16/9': 16/9 };

document.querySelectorAll('[data-ratio]').forEach(btn => {
  btn.addEventListener('click', e => {
    e.stopPropagation();
    if (!cropperInstance) return;
    document.querySelectorAll('[data-ratio]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    cropperInstance.setAspectRatio(ratioMap[btn.dataset.ratio] ?? NaN);
  });
});

document.addEventListener('click', e => {
  const id = e.target?.id;
  if (!cropperInstance) return;
  if (id === 'crop-rotate-l')  { e.stopPropagation(); cropperInstance.rotate(-90); }
  if (id === 'crop-rotate-r')  { e.stopPropagation(); cropperInstance.rotate(90); }
  if (id === 'crop-flip-h')    { e.stopPropagation(); cropperInstance.scaleX(cropperInstance.getData().scaleX === -1 ? 1 : -1); }
  if (id === 'crop-zoom-in')   { e.stopPropagation(); cropperInstance.zoom(0.1); }
  if (id === 'crop-zoom-out')  { e.stopPropagation(); cropperInstance.zoom(-0.1); }
  if (id === 'crop-reset-btn') { e.stopPropagation(); cropperInstance.reset(); }
});

// ── Drop zone setup ───────────────────────────────────────────────────────────

function setupDropZone(dropZoneId, fileInputId, uploadInnerId, onFile) {
  const zone  = $(dropZoneId);
  const input = $(fileInputId);

  zone.addEventListener('click', e => {
    // Only open picker when the upload-inner (empty state) is visible
    if ($(uploadInnerId).classList.contains('hidden')) return;
    if (e.target.tagName !== 'BUTTON') input.click();
  });

  zone.addEventListener('dragover', e => {
    e.preventDefault();
    zone.classList.add('drag-over');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file) onFile(file);
  });

  input.addEventListener('change', () => {
    if (input.files[0]) onFile(input.files[0]);
    input.value = '';
  });
}

function showPreview(file, innerEl, previewWrapEl, previewImgEl) {
  const reader = new FileReader();
  reader.onload = e => {
    previewImgEl.src = e.target.result;
    hideEl(innerEl);
    showEl(previewWrapEl);
  };
  reader.readAsDataURL(file);
}

// ── Search drop zone ──────────────────────────────────────────────────────────

// Simpler click handler for the search drop zone
$('search-drop-zone').addEventListener('click', e => {
  // Only open file picker when NO file is loaded (upload-inner is visible, not hidden)
  if ($('search-upload-inner').classList.contains('hidden')) return;
  if (e.target.tagName === 'BUTTON') return;
  $('search-file-input').click();
});

$('search-drop-zone').addEventListener('dragover', e => {
  e.preventDefault();
  $('search-drop-zone').classList.add('drag-over');
});
$('search-drop-zone').addEventListener('dragleave', () => {
  $('search-drop-zone').classList.remove('drag-over');
});
$('search-drop-zone').addEventListener('drop', e => {
  e.preventDefault();
  $('search-drop-zone').classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) handleSearchFile(file);
});
$('search-file-input').addEventListener('change', () => {
  if ($('search-file-input').files[0]) handleSearchFile($('search-file-input').files[0]);
  $('search-file-input').value = '';
});

function handleSearchFile(file) {
  searchFile       = file;
  currentQueryFile = file.name;

  // Reset crop and cropper
  destroyCropper();
  $('crop-toggle').checked = false;
  hideEl($('crop-preview-wrap'));
  showEl($('normal-preview-wrap'));

  // Show preview
  const reader = new FileReader();
  reader.onload = ev => {
    $('search-preview-img').src = ev.target.result;
    hideEl($('search-upload-inner'));
    showEl($('search-preview-wrap'));
  };
  reader.readAsDataURL(file);
}

$('search-clear-btn').addEventListener('click', () => {
  searchFile       = null;
  currentQueryFile = null;
  destroyCropper();
  $('crop-toggle').checked = false;
  hideEl($('crop-preview-wrap'));
  showEl($('normal-preview-wrap'));
  hideEl($('search-preview-wrap'));
  showEl($('search-upload-inner'));
  hideEl($('results-header'));
  hideEl($('detection-bar'));
  $('results-grid').innerHTML = '';
  hideEl($('search-loading'));
});

$('search-btn').addEventListener('click', async () => {
  if (!searchFile) return;
  await runSearch();
});

// ── Search ────────────────────────────────────────────────────────────────────

async function runSearch() {
  hideEl($('results-header'));
  $('results-grid').innerHTML = '';
  showEl($('search-loading'));
  $('search-btn').disabled = true;

  const useCrop = $('crop-toggle').checked && cropperInstance;

  $('search-loading-text').textContent = 'Analysing image with OpenCLIP and searching vectors...';

  try {
    let fileToSend;

    if (useCrop) {
      const canvas = cropperInstance.getCroppedCanvas({ maxWidth: 1024, maxHeight: 1024 });
      fileToSend = await new Promise(resolve => {
        canvas.toBlob(
          blob => resolve(new File([blob], currentQueryFile || 'crop.jpg', { type: 'image/jpeg' })),
          'image/jpeg',
          0.92
        );
      });
    } else {
      fileToSend = searchFile;
    }

    const form = new FormData();
    form.append('file', fileToSend);

    const url = `${API}/search`;
    const res = await fetch(url, { method: 'POST', body: form });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();

    hideEl($('search-loading'));
    renderResults(data.results);
    renderDetectionBar(data);
  } catch (e) {
    hideEl($('search-loading'));
    showError('Search failed: ' + e.message);
  } finally {
    $('search-btn').disabled = false;
  }
}

// ── Detection bar ─────────────────────────────────────────────────────────────

function renderDetectionBar(data) {
  const bar      = $('detection-bar');
  const label    = $('detection-label');
  const fallback = $('detection-fallback');

  if (!data.detected_category || data.detected_category === 'all') {
    hideEl(bar);
    return;
  }

  const cat  = data.detected_category.replace(/_/g, ' ');
  const conf = data.cat_confidence + '%';

  label.innerHTML = `<span class="det-label">Detected:</span> <span class="det-cat">${cat}</span> <span class="det-conf">${conf} confidence</span>`;

  if (data.fallback_used) {
    fallback.textContent = 'Low dataset coverage — showing best available matches';
    showEl(fallback);
  } else {
    hideEl(fallback);
  }

  showEl(bar);
}


// ── Results ───────────────────────────────────────────────────────────────────

function renderResults(results) {
  const grid   = $('results-grid');
  const header = $('results-header');
  const count  = $('results-count');

  grid.innerHTML = '';

  if (!results || results.length === 0) {
    grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;color:var(--text-muted);padding:3rem">
      No similar images found. Try indexing the dataset first.
    </div>`;
    showEl(header);
    count.textContent = '0 results';
    return;
  }

  count.textContent = `${results.length} results`;
  showEl(header);

  results.forEach((item, i) => {
    const card = document.createElement('div');
    card.className = 'result-card';
    card.style.animationDelay = `${i * 0.05}s`;

    const imgSrc     = item.image_url.startsWith('http') ? item.image_url : (item.image_url.startsWith('/') ? item.image_url : '/' + item.image_url);
    const pct        = item.similarity ?? 0;
    const catText    = (item.category || 'unknown').replace(/_/g, ' ');
    const metalColor = item.metal_color || 'other';
    const metalLabel = metalColor.replace('_', ' ');
    const metalBadge = (metalColor && metalColor !== 'other' && metalColor !== 'unknown')
      ? `<span class="metal-badge metal-${metalColor}">${metalLabel}</span>`
      : '';
    // Escape for use in onclick attribute
    const fn  = (item.filename || '').replace(/'/g, "\\'");
    const cat = (item.category || '').replace(/'/g, "\\'");

    card.innerHTML = `
      <div style="overflow:hidden;height:200px">
        <img src="${imgSrc}" alt="${item.filename}"
             onerror="this.onerror=null;this.style.cssText='width:100%;height:100%;object-fit:contain;background:#1a1a1a;opacity:0.4;filter:grayscale(1)';this.src='data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyMDAiIGhlaWdodD0iMjAwIj48cmVjdCBmaWxsPSIjMjIyIiB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIvPjx0ZXh0IHg9IjUwJSIgeT0iNDUlIiBmaWxsPSIjNTU1IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBkb21pbmFudC1iYXNlbGluZT0ibWlkZGxlIiBmb250LXNpemU9IjQ4Ij7wn5qEPC90ZXh0Pjx0ZXh0IHg9IjUwJSIgeT0iNjUlIiBmaWxsPSIjNDQ0IiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBkb21pbmFudC1iYXNlbGluZT0ibWlkZGxlIiBmb250LXNpemU9IjEyIj5JbWFnZSB1bmF2YWlsYWJsZTwvdGV4dD48L3N2Zz4='"
             loading="lazy" />
      </div>
      <div class="result-card-body">
        <div class="result-card-filename" title="${item.filename}">${item.filename}</div>
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <span class="result-card-category">${catText}</span>
          ${metalBadge}
        </div>
        <div class="similarity-bar">
          <div class="similarity-fill" style="width:${pct}%"></div>
        </div>
        <div class="similarity-label">${pct}% match</div>
        <div class="feedback-btns">
          <button class="btn-feedback" title="Relevant - good result"
            onclick="submitFeedback('${fn}', '${cat}', true, this)">&#128077;</button>
          <button class="btn-feedback" title="Not relevant - bad result"
            onclick="submitFeedback('${fn}', '${cat}', false, this)">&#128078;</button>
        </div>
      </div>`;

    grid.appendChild(card);
  });
}

// ── Feedback ──────────────────────────────────────────────────────────────────

async function submitFeedback(resultFilename, resultCategory, relevant, btn) {
  const queryFilename = currentQueryFile || 'unknown';

  // Visual feedback immediately
  const btns = btn.parentElement.querySelectorAll('.btn-feedback');
  btns.forEach(b => { b.disabled = true; });
  btn.classList.add(relevant ? 'voted-yes' : 'voted-no');

  try {
    await fetch(`${API}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query_filename:  queryFilename,
        result_filename: resultFilename,
        result_category: resultCategory,
        relevant:        relevant,
      }),
    });
    // Refresh accuracy stats silently
    loadAccuracy();
  } catch (e) {
    console.warn('Feedback submit failed:', e);
    // Re-enable on failure
    btns.forEach(b => { b.disabled = false; });
    btn.classList.remove('voted-yes', 'voted-no');
  }
}

// ── Admin Upload ──────────────────────────────────────────────────────────────

let adminFile = null;

setupDropZone('admin-drop-zone', 'admin-file-input', 'admin-upload-inner', file => {
  adminFile = file;
  showPreview(file, $('admin-upload-inner'), $('admin-preview-wrap'), $('admin-preview-img'));
  $('admin-upload-btn').disabled = false;
  hideEl($('admin-success'));
});

$('admin-upload-btn').addEventListener('click', async () => {
  if (!adminFile) return;
  await adminUpload(adminFile);
});

async function adminUpload(file) {
  const btn      = $('admin-upload-btn');
  const progress = $('admin-progress');
  const success  = $('admin-success');

  btn.disabled = true;
  hideEl(success);
  showEl(progress);
  setProgress(20, 'Uploading image...');

  try {
    const category = $('admin-category').value;
    const form     = new FormData();
    form.append('file', file);
    form.append('category', category);

    setProgress(50, 'Generating OpenCLIP embedding...');

    const res = await fetch(`${API}/admin/upload`, { method: 'POST', body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    setProgress(90, 'Storing in Qdrant...');
    await new Promise(r => setTimeout(r, 400));
    setProgress(100, 'Done!');

    showEl(success);
    await loadStats();

    setTimeout(() => {
      hideEl(progress);
      adminFile = null;
      hideEl($('admin-preview-wrap'));
      showEl($('admin-upload-inner'));
      btn.disabled = true;
    }, 2500);

  } catch (e) {
    hideEl(progress);
    showError('Upload failed: ' + e.message);
    btn.disabled = false;
  }
}

// ── Manage Uploads ────────────────────────────────────────────────────────────

async function loadUploadedItems() {
  const container = $('uploads-list');
  container.innerHTML = '<p style="color:#888">Loading...</p>';
  try {
    const items = await fetch(`${API}/admin/uploads`).then(r => r.json());
    if (!items.length) {
      container.innerHTML = '<p style="color:#888">No admin-uploaded items found.</p>';
      return;
    }
    container.innerHTML = '';
    items.forEach(item => {
      const isLocal = item.image_path.startsWith('/uploads/');
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:0.75rem;padding:0.5rem;background:#111;border-radius:8px;border:1px solid #222';
      row.innerHTML = `
        <div style="flex:1;min-width:0">
          <div style="font-size:0.8rem;color:#ccc;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${item.filename}</div>
          <div style="font-size:0.7rem;color:${isLocal ? '#f87171' : '#4ade80'}">${isLocal ? '⚠ Local (will break on restart)' : '✓ Cloudinary'} · ${item.category}</div>
        </div>
        <button onclick="deleteUpload('${item.filename}', this)" style="background:#7f1d1d;color:#fca5a5;border:none;border-radius:6px;padding:4px 10px;cursor:pointer;font-size:0.75rem;flex-shrink:0">Delete</button>
      `;
      container.appendChild(row);
    });
  } catch (e) {
    container.innerHTML = `<p style="color:#f87171">Error: ${e.message}</p>`;
  }
}

async function deleteUpload(filename, btn) {
  if (!confirm(`Delete "${filename}"? This removes it from search results.`)) return;
  btn.disabled = true;
  btn.textContent = '...';
  try {
    const res = await fetch(`${API}/admin/delete/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    btn.closest('div[style]').remove();
    await loadStats();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Delete';
    showError('Delete failed: ' + e.message);
  }
}

// ── Dataset Indexing ──────────────────────────────────────────────────────────

$('index-dataset-btn').addEventListener('click', async () => {
  const btn     = $('index-dataset-btn');
  const success = $('index-success');

  btn.disabled = true;
  btn.textContent = 'Starting...';
  hideEl(success);

  try {
    await fetch(`${API}/index-dataset`, { method: 'POST' });
    showEl(success);
    pollStats();
  } catch (e) {
    showError('Indexing failed: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span class="btn-icon">&#128194;</span> Start Dataset Indexing';
  }
});

let pollInterval = null;

function pollStats() {
  if (pollInterval) return;
  pollInterval = setInterval(async () => {
    const data = await fetch(`${API}/stats`).then(r => r.json()).catch(() => null);
    if (data) renderStats(data);
  }, 3000);
  setTimeout(() => {
    clearInterval(pollInterval);
    pollInterval = null;
  }, 300_000);
}

// ── Error toast ───────────────────────────────────────────────────────────────

function showError(msg) {
  const toast = document.createElement('div');
  toast.style.cssText = `
    position:fixed;bottom:2rem;right:2rem;z-index:9999;
    padding:1rem 1.5rem;border-radius:12px;
    background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.4);
    color:#fca5a5;font-size:0.9rem;max-width:360px;
    animation:fadeIn 0.3s ease;
  `;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 5000);
}

// ── Boot ──────────────────────────────────────────────────────────────────────

loadStats();
loadAccuracy();
checkRembgStatus();

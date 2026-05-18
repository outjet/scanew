let currentAudio = null;
let currentPlayingRow = null;
let isSearchActive = false;
let lastProcessedId = 0;
let eventSource = null;
let lastServerSignalAt = 0;
let heartbeatMonitorId = null;
let liveFlashTimeoutId = null;
let scrubIntervalId = null;
let longPressActivated = false;
let longPressTimer = null;
let ctxActiveRow = null;
let filterActive = false; // set by applyFilter() once DOM + server vars are available

const SSE_ENABLED = true;
const STALE_RECONNECT_MS = 20000;
const STALE_FAILED_MS = 60000;
const toneOptions = ['', 'Moto', 'Kenwood', 'TRBO', 'MP7', 'Moto TPS', 'MDC-1200'];
const windowOptions = [1, 2, 6, 12, 24];
const toneUrls = {
  Moto: '/static/tones/Moto Talk Permit.mp3',
  Kenwood: '/static/tones/Kenwwod_Talk_Permit.mp3',
  TRBO: '/static/tones/TRBO_Normal_TPT.mp3',
  MP7: '/static/tones/Long MP7 ID.mp3',
  'Moto TPS': '/static/tones/TPS.mp3',
  'MDC-1200': '/static/tones/MDC-1200_DOS.mp3'
};

function formatTimestampEastern(isoString) {
  const date = new Date(isoString);
  if (isNaN(date)) return isoString;

  const options = {
    timeZone: 'America/New_York',
    weekday: 'short',
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  };

  const parts = new Intl.DateTimeFormat('en-US', options).formatToParts(date);
  const lookup = {};
  for (const part of parts) {
    if (part.type !== 'literal') lookup[part.type] = part.value;
  }

  return `${lookup.weekday.toUpperCase()} ${lookup.day}-${lookup.month.toUpperCase()} · ${lookup.hour}:${lookup.minute}:${lookup.second}`;
}

function updateConnectionStatus(status) {
  const el = document.getElementById('sseStatus');
  if (!el) return;
  el.className = 'live-dot ' + status;
}

function flashLiveIndicator() {
  const el = document.getElementById('sseStatus');
  if (!el) return;
  el.classList.add('is-flashing');
  if (liveFlashTimeoutId) window.clearTimeout(liveFlashTimeoutId);
  liveFlashTimeoutId = window.setTimeout(() => {
    el.classList.remove('is-flashing');
    liveFlashTimeoutId = null;
  }, 550);
}

function recordServerSignal() {
  lastServerSignalAt = Date.now();
  updateConnectionStatus('connected');
  flashLiveIndicator();
}

function startHeartbeatMonitor() {
  if (heartbeatMonitorId) window.clearInterval(heartbeatMonitorId);
  heartbeatMonitorId = window.setInterval(() => {
    if (isSearchActive || !eventSource) return;
    if (eventSource.readyState !== EventSource.OPEN) return;
    if (!lastServerSignalAt) return;

    const silenceMs = Date.now() - lastServerSignalAt;
    if (silenceMs >= STALE_FAILED_MS) {
      updateConnectionStatus('failed');
    } else if (silenceMs >= STALE_RECONNECT_MS) {
      updateConnectionStatus('disconnected');
    }
  }, 1000);
}

function setupSSE(calledFrom = 'unknown') {
  if (!SSE_ENABLED || typeof streamUrl === 'undefined' || !streamUrl) {
    updateConnectionStatus('failed');
    return;
  }

  if (eventSource) eventSource.close();
  eventSource = new EventSource(streamUrl);
  lastServerSignalAt = 0;
  startHeartbeatMonitor();

  eventSource.onopen = function () {
    updateConnectionStatus('disconnected');
  };

  eventSource.onmessage = function (event) {
    if (!event.data || (!event.data.startsWith('{') && !event.data.startsWith('['))) return;

    try {
      const transcription = JSON.parse(event.data);
      recordServerSignal();
      processNewTranscription(transcription);
    } catch (error) {
      console.error(`SSE parse error (${calledFrom}):`, error);
    }
  };
  
  eventSource.addEventListener('heartbeat', function () {
    recordServerSignal();
  });

  eventSource.onerror = function (event) {
    if (event.target.readyState === EventSource.CONNECTING) {
      updateConnectionStatus('disconnected');
      return;
    }

    updateConnectionStatus('failed');
    if (!isSearchActive) {
      setTimeout(() => setupSSE(`reconnect-${calledFrom}`), 5000);
    }
  };
}

function buildAudioUrl(transcription) {
  if (transcription.url) return transcription.url;
  if (transcription.wav_filename) return `/recordings/${transcription.wav_filename}`;
  if (transcription.wavFilename) return `/recordings/${transcription.wavFilename}`;
  return null;
}

function getTranscriptionText(transcription) {
  return transcription.text || transcription.transcript || '';
}

function matchesAlertPatternText(text) {
  if (!text) return false;
  if (typeof alertPatterns === 'undefined' || !Array.isArray(alertPatterns)) return false;

  return alertPatterns.some((pattern) => {
    try {
      const re = new RegExp(pattern, 'i');
      return re.test(text);
    } catch (_) {
      return false;
    }
  });
}

function createTranscriptionRow(transcription) {
  const row = document.createElement('div');
  row.className = 'transcript-row';
  row.setAttribute('data-id', transcription.id);

  const audioUrl = buildAudioUrl(transcription);
  const timestamp = formatTimestampEastern(transcription.timestamp);
  const text = getTranscriptionText(transcription);
  const alertMatch = Boolean(transcription.alert_match) || matchesAlertPatternText(text);
  const classCode = transcription.class_code ?? transcription.classCode ?? '';
  const initialDispatch = Boolean(transcription.initialdispatch);

  row.setAttribute('data-timestamp', transcription.timestamp);
  row.setAttribute('data-alert-match', alertMatch ? 'true' : 'false');
  row.setAttribute('data-class-code', String(classCode));
  row.setAttribute('data-initialdispatch', initialDispatch ? 'true' : 'false');
  if (audioUrl) row.setAttribute('data-audio-url', audioUrl);

  const isValidated = Boolean(transcription.validated);
  const isEdited = Boolean(transcription.edited);
  row.setAttribute('data-validated', isValidated ? 'true' : 'false');
  row.setAttribute('data-edited', isEdited ? 'true' : 'false');

  if (alertMatch) row.classList.add('alert-match');
  if (initialDispatch) row.classList.add('initial-dispatch');
  if (isValidated) row.classList.add('is-validated');
  if (isEdited) row.classList.add('is-edited');

  const tagHtml = alertMatch
    ? '<span class="row-tag tag-alert">ALERT</span>'
    : initialDispatch
      ? '<span class="row-tag tag-initial">DISPATCH</span>'
      : '';

  const searchInput = document.getElementById('searchInput');
  const searchQuery = searchInput ? searchInput.value.trim() : '';
  const contextLinkHtml = searchQuery
    ? `<a href="/transcription_context/${transcription.id}" class="context-link" title="See in context">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/></svg>
       </a>`
    : '';

  const tenFourHtml = (userHasAdminRole && text.length < 10)
    ? `<button class="ten-four-btn" title="Save as 10-4">10-4</button>`
    : '';

  const editActionHtml = userHasAdminRole
    ? `<button class="row-action-btn row-action-edit" type="button" title="Edit transcript">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
       </button>`
    : '';

  row.innerHTML = `
    <div class="row-bar"></div>
    <div class="row-header">
      <span class="row-time">${timestamp}</span>
      ${tagHtml}
      ${contextLinkHtml}
      <span class="row-waveform" aria-hidden="true">
        <span></span><span></span><span></span><span></span><span></span><span></span>
        <span></span><span></span><span></span><span></span><span></span><span></span>
      </span>
      ${tenFourHtml}
    </div>
    <div class="row-text"></div>
    <div class="row-scrubber">
      <span class="scrub-elapsed">0:00</span>
      <div class="scrub-track"><div class="scrub-fill"></div></div>
      <span class="scrub-total">0:00</span>
    </div>
    <div class="row-actions">
      ${editActionHtml}
      <button class="row-action-btn row-action-validate" type="button" title="Validate">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
      </button>
    </div>
  `;

  row.querySelector('.row-text').textContent = text;
  return row;
}

function processNewTranscription(transcription) {
  if (!transcription || !transcription.id) return;

  if (transcription.type === 'classification_update') {
    applyClassificationUpdate(transcription);
    return;
  }
  if (transcription.id <= lastProcessedId) return;

  const feedList = document.getElementById('feedList');
  if (!feedList) return;

  const newRow = createTranscriptionRow(transcription);
  newRow.classList.add('row-new');
  feedList.insertBefore(newRow, feedList.firstChild);
  window.setTimeout(() => newRow.classList.remove('row-new'), 1500);
  if (filterActive) {
    const isMatch = newRow.classList.contains('alert-match') || newRow.classList.contains('initial-dispatch');
    newRow.hidden = !isMatch;
  }

  lastProcessedId = transcription.id;
  playSelectedTone();

  const autoPlayMode = localStorage.getItem('autoPlayMode') || 'OFF';
  if (autoPlayMode === 'ON') {
    const text = getTranscriptionText(transcription);
    const alertMatch = Boolean(transcription.alert_match) || matchesAlertPatternText(text);
    const initialDispatch = Boolean(transcription.initialdispatch);

    if (alertMatch || initialDispatch) {
      const audioUrl = buildAudioUrl(transcription);
      if (audioUrl) {
        const delay = (localStorage.getItem('selectedTone') || '') ? 1200 : 0;
        setTimeout(() => {
          playAudio(audioUrl, newRow);
        }, delay);
      }
    }
  }
}

function applyClassificationUpdate(transcription) {
  const row = document.querySelector(`.transcript-row[data-id="${transcription.id}"]`);
  if (!row) return;

  const classCode = transcription.class_code ?? transcription.classCode ?? '';
  const initialDispatch = Boolean(transcription.initialdispatch);

  row.setAttribute('data-class-code', String(classCode));
  row.setAttribute('data-initialdispatch', initialDispatch ? 'true' : 'false');
  row.classList.toggle('initial-dispatch', initialDispatch);

  const autoPlayMode = localStorage.getItem('autoPlayMode') || 'OFF';
  if (autoPlayMode === 'ON' && initialDispatch) {
    const wasAlertMatch = row.getAttribute('data-alert-match') === 'true';
    if (!wasAlertMatch) {
      const audioUrl = row.getAttribute('data-audio-url');
      if (audioUrl) {
        playAudio(audioUrl, row);
      }
    }
  }
}

function fmtSec(s) {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}:${String(r).padStart(2, '0')}`;
}

function stopScrubber() {
  if (scrubIntervalId) { window.clearInterval(scrubIntervalId); scrubIntervalId = null; }
}

function startScrubber(audio, row, onDone) {
  stopScrubber();
  const fill    = row.querySelector('.scrub-fill');
  const elapsed = row.querySelector('.scrub-elapsed');
  const total   = row.querySelector('.scrub-total');

  audio.addEventListener('loadedmetadata', () => {
    if (isFinite(audio.duration) && total) total.textContent = fmtSec(audio.duration);
  });

  scrubIntervalId = window.setInterval(() => {
    if (!audio) { stopScrubber(); return; }
    const dur = isFinite(audio.duration) ? audio.duration : 0;
    const cur = audio.currentTime || 0;
    if (fill)    fill.style.width = dur ? `${(cur / dur) * 100}%` : '0%';
    if (elapsed) elapsed.textContent = fmtSec(cur);
    if (dur && total) total.textContent = fmtSec(dur);

    // Fallback: if audio ended but onended didn't fire (common on mobile)
    if (dur > 0 && cur >= dur - 0.15 && audio.paused) {
      stopScrubber();
      if (onDone) onDone();
    }
  }, 150);
}

function playAudio(url, rowToHighlight = null) {
  if (!url) return;

  stopScrubber();
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  if (currentPlayingRow) { currentPlayingRow.classList.remove('is-playing'); currentPlayingRow = null; }

  if (rowToHighlight) {
    rowToHighlight.classList.add('is-playing');
    currentPlayingRow = rowToHighlight;
  }

  const audio = new Audio(url);
  currentAudio = audio;
  const row = rowToHighlight;

  function cleanup() {
    if (currentAudio !== audio) return; // a newer playAudio call took over
    stopScrubber();
    if (row) row.classList.remove('is-playing');
    if (currentPlayingRow === row) { currentPlayingRow = null; }
    currentAudio = null;
  }

  audio.onended = cleanup;
  audio.onerror = cleanup;

  const p = audio.play();
  if (p && typeof p.catch === 'function') {
    p.catch((err) => {
      console.warn('Audio playback failed:', err);
      cleanup();
    });
  }

  if (row) startScrubber(audio, row, cleanup);
}

function updateToneLabel() {
  const el = document.getElementById('toneValue');
  if (!el) return;
  const tone = localStorage.getItem('selectedTone') || '';
  el.textContent = tone || 'None';
}

function updateWindowPicker() {
  const hours = parseInt(localStorage.getItem('blotterHours') || '2', 10);
  document.querySelectorAll('.win-btn').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.dataset.hours, 10) === hours);
  });
}

function updateAutoPlayToggle() {
  const btn = document.getElementById('autoPlayToggle');
  if (!btn) return;
  const on = (localStorage.getItem('autoPlayMode') || 'OFF') === 'ON';
  btn.setAttribute('aria-checked', on ? 'true' : 'false');
}

function setAutoPlay(on) {
  localStorage.setItem('autoPlayMode', on ? 'ON' : 'OFF');
  updateAutoPlayToggle();
}

function cycleTone() {
  const current = localStorage.getItem('selectedTone') || '';
  const idx = toneOptions.indexOf(current);
  const next = toneOptions[(idx + 1) % toneOptions.length];
  localStorage.setItem('selectedTone', next);
  updateToneLabel();
  playSelectedTone();
}

function setBlotterWindow(hours) {
  localStorage.setItem('blotterHours', String(hours));
  updateWindowPicker();
  if (document.getElementById('panel-blotter') && !document.getElementById('panel-blotter').hidden) {
    fetchBlotter();
  }
}

function playSelectedTone() {
  const selectedTone = localStorage.getItem('selectedTone') || '';
  if (!selectedTone || !toneUrls[selectedTone]) return;

  const audio = new Audio(toneUrls[selectedTone]);
  audio.play().catch(() => {});
}

function fetchBlotter() {
  if (typeof blotterUrl === 'undefined' || !blotterUrl) return;

  const blotterOutput = document.getElementById('blotterOutput');
  const refreshBtn    = document.getElementById('refreshBlotterBtn');
  if (!blotterOutput) return;

  const hours = parseInt(localStorage.getItem('blotterHours') || '2', 10);
  blotterOutput.innerHTML = '<div class="panel-loading" style="display:flex"><div class="spinner"></div><p>Loading blotter…</p></div>';
  if (refreshBtn) refreshBtn.disabled = true;

  fetch(`${blotterUrl}?hours=${encodeURIComponent(hours)}`)
    .then((resp) => { if (!resp.ok) throw new Error('Network error'); return resp.text(); })
    .then((html) => { blotterOutput.innerHTML = html; })
    .catch(() => {
      blotterOutput.innerHTML = '<div class="panel-empty"><p>Could not load blotter. Tap Refresh to try again.</p></div>';
    })
    .finally(() => { if (refreshBtn) refreshBtn.disabled = false; });
}

function toggleSearchMode(active) {
  isSearchActive = active;
  const searchBar  = document.getElementById('searchBar');
  const searchMeta = document.getElementById('searchMeta');
  const searchBtn  = document.getElementById('searchBtn');

  if (searchBar)  searchBar.hidden  = !active;
  if (searchBtn)  searchBtn.classList.toggle('active', active);

  if (active && eventSource) {
    eventSource.close();
    eventSource = null;
    if (heartbeatMonitorId) { window.clearInterval(heartbeatMonitorId); heartbeatMonitorId = null; }
    updateConnectionStatus('search_mode');
  } else if (!active && document.getElementById('feedList')) {
    if (searchMeta) searchMeta.hidden = true;
    setupSSE('search-exit');
  }
}

function openSearch() {
  const searchBar = document.getElementById('searchBar');
  if (searchBar) searchBar.hidden = false;
  document.getElementById('searchBtn').classList.add('active');
  const input = document.getElementById('searchInput');
  if (input) { input.focus(); input.select(); }
}

function closeSearch() {
  toggleSearchMode(false);
  window.location.href = '/';
}

function getTimeDifference(timestamp) {
  const updateTime = new Date(timestamp);
  if (isNaN(updateTime.getTime())) return 'unknown';

  const diffMs = Date.now() - updateTime.getTime();
  if (diffMs < 60000) return 'just now';

  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 60) return `${minutes} min ago`;

  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours > 1 ? 's' : ''} ago`;

  const days = Math.floor(hours / 24);
  return `${days} day${days > 1 ? 's' : ''} ago`;
}

function displayUnitLocations(unitData) {
  const content = document.getElementById('unitLocationsContent');
  if (!content) return;

  if (unitData.error) {
    content.innerHTML = `<div class="panel-empty"><p>Error: ${unitData.error}</p></div>`;
    return;
  }

  const cardHtml = Object.entries(unitData).map(([unit, data]) => {
    const icon = data.type && data.type.toLowerCase() === 'fire' ? '🚒' : '🚓';
    const status = data.status || 'Unknown';
    const location = data.location || 'Unknown';
    const detail = data.detail || 'Not available';
    const lastUpdate = getTimeDifference(data.last_update);
    return `
      <div class="unit-card">
        <div class="unit-header">${icon} ${unit}</div>
        <div class="unit-body">
          <p><strong>Status:</strong> ${status}</p>
          <p><strong>Location:</strong> ${location}</p>
          <p><strong>Detail:</strong> ${detail}</p>
          <p><small>Updated ${lastUpdate}</small></p>
        </div>
      </div>`;
  }).join('');

  content.innerHTML = `<div class="unit-grid">${cardHtml}</div>`;
}

function fetchUnitLocations() {
  const empty   = document.getElementById('unitsEmpty');
  const loading = document.getElementById('unitLocationsLoading');
  const content = document.getElementById('unitLocationsContent');

  if (empty)   empty.hidden   = true;
  if (loading) loading.hidden = false;
  if (content) content.innerHTML = '';

  fetch('/unit_locations')
    .then((r) => r.json())
    .then((data) => {
      if (loading) loading.hidden = true;
      displayUnitLocations(data);
    })
    .catch(() => {
      if (loading) loading.hidden = true;
      if (content) content.innerHTML = '<div class="panel-empty"><p>Could not load unit locations.</p></div>';
    });
}

// ── Feed filter (Dispatches & Alerts only) ───────────────────
function applyFilter() {
  // Sync button + banner to current URL state (no JS hide/show — server handles it)
  const btn    = document.getElementById('filterBtn');
  const banner = document.getElementById('filterBanner');
  const isFiltered = (typeof currentFeedFilter !== 'undefined') && currentFeedFilter === 'dispatches';
  if (btn)    btn.classList.toggle('active', isFiltered);
  if (banner) banner.hidden = !isFiltered;
  // Keep module-level flag in sync for SSE rows
  filterActive = isFiltered;
}

function toggleFilter() {
  const isFiltered = (typeof currentFeedFilter !== 'undefined') && currentFeedFilter === 'dispatches';
  const url = new URL(feedUrl, window.location.origin);
  url.searchParams.set('page', '1');
  url.searchParams.set('per_page', String(currentPerPage));
  if (isFiltered) {
    url.searchParams.delete('filter');
  } else {
    url.searchParams.set('filter', 'dispatches');
  }
  window.location.href = url.toString();
}

// ── Swipe to validate / edit ─────────────────────────────────
function validateTranscription(id, row) {
  if (typeof validateTranscriptionUrl === 'undefined' || !validateTranscriptionUrl) return;
  const alreadyValidated = row.classList.contains('is-validated');
  fetch(validateTranscriptionUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, validated: !alreadyValidated })
  })
    .then(r => r.json())
    .then(data => {
      if (data.success) {
        row.classList.toggle('is-validated', !alreadyValidated);
        row.setAttribute('data-validated', (!alreadyValidated).toString());
        row.classList.add('swipe-flash-right');
        setTimeout(() => row.classList.remove('swipe-flash-right'), 600);
      }
    })
    .catch(err => console.error('Validate error:', err));
}

// ── Context menu (long-press / right-click) ──────────────────
function showContextMenu(row) {
  longPressActivated = true;
  ctxActiveRow = row;
  const preview = (row.querySelector('.row-text') || {}).textContent || '';
  const previewEl = document.getElementById('ctxPreview');
  if (previewEl) previewEl.textContent = preview.length > 120 ? preview.slice(0, 117) + '…' : preview;
  const editBtn = document.getElementById('ctxEditBtn');
  if (editBtn) editBtn.hidden = !userHasAdminRole;
  const validateLabel = document.getElementById('ctxValidateLabel');
  if (validateLabel) validateLabel.textContent = row.classList.contains('is-validated') ? 'Unvalidate' : 'Validate';
  const overlay = document.getElementById('ctxOverlay');
  const sheet = document.getElementById('ctxSheet');
  row.classList.add('ctx-highlight');
  if (overlay) overlay.hidden = false;
  if (sheet) { sheet.hidden = false; requestAnimationFrame(() => sheet.classList.add('is-open')); }
}

function closeContextMenu() {
  const overlay = document.getElementById('ctxOverlay');
  const sheet = document.getElementById('ctxSheet');
  if (sheet) {
    sheet.classList.remove('is-open');
    sheet.addEventListener('transitionend', () => { sheet.hidden = true; }, { once: true });
  }
  if (overlay) overlay.hidden = true;
  if (ctxActiveRow) ctxActiveRow.classList.remove('ctx-highlight');
  ctxActiveRow = null;
}

function openEditDialog(row) {
  closeContextMenu();
  const id = row.getAttribute('data-id');
  const text = (row.querySelector('.row-text') || {}).textContent || '';
  const audioUrl = row.getAttribute('data-audio-url') || '';
  const textarea = document.getElementById('editTextarea');
  const dialog = document.getElementById('editDialog');
  if (!textarea || !dialog) return;
  textarea.value = text;
  dialog.dataset.editId = id;
  dialog.dataset.audioUrl = audioUrl;
  const replayBtn = document.getElementById('editReplayBtn');
  if (replayBtn) replayBtn.hidden = !audioUrl;
  dialog.showModal();
}

function saveEdit() {
  if (typeof editTranscriptionUrl === 'undefined' || !editTranscriptionUrl) return;
  const dialog = document.getElementById('editDialog');
  const textarea = document.getElementById('editTextarea');
  if (!dialog || !textarea) return;
  const id = dialog.dataset.editId;
  const transcript = textarea.value;
  if (!id) return;
  fetch(editTranscriptionUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, transcript })
  })
    .then(r => { if (!r.ok) return r.text().then(t => { throw new Error(t); }); return r.json(); })
    .then(data => {
      if (!data.success) throw new Error(data.error || 'Unknown error');
      const row = document.querySelector(`.transcript-row[data-id="${id}"]`);
      if (row) {
        const el = row.querySelector('.row-text');
        if (el) el.textContent = transcript;
        row.classList.add('is-edited');
        row.setAttribute('data-edited', 'true');
      }
      dialog.close();
    })
    .catch(err => { console.error('Error saving edit:', err); alert('Error saving: ' + err.message); });
}

// ── Tab switching ──────────────────────────────────────────
let unitsLoaded = false;

function switchTab(tabId) {
  document.querySelectorAll('.tab-panel').forEach(p => { p.hidden = true; });
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tabId);
  });
  const panel = document.getElementById('panel-' + tabId);
  if (panel) panel.hidden = false;

  if (tabId === 'blotter') fetchBlotter();
  if (tabId === 'units' && !unitsLoaded) { unitsLoaded = true; fetchUnitLocations(); }
}

// ── Dark mode ──────────────────────────────────────────────
function applyTheme(dark) {
  const shell = document.getElementById('appShell');
  if (shell) shell.setAttribute('data-theme', dark ? 'dark' : '');
  const btn = document.getElementById('darkModeToggle');
  if (btn) btn.setAttribute('aria-checked', dark ? 'true' : 'false');
}

function toggleDarkMode() {
  const dark = localStorage.getItem('darkMode') !== 'true';
  localStorage.setItem('darkMode', dark ? 'true' : 'false');
  applyTheme(dark);
}

// ── Density ────────────────────────────────────────────────
function applyDensity(compact) {
  const shell = document.getElementById('appShell');
  if (shell) shell.setAttribute('data-density', compact ? 'compact' : 'comfy');
  const btn = document.getElementById('densityToggle');
  if (btn) btn.setAttribute('aria-checked', compact ? 'true' : 'false');
}

function toggleDensity() {
  const compact = localStorage.getItem('density') !== 'comfy';
  localStorage.setItem('density', compact ? 'compact' : 'comfy');
  applyDensity(compact);
}

$(document).ready(function () {
  // Restore localStorage defaults
  if (!localStorage.getItem('blotterHours'))  localStorage.setItem('blotterHours', '2');
  if (!localStorage.getItem('selectedTone'))  localStorage.setItem('selectedTone', '');
  if (!localStorage.getItem('autoPlayMode'))  localStorage.setItem('autoPlayMode', 'OFF');
  if (!localStorage.getItem('density'))       localStorage.setItem('density', 'compact');

  // Apply theme + density immediately
  applyTheme(localStorage.getItem('darkMode') === 'true');
  applyDensity(localStorage.getItem('density') !== 'comfy');

  updateToneLabel();
  updateWindowPicker();
  updateAutoPlayToggle();

  // Re-format timestamps rendered server-side
  document.querySelectorAll('.transcript-row[data-timestamp]').forEach((row) => {
    const iso = row.getAttribute('data-timestamp');
    const timeEl = row.querySelector('.row-time');
    if (timeEl) timeEl.textContent = formatTimestampEastern(iso);
  });

  // SSE or search
  const searchInput = $('#searchInput');
  const searchQuery = searchInput.length ? (searchInput.val() || '').trim() : '';
  isSearchActive = searchQuery !== '';

  if (document.getElementById('feedList') && !isSearchActive) {
    setupSSE('document-ready');
  } else if (isSearchActive) {
    updateConnectionStatus('search_mode');
    const searchBar  = document.getElementById('searchBar');
    const searchMeta = document.getElementById('searchMeta');
    if (searchBar)  searchBar.hidden  = false;
    if (searchMeta) searchMeta.hidden = false;
    document.getElementById('searchBtn').classList.add('active');
  } else {
    updateConnectionStatus('failed');
  }

  // Search button
  $('#searchBtn').on('click', function () {
    const searchBar = document.getElementById('searchBar');
    if (searchBar && !searchBar.hidden) {
      closeSearch();
    } else {
      openSearch();
    }
  });

  // Clear button in search bar
  $('#searchClearBtn').on('click', function () {
    closeSearch();
  });

  // Search form submit
  $('#searchForm').on('submit', function (e) {
    e.preventDefault();
    const value = ($('#searchInput').val() || '').trim();
    if (value) {
      toggleSearchMode(true);
      this.submit();
    } else {
      closeSearch();
    }
  });

  // Tab switching
  $(document).on('click', '.tab-btn', function () {
    switchTab(this.dataset.tab);
  });

  // Feed filter toggle
  applyFilter(); // sync button/banner to URL state on load
  $('#filterBtn').on('click', toggleFilter);
  $('#filterBannerClear').on('click', toggleFilter);

  // Transcript row click → play audio (guarded against long-press)
  $(document).on('click', '.transcript-row', function (event) {
    if (longPressActivated) { longPressActivated = false; return; }
    if ($(event.target).closest('a').length) return;
    if (this.classList.contains('is-playing')) {
      if (currentAudio) { currentAudio.pause(); currentAudio = null; }
      stopScrubber();
      this.classList.remove('is-playing');
      if (currentPlayingRow === this) currentPlayingRow = null;
      return;
    }
    const audioUrl = this.getAttribute('data-audio-url');
    if (!audioUrl) return;
    playAudio(audioUrl, this);
  });

  // Swipe + long-press detection (mobile)
  let swipeTouchStartX = 0;
  let swipeTouchStartY = 0;
  let swipeActivated = false;

  $(document).on('touchstart', '.transcript-row', function (e) {
    const row = this;
    const touch = e.originalEvent.touches[0];
    swipeTouchStartX = touch.clientX;
    swipeTouchStartY = touch.clientY;
    swipeActivated = false;
    longPressActivated = false;
    longPressTimer = setTimeout(() => { showContextMenu(row); }, 1000);
  });

  $(document).on('touchmove', '.transcript-row', function (e) {
    const touch = e.originalEvent.touches[0];
    const dx = touch.clientX - swipeTouchStartX;
    const dy = touch.clientY - swipeTouchStartY;
    if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 10) {
      clearTimeout(longPressTimer);
      e.preventDefault(); // prevent scroll during horizontal swipe
    }
  });

  $(document).on('touchend', '.transcript-row', function (e) {
    clearTimeout(longPressTimer);
    if (swipeActivated) return;
    const touch = e.originalEvent.changedTouches[0];
    const dx = touch.clientX - swipeTouchStartX;
    const dy = touch.clientY - swipeTouchStartY;
    if (Math.abs(dx) > Math.abs(dy) && Math.abs(dx) > 60) {
      swipeActivated = true;
      longPressActivated = true; // suppress click
      if (dx > 0) {
        // Swipe right → validate
        validateTranscription(this.getAttribute('data-id'), this);
      } else {
        // Swipe left → play audio + open edit dialog
        if (userHasAdminRole) {
          const audioUrl = this.getAttribute('data-audio-url');
          if (audioUrl) playAudio(audioUrl, this);
          openEditDialog(this);
        }
      }
    }
  });

  $(document).on('touchcancel', '.transcript-row', function () {
    clearTimeout(longPressTimer);
  });

  // Right-click → context menu (desktop)
  $(document).on('contextmenu', '.transcript-row', function (e) {
    e.preventDefault();
    showContextMenu(this);
  });

  // Context menu actions
  $('#ctxOverlay').on('click', closeContextMenu);
  $('#ctxCancelBtn').on('click', closeContextMenu);
  $('#ctxCopyBtn').on('click', function () {
    const text = (ctxActiveRow && ctxActiveRow.querySelector('.row-text') || {}).textContent || '';
    navigator.clipboard.writeText(text).catch(() => {});
    closeContextMenu();
  });
  $('#ctxEditBtn').on('click', function () {
    if (ctxActiveRow) openEditDialog(ctxActiveRow);
  });
  $('#ctxValidateBtn').on('click', function () {
    if (ctxActiveRow) validateTranscription(ctxActiveRow.getAttribute('data-id'), ctxActiveRow);
    closeContextMenu();
  });

  // Row hover action buttons (desktop)
  $(document).on('click', '.row-action-edit', function (e) {
    e.stopPropagation();
    const row = $(this).closest('.transcript-row')[0];
    if (row) openEditDialog(row);
  });
  $(document).on('click', '.row-action-validate', function (e) {
    e.stopPropagation();
    const row = $(this).closest('.transcript-row')[0];
    if (row) validateTranscription(row.getAttribute('data-id'), row);
  });

  // Edit dialog actions
  $('#editSaveBtn').on('click', saveEdit);
  $('#editCancelBtn').on('click', function () { document.getElementById('editDialog').close(); });
  $('#editCloseBtn').on('click', function () { document.getElementById('editDialog').close(); });
  $('#editReplayBtn').on('click', function () {
    const dialog = document.getElementById('editDialog');
    const audioUrl = dialog && dialog.dataset.audioUrl;
    if (audioUrl) playAudio(audioUrl);
  });

  // 10-4 quick-save button
  $(document).on('click', '.ten-four-btn', function (e) {
    e.stopPropagation();
    if (typeof editTranscriptionUrl === 'undefined' || !editTranscriptionUrl) return;
    const row = $(this).closest('.transcript-row')[0];
    if (!row) return;
    const id = row.getAttribute('data-id');
    if (!id) return;
    const btn = this;
    fetch(editTranscriptionUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, transcript: '10-4' })
    })
      .then(r => r.json())
      .then(data => {
        if (data.success) {
          const el = row.querySelector('.row-text');
          if (el) el.textContent = '10-4';
          row.classList.add('is-edited');
          row.setAttribute('data-edited', 'true');
          btn.hidden = true;
        }
      })
      .catch(err => console.error('10-4 save error:', err));
  });

  // Blotter refresh
  $('#refreshBlotterBtn').on('click', fetchBlotter);

  // Blotter window picker
  $(document).on('click', '.win-btn', function () {
    setBlotterWindow(parseInt(this.dataset.hours, 10));
  });

  // Tone cycler (in You tab)
  $('#toneCycleBtn').on('click', cycleTone);

  // Auto-play toggle (in You tab)
  $('#autoPlayToggle').on('click', function () {
    const current = (localStorage.getItem('autoPlayMode') || 'OFF') === 'ON';
    setAutoPlay(!current);
  });

  // Dark mode toggle (in You tab)
  $('#darkModeToggle').on('click', toggleDarkMode);

  // Density toggle (in You tab)
  $('#densityToggle').on('click', toggleDensity);

  // Visibility change
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && !isSearchActive && eventSource && eventSource.readyState === EventSource.OPEN) {
      updateConnectionStatus('connected');
    }
  });
});

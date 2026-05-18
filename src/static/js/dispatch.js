let currentAudio = null;
let currentPlayingRow = null;
let isSearchActive = false;
let lastProcessedId = 0;
let eventSource = null;
let lastServerSignalAt = 0;
let heartbeatMonitorId = null;
let liveFlashTimeoutId = null;
let scrubIntervalId = null;

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

  if (alertMatch) row.classList.add('alert-match');
  if (initialDispatch) row.classList.add('initial-dispatch');

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
    </div>
    <div class="row-text"></div>
    <div class="row-scrubber">
      <span class="scrub-elapsed">0:00</span>
      <div class="scrub-track"><div class="scrub-fill"></div></div>
      <span class="scrub-total">0:00</span>
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

function startScrubber(audio, row) {
  stopScrubber();
  const fill    = row.querySelector('.scrub-fill');
  const elapsed = row.querySelector('.scrub-elapsed');
  const total   = row.querySelector('.scrub-total');

  scrubIntervalId = window.setInterval(() => {
    if (!audio || audio.paused) return;
    const dur = audio.duration || 0;
    const cur = audio.currentTime || 0;
    if (fill)    fill.style.width = dur ? `${(cur / dur) * 100}%` : '0%';
    if (elapsed) elapsed.textContent = fmtSec(cur);
    if (total)   total.textContent   = fmtSec(dur);
  }, 100);

  audio.addEventListener('loadedmetadata', () => {
    if (total) total.textContent = fmtSec(audio.duration);
  });
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

  currentAudio = new Audio(url);
  currentAudio.play().catch((err) => console.warn('Audio playback failed:', err));
  if (rowToHighlight) startScrubber(currentAudio, rowToHighlight);

  currentAudio.onended = function () {
    stopScrubber();
    if (currentPlayingRow) currentPlayingRow.classList.remove('is-playing');
    currentPlayingRow = null;
    currentAudio = null;
  };
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

function editTranscription(id, text, audioUrl) {
  if (!id) return;
  $('#editModal').data('transcriptionId', id);
  $('#editModal').find('#editText').val(text);
  $('#editModal').find('#playPauseButton').data('audio-url', audioUrl);
  $('#editModal').modal('show');
}

function saveEdit() {
  if (typeof editTranscriptionUrl === 'undefined' || !editTranscriptionUrl) return;

  const id = $('#editModal').data('transcriptionId');
  const text = $('#editModal').find('#editText').val();
  if (!id) return;

  fetch(editTranscriptionUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, text })
  })
    .then((response) => {
      if (!response.ok) return response.text().then((txt) => { throw new Error(txt); });
      return response.json();
    })
    .then((data) => {
      if (!data.success) throw new Error(data.error || 'Unknown error');
      $(`tr[data-id="${id}"] .transcription-text`).text(text);
      $('#editModal').modal('hide');
    })
    .catch((error) => {
      console.error('Error saving edit:', error);
      alert(`An error occurred while saving changes: ${error.message}`);
    });
}

function togglePlayPause() {
  const audioUrl = $('#playPauseButton').data('audio-url');
  const icon = $('#playPauseButton i');

  if (!currentAudio) {
    currentAudio = new Audio(audioUrl);
    currentAudio.play();
    icon.removeClass('fa-play').addClass('fa-pause');
    $('#playPauseButton').contents().last()[0].textContent = ' Pause';
    return;
  }

  if (currentAudio.paused) {
    currentAudio.play();
    icon.removeClass('fa-play').addClass('fa-pause');
    $('#playPauseButton').contents().last()[0].textContent = ' Pause';
  } else {
    currentAudio.pause();
    icon.removeClass('fa-pause').addClass('fa-play');
    $('#playPauseButton').contents().last()[0].textContent = ' Play';
  }
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

  // Transcript row click → play audio
  $(document).on('click', '.transcript-row', function (event) {
    if ($(event.target).closest('a').length) return;
    const audioUrl = this.getAttribute('data-audio-url');
    if (!audioUrl) return;
    playAudio(audioUrl, this);
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

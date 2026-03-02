let currentAudio = null;
let currentPlayingRow = null;
let isSearchActive = false;
let lastProcessedId = 0;
let eventSource = null;

const SSE_ENABLED = true;
const toneOptions = ['', 'Moto', 'Kenwood', 'TRBO', 'MP7', 'Moto TPS', 'MDC-1200'];
const windowOptions = [1, 2, 4, 8, 12];
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
  const statusElement = document.getElementById('sseStatus');
  if (!statusElement) return;

  switch (status) {
    case 'connected':
      statusElement.innerHTML = '<span class="status-dot">●</span> LIVE';
      break;
    case 'disconnected':
      statusElement.innerHTML = '<span class="status-dot">●</span> RECONNECT';
      break;
    case 'failed':
      statusElement.innerHTML = '<span class="status-dot">●</span> FAILED';
      break;
    case 'search_mode':
      statusElement.innerHTML = '<span class="status-dot">●</span> SEARCH';
      break;
    default:
      statusElement.innerHTML = '<span class="status-dot">●</span> IDLE';
  }
}

function setupSSE(calledFrom = 'unknown') {
  if (!SSE_ENABLED || typeof streamUrl === 'undefined' || !streamUrl) {
    updateConnectionStatus('failed');
    return;
  }

  if (eventSource) eventSource.close();
  eventSource = new EventSource(streamUrl);

  eventSource.onopen = function () {
    updateConnectionStatus('connected');
  };

  eventSource.onmessage = function (event) {
    if (!event.data || (!event.data.startsWith('{') && !event.data.startsWith('['))) return;

    try {
      const transcription = JSON.parse(event.data);
      processNewTranscription(transcription);
    } catch (error) {
      console.error(`SSE parse error (${calledFrom}):`, error);
    }
  };

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

function buildWaveformHtml() {
  return '<div class="play-waveform" aria-hidden="true"><span></span><span></span><span></span><span></span><span></span></div>';
}

function createTranscriptionRow(transcription) {
  const row = document.createElement('tr');
  row.setAttribute('data-id', transcription.id);

  const audioUrl = buildAudioUrl(transcription);
  const timestamp = formatTimestampEastern(transcription.timestamp);

  row.innerHTML = `
    <td class="transcription-cell" data-timestamp="${transcription.timestamp}" ${audioUrl ? `data-audio-url="${audioUrl}"` : ''}>
      <small class="transcription-meta">${timestamp}</small>
      <span class="transcription-text"></span>
      ${buildWaveformHtml()}
    </td>
  `;

  const searchInput = document.getElementById('searchInput');
  const searchQuery = searchInput ? searchInput.value.trim() : '';
  if (searchQuery) {
    const contextLink = document.createElement('a');
    contextLink.href = `/transcription_context/${transcription.id}`;
    contextLink.className = 'context-icon';
    contextLink.title = 'See in context (29 before + 70 after)';
    contextLink.innerHTML = '<i class="fas fa-recycle"></i>';
    row.querySelector('.transcription-cell').insertBefore(contextLink, row.querySelector('.transcription-text'));
  }

  row.querySelector('.transcription-text').textContent = getTranscriptionText(transcription);
  return row;
}

function processNewTranscription(transcription) {
  if (!transcription || !transcription.id || transcription.id <= lastProcessedId) return;

  const tableBody = document.getElementById('transcriptionTable');
  if (!tableBody) return;

  const newRow = createTranscriptionRow(transcription);
  newRow.classList.add('row-new');
  tableBody.insertBefore(newRow, tableBody.firstChild);
  window.setTimeout(() => newRow.classList.remove('row-new'), 1500);

  lastProcessedId = transcription.id;
  playSelectedTone();
}

function playAudio(url, rowToHighlight = null) {
  if (!url) return;

  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }

  if (currentPlayingRow) {
    currentPlayingRow.classList.remove('is-playing');
    currentPlayingRow = null;
  }

  if (rowToHighlight) {
    rowToHighlight.classList.add('is-playing');
    currentPlayingRow = rowToHighlight;
  }

  currentAudio = new Audio(url);
  currentAudio.play().catch((err) => console.warn('Audio playback failed:', err));
  currentAudio.onended = function () {
    if (currentPlayingRow) currentPlayingRow.classList.remove('is-playing');
    currentPlayingRow = null;
    currentAudio = null;
  };
}

function updateToneLabel() {
  const toneValue = document.getElementById('toneValue');
  if (!toneValue) return;
  const tone = localStorage.getItem('selectedTone') || '';
  toneValue.textContent = `TONE: ${tone ? tone.toUpperCase() : 'NONE'}`;
}

function updateWindowLabel() {
  const windowValue = document.getElementById('windowValue');
  if (!windowValue) return;
  const hours = parseInt(localStorage.getItem('blotterHours') || '2', 10);
  windowValue.textContent = `WINDOW: ${hours}H`;
}

function cycleTone() {
  const current = localStorage.getItem('selectedTone') || '';
  const idx = toneOptions.indexOf(current);
  const next = toneOptions[(idx + 1) % toneOptions.length];
  localStorage.setItem('selectedTone', next);
  updateToneLabel();
  playSelectedTone();
}

function cycleWindow() {
  const current = parseInt(localStorage.getItem('blotterHours') || '2', 10);
  const idx = windowOptions.indexOf(current);
  const next = windowOptions[(idx + 1) % windowOptions.length];
  localStorage.setItem('blotterHours', String(next));
  updateWindowLabel();
  updateTimeRangeDisplay();

  if ($('#blotterSection').is(':visible')) {
    fetchBlotter();
  }
}

function playSelectedTone() {
  const selectedTone = localStorage.getItem('selectedTone') || '';
  if (!selectedTone || !toneUrls[selectedTone]) return;

  const audio = new Audio(toneUrls[selectedTone]);
  audio.play().catch(() => {});
}

function resetBlotterUI() {
  const blotterOutput = document.getElementById('blotterOutput');
  if (!blotterOutput) return;

  blotterOutput.innerHTML = `
    <div class="text-center text-muted">
      <div class="spinner-border spinner-border-sm" role="status">
        <span class="sr-only">Loading blotter...</span>
      </div>
      <p class="mb-0 mt-2">Loading blotter...</p>
    </div>
  `;
}

function updateTimeRangeDisplay() {
  const display = document.getElementById('timeRangeDisplay');
  if (!display) return;

  const hours = parseInt(localStorage.getItem('blotterHours') || '2', 10);
  const now = new Date();
  const past = new Date(now.getTime() - hours * 60 * 60 * 1000);
  const fmt = { hour: '2-digit', minute: '2-digit', hour12: false };

  display.textContent = `RANGE ${past.toLocaleTimeString([], fmt)} - ${now.toLocaleTimeString([], fmt)} (${hours}H)`;
}

function fetchBlotter() {
  if (typeof blotterUrl === 'undefined' || !blotterUrl) return;

  const blotterOutput = document.getElementById('blotterOutput');
  const loadButton = document.getElementById('loadBlotterBtn');
  const refreshButton = document.getElementById('refreshBlotterBtn');
  const blotterSection = document.getElementById('blotterSection');

  if (!blotterOutput || !blotterSection) return;

  if (blotterSection.style.display === 'none') {
    blotterSection.style.display = 'block';
  }

  const hours = parseInt(localStorage.getItem('blotterHours') || '2', 10);
  updateTimeRangeDisplay();

  blotterOutput.innerHTML = `
    <div class="text-center text-muted">
      <div class="spinner-border" role="status">
        <span class="sr-only">Loading blotter...</span>
      </div>
      <p class="mt-2 mb-0">Loading blotter...</p>
    </div>
  `;

  if (loadButton) {
    loadButton.disabled = true;
    loadButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
  }
  if (refreshButton) {
    refreshButton.disabled = true;
    refreshButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
  }

  fetch(`${blotterUrl}?hours=${encodeURIComponent(hours)}`)
    .then((resp) => {
      if (!resp.ok) throw new Error('Network error fetching blotter');
      return resp.text();
    })
    .then((html) => {
      blotterOutput.innerHTML = html;
    })
    .catch((err) => {
      console.error('Failed to load blotter:', err);
      blotterOutput.innerHTML = `
        <div class="text-center text-danger">
          <i class="fas fa-exclamation-triangle"></i>
          <p class="mb-0">Could not load blotter. Please try again.</p>
        </div>
      `;
    })
    .finally(() => {
      if (loadButton) {
        loadButton.disabled = false;
        loadButton.innerHTML = '<i class="fas fa-newspaper"></i>';
      }
      if (refreshButton) {
        refreshButton.disabled = false;
        refreshButton.innerHTML = '<i class="fas fa-sync-alt"></i>';
      }
    });
}

function toggleSearchMode(active) {
  isSearchActive = active;
  const searchIndicator = document.getElementById('searchIndicator');
  if (searchIndicator) searchIndicator.classList.toggle('d-none', !active);

  if (active && eventSource) {
    eventSource.close();
    updateConnectionStatus('search_mode');
  } else if (!active && document.getElementById('transcriptionTable')) {
    setupSSE('search-exit');
  }
}

function toggleSearchPanel(forceOpen = null) {
  const panel = document.querySelector('.search-panel');
  if (!panel) return;

  const shouldOpen = forceOpen === null ? panel.classList.contains('is-collapsed') : forceOpen;
  panel.classList.toggle('is-collapsed', !shouldOpen);
  if (shouldOpen) {
    const input = document.getElementById('searchInput');
    if (input) input.focus();
  }
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
  if (unitData.error) {
    $('#unitLocationsContent').html(`<p class="text-danger">Error: ${unitData.error}</p>`);
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
          <p><small>Last updated ${lastUpdate}</small></p>
        </div>
      </div>
    `;
  }).join('');

  $('#unitLocationsContent').html(`<div class="unit-grid">${cardHtml}</div>`);
}

function fetchUnitLocations() {
  $('#unitLocationsModal').modal('show');
  $('#unitLocationsLoading').show();
  $('#unitLocationsContent').hide();

  fetch('/unit_locations')
    .then((response) => response.json())
    .then((data) => {
      $('#unitLocationsLoading').hide();
      $('#unitLocationsContent').show();
      displayUnitLocations(data);
    })
    .catch((error) => {
      console.error('Error:', error);
      $('#unitLocationsContent').html('<p class="text-danger">An error occurred while fetching unit locations.</p>');
      $('#unitLocationsLoading').hide();
      $('#unitLocationsContent').show();
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

$(document).ready(function () {
  if (!localStorage.getItem('blotterHours')) {
    localStorage.setItem('blotterHours', '2');
  }
  if (!localStorage.getItem('selectedTone')) {
    localStorage.setItem('selectedTone', '');
  }

  updateToneLabel();
  updateWindowLabel();

  const searchInput = $('#searchInput');
  const searchQuery = searchInput.length ? (searchInput.val() || '').trim() : '';
  isSearchActive = searchQuery !== '';
  toggleSearchMode(isSearchActive);

  document.querySelectorAll('#transcriptionTable .transcription-cell[data-timestamp], #transcriptionTable td[data-timestamp]').forEach((cell) => {
    const iso = cell.getAttribute('data-timestamp');
    const small = cell.querySelector('.transcription-meta, small');
    if (small) small.textContent = formatTimestampEastern(iso);
  });

  if ($('#transcriptionTable').length && !isSearchActive) {
    setupSSE('document-ready');
  } else if (isSearchActive) {
    updateConnectionStatus('search_mode');
  } else {
    updateConnectionStatus('failed');
  }

  $('#toggleSearchBtn').on('click', function () {
    toggleSearchPanel();
  });

  $('#loadBlotterBtn').on('click', fetchBlotter);
  $('#refreshBlotterBtn').on('click', fetchBlotter);

  $('#toneCycleBtn').on('click', cycleTone);
  $('#windowCycleBtn').on('click', cycleWindow);

  $('#closeBlotterBtn').on('click', function () {
    const blotterSection = document.getElementById('blotterSection');
    if (blotterSection) blotterSection.style.display = 'none';
    resetBlotterUI();
  });

  updateTimeRangeDisplay();

  $('#unitLocations').on('click', fetchUnitLocations);

  $('#searchForm').on('submit', function (e) {
    e.preventDefault();
    const value = ($('#searchInput').val() || '').trim();
    if (value) {
      toggleSearchMode(true);
      this.submit();
      return;
    }

    toggleSearchMode(false);
    window.location.href = '/';
  });

  $(document).on('click', '.transcription-cell', function (event) {
    if ($(event.target).closest('a').length) return;

    const audioUrl = $(this).data('audio-url');
    if (!audioUrl) return;

    const row = this.closest('tr');
    playAudio(audioUrl, row);
  });

  $(document).on('click', '.edit-btn', function () {
    const id = $(this).data('id');
    if (!id) return;

    const row = $(this).closest('tr');
    const text = row.find('.transcription-text').text();
    let audioUrl = row.find('.transcription-cell').data('audio-url') || null;

    if (!audioUrl) {
      const audioIconElement = row.find('.audio-icon');
      if (audioIconElement.length) {
        const onclickAttr = audioIconElement.attr('onclick');
        if (onclickAttr) {
          const match = onclickAttr.match(/'(.+?)'/);
          audioUrl = match ? match[1] : null;
        }
      }
    }

    editTranscription(id, text, audioUrl);
  });

  $('#saveEdit').on('click', saveEdit);
  $('#playPauseButton').on('click', togglePlayPause);

  $('#editModal').on('hidden.bs.modal', function () {
    $(this).removeData('transcriptionId');
    if (currentAudio) {
      currentAudio.pause();
      currentAudio = null;
    }
    if (currentPlayingRow) {
      currentPlayingRow.classList.remove('is-playing');
      currentPlayingRow = null;
    }

    const btn = $('#playPauseButton');
    btn.find('i').removeClass('fa-pause').addClass('fa-play');
    if (btn.contents().last().length && btn.contents().last()[0].nodeType === Node.TEXT_NODE) {
      btn.contents().last()[0].textContent = ' Play';
    }
  });

  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && !isSearchActive && eventSource && eventSource.readyState === EventSource.OPEN) {
      updateConnectionStatus('connected');
    }
  });
});

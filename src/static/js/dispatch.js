// static/js/dispatch.js
//ok

let currentAudio = null;
let isSearchActive = false;
let lastProcessedId = 0;
let eventSource;
let reconnectAttempts = 0;
const maxReconnectAttempts = 5;
const audioUrl = '/static/tones/Long MP7 ID.mp3';  // Updated path
const SSE_ENABLED = true;

function formatTimestampEastern(isoString) {
    const date = new Date(isoString);
    if (isNaN(date)) {
        return isoString;
    }
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
        if (part.type !== 'literal') {
            lookup[part.type] = part.value;
        }
    }
    return `${lookup.weekday} ${lookup.day}-${lookup.month} ${lookup.hour}:${lookup.minute}:${lookup.second}`;
}

// Replace hardcoded '/streamers' with streamUrl
function setupSSE(calledFrom = 'unknown') {
    if (!SSE_ENABLED) {
        console.log(`SSE disabled - skipping setup (called from: ${calledFrom})`);
        updateConnectionStatus('disabled');
        return;
    }
    if (eventSource) {
        eventSource.close();
    }
    
    console.log(`Setting up SSE connection (called from: ${calledFrom})`);
    eventSource = new EventSource(streamUrl);

    eventSource.onopen = function(event) {
        console.log(`SSE connection opened (called from: ${calledFrom})`);
        updateConnectionStatus('connected');
    };

    eventSource.onmessage = function(event) {
        console.log(`Received SSE message (connection from: ${calledFrom}):`, event.data);
        
        // Simple check to see if the data might be JSON
        if (event.data.startsWith('{') || event.data.startsWith('[')) {
            try {
                const transcription = JSON.parse(event.data);
                processNewTranscription(transcription);
            } catch (e) {
                console.error('Error parsing SSE message as JSON:', e);
            }
        } else {
            console.warn('Received non-JSON message:', event.data);
            // Optionally handle non-JSON messages here or ignore
        }
    };

    eventSource.onerror = function(event) {
        console.error(`SSE error (connection from: ${calledFrom}):`, event);
        if (event.target.readyState === EventSource.CLOSED) {
            console.log(`SSE connection closed (called from: ${calledFrom})`);
            setTimeout(() => setupSSE(`reconnect-${calledFrom}`), 5000);  // Try to reconnect after 5 seconds
        } else if (event.target.readyState === EventSource.CONNECTING) {
            console.log(`SSE connection lost, attempting to reconnect (called from: ${calledFrom})`);
            updateConnectionStatus('disconnected');
        } else {
            console.log(`SSE connection error (called from: ${calledFrom})`);
            updateConnectionStatus('failed');
            setTimeout(() => setupSSE(`error-reconnect-${calledFrom}`), 5000);  // Try to reconnect after 5 seconds
        }
    };
}

function fetchBlotter() {
    console.log('fetchBlotter() called - this should only happen when button is clicked');
    const blotterOutput = document.getElementById('blotterOutput');
    const loadButton = document.getElementById('loadBlotterBtn');
    const refreshButton = document.getElementById('refreshBlotterBtn');
    const blotterSection = document.getElementById('blotterSection');
    
    // Show the blotter section if it's hidden
    if (blotterSection && blotterSection.style.display === 'none') {
        blotterSection.style.display = 'block';
    }
    
    // Show loading state
    blotterOutput.innerHTML = `
      <div class="text-center">
        <div class="spinner-border text-primary" role="status">
          <span class="sr-only">Loading blotter…</span>
        </div>
        <p class="mt-2 mb-0">Loading blotter...</p>
      </div>
    `;
    
    // Disable buttons during loading
    if (loadButton) {
      loadButton.disabled = true;
      loadButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
    }
    if (refreshButton) {
      refreshButton.disabled = true;
      refreshButton.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
    }
    
    fetch(blotterUrl)
      .then(resp => {
        if (!resp.ok) throw new Error('Network error fetching blotter');
        return resp.text();
      })
      .then(html => {
        blotterOutput.innerHTML = html;
      })
      .catch(err => {
        console.error('Failed to load blotter:', err);
        blotterOutput.innerHTML = `
          <div class="text-center text-danger">
            <i class="fas fa-exclamation-triangle"></i>
            <p class="mb-0">Could not load blotter. Please try again.</p>
          </div>
        `;
      })
      .finally(() => {
        // Re-enable buttons after loading
        if (loadButton) {
          loadButton.disabled = false;
          loadButton.innerHTML = '<i class="fas fa-newspaper"></i> Load Blotter';
        }
        if (refreshButton) {
          refreshButton.disabled = false;
          refreshButton.innerHTML = '<i class="fas fa-sync-alt"></i> Refresh';
        }
      });
}

function updateConnectionStatus(status) {
    const statusElement = document.getElementById('sseStatus');
    console.log('updateConnectionStatus called with:', status);
    if (statusElement) {
        console.log('Status element found, updating...');
        switch(status) {
            case 'connected':
                statusElement.innerHTML = '<i class="fas fa-wifi"></i> Live';
                statusElement.className = 'sse-status connected';
                statusElement.title = 'Real-time updates are active';
                break;
            case 'disconnected':
                statusElement.innerHTML = '<i class="fas fa-exclamation-triangle"></i> Reconnecting...';
                statusElement.className = 'sse-status disconnected';
                statusElement.title = 'Connection lost, attempting to reconnect';
                break;
            case 'failed':
                statusElement.innerHTML = '<i class="fas fa-times-circle"></i> Failed';
                statusElement.className = 'sse-status failed';
                statusElement.title = 'Connection failed. Please refresh the page';
                break;
            case 'disabled':
                statusElement.innerHTML = '<i class="fas fa-ban"></i> SSE Off';
                statusElement.className = 'sse-status disabled';
                statusElement.title = 'Real-time updates are temporarily disabled';
                break;
            case 'search_mode':
                statusElement.innerHTML = '<i class="fas fa-search"></i> Search Mode';
                statusElement.className = 'sse-status search-mode';
                statusElement.title = 'Real-time updates paused during search';
                break;
            default:
                statusElement.innerHTML = '<i class="fas fa-question-circle"></i> Unknown';
                statusElement.className = 'sse-status unknown';
                statusElement.title = 'Connection status unknown';
        }
        console.log('Status updated to:', statusElement.innerHTML);
    } else {
        console.error('Status element not found!');
    }
}

let selectedTone = localStorage.getItem('selectedTone') || '';

// Update the toneUrls object
const toneUrls = {
    'Moto': '/static/tones/Moto Talk Permit.mp3',
    'Kenwood': '/static/tones/Kenwwod_Talk_Permit.mp3',
    'TRBO': '/static/tones/TRBO_Normal_TPT.mp3',
    'MP7': '/static/tones/Long MP7 ID.mp3',
    'Moto TPS': '/static/tones/TPS.mp3',
    'MDC-1200': '/static/tones/MDC-1200_DOS.mp3'
};

document.getElementById('toneSelect').value = selectedTone;

document.getElementById('toneSelect').addEventListener('change', function() {
    selectedTone = this.value;
    localStorage.setItem('selectedTone', selectedTone);
    if (selectedTone && toneUrls[selectedTone]) {
        const audio = new Audio(toneUrls[selectedTone]);
        audio.play();}
});

function playSelectedTone() {
    if (selectedTone && toneUrls[selectedTone]) {
        const audio = new Audio(toneUrls[selectedTone]);
        audio.play();
    }
}

let voices = [];

function loadVoices() {
    voices = speechSynthesis.getVoices();
}

speechSynthesis.onvoiceschanged = loadVoices;


document.addEventListener('DOMContentLoaded', function() {
    const searchQuery = $('#searchInput').val().trim();
    isSearchActive = searchQuery !== '';
    toggleSearchMode(isSearchActive);

    document.querySelectorAll('#transcriptionTable td[data-timestamp]').forEach(td => {
        const iso = td.getAttribute('data-timestamp');
        const small = td.querySelector('small');
        if (small) {
            small.textContent = formatTimestampEastern(iso);
        }
    });
});

function processNewTranscription(transcription) {
    if (transcription.id > lastProcessedId) {
        const tableBody = document.getElementById('transcriptionTable');
        const newRow = createTranscriptionRow(transcription);
        tableBody.insertBefore(newRow, tableBody.firstChild);
        lastProcessedId = transcription.id;
        
        // Play the selected tone
        playSelectedTone();
        
        // Read the transcription using TTS
        // readTranscription(transcription.text);
    } else {
        console.log(`Skipping duplicate transcription with id ${transcription.id}`);
    }
}

function readTranscription(text) {
    if ('speechSynthesis' in window) {
        const utterance = new SpeechSynthesisUtterance(text);

        utterance.voice = voices.find(voice => voice.name === 'Google US English') || null;
        utterance.pitch = 1;
        utterance.rate = 1;

        speechSynthesis.speak(utterance);
    } else {
        console.warn('Speech Synthesis not supported in this browser.');
    }
}

// Example of using addTranscriptionUrl
function addNewTranscription(timestamp, url, text) {
    fetch(addTranscriptionUrl, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ timestamp, url, text }),
    })
    .then(response => {
        if (!response.ok) {
            return response.text().then(text => { throw new Error(text) });
        }
        return response.json();
    })
    .then(data => {
        if (data.message) {
            console.log('Transcription added:', data.message);
            // Optionally update the UI
        }
    })
    .catch(error => {
        console.error('Error adding transcription:', error);
    });
}

function createTranscriptionRow(transcription) {
    const newRow = document.createElement('tr');
    newRow.setAttribute('data-id', transcription.id);
    
    // Build left column HTML (audio + context icon)
    let leftColumnHtml = '';
    if (transcription.url) {
        leftColumnHtml += `<span class="audio-icon" onclick="playAudio('${transcription.url}')">▶︎</span>`;
    }
    
    // Add context icon only when there's a search query
    const searchQuery = $('#searchInput').val().trim();
    if (searchQuery) {
        leftColumnHtml += `<br><a href="/transcription_context/${transcription.id}" class="context-icon" title="See in context (29 before + 70 after)">
            <i class="fas fa-recycle"></i>
        </a>`;
    }
    
    // Build actions HTML (edit button only)
    let actionsHtml = '';
    if (userHasAdminRole) {
        actionsHtml = `<button class="btn btn-sm btn-primary edit-btn" data-id="${transcription.id}">Edit</button>`;
    }
    
    const actionsCellHtml = actionsHtml ? `<td class="transcriptionactions">${actionsHtml}</td>` : '';

    newRow.innerHTML = `
        <td>
            ${leftColumnHtml}
        </td>
        <td data-timestamp="${transcription.timestamp}">
            <small>${formatTimestampEastern(transcription.timestamp)}</small><br>
            <span class="transcription-text">${transcription.text}</span>
        </td>
        ${actionsCellHtml}
    `;
    return newRow;
}

function playAudio(url) {
    const audio = new Audio(url);
    audio.play();
}

function editTranscription(id, text, audioUrl) {
    console.log('Opening edit modal for transcription:', id);  // Debug log
    if (!id) {
        console.error('Invalid transcription ID:', id);
        alert('Error: Invalid transcription ID. Please try again.');
        return;
    }
    $('#editModal').data('transcriptionId', id);  // Store ID in modal's data
    $('#editModal').find('#editText').val(text);
    $('#editModal').find('#playPauseButton').data('audio-url', audioUrl);
    $('#editModal').modal('show');
}

function saveEdit() {
    const id = $('#editModal').data('transcriptionId');  // Retrieve ID from modal's data
    const text = $('#editModal').find('#editText').val();
    
    console.log('Attempting to save edit for transcription ID:', id);  // Debug log

    if (!id) {
        console.error('Missing transcription ID');
        alert('Error: Missing transcription ID. Please try again.');
        return;
    }

    fetch(editTranscriptionUrl, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ id, text }),
    })
    .then(response => {
        if (!response.ok) {
            return response.text().then(text => { throw new Error(text) });
        }
        return response.json();
    })
    .then(data => {
        if (data.success) {
            console.log('Edit saved successfully for transcription ID:', id);  // Debug log
            $(`tr[data-id="${id}"] .transcription-text`).text(text);
            $('#editModal').modal('hide');
        } else {
            throw new Error(data.error || 'Unknown error');
        }
    })
    .catch(error => {
        console.error('Error saving edit:', error);
        alert('An error occurred while saving changes: ' + error.message);
    });
}

function togglePlayPause() {
    const audioUrl = $('#playPauseButton').data('audio-url');
    const playPauseIcon = $('#playPauseButton i');

    if (!currentAudio) {
        currentAudio = new Audio(audioUrl);
        currentAudio.play();
        playPauseIcon.removeClass('fa-play').addClass('fa-pause');
        $('#playPauseButton').text(' Pause');
    } else if (currentAudio.paused) {
        currentAudio.play();
        playPauseIcon.removeClass('fa-play').addClass('fa-pause');
        $('#playPauseButton').text(' Pause');
    } else {
        currentAudio.pause();
        playPauseIcon.removeClass('fa-pause').addClass('fa-play');
        $('#playPauseButton').text(' Play');
    }
}

function toggleSearchMode(active) {
    isSearchActive = active;
    const searchIndicator = document.getElementById('searchIndicator');
    if (searchIndicator) {
        searchIndicator.classList.toggle('d-none', !active);
    }
    if (active && eventSource) {
        eventSource.close();
        updateConnectionStatus('search_mode');
    } else if (!active) {
        // If exiting search mode, try to reconnect
        setupSSE('search-exit');
    }
}

function updateTimeRangeDisplay() {
    const hours = parseInt($('#hourSelect').val());
    const now = new Date();
    const pastTime = new Date(now.getTime() - hours * 60 * 60 * 1000);
    $('#timeRangeDisplay').text(`From ${pastTime.toLocaleTimeString()} to ${now.toLocaleTimeString()}`);
}


function fetchUnitLocations() {
    $('#unitLocationsModal').modal('show');
    $('#unitLocationsLoading').show();
    $('#unitLocationsContent').hide();

    fetch('/unit_locations')
        .then(response => response.json())
        .then(data => {
            $('#unitLocationsLoading').hide();
            $('#unitLocationsContent').show();
            displayUnitLocations(data);
        })
        .catch(error => {
            console.error('Error:', error);
            $('#unitLocationsContent').html('<p class="text-danger">An error occurred while fetching unit locations.</p>');
            $('#unitLocationsLoading').hide();
            $('#unitLocationsContent').show();
        });
}

// Improved getTimeDifference: Provides minutes, hours, or days as appropriate
function getTimeDifference(timestamp) {
    const now = new Date();
    const updateTime = new Date(timestamp);
    const diffMs = now - updateTime;
    if (isNaN(updateTime.getTime())) {
        return 'unknown';
    }
    if (diffMs < 1000 * 60) {
        return 'just now';
    }
    const diffMinutes = Math.floor(diffMs / (1000 * 60));
    if (diffMinutes < 60) {
        return `${diffMinutes} min ago`;
    }
    const diffHours = Math.floor(diffMinutes / 60);
    if (diffHours < 24) {
        return `${diffHours} hour${diffHours > 1 ? 's' : ''} ago`;
    }
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays} day${diffDays > 1 ? 's' : ''} ago`;
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

$(document).ready(function() {
    const searchQuery = $('#searchInput').val().trim();
    isSearchActive = searchQuery !== '';
    toggleSearchMode(isSearchActive);

    if ($('#transcriptionTable').length && !isSearchActive) {
        setupSSE('document-ready');
    } else if (isSearchActive) {
        updateConnectionStatus('search_mode');
    } else {
        // Set initial status if not in search mode and no SSE setup
        updateConnectionStatus('unknown');
    }

    // Add event listener for Load Blotter button
    $('#loadBlotterBtn').on('click', function() {
        console.log('Load Blotter button clicked');
        fetchBlotter();
    });

    // Add event listener for Refresh Blotter button
    $('#refreshBlotterBtn').on('click', function() {
        console.log('Refresh Blotter button clicked');
        fetchBlotter();
    });

    $('#unitLocations').on('click', function() {
        fetchUnitLocations();
    });

    // Handle page visibility changes (tab switching)
    document.addEventListener('visibilitychange', function() {
        if (document.hidden) {
            console.log('Page hidden - user switched tabs');
            // Could add a "tab inactive" indicator here if needed
        } else {
            console.log('Page visible - user returned to tab');
            // Check connection status when user returns
            if (!isSearchActive && eventSource && eventSource.readyState === EventSource.OPEN) {
                updateConnectionStatus('connected');
            }
        }
    });
    

    $('#searchForm').on('submit', function(e) {
        e.preventDefault();
        const searchQuery = $('#searchInput').val().trim();
        if (searchQuery !== '') {
            toggleSearchMode(true);
            this.submit();
        }
    });

    $('#resetButton').on('click', function() {
        window.location.href = '/';
    });
    

    $(document).on('click', '.edit-btn', function() {
        const id = $(this).data('id');
        console.log('Edit button clicked for transcription ID:', id);  // Debug log
        if (!id) {
            console.error('Edit button clicked with no transcription ID');
            return;
        }
        const text = $(this).closest('tr').find('.transcription-text').text();
        const audioIconElement = $(this).closest('tr').find('.audio-icon');
        let audioUrl = null;
        if (audioIconElement.length) {
            const onclickAttr = audioIconElement.attr('onclick');
            if (onclickAttr) {
                const match = onclickAttr.match(/'(.+?)'/);
                audioUrl = match ? match[1] : null;
            }
        }
        if (!audioUrl) {
            console.warn('No audio URL found for transcription ID:', id);
        }
        editTranscription(id, text, audioUrl);
    });    

    $('#saveEdit').on('click', saveEdit);

    $(document).on('click', '.refine-btn', function() {
        const id = $(this).data('id');
        refineTranscription(id);
    });

    $('#editModal').on('show.bs.modal', function () {
        console.log('Modal opened with transcription ID:', $(this).data('transcriptionId'));
    });

    $('#editModal').on('hidden.bs.modal', function () {
        $(this).removeData('transcriptionId');
        console.log('Modal closed and transcription ID cleared');
    });

    // Add event listener for play/pause button
    $('#playPauseButton').on('click', togglePlayPause);

    // Stop audio playback when modal is closed
    $('#editModal').on('hidden.bs.modal', function () {
        if (currentAudio) {
            currentAudio.pause();
            currentAudio = null;
        }
        $('#playPauseButton i').removeClass('fa-pause').addClass('fa-play');
        $('#playPauseButton').text(' Play');
    });
});

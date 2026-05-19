/* SmartHelm Dashboard — polling logic */

(function () {
  'use strict';

  // -------------------------------------------------------------------
  // Element refs (grabbed once on load)
  // -------------------------------------------------------------------
  const els = {
    alertBanner:      document.getElementById('alert-banner'),
    eyeBadge:         document.getElementById('eye-state-badge'),
    eyeOverlay:       document.getElementById('eye-state-overlay'),
    confidenceVal:    document.getElementById('confidence-val'),
    earVal:           document.getElementById('ear-val'),
    perclosVal:       document.getElementById('perclos-val'),
    perclosBar:       document.getElementById('perclos-bar-fill'),
    closureVal:       document.getElementById('closure-val'),
    fpsDisplay:       document.getElementById('fps-display'),
    fpsVal:           document.getElementById('fps-val'),
    latencyVal:       document.getElementById('latency-val'),
    faceVal:          document.getElementById('face-val'),
    modeVal:          document.getElementById('mode-val'),
    modeBadge:        document.getElementById('detection-mode'),
    connectionDot:    document.getElementById('connection-dot'),
    eventList:        document.getElementById('event-list'),
    eventCount:       document.getElementById('event-count'),
  };

  let lastEventCount = 0;
  let connected = false;

  // -------------------------------------------------------------------
  // Status polling — every 500ms
  // -------------------------------------------------------------------
  function pollStatus() {
    fetch('/api/status')
      .then(r => {
        if (!r.ok) throw new Error('bad response');
        return r.json();
      })
      .then(data => {
        setConnected(true);
        updateStatus(data);
      })
      .catch(() => {
        setConnected(false);
      });
  }

  function updateStatus(d) {
    // Eye state
    const state = (d.eye_state || 'UNKNOWN').toLowerCase();
    setEyeState(state, d.eye_state, d.confidence, d.ear_smoothed);

    // PERCLOS
    const pc = parseFloat(d.perclos) || 0;
    els.perclosVal.textContent = pc.toFixed(1);
    const pct = Math.min(pc, 100);
    els.perclosBar.style.width = pct + '%';
    els.perclosBar.className = 'perclos-bar-fill';
    if (pc >= 30) els.perclosBar.classList.add('danger');
    else if (pc >= 20) els.perclosBar.classList.add('warning');

    // Continuous closure
    els.closureVal.textContent = (parseFloat(d.continuous_closure_sec) || 0).toFixed(1);

    // Performance
    const fps = parseFloat(d.fps) || 0;
    els.fpsDisplay.textContent = fps.toFixed(1) + ' FPS';
    els.fpsVal.textContent = fps.toFixed(1);
    els.latencyVal.textContent = (parseFloat(d.latency_ms) || 0).toFixed(0) + ' ms';
    els.faceVal.textContent = d.face_detected ? 'Yes' : 'No';
    els.modeVal.textContent = d.detection_mode || '--';
    if (els.modeBadge) els.modeBadge.textContent = d.detection_mode || '--';

    // Alert
    setAlert(d.alert);
  }

  // -------------------------------------------------------------------
  // Eye state helpers
  // -------------------------------------------------------------------
  function setEyeState(stateLower, stateLabel, confidence, ear) {
    // Badge
    els.eyeBadge.className = 'badge ' + stateLower;
    els.eyeBadge.textContent = stateLabel || 'UNKNOWN';

    // Overlay on video
    els.eyeOverlay.className = 'eye-state-overlay ' + stateLower;
    els.eyeOverlay.textContent = stateLabel || 'UNKNOWN';

    // Sub-info
    els.confidenceVal.textContent = ((parseFloat(confidence) || 0) * 100).toFixed(0) + '%';
    els.earVal.textContent = (parseFloat(ear) || 0).toFixed(3);
  }

  // -------------------------------------------------------------------
  // Alert helpers
  // -------------------------------------------------------------------
  function setAlert(active) {
    if (active) {
      els.alertBanner.classList.remove('hidden');
      document.body.classList.add('alert-active');
    } else {
      els.alertBanner.classList.add('hidden');
      document.body.classList.remove('alert-active');
    }
  }

  // -------------------------------------------------------------------
  // Connection indicator
  // -------------------------------------------------------------------
  function setConnected(ok) {
    if (ok === connected) return;
    connected = ok;
    els.connectionDot.className = 'dot ' + (ok ? 'dot-green' : 'dot-red');
    els.connectionDot.title = ok ? 'Connected' : 'Disconnected';
  }

  // -------------------------------------------------------------------
  // Event history polling — every 3 seconds
  // -------------------------------------------------------------------
  function pollEvents() {
    fetch('/api/events')
      .then(r => r.json())
      .then(events => {
        if (!Array.isArray(events)) return;
        renderEvents(events);
      })
      .catch(() => {});
  }

  function renderEvents(events) {
    if (events.length === 0) {
      els.eventList.innerHTML = '<tr><td colspan="4" class="no-events">No alerts yet</td></tr>';
      els.eventCount.textContent = '0 events';
      return;
    }

    // Most recent first
    const sorted = [...events].reverse();
    els.eventCount.textContent = sorted.length + ' event' + (sorted.length !== 1 ? 's' : '');
    els.eventList.innerHTML = sorted.map(e => {
      const t = new Date(e.ts * 1000).toLocaleTimeString();
      const type = (e.type || 'ALERT').toUpperCase();
      const typeClass = 'event-type-' + type.toLowerCase().replace('_', '-');
      const perclos = (parseFloat(e.perclos) || 0).toFixed(1);
      const dur = (parseFloat(e.continuous_sec) || 0).toFixed(1);
      return `<tr>
        <td>${t}</td>
        <td class="${typeClass}">${type}</td>
        <td>${perclos}%</td>
        <td>${dur}s</td>
      </tr>`;
    }).join('');
  }

  // -------------------------------------------------------------------
  // Start polling
  // -------------------------------------------------------------------
  pollStatus();
  pollEvents();
  setInterval(pollStatus, 500);
  setInterval(pollEvents, 3000);

})();

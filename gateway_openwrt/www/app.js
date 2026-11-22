/* Mawson MX921 RS-232-over-LoRa Bridge — Lightweight Control Bar Application Logic */

(function () {
  'use strict';

  // API Configuration
  const API_URL = '/cgi-bin/mx921_api';
  const POLL_INTERVAL_MS = 1500;

  // DOM Elements
  const elGatewayUtc = document.getElementById('txtGatewayUtc');
  const elTcpPort = document.getElementById('txtTcpPort');
  const elSelNode = document.getElementById('selNode');
  const elBadgeLiveState = document.getElementById('badgeLiveState');
  const elTxtLastContact = document.getElementById('txtLastContact');
  const elBadgeCurrentSf = document.getElementById('badgeCurrentSf');
  const elValRssi = document.getElementById('valRssi');
  const elValSnr = document.getElementById('valSnr');
  const elSignalBarFill = document.getElementById('signalBarFill');

  const elBtnToggleStayAwake = document.getElementById('btnToggleStayAwake');
  const elIconStayAwake = document.getElementById('iconStayAwake');
  const elTxtStayAwake = document.getElementById('txtStayAwake');
  const elTxtLockDesc = document.getElementById('txtLockDesc');

  const elBtnSf9 = document.getElementById('btnSf9');
  const elBtnSf11 = document.getElementById('btnSf11');
  const elTxtSfDesc = document.getElementById('txtSfDesc');

  const elInpWakeHour = document.getElementById('inpWakeHour');
  const elInpDuration = document.getElementById('inpDuration');
  const elInpHoldoff = document.getElementById('inpHoldoff');
  const elBtnSaveSchedule = document.getElementById('btnSaveSchedule');
  const elMsgScheduleSaved = document.getElementById('msgScheduleSaved');

  const elValVbat = document.getElementById('valVbat');
  const elValTemp = document.getElementById('valTemp');
  const elValVext = document.getElementById('valVext');
  const elValFallback = document.getElementById('valFallback');

  const elTxtPacketCount = document.getElementById('txtPacketCount');
  const elTblPacketLog = document.getElementById('tblPacketLog');

  // Application State
  let currentNodeId = 1;
  let currentLockState = false;
  let currentSf = 9;
  let scheduleFormTouched = false;
  let isRequestPending = false;

  // Setup Dynamic Gateway IP in code examples
  function setupGatewayIp() {
    const host = window.location.hostname || '10.130.1.1';
    document.querySelectorAll('.gw-ip-txt').forEach(function (span) {
      span.textContent = host;
    });
  }

  // Calculate RSSI Signal Bar percentage (-120 dBm = 0%, -55 dBm = 100%)
  function calcSignalPercent(rssi) {
    if (!rssi || rssi <= -120) return 5;
    if (rssi >= -55) return 100;
    return Math.round(((rssi + 120) / 65) * 100);
  }

  // Fetch Gateway & Node State
  async function fetchState() {
    try {
      const response = await fetch(API_URL + '?t=' + Date.now());
      if (!response.ok) return;
      const data = await response.json();
      renderState(data);
    } catch (err) {
      console.warn('Network sync issue:', err);
    }
  }

  // Render Data into UI
  function renderState(data) {
    if (!data) return;

    // 1. Gateway Status
    if (data.gateway) {
      if (data.gateway.utc_time) {
        elGatewayUtc.textContent = data.gateway.utc_time;
      }
      if (data.gateway.tcp_port) {
        elTcpPort.textContent = 'PORT ' + data.gateway.tcp_port;
      }
      if (data.gateway.current_sf) {
        currentSf = data.gateway.current_sf;
        updateSfButtons(currentSf);
      }
    }

    // 2. Active Node Details
    const node = (data.nodes && data.nodes[String(currentNodeId)]) || {};

    // Live State Badge
    currentLockState = !!node.maintenance_lock;
    updateStayAwakeButton(currentLockState);

    if (node.maintenance_lock) {
      elBadgeLiveState.textContent = 'MAINTENANCE LOCK';
      elBadgeLiveState.className = 'state-badge badge-lock';
      elTxtLockDesc.textContent = 'Node locked awake 24/7';
    } else if (node.activity_hold) {
      const remSec = node.holdoff_rem_sec || 0;
      const remM = Math.floor(remSec / 60);
      const remS = remSec % 60;
      const timeStr = String(remM).padStart(2, '0') + ':' + String(remS).padStart(2, '0');
      elBadgeLiveState.textContent = 'AWAKE - IDLE HOLD (' + timeStr + ')';
      elBadgeLiveState.className = 'state-badge badge-idle-hold';
      elTxtLockDesc.textContent = 'Watchdog active (resets on traffic)';
    } else if (node.scheduled_window) {
      elBadgeLiveState.textContent = 'AWAKE - SCHEDULED';
      elBadgeLiveState.className = 'state-badge badge-scheduled';
      elTxtLockDesc.textContent = 'Inside scheduled daily window';
    } else {
      const wakeHr = (node.schedule_config && node.schedule_config.wake_hour_utc) || 0;
      elBadgeLiveState.textContent = 'SLEEPING - NEXT WAKE ' + String(wakeHr).padStart(2, '0') + ':00';
      elBadgeLiveState.className = 'state-badge badge-sleeping';
      elTxtLockDesc.textContent = 'Deep sleep (~1 mA) outside window';
    }

    // Last contact
    if (node.last_seen_epoch && node.last_seen_epoch > 0) {
      const secAgo = node.seconds_ago !== undefined ? node.seconds_ago : -1;
      if (secAgo >= 0) {
        elTxtLastContact.textContent = 'Last contact: ' + secAgo + 's ago';
      } else {
        elTxtLastContact.textContent = 'Last contact: ' + (node.last_seen_str || '--');
      }
    } else {
      elTxtLastContact.textContent = 'Last contact: No packets yet';
    }

    // RF Metrics
    const rssi = node.link ? node.link.rssi_dbm : -65.0;
    const snr = node.link ? node.link.snr_db : 0.0;
    elValRssi.innerHTML = rssi.toFixed(1) + ' <small>dBm</small>';
    elValSnr.textContent = 'SNR: ' + (snr >= 0 ? '+' : '') + snr.toFixed(1) + ' dB';
    elSignalBarFill.style.width = calcSignalPercent(rssi) + '%';

    // Telemetry
    if (node.telemetry) {
      const vbat = node.telemetry.vbat_mv || 5000;
      elValVbat.innerHTML = (vbat / 1000.0).toFixed(1) + ' <small>V (USB-C)</small>';
      const temp = node.telemetry.temp_c || 21.5;
      elValTemp.innerHTML = temp.toFixed(1) + ' <small>°C</small>';
    }

    // Switched Vext & Dead-Man Fallback status
    if (node.vext_enabled !== undefined) {
      elValVext.textContent = node.vext_enabled ? '3.3V ON' : 'POWER OFF';
      elValVext.className = node.vext_enabled ? 'telem-val text-success' : 'telem-val text-muted';
    }

    if (node.sf_fallback_active) {
      elValFallback.textContent = 'ACTIVE (60s)';
      elValFallback.className = 'telem-val text-warning';
    } else {
      elValFallback.textContent = 'Inactive';
      elValFallback.className = 'telem-val text-muted';
    }

    // Schedule Form (only update if user is not actively editing it)
    if (!scheduleFormTouched && node.schedule_config) {
      elInpWakeHour.value = node.schedule_config.wake_hour_utc !== undefined ? node.schedule_config.wake_hour_utc : 0;
      elInpDuration.value = node.schedule_config.duration_min || 60;
      elInpHoldoff.value = node.schedule_config.holdoff_min || 10;
    }

    // Packet Log Table
    if (data.packet_log && Array.isArray(data.packet_log)) {
      renderPacketLog(data.packet_log);
    }
  }

  // Render Packet Log Rows
  function renderPacketLog(packets) {
    elTxtPacketCount.textContent = packets.length + ' Packets';
    if (packets.length === 0) return;

    let html = '';
    for (let i = 0; i < Math.min(packets.length, 15); i++) {
      const p = packets[i];
      const dirClass = (p.dir || 'RX').toLowerCase();
      const rssiStr = p.rssi !== undefined ? p.rssi.toFixed(1) + ' dBm' : '--';
      const snrStr = p.snr !== undefined ? (p.snr >= 0 ? '+' : '') + p.snr.toFixed(1) + ' dB' : '--';

      html += '<tr>' +
        '<td>' + (p.time || '--') + '</td>' +
        '<td><span class="badge-dir ' + dirClass + '">' + (p.dir || 'RX') + '</span></td>' +
        '<td>SF' + (p.sf || 9) + '</td>' +
        '<td>' + rssiStr + '</td>' +
        '<td>' + snrStr + '</td>' +
        '<td>' + escapeHtml(p.summary || '') + '</td>' +
        '<td class="text-muted" title="' + (p.hex || '') + '">' + escapeHtml(p.hex || '') + '</td>' +
        '</tr>';
    }
    elTblPacketLog.innerHTML = html;
  }

  function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // Update Visuals for Stay Awake Toggle
  function updateStayAwakeButton(isLocked) {
    if (isLocked) {
      elBtnToggleStayAwake.className = 'btn-toggle btn-lock-on';
      elIconStayAwake.textContent = '🔒';
      elTxtStayAwake.textContent = 'STAY AWAKE: ON';
    } else {
      elBtnToggleStayAwake.className = 'btn-toggle btn-lock-off';
      elIconStayAwake.textContent = '🔓';
      elTxtStayAwake.textContent = 'STAY AWAKE: OFF';
    }
  }

  // Update Visuals for SF Buttons
  function updateSfButtons(sf) {
    if (sf === 11) {
      elBtnSf9.classList.remove('active');
      elBtnSf11.classList.add('active');
      elBadgeCurrentSf.textContent = 'SF11 STORM';
      elBadgeCurrentSf.className = 'sf-pill sf-11';
    } else {
      elBtnSf9.classList.add('active');
      elBtnSf11.classList.remove('active');
      elBadgeCurrentSf.textContent = 'SF9 NORMAL';
      elBadgeCurrentSf.className = 'sf-pill sf-9';
    }
  }

  // POST Action Helper
  async function postAction(action, payload) {
    if (isRequestPending) return;
    isRequestPending = true;

    try {
      const body = Object.assign({ action: action, node_id: currentNodeId }, payload || {});
      const res = await fetch(API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const data = await res.json();
      setTimeout(fetchState, 300);
      return data;
    } catch (err) {
      console.error('API Error:', err);
    } finally {
      isRequestPending = false;
    }
  }

  // Event Handlers
  elSelNode.addEventListener('change', function () {
    currentNodeId = parseInt(this.value, 10);
    postAction('select_node', {});
    fetchState();
  });

  elBtnToggleStayAwake.addEventListener('click', async function () {
    const nextState = !currentLockState;
    updateStayAwakeButton(nextState);
    await postAction('set_maintenance_lock', { enabled: nextState });
  });

  elBtnSf9.addEventListener('click', async function () {
    if (currentSf === 9) return;
    updateSfButtons(9);
    await postAction('set_sf', { sf: 9 });
  });

  elBtnSf11.addEventListener('click', async function () {
    if (currentSf === 11) return;
    updateSfButtons(11);
    await postAction('set_sf', { sf: 11 });
  });

  [elInpWakeHour, elInpDuration, elInpHoldoff].forEach(function (inp) {
    inp.addEventListener('input', function () {
      scheduleFormTouched = true;
    });
  });

  elBtnSaveSchedule.addEventListener('click', async function () {
    const hh = parseInt(elInpWakeHour.value, 10) || 0;
    const dur = parseInt(elInpDuration.value, 10) || 60;
    const hold = parseInt(elInpHoldoff.value, 10) || 10;

    await postAction('set_schedule', {
      wake_hour_utc: hh,
      duration_min: dur,
      holdoff_min: hold
    });

    scheduleFormTouched = false;
    elMsgScheduleSaved.textContent = '✓ Schedule saved! Wake at ' + String(hh).padStart(2, '0') + ':00 UTC for ' + dur + 'm.';
    elMsgScheduleSaved.style.display = 'block';
    setTimeout(function () {
      elMsgScheduleSaved.style.display = 'none';
    }, 4000);
  });

  // =========================================================================
  // Spectra MX921 Interactive Diagnostics & Control Hub Handlers
  // =========================================================================
  const elMxStatusPill = document.getElementById('mxStatusPill');
  const elMxProgressBarContainer = document.getElementById('mxProgressBarContainer');
  const elMxProgressText = document.getElementById('mxProgressText');
  const elMxProgressTimer = document.getElementById('mxProgressTimer');
  const elMxProgressBar = document.getElementById('mxProgressBar');

  const elBtnMxIdentity = document.getElementById('btnMxIdentity');
  const elBtnMxChannel1 = document.getElementById('btnMxChannel1');
  const elBtnMxConfig = document.getElementById('btnMxConfig');
  const elBtnMxAlignment = document.getElementById('btnMxAlignment');
  const elBtnMxResetMenu = document.getElementById('btnMxResetMenu');

  const elLblMxModel = document.getElementById('lblMxModel');
  const elLblMxSerial = document.getElementById('lblMxSerial');
  const elLblMxVersion = document.getElementById('lblMxVersion');

  const elLblTxFreq = document.getElementById('lblTxFreq');
  const elLblRxFreq = document.getElementById('lblRxFreq');
  const elLblTxCtcss = document.getElementById('lblTxCtcss');
  const elLblRxCtcss = document.getElementById('lblRxCtcss');
  const elLblTxPower = document.getElementById('lblTxPower');
  const elLblDuplexBw = document.getElementById('lblDuplexBw');

  const elTxtTerminalOut = document.getElementById('txtTerminalOut');
  const elInpMxCustomCmd = document.getElementById('inpMxCustomCmd');
  const elBtnSendCustomCmd = document.getElementById('btnSendCustomCmd');

  const elBtnSendEsc = document.getElementById('btnSendEsc');
  const elBtnSendEnter = document.getElementById('btnSendEnter');
  const elBtnClearTerm = document.getElementById('btnClearTerm');

  let timerInterval = null;
  let timerStart = 0;

  function setMxStatus(state, msg, timerText) {
    if (!elMxStatusPill) return;
    elMxStatusPill.className = 'mx-status-pill';
    if (state === 'busy') {
      elMxStatusPill.classList.add('status-busy');
      elMxStatusPill.textContent = '🟡 Transmitting...';
    } else if (state === 'waiting') {
      elMxStatusPill.classList.add('status-waiting');
      elMxStatusPill.textContent = '🔵 Waiting Reply...';
    } else if (state === 'ready') {
      elMxStatusPill.classList.add('status-ready');
      elMxStatusPill.textContent = '🟢 Ready';
    } else if (state === 'error') {
      elMxStatusPill.classList.add('status-error');
      elMxStatusPill.textContent = '🔴 ' + (msg || 'Error');
    }

    if (elMxProgressBarContainer) {
      if (state === 'busy' || state === 'waiting') {
        elMxProgressBarContainer.style.display = 'block';
        elMxProgressText.textContent = msg;
        if (!timerInterval) {
          timerStart = Date.now();
          timerInterval = setInterval(function () {
            const ms = Date.now() - timerStart;
            elMxProgressTimer.textContent = ms + ' ms';
          }, 50);
        }
      } else {
        if (timerInterval) {
          clearInterval(timerInterval);
          timerInterval = null;
        }
        if (state === 'ready') {
          elMxProgressText.textContent = msg || 'Done';
          if (timerText) elMxProgressTimer.textContent = timerText;
          setTimeout(function () {
            if (!timerInterval) elMxProgressBarContainer.style.display = 'none';
          }, 3500);
        } else {
          elMxProgressBarContainer.style.display = 'none';
        }
      }
    }
  }

  function appendTerminal(prefix, text) {
    if (!elTxtTerminalOut) return;
    const timeStr = new Date().toTimeString().split(' ')[0];
    const line = '\n[' + timeStr + '] ' + (prefix ? prefix + ' ' : '') + text;
    elTxtTerminalOut.textContent += line;
    elTxtTerminalOut.scrollTop = elTxtTerminalOut.scrollHeight;
  }

  function applyParsedData(parsed) {
    if (!parsed) return;
    if (parsed.model_number && elLblMxModel) elLblMxModel.textContent = parsed.model_number;
    if (parsed.serial_number && elLblMxSerial) elLblMxSerial.textContent = parsed.serial_number;
    if (parsed.firmware_version && elLblMxVersion) elLblMxVersion.textContent = 'v' + parsed.firmware_version;

    if (parsed.tx_freq_mhz && elLblTxFreq) elLblTxFreq.innerHTML = parsed.tx_freq_mhz + ' <small>MHz</small>';
    if (parsed.rx_freq_mhz && elLblRxFreq) elLblRxFreq.innerHTML = parsed.rx_freq_mhz + ' <small>MHz</small>';
    if (parsed.tx_ctcss_hz && elLblTxCtcss) elLblTxCtcss.innerHTML = parsed.tx_ctcss_hz + ' <small>Hz</small>';
    if (parsed.rx_ctcss_hz && elLblRxCtcss) elLblRxCtcss.innerHTML = parsed.rx_ctcss_hz + ' <small>Hz</small>';
    if (parsed.tx_power_w && elLblTxPower) elLblTxPower.innerHTML = parsed.tx_power_w + ' <small>W</small>';
    if ((parsed.duplex || parsed.bandwidth) && elLblDuplexBw) {
      elLblDuplexBw.textContent = (parsed.duplex || 'Simplex') + ' / ' + (parsed.bandwidth || 'WIDE');
    }
  }

  async function executeAutoQuery(queryType, params, desc) {
    setMxStatus('busy', 'Transmitting ' + desc + ' over LoRa RF (916.8 MHz)...');
    appendTerminal('TX ➔', 'Running query: ' + desc + '...');

    setTimeout(function () {
      if (timerInterval) setMxStatus('waiting', 'Awaiting MX921 response over air (RTT ~500ms)...');
    }, 400);

    try {
      const payload = Object.assign({ action: 'mx921_auto_query', query: queryType, node_id: currentNodeId }, params || {});
      const res = await fetch(API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data && data.success) {
        setMxStatus('ready', '✓ Received reply (' + (data.output ? data.output.length : 0) + ' bytes)', data.elapsed_ms + ' ms');
        appendTerminal('RX 🠔', data.output || '(No response text)');
        applyParsedData(data.parsed);
      } else {
        setMxStatus('error', data.error || 'Failed');
        appendTerminal('ERR', data.error || 'Failed query');
      }
    } catch (err) {
      setMxStatus('error', 'Network error');
      appendTerminal('ERR', String(err));
    }
  }

  async function executeCustomCmd(cmdStr) {
    if (!cmdStr) return;
    setMxStatus('busy', 'Transmitting command [' + cmdStr + '] over LoRa (916.8 MHz)...');
    appendTerminal('TX ➔', cmdStr);

    setTimeout(function () {
      if (timerInterval) setMxStatus('waiting', 'Waiting for MX921 reply...');
    }, 400);

    try {
      const payload = { action: 'mx921_cmd', cmd: cmdStr, node_id: currentNodeId };
      const res = await fetch(API_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data && data.success) {
        setMxStatus('ready', '✓ Reply received (' + (data.output ? data.output.length : 0) + ' bytes)', data.elapsed_ms + ' ms');
        appendTerminal('RX 🠔', data.output || '(No response text)');
        applyParsedData(data.parsed);
      } else {
        setMxStatus('error', data.error || 'Failed');
        appendTerminal('ERR', data.error || 'Failed');
      }
    } catch (err) {
      setMxStatus('error', 'Network error');
      appendTerminal('ERR', String(err));
    }
  }

  // Setup Button Listeners
  if (elBtnMxIdentity) {
    elBtnMxIdentity.addEventListener('click', function () {
      executeAutoQuery('identity', {}, 'Read Identity');
    });
  }

  if (elBtnMxChannel1) {
    elBtnMxChannel1.addEventListener('click', function () {
      executeAutoQuery('channel', { channel: 1 }, 'Read Channel 1');
    });
  }

  if (elBtnMxConfig) {
    elBtnMxConfig.addEventListener('click', function () {
      executeAutoQuery('config', {}, 'Configuration Menu');
    });
  }

  if (elBtnMxAlignment) {
    elBtnMxAlignment.addEventListener('click', function () {
      executeAutoQuery('alignment', {}, 'Alignment Menu');
    });
  }

  if (elBtnMxResetMenu) {
    elBtnMxResetMenu.addEventListener('click', function () {
      executeAutoQuery('reset_menu', {}, 'Reset Menu (RMENU)');
    });
  }

  if (elBtnSendCustomCmd && elInpMxCustomCmd) {
    elBtnSendCustomCmd.addEventListener('click', function () {
      const val = elInpMxCustomCmd.value.trim();
      if (val) {
        executeCustomCmd(val);
        elInpMxCustomCmd.value = '';
      }
    });

    elInpMxCustomCmd.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        const val = elInpMxCustomCmd.value.trim();
        if (val) {
          executeCustomCmd(val);
          elInpMxCustomCmd.value = '';
        }
      }
    });
  }

  if (elBtnSendEsc) {
    elBtnSendEsc.addEventListener('click', function () {
      executeCustomCmd('ESC');
    });
  }

  if (elBtnSendEnter) {
    elBtnSendEnter.addEventListener('click', function () {
      executeCustomCmd('');
    });
  }

  if (elBtnClearTerm && elTxtTerminalOut) {
    elBtnClearTerm.addEventListener('click', function () {
      elTxtTerminalOut.textContent = 'Console cleared. Ready for next command.';
    });
  }

  // Initialization
  setupGatewayIp();
  fetchState();
  setInterval(fetchState, POLL_INTERVAL_MS);

})();

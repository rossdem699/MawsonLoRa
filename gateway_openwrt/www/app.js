/**
 * Mawson Station MX921 LoRa Repeater Bridge — Web Dashboard Application
 * Handles tab navigation, real-time polling, control dispatches, and RMENU terminal.
 */

document.addEventListener('DOMContentLoaded', () => {
  const API_ENDPOINT = '/cgi-bin/mx921_api';
  let currentNodeId = "1";
  let currentFilter = "ALL";
  let lastState = null;

  // -------------------------------------------------------------
  // 1. WAI-ARIA Tab Navigation (Follows Modern Web Guidance)
  // -------------------------------------------------------------
  const tabButtons = document.querySelectorAll('[role="tab"]');
  const tabPanels = document.querySelectorAll('[role="tabpanel"]');

  function selectTab(selectedTab) {
    tabButtons.forEach(tab => {
      const isSelected = (tab === selectedTab);
      tab.setAttribute('aria-selected', isSelected ? 'true' : 'false');
      tab.classList.toggle('active', isSelected);
    });

    const targetPanelId = selectedTab.id.replace('tab-', 'panel-');
    tabPanels.forEach(panel => {
      if (panel.id === targetPanelId) {
        panel.removeAttribute('hidden');
        panel.classList.add('active');
      } else {
        panel.setAttribute('hidden', '');
        panel.classList.remove('active');
      }
    });
  }

  tabButtons.forEach(tab => {
    tab.addEventListener('click', () => selectTab(tab));

    // Keyboard Arrow navigation for tablist
    tab.addEventListener('keydown', (e) => {
      const tabs = Array.from(tabButtons);
      const index = tabs.indexOf(tab);
      let newIndex = index;

      if (e.key === 'ArrowRight') {
        newIndex = (index + 1) % tabs.length;
      } else if (e.key === 'ArrowLeft') {
        newIndex = (index - 1 + tabs.length) % tabs.length;
      }

      if (newIndex !== index) {
        e.preventDefault();
        tabs[newIndex].focus();
        selectTab(tabs[newIndex]);
      }
    });
  });

  // -------------------------------------------------------------
  // 2. Node Selection & Live Polling
  // -------------------------------------------------------------
  const nodeSelect = document.getElementById('nodeSelect');
  nodeSelect.addEventListener('change', (e) => {
    currentNodeId = e.target.value;
    updateUi();
  });

  async function fetchState() {
    try {
      const resp = await fetch(API_ENDPOINT, { cache: 'no-store' });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      lastState = data;
      updateUi();
    } catch (err) {
      console.warn("Polling error:", err);
      document.getElementById('gatewayUtc').textContent = "Gateway: Reconnecting...";
    }
  }

  // -------------------------------------------------------------
  // 3. UI Update Engine
  // -------------------------------------------------------------
  function updateUi() {
    if (!lastState) return;

    // Gateway Info
    if (lastState.gateway) {
      document.getElementById('gatewayUtc').textContent = lastState.gateway.utc_time || "UTC";
    }

    const node = lastState.nodes ? lastState.nodes[currentNodeId] : null;
    if (!node) return;

    // Power / Sleep State
    const badgePower = document.getElementById('badgePowerState');
    badgePower.textContent = node.power_state_desc || "UNKNOWN";
    badgePower.className = 'badge-status';

    if (node.maintenance_lock) {
      badgePower.classList.add('badge-warning');
    } else if (node.scheduled_window || node.activity_hold) {
      badgePower.classList.add('badge-success');
    } else {
      badgePower.classList.add('badge-danger');
    }

    // Last seen
    const txtLastSeen = document.getElementById('txtLastSeen');
    if (node.seconds_ago >= 0) {
      txtLastSeen.textContent = `Last contact: ${node.seconds_ago}s ago (${node.last_seen_str.split(' ')[1] || ''})`;
    } else {
      txtLastSeen.textContent = "Last contact: Never (Node offline)";
    }

    // LoRa Link Metrics
    const rssi = node.link ? node.link.rssi_dbm : -120;
    const snr = node.link ? node.link.snr_db : 0;
    document.getElementById('valRssi').innerHTML = `${rssi} <small>dBm</small>`;
    document.getElementById('valSnr').textContent = `SNR: ${snr > 0 ? '+' : ''}${snr.toFixed(1)} dB`;

    // Map RSSI (-120 dBm to -40 dBm) to 0-100%
    const rssiPct = Math.min(100, Math.max(5, ((rssi + 120) / 80) * 100));
    document.getElementById('rssiBarFill').style.width = `${rssiPct}%`;

    // Active Channel & Repeater State
    const activeCh = node.telemetry ? node.telemetry.active_channel : 1;
    document.getElementById('valActiveChannel').textContent = `CH ${activeCh}`;
    
    // Broadcast Beacon Message Banner
    const bannerBeacon = document.getElementById('bannerBeacon');
    const txtBeaconMsg = document.getElementById('txtBeaconMsg');
    if (bannerBeacon && txtBeaconMsg) {
      if (node.beacon_msg) {
        bannerBeacon.style.display = 'flex';
        txtBeaconMsg.textContent = `"${node.beacon_msg}"`;
      } else {
        bannerBeacon.style.display = 'none';
      }
    }
    
    const badgeKnockdown = document.getElementById('badgeKnockdown');
    if (node.repeater_inhibited) {
      badgeKnockdown.textContent = "KNOCKED DOWN";
      badgeKnockdown.className = "badge-status badge-danger";
    } else {
      badgeKnockdown.textContent = "REPEATER ON";
      badgeKnockdown.className = "badge-status badge-success";
    }

    if (node.telemetry) {
      document.getElementById('txtChannelFreq').textContent = 
        `RX: ${node.telemetry.rx_freq_mhz || 149.825} MHz / TX: ${node.telemetry.tx_freq_mhz || 149.225} MHz`;
      
      const vbatV = ((node.telemetry.vbat_mv || 5000) / 1000).toFixed(1);
      document.getElementById('valVbat').innerHTML = `${vbatV} <small>V</small>`;
      document.getElementById('valTemp').innerHTML = `${node.telemetry.temp_c || 20.0} <small>°C</small>`;

      // Analog RSSI Meter Dial (-120 dBm to -50 dBm -> -60 deg to +60 deg)
      const analogDbm = node.telemetry.analog_rssi_dbm || -120.0;
      document.getElementById('valAnalogRssi').textContent = analogDbm.toFixed(1);
      const dialDeg = -60 + Math.min(120, Math.max(0, ((analogDbm + 120) / 70) * 120));
      document.getElementById('meterNeedle').style.transform = `rotate(${dialDeg}deg)`;
    }

    // Alarms
    const alarms = node.alarms || {};
    updateAlarmItem('alarmVswr', alarms.vswr_fault, 'FAULT', 'NOMINAL');
    updateAlarmItem('alarmFwdPower', alarms.low_fwd_power, 'LOW PWR', 'NOMINAL');
    updateAlarmItem('alarmTemp', alarms.pa_overtemp, 'OVERTEMP', 'NOMINAL');
    updateAlarmItem('alarmVoltage', alarms.vbat_mv < 11000 && alarms.vbat_mv > 0, 'LOW VOLT', 'NOMINAL');
    updateAlarmItem('alarmPll', alarms.pll_unlock, 'UNLOCKED', 'LOCKED');
    updateAlarmItem('alarmCor', alarms.cor_active, 'ACTIVE (RX)', 'IDLE');

    // Overall alarm summary badge
    const hasFault = alarms.vswr_fault || alarms.low_fwd_power || alarms.pa_overtemp || alarms.pll_unlock;
    const badgeOverall = document.getElementById('badgeOverallAlarm');
    if (hasFault) {
      badgeOverall.textContent = "ALARM ACTIVE";
      badgeOverall.className = "badge-status badge-danger";
    } else {
      badgeOverall.textContent = "ALL SYSTEMS NOMINAL";
      badgeOverall.className = "badge-status badge-success";
    }

    // Sync Tab 2 Maintenance Lock toggle without causing re-trigger
    const chkMaintLock = document.getElementById('chkMaintLock');
    if (chkMaintLock) {
      chkMaintLock.checked = !!node.maintenance_lock;
    }

    // Tab 2: Pending Command Queue
    renderCommandQueue(lastState.command_queue || []);

    // Tab 3: RMENU History
    renderRmenuHistory(lastState.rmenu_history || []);

    // Tab 4: Packet Log Table
    renderPacketLog(lastState.packet_log || []);

    // Tab 4: JSON State Viewer
    const jsonViewer = document.getElementById('jsonStateViewer');
    if (jsonViewer) {
      jsonViewer.textContent = JSON.stringify(lastState, null, 2);
    }
  }

  function updateAlarmItem(elemId, isActive, activeText, nominalText) {
    const el = document.getElementById(elemId);
    if (!el) return;
    el.classList.toggle('active', !!isActive);
    const tag = el.querySelector('.alarm-tag');
    if (tag) tag.textContent = isActive ? activeText : nominalText;
  }

  function renderCommandQueue(queue) {
    const container = document.getElementById('queuedCommandsList');
    if (!container) return;

    const pending = queue.filter(cmd => String(cmd.node_id) === String(currentNodeId) && cmd.status === "QUEUED");
    if (pending.length === 0) {
      container.innerHTML = '<p class="empty-state">No commands currently queued. Dispatched automatically upon wake.</p>';
      return;
    }

    let html = '<ul class="queue-ul">';
    pending.forEach(cmd => {
      html += `
        <li class="queue-item">
          <strong>${cmd.cmd_type}</strong>
          <span>Params: ${JSON.stringify(cmd.params)}</span>
          <span class="badge-status badge-warning">QUEUED (${cmd.created_at})</span>
        </li>
      `;
    });
    html += '</ul>';
    container.innerHTML = html;
  }

  function renderRmenuHistory(history) {
    const screen = document.getElementById('terminalOutput');
    if (!screen) return;

    // Preserve initial greeting lines
    const systemLines = screen.querySelectorAll('.terminal-line.system');
    let html = '';
    systemLines.forEach(l => { html += l.outerHTML; });

    history.slice(0, 20).reverse().forEach(entry => {
      const cls = entry.dir === 'REQ' ? 'req' : 'resp';
      const pfx = entry.dir === 'REQ' ? '>> ' : '<< ';
      html += `<div class="terminal-line ${cls}">[${entry.time}] ${pfx}${entry.text}</div>`;
    });

    screen.innerHTML = html;
    screen.scrollTop = screen.scrollHeight;
  }

  function hexToEnglish(hexStr, dir) {
    if (!hexStr || hexStr.length < 14) return hexStr || "--";

    try {
      // Convert hex string to byte array
      const bytes = [];
      for (let i = 0; i < hexStr.length; i += 2) {
        bytes.push(parseInt(hexStr.substr(i, 2), 16));
      }

      // Check 'MX' magic
      if (bytes[0] !== 0x4D || bytes[1] !== 0x58) return hexStr;

      const nodeId = bytes[2];
      const msgType = bytes[3];
      const seq = bytes[4] | (bytes[5] << 8);
      const nodeName = (nodeId === 1) ? "Mt Henderson" : (nodeId === 2 ? "Mt Parsons" : "Mawson Gateway");

      if (msgType === 1) { // PING / Heartbeat
        let vbat = 0, temp = 0, ch = 1, asciiMsg = "";
        if (bytes.length >= 15) {
          vbat = (bytes[7] | (bytes[8] << 8)) / 1000;
          let rawTemp = bytes[9] | (bytes[10] << 8);
          if (rawTemp > 32767) rawTemp -= 65536;
          temp = (rawTemp / 10).toFixed(1);
          ch = bytes[14];
        }

        // Extract ASCII message if present (bytes between index 15 and trailer: last 6 bytes)
        if (bytes.length > 21) {
          const textBytes = bytes.slice(15, bytes.length - 6);
          asciiMsg = String.fromCharCode(...textBytes).replace(/[^\x20-\x7E]/g, '').trim();
        }

        if (asciiMsg) {
          return `<strong>"${asciiMsg}"</strong> <span class="card-subtext">[${nodeName} • Ch ${ch} • ${vbat.toFixed(1)}V • ${temp}°C]</span>`;
        } else {
          return `<strong>Heartbeat Ping #${seq}</strong> <span class="card-subtext">[${nodeName} • Ch ${ch} • ${vbat.toFixed(1)}V • ${temp}°C]</span>`;
        }
      } else if (msgType === 2) { // ACK
        let pending = 0;
        if (bytes.length >= 15) {
          pending = bytes[13];
        }
        return `<strong>Gateway Acknowledge #${seq}</strong> <span class="card-subtext">[NTP UTC Time Sync • ${pending} Queued Commands]</span>`;
      } else if (msgType === 5 || msgType === 6) { // RMENU CMD / RESP
        const textBytes = bytes.slice(7, bytes.length - 6);
        const txt = String.fromCharCode(...textBytes).replace(/[^\x20-\x7E]/g, '').trim();
        return `<strong>RMENU Console:</strong> "${txt}"`;
      } else if (msgType === 7) {
        return `<strong style="color:var(--status-danger);">CRITICAL HARDWARE ALARM BROADCAST</strong>`;
      }

      return `Packet Seq #${seq} (Type 0x0${msgType})`;
    } catch (e) {
      return hexStr;
    }
  }

  function renderPacketLog(logs) {
    const tbody = document.getElementById('tblPacketLog');
    if (!tbody) return;

    let filtered = logs;
    if (currentFilter === 'RX') filtered = logs.filter(l => l.dir === 'RX');
    if (currentFilter === 'TX') filtered = logs.filter(l => l.dir === 'TX');

    if (filtered.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;">No packet records yet.</td></tr>';
      return;
    }

    tbody.innerHTML = filtered.slice(0, 35).map(pkt => `
      <tr>
        <td>${pkt.time}</td>
        <td><span class="badge-status ${pkt.dir === 'RX' ? 'badge-success' : 'badge-warning'}">${pkt.dir}</span></td>
        <td>${pkt.rssi ? pkt.rssi + ' dBm' : '--'}</td>
        <td>${pkt.snr ? pkt.snr + ' dB' : '--'}</td>
        <td>${pkt.summary ? pkt.summary.split(':')[0] : 'Packet'}</td>
        <td style="font-family: var(--font-sans); font-size: 0.88rem;">${hexToEnglish(pkt.hex, pkt.dir)}</td>
      </tr>
    `).join('');
  }

  // -------------------------------------------------------------
  // 4. Action Handlers (Dispatch to CGI API)
  // -------------------------------------------------------------
  async function postApi(action, payload = {}) {
    payload.action = action;
    payload.node_id = parseInt(currentNodeId, 10);

    try {
      const resp = await fetch(API_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const res = await resp.json();
      fetchState(); // Immediate refresh
      return res;
    } catch (err) {
      alert(`API Error: ${err.message}`);
    }
  }

  // Maintenance Lock Toggle
  const chkMaintLock = document.getElementById('chkMaintLock');
  chkMaintLock.addEventListener('change', (e) => {
    const isChecked = e.target.checked;
    if (isChecked) {
      if (!confirm("Enable Maintenance Lock?\n\nThis forces the remote node to remain awake 24/7 until explicitly toggled off.")) {
        e.target.checked = false;
        return;
      }
    }
    postApi('set_maintenance_lock', { enabled: isChecked });
  });

  // Switch Channel
  document.getElementById('btnApplyChannel').addEventListener('click', () => {
    const ch = parseInt(document.getElementById('selQuickChannel').value, 10);
    postApi('set_channel', { channel: ch });
  });

  // Repeater Inhibit / Knockdown
  document.getElementById('btnRepeaterEnable').addEventListener('click', () => {
    postApi('set_knockdown', { knockdown: false });
  });
  document.getElementById('btnRepeaterKnockdown').addEventListener('click', () => {
    if (confirm("Knock down (inhibit) the repeater transmitter? This will assert GPIO 4 to silence the repeater.")) {
      postApi('set_knockdown', { knockdown: true });
    }
  });

  // Force Poll
  document.getElementById('btnForcePoll').addEventListener('click', () => {
    postApi('force_poll');
  });

  // Update Schedule
  document.getElementById('btnSaveSchedule').addEventListener('click', () => {
    const wakeHour = parseInt(document.getElementById('inpWakeHour').value, 10);
    const duration = parseInt(document.getElementById('inpWakeDuration').value, 10);
    const holdoff = parseInt(document.getElementById('inpHoldoff').value, 10);
    postApi('configure_schedule', {
      wake_hour_utc: wakeHour,
      duration_min: duration,
      holdoff_min: holdoff
    });
    alert("Schedule updated! Command queued for dispatch in next ACK.");
  });

  // RMENU Terminal Form
  const frmTerminal = document.getElementById('frmTerminal');
  const inpTerminalCmd = document.getElementById('inpTerminalCmd');

  frmTerminal.addEventListener('submit', (e) => {
    e.preventDefault();
    const cmd = inpTerminalCmd.value.trim();
    if (!cmd) return;
    postApi('send_rmenu', { cmd: cmd });
    inpTerminalCmd.value = '';
  });

  // RMENU Macro Buttons
  document.querySelectorAll('.btn-terminal-macro').forEach(btn => {
    btn.addEventListener('click', () => {
      const cmd = btn.getAttribute('data-cmd');
      postApi('send_rmenu', { cmd: cmd });
    });
  });

  // Packet Log Filters
  document.getElementById('btnFilterAll').addEventListener('click', () => {
    currentFilter = 'ALL';
    updateFilterButtons('btnFilterAll');
    renderPacketLog(lastState ? lastState.packet_log : []);
  });
  document.getElementById('btnFilterRx').addEventListener('click', () => {
    currentFilter = 'RX';
    updateFilterButtons('btnFilterRx');
    renderPacketLog(lastState ? lastState.packet_log : []);
  });
  document.getElementById('btnFilterTx').addEventListener('click', () => {
    currentFilter = 'TX';
    updateFilterButtons('btnFilterTx');
    renderPacketLog(lastState ? lastState.packet_log : []);
  });

  function updateFilterButtons(activeId) {
    ['btnFilterAll', 'btnFilterRx', 'btnFilterTx'].forEach(id => {
      const b = document.getElementById(id);
      b.className = (id === activeId) ? 'btn btn-xs btn-primary' : 'btn btn-xs btn-outline';
    });
  }

  // Initial fetch and start polling loop (every 1.5 seconds)
  fetchState();
  setInterval(fetchState, 1500);
});

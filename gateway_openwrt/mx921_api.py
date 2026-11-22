#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mawson MX921 LoRa Gateway — CGI REST API Endpoint
Location on OpenWrt: /www/cgi-bin/mx921_api
Serves JSON requests from the lightweight Web Control Bar.
"""

import os
import sys
import json
import time
import socket
import re

STATE_FILE = '/tmp/mx921_state.json'
CMD_QUEUE_FILE = '/tmp/mx921_cmd_queue.json'

def talk_to_radio(cmds, wait_sec=1.0):
    """
    Connect to local virtual COM port 9001 and execute sequence of commands.
    Returns (raw_output_text, elapsed_ms)
    """
    s = socket.socket()
    s.settimeout(2.5)
    try:
        s.connect(('127.0.0.1', 9001))
        s.recv(1024) # banner
    except Exception as e:
        return ("ERROR: Cannot connect to virtual COM on port 9001: %s" % e, 0)

    # Initial reset: send Enter to clear any typed command or cancel prompt
    try:
        s.sendall(b'\r')
        time.sleep(0.5)
        while True:
            c = s.recv(1024)
            if not c: break
    except Exception:
        pass

    total_output = ""
    t0 = time.time()
    for c in cmds:
        if c == 'ESC':
            s.sendall(b'\x1b')
        else:
            s.sendall((c + '\r').encode('ascii'))
        time.sleep(wait_sec)
        while True:
            try:
                chunk = s.recv(1024)
                if chunk:
                    total_output += chunk.decode('ascii', errors='replace')
                else:
                    break
            except socket.timeout:
                break
            except Exception:
                break

    elapsed = int((time.time() - t0) * 1000)
    try:
        s.close()
    except Exception:
        pass

    return (total_output, elapsed)

def parse_mx921_info(text):
    info = {}
    for line in text.splitlines():
        line = line.strip()
        m = re.search(r'Model number\s*:\s*([A-Za-z0-9]+)', line)
        if m: info['model_number'] = m.group(1)
        m = re.search(r'Serial number\s*:\s*([A-Za-z0-9]+)', line)
        if m: info['serial_number'] = m.group(1)
        m = re.search(r'Version\s*:\s*([0-9\.]+)', line)
        if m: info['firmware_version'] = m.group(1)
        m = re.search(r'Channel number\s*:\s*(\d+)', line)
        if m: info['channel_number'] = m.group(1)
        m = re.search(r'Tx frequency\s*:\s*([\d\.]+)\s*MHz', line)
        if m: info['tx_freq_mhz'] = m.group(1)
        m = re.search(r'Rx frequency\s*:\s*([\d\.]+)\s*MHz', line)
        if m: info['rx_freq_mhz'] = m.group(1)
        m = re.search(r'Tx output power\s*:\s*([\d\.]+)\s*W', line)
        if m: info['tx_power_w'] = m.group(1)
        m = re.search(r'Tx CTCSS frequency\s*:\s*([\d\.]+)\s*Hz', line)
        if m: info['tx_ctcss_hz'] = m.group(1)
        m = re.search(r'Rx CTCSS frequency\s*:\s*([\d\.]+)\s*Hz', line)
        if m: info['rx_ctcss_hz'] = m.group(1)
        m = re.search(r'Simplex / Duplex\s*:\s*(\w+)', line)
        if m: info['duplex'] = m.group(1)
        m = re.search(r'Rx bandwidth\s*:\s*(\w+)', line)
        if m: info['bandwidth'] = m.group(1)
        m = re.search(r'Busy channel lock-out\s*:\s*(\w+)', line)
        if m: info['bclo'] = m.group(1)
    return info

def send_response(status_code, data):
    print("Status: %d OK" % status_code)
    print("Content-Type: application/json; charset=utf-8")
    print("Cache-Control: no-cache, no-store, must-revalidate")
    print("Access-Control-Allow-Origin: *")
    print("")
    print(json.dumps(data))
    sys.exit(0)

def get_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "gateway": {"status": "INITIALIZING", "current_sf": 9, "active_target_node": 1},
        "nodes": {},
        "packet_log": []
    }

def queue_command(action, node_id, params=None):
    cmd_entry = {
        "id": int(time.time() * 1000),
        "action": action,
        "node_id": int(node_id),
        "params": params or {},
        "time": time.strftime("%H:%M:%S", time.gmtime())
    }

    existing = []
    if os.path.exists(CMD_QUEUE_FILE):
        try:
            with open(CMD_QUEUE_FILE, 'r') as f:
                existing = json.load(f)
        except Exception:
            existing = []

    existing.append(cmd_entry)
    try:
        with open(CMD_QUEUE_FILE, 'w') as f:
            json.dump(existing, f)
    except Exception as e:
        return {"error": "Failed writing cmd queue: %s" % e}

    return {"success": True, "command": cmd_entry}

def main():
    method = os.environ.get('REQUEST_METHOD', 'GET').upper()

    if method == 'GET':
        state = get_state()
        send_response(200, state)

    elif method == 'POST':
        try:
            length = int(os.environ.get('CONTENT_LENGTH', 0))
            body = sys.stdin.read(length) if length > 0 else ""
            req = json.loads(body) if body else {}
        except Exception as e:
            send_response(400, {"error": "Invalid JSON body: %s" % e})

        action = req.get('action')
        node_id = req.get('node_id', 1)

        if action == 'set_maintenance_lock':
            enabled = bool(req.get('enabled', False))
            res = queue_command('set_maintenance_lock', node_id, {"enabled": enabled})
            send_response(200, res)

        elif action == 'set_sf':
            sf = int(req.get('sf', 9))
            if sf not in (9, 11):
                send_response(400, {"error": "Invalid SF. Must be 9 or 11."})
            res = queue_command('set_sf', node_id, {"sf": sf})
            send_response(200, res)

        elif action == 'set_schedule':
            hh = int(req.get('wake_hour_utc', 0))
            dur = int(req.get('duration_min', 60))
            hold = int(req.get('holdoff_min', 10))
            res = queue_command('set_schedule', node_id, {
                "wake_hour_utc": hh,
                "duration_min": dur,
                "holdoff_min": hold
            })
            send_response(200, res)

        elif action == 'select_node':
            res = queue_command('select_node', node_id)
            send_response(200, res)

        elif action == 'mx921_cmd':
            # Send single or sequence of commands directly to MX921
            cmd = req.get('cmd', '')
            if not cmd:
                send_response(400, {"error": "Missing cmd parameter"})
            out, elapsed = talk_to_radio([cmd], wait_sec=float(req.get('wait', 1.0)))
            parsed = parse_mx921_info(out)
            send_response(200, {
                "success": True,
                "command": cmd,
                "output": out,
                "parsed": parsed,
                "elapsed_ms": elapsed
            })

        elif action == 'mx921_auto_query':
            query = req.get('query', 'identity')
            if query == 'identity':
                # Launch RMENU, grab model & serial
                out, elapsed = talk_to_radio(['RMENU'], wait_sec=1.2)
                parsed = parse_mx921_info(out)
                send_response(200, {
                    "success": True,
                    "query": "identity",
                    "output": out,
                    "parsed": parsed,
                    "elapsed_ms": elapsed
                })
            elif query == 'channel':
                ch = str(req.get('channel', 1))
                # Navigate RMENU -> E -> E -> Channel #
                out, elapsed = talk_to_radio(['RMENU', 'E', 'E', ch], wait_sec=1.0)
                parsed = parse_mx921_info(out)
                send_response(200, {
                    "success": True,
                    "query": "channel",
                    "channel": ch,
                    "output": out,
                    "parsed": parsed,
                    "elapsed_ms": elapsed
                })
            elif query == 'config':
                out, elapsed = talk_to_radio(['RMENU', 'C'], wait_sec=1.2)
                parsed = parse_mx921_info(out)
                send_response(200, {
                    "success": True,
                    "query": "config",
                    "output": out,
                    "parsed": parsed,
                    "elapsed_ms": elapsed
                })
            elif query == 'alignment':
                out, elapsed = talk_to_radio(['RMENU', 'A'], wait_sec=1.2)
                parsed = parse_mx921_info(out)
                send_response(200, {
                    "success": True,
                    "query": "alignment",
                    "output": out,
                    "parsed": parsed,
                    "elapsed_ms": elapsed
                })
            elif query == 'reset_menu':
                # Send ESC multiple times to cleanly return to top
                out, elapsed = talk_to_radio(['ESC', 'ESC', 'RMENU'], wait_sec=0.8)
                parsed = parse_mx921_info(out)
                send_response(200, {
                    "success": True,
                    "query": "reset_menu",
                    "output": out,
                    "parsed": parsed,
                    "elapsed_ms": elapsed
                })
            else:
                send_response(400, {"error": "Unknown auto_query: %s" % query})

        else:
            send_response(400, {"error": "Unknown action: %s" % action})

if __name__ == '__main__':
    main()

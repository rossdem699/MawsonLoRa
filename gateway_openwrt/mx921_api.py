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

STATE_FILE = '/tmp/mx921_state.json'
CMD_QUEUE_FILE = '/tmp/mx921_cmd_queue.json'

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

        else:
            send_response(400, {"error": "Unknown action: %s" % action})

if __name__ == '__main__':
    main()

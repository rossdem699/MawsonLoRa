#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mawson MX921 LoRa Gateway — CGI API Endpoint
Installed at /www/cgi-bin/mx921_api
Handles JSON GET / POST requests from the 4-tab web dashboard.
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
    return {"status": "INITIALIZING", "nodes": {}, "packet_log": []}

def queue_command(node_id, cmd_type, params=None):
    cmd_entry = {
        "id": int(time.time() * 1000),
        "node_id": int(node_id),
        "cmd_type": cmd_type,
        "params": params or {},
        "created_at": time.strftime("%H:%M:%S", time.gmtime()),
        "status": "QUEUED"
    }
    
    existing = []
    if os.path.exists(CMD_QUEUE_FILE):
        try:
            with open(CMD_QUEUE_FILE, 'r') as f:
                existing = json.load(f)
        except Exception:
            existing = []
    
    existing.append(cmd_entry)
    with open(CMD_QUEUE_FILE, 'w') as f:
        json.dump(existing, f)

    # Also reflect directly in state file command_queue if possible
    state = get_state()
    if "command_queue" not in state:
        state["command_queue"] = []
    state["command_queue"].append(cmd_entry)
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception:
        pass

    return cmd_entry

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

        state = get_state()
        node_str = str(node_id)
        node = state.get("nodes", {}).get(node_str)

        if action == 'set_maintenance_lock':
            enabled = bool(req.get('enabled', False))
            if node:
                node["maintenance_lock"] = enabled
                node["power_state_desc"] = "MAINTENANCE LOCK (STAY AWAKE)" if enabled else "SCHEDULED WINDOW"
            queue_command(node_id, "SET_MAINTENANCE_LOCK", {"enabled": enabled})
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
            send_response(200, {"success": True, "maintenance_lock": enabled})

        elif action == 'set_channel':
            ch = int(req.get('channel', 1))
            if node:
                node["telemetry"]["active_channel"] = ch
            queue_command(node_id, "SET_CHANNEL", {"channel": ch})
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
            send_response(200, {"success": True, "channel": ch})

        elif action == 'set_knockdown':
            knockdown = bool(req.get('knockdown', False))
            if node:
                node["repeater_inhibited"] = knockdown
            queue_command(node_id, "SET_KNOCKDOWN", {"knockdown": knockdown})
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
            send_response(200, {"success": True, "knockdown": knockdown})

        elif action == 'send_rmenu':
            cmd_text = str(req.get('cmd', '')).strip()
            if cmd_text:
                queue_command(node_id, "RMENU_RAW", {"cmd": cmd_text})
                # Add to history
                if "rmenu_history" not in state:
                    state["rmenu_history"] = []
                state["rmenu_history"].insert(0, {
                    "time": time.strftime("%H:%M:%S", time.gmtime()),
                    "dir": "REQ",
                    "text": cmd_text
                })
                with open(STATE_FILE, 'w') as f:
                    json.dump(state, f)
            send_response(200, {"success": True, "queued_cmd": cmd_text})

        elif action == 'force_poll':
            queue_command(node_id, "FORCE_POLL", {})
            send_response(200, {"success": True, "message": "Telemetry poll queued for next wake window"})

        elif action == 'configure_schedule':
            wake_hour = int(req.get('wake_hour_utc', 0))
            duration = int(req.get('duration_min', 60))
            holdoff = int(req.get('holdoff_min', 10))
            if node:
                node["schedule_config"] = {
                    "wake_hour_utc": wake_hour,
                    "duration_min": duration,
                    "holdoff_min": holdoff
                }
            queue_command(node_id, "SET_SCHEDULE", {"wake_hour_utc": wake_hour, "duration_min": duration, "holdoff_min": holdoff})
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
            send_response(200, {"success": True, "schedule": node.get("schedule_config") if node else {}})

        else:
            send_response(400, {"error": "Unknown action: %s" % action})

if __name__ == '__main__':
    main()

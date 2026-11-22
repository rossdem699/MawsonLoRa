#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mawson MX921 LoRa Repeater Bridge — OpenWrt Gateway Daemon
Target: Duinotech XC-4394 (Atheros AR9331 OpenWrt Chaos Calmer, Python 2.7 / 3 compatible)

Features:
  - Interfaces with ATmega328P LoRa bridge MCU over /dev/ttyATH0 (115200 baud).
  - TCP Serial Bridge on Port 9001 for Spectra 900Tools / PuTTY / RMENU.
  - Intercepts Hayes-style +++ / AT commands (AT+STATUS?, AT+AWAKE=1|0, AT+SF=9|11, AT+SCHED=HH,DUR).
  - Transparent LoRa data framing with 15ms idle-gap coalescing (<=180B payload).
  - Synchronized Spreading Factor switching with 60s dead-man fallback support.
  - Maintains /tmp/mx921_state.json and processes /tmp/mx921_cmd_queue.json for Web Control Bar.
"""

import os
import sys
import time
import socket
import select
import struct
import json

# Platform-specific serial import
try:
    import termios
except ImportError:
    termios = None

SERIAL_PORT = '/dev/ttyATH0'
SERIAL_BAUD_INT = 115200
TCP_BRIDGE_PORT = 9001
STATE_FILE = '/tmp/mx921_state.json'
CMD_QUEUE_FILE = '/tmp/mx921_cmd_queue.json'
LOG_MAX_ENTRIES = 50

# Protocol Constants
MX_MAGIC = b'MX'
NODE_ID_GATEWAY = 0x00
NODE_ID_MT_HENDERSON = 0x01
NODE_ID_MT_PARSONS = 0x02

PKT_TYPE_DATA = 0x01
PKT_TYPE_CTRL = 0x02
PKT_TYPE_ACK  = 0x03
PKT_TYPE_PING = 0x04

CMD_SET_SF     = 0x10
CMD_SET_LOCK   = 0x11
CMD_SET_SCHED  = 0x12
CMD_REQ_STATUS = 0x13

PWR_STATE_SCHEDULED_WINDOW   = (1 << 0)
PWR_STATE_ACTIVITY_HOLD      = (1 << 1)
PWR_STATE_MAINTENANCE_LOCK   = (1 << 2)
PWR_STATE_VEXT_ENABLED       = (1 << 3)
PWR_STATE_SF_FALLBACK_ACTIVE = (1 << 4)

SHARED_SECRET = b"MWS921LK"

def crc16_ccitt(data):
    """CCITT CRC-16 (Polynomial 0x1021, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in bytearray(data):
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc

def calc_auth_tag(data, secret=SHARED_SECRET):
    """32-bit keyed FNV-1a hash matching node firmware."""
    hash_val = 2166136261
    for b in bytearray(secret):
        hash_val ^= b
        hash_val = (hash_val * 16777619) & 0xFFFFFFFF
    for b in bytearray(data):
        hash_val ^= b
        hash_val = (hash_val * 16777619) & 0xFFFFFFFF
    return hash_val

class Mx921GatewayDaemon(object):
    def __init__(self):
        self.fd = None
        self.tcp_server = None
        self.tcp_clients = []
        self.tcp_rx_buffers = {}
        self.tx_seq = 0
        self.active_node_id = NODE_ID_MT_HENDERSON
        self.current_sf = 9
        self.pending_sf_switch = None
        self.pending_cmd_ack = None

        self.state = {
            "gateway": {
                "uptime_sec": 0,
                "utc_time": "",
                "lora_freq_mhz": 916.8,
                "current_sf": 9,
                "bw_khz": 125.0,
                "active_target_node": 1,
                "tcp_port": TCP_BRIDGE_PORT,
                "connected_clients": 0,
                "status": "RUNNING"
            },
            "nodes": {
                "1": {
                    "node_id": 1,
                    "name": "Mount Henderson (~13 km SSE)",
                    "last_seen_epoch": 0,
                    "last_seen_str": "Never",
                    "seconds_ago": -1,
                    "power_state_raw": 0,
                    "power_state_desc": "UNKNOWN",
                    "maintenance_lock": False,
                    "scheduled_window": False,
                    "activity_hold": False,
                    "holdoff_rem_sec": 0,
                    "vext_enabled": False,
                    "sf_fallback_active": False,
                    "current_sf": 9,
                    "link": {
                        "rssi_dbm": 0.0,
                        "snr_db": 0.0,
                        "packet_count": 0,
                        "crc_errors": 0
                    },
                    "telemetry": {
                        "vbat_mv": 5000,
                        "temp_c": 21.5
                    },
                    "schedule_config": {
                        "wake_hour_utc": 0,
                        "duration_min": 60,
                        "holdoff_min": 10
                    }
                },
                "2": {
                    "node_id": 2,
                    "name": "Mount Parsons (Standby)",
                    "last_seen_epoch": 0,
                    "last_seen_str": "Never",
                    "seconds_ago": -1,
                    "power_state_raw": 0,
                    "power_state_desc": "STANDBY",
                    "maintenance_lock": False,
                    "scheduled_window": False,
                    "activity_hold": False,
                    "holdoff_rem_sec": 0,
                    "vext_enabled": False,
                    "sf_fallback_active": False,
                    "current_sf": 9,
                    "link": {"rssi_dbm": 0.0, "snr_db": 0.0, "packet_count": 0, "crc_errors": 0},
                    "telemetry": {"vbat_mv": 0, "temp_c": 0.0},
                    "schedule_config": {"wake_hour_utc": 0, "duration_min": 60, "holdoff_min": 10}
                }
            },
            "command_queue": [],
            "packet_log": []
        }
        self.start_time = time.time()

    def open_serial(self):
        if not os.path.exists(SERIAL_PORT):
            print("[DAEMON] Serial port %s not found (running in mock mode)." % SERIAL_PORT)
            return

        print("[DAEMON] Opening %s at 115200 baud..." % SERIAL_PORT)
        try:
            self.fd = os.open(SERIAL_PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            if termios:
                attrs = termios.tcgetattr(self.fd)
                attrs[0] = 0  # iflag
                attrs[1] = 0  # oflag
                attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # cflag
                attrs[3] = 0  # lflag
                attrs[4] = termios.B115200
                attrs[5] = termios.B115200
                termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
                termios.tcflush(self.fd, termios.TCIOFLUSH)
            print("[DAEMON] Serial port %s initialized." % SERIAL_PORT)
        except Exception as e:
            print("[DAEMON] Error opening serial port: %s" % e)
            self.fd = None

    def start_tcp_server(self):
        print("[DAEMON] Starting TCP Serial Bridge on 0.0.0.0:%d..." % TCP_BRIDGE_PORT)
        self.tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp_server.bind(('0.0.0.0', TCP_BRIDGE_PORT))
        self.tcp_server.listen(4)
        self.tcp_server.setblocking(0)
        print("[DAEMON] TCP Serial Bridge ready.")

    def save_state(self):
        now = time.time()
        self.state["gateway"]["uptime_sec"] = int(now - self.start_time)
        self.state["gateway"]["utc_time"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now))
        self.state["gateway"]["connected_clients"] = len(self.tcp_clients)
        self.state["gateway"]["current_sf"] = self.current_sf
        self.state["gateway"]["active_target_node"] = self.active_node_id

        for nid_str, node in self.state["nodes"].items():
            if node["last_seen_epoch"] > 0:
                node["seconds_ago"] = int(now - node["last_seen_epoch"])
                
                # Format live power state description
                if node["maintenance_lock"]:
                    node["power_state_desc"] = "MAINTENANCE LOCK"
                elif node["activity_hold"]:
                    rem_m = node["holdoff_rem_sec"] // 60
                    rem_s = node["holdoff_rem_sec"] % 60
                    node["power_state_desc"] = "AWAKE - IDLE HOLD (%02d:%02d)" % (rem_m, rem_s)
                elif node["scheduled_window"]:
                    node["power_state_desc"] = "AWAKE - SCHEDULED"
                else:
                    # Sleeping
                    wh = node["schedule_config"]["wake_hour_utc"]
                    node["power_state_desc"] = "SLEEPING - NEXT WAKE %02d:00" % wh

        tmp_file = STATE_FILE + ".tmp"
        try:
            with open(tmp_file, 'w') as f:
                json.dump(self.state, f, indent=2)
            os.rename(tmp_file, STATE_FILE)
        except Exception as e:
            print("[DAEMON] Failed writing state file: %s" % e)

    def log_packet(self, direction, hex_data, summary, rssi=None, snr=None):
        entry = {
            "time": time.strftime("%H:%M:%S", time.gmtime()),
            "dir": direction,
            "hex": hex_data[:48] + ("..." if len(hex_data) > 48 else ""),
            "len": len(hex_data) // 2,
            "summary": summary,
            "sf": self.current_sf
        }
        if rssi is not None:
            entry["rssi"] = float(rssi)
        if snr is not None:
            entry["snr"] = float(snr)

        self.state["packet_log"].insert(0, entry)
        self.state["packet_log"] = self.state["packet_log"][:LOG_MAX_ENTRIES]

    def send_raw_lora(self, payload_bytes, summary=""):
        hex_data = "".join(["%02X" % (b if isinstance(b, int) else ord(b)) for b in payload_bytes])
        cmd = "+TX:%s\r\n" % hex_data
        if self.fd:
            try:
                os.write(self.fd, cmd.encode('ascii') if sys.version_info[0] >= 3 else cmd)
                self.log_packet("TX", hex_data, summary)
            except Exception as e:
                print("[DAEMON] Serial TX Error: %s" % e)
        else:
            self.log_packet("TX(mock)", hex_data, summary)

    def send_mcu_cmd(self, cmd_str):
        if self.fd:
            try:
                line = cmd_str.strip() + "\r\n"
                os.write(self.fd, line.encode('ascii') if sys.version_info[0] >= 3 else line)
                print("[DAEMON] Sent MCU CMD: %s" % cmd_str)
            except Exception as e:
                print("[DAEMON] Error writing MCU CMD: %s" % e)

    def send_control_packet(self, node_id, cmd_id, p1=0, p2=0, p3=0, summary=""):
        self.tx_seq = (self.tx_seq + 1) & 0xFFFF
        hdr = struct.pack('<2sBBHB', MX_MAGIC, node_id, PKT_TYPE_CTRL, self.tx_seq, 0)

        epoch_utc = int(time.time())
        ctrl_body = struct.pack('<BBBBI', cmd_id, p1, p2, p3, epoch_utc)
        auth_tag_val = calc_auth_tag(ctrl_body, SHARED_SECRET)
        auth_tag_bytes = struct.pack('<I', auth_tag_val)

        packet_body = hdr + ctrl_body + auth_tag_bytes
        crc = crc16_ccitt(packet_body)
        full_packet = packet_body + struct.pack('<H', crc)

        self.send_raw_lora(full_packet, summary or ("CTRL CMD=0x%02X -> Node %d" % (cmd_id, node_id)))

    def send_data_packet(self, data_bytes, node_id=None):
        if not data_bytes:
            return
        if node_id is None:
            node_id = self.active_node_id

        # Chunk into max 180 byte frames
        chunk_size = 180
        for i in range(0, len(data_bytes), chunk_size):
            chunk = data_bytes[i:i + chunk_size]
            self.tx_seq = (self.tx_seq + 1) & 0xFFFF
            hdr = struct.pack('<2sBBHB', MX_MAGIC, node_id, PKT_TYPE_DATA, self.tx_seq, 0)
            packet_body = hdr + chunk
            crc = crc16_ccitt(packet_body)
            full_packet = packet_body + struct.pack('<H', crc)

            summary = "DATA len=%d -> Node %d (Transparent RS-232)" % (len(chunk), node_id)
            self.send_raw_lora(full_packet, summary)

    def initiate_sf_switch(self, target_sf, node_id=None):
        if target_sf not in (9, 11):
            return False
        if node_id is None:
            node_id = self.active_node_id

        print("[DAEMON] Initiating synchronized SF switch to SF%d for Node %d..." % (target_sf, node_id))
        self.pending_sf_switch = {
            "target_sf": target_sf,
            "node_id": node_id,
            "start_time": time.time(),
            "timeout": 5.0
        }
        # Send CMD_SET_SF at current SF to node
        self.send_control_packet(
            node_id,
            CMD_SET_SF,
            p1=target_sf,
            summary="CMD_SET_SF (target SF%d) -> Node %d" % (target_sf, node_id)
        )
        return True

    def handle_incoming_lora(self, rssi, snr, length, hex_str):
        if len(hex_str) % 2 != 0:
            return
        try:
            raw = bytearray.fromhex(hex_str)
        except Exception:
            return

        if len(raw) < 9:  # Min: Header(7) + CRC(2)
            return

        if raw[0] != 0x4D or raw[1] != 0x58:  # 'M', 'X'
            return

        # CRC verification
        body = raw[:-2]
        expected_crc = struct.unpack('<H', bytes(raw[-2:]))[0]
        calc_crc = crc16_ccitt(body)

        node_id = raw[2]
        pkt_type = raw[3]
        seq = struct.unpack('<H', bytes(raw[4:6]))[0]
        flags = raw[6]

        node_str = str(node_id)
        if node_str not in self.state["nodes"]:
            return

        node = self.state["nodes"][node_str]
        node["link"]["packet_count"] += 1
        node["link"]["rssi_dbm"] = float(rssi)
        node["link"]["snr_db"] = float(snr)

        if expected_crc != calc_crc:
            node["link"]["crc_errors"] += 1
            self.log_packet("RX", hex_str, "CRC ERROR (Calc 0x%04X != Exp 0x%04X)" % (calc_crc, expected_crc), rssi, snr)
            return

        payload = bytes(raw[7:-2])
        node["last_seen_epoch"] = int(time.time())
        node["last_seen_str"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(node["last_seen_epoch"]))
        node["power_state_raw"] = flags

        node["scheduled_window"] = bool(flags & PWR_STATE_SCHEDULED_WINDOW)
        node["activity_hold"] = bool(flags & PWR_STATE_ACTIVITY_HOLD)
        node["maintenance_lock"] = bool(flags & PWR_STATE_MAINTENANCE_LOCK)
        node["vext_enabled"] = bool(flags & PWR_STATE_VEXT_ENABLED)
        node["sf_fallback_active"] = bool(flags & PWR_STATE_SF_FALLBACK_ACTIVE)

        summary = "Node %d Type=0x%02X Seq=%d" % (node_id, pkt_type, seq)

        if pkt_type == PKT_TYPE_DATA:
            # Transparent serial data from MX921 -> broadcast to all connected TCP clients on Port 9001
            summary = "DATA from Node %d: %d bytes (transparent serial)" % (node_id, len(payload))
            self.broadcast_to_tcp_clients(payload)

        elif pkt_type == PKT_TYPE_ACK:
            if len(payload) >= 14:
                cmd_ack, status_code, current_sf, ack_flags, holdoff_rem, vbat, temp_x10, epoch_utc = struct.unpack('<BBBBHHhI', payload[:14])
                node["current_sf"] = current_sf
                node["holdoff_rem_sec"] = holdoff_rem
                node["telemetry"]["vbat_mv"] = vbat
                node["telemetry"]["temp_c"] = temp_x10 / 10.0

                summary = "ACK #%d: cmd=0x%02X, SF%d, Holdoff=%ds, %dmV, %.1fC" % (
                    seq, cmd_ack, current_sf, holdoff_rem, vbat, node["telemetry"]["temp_c"]
                )

                # Check if this ACK completes a pending SF switch!
                if self.pending_sf_switch and self.pending_sf_switch["node_id"] == node_id:
                    target_sf = self.pending_sf_switch["target_sf"]
                    print("[DAEMON] Node %d confirmed SF switch to SF%d! Switching local MCU..." % (node_id, target_sf))
                    self.send_mcu_cmd("+SF:%d" % target_sf)
                    self.current_sf = target_sf
                    self.pending_sf_switch = None
                    # Send a confirmation ping at the new SF to satisfy node's 60s dead-man watchdog
                    time.sleep(0.1)
                    self.send_control_packet(node_id, CMD_REQ_STATUS, summary="CONFIRM_SF%d_PING -> Node %d" % (target_sf, node_id))

        self.log_packet("RX", hex_str, summary, rssi, snr)
        self.save_state()

    def broadcast_to_tcp_clients(self, data):
        disconnected = []
        for client in self.tcp_clients:
            try:
                client.sendall(data)
            except Exception:
                disconnected.append(client)
        for d in disconnected:
            self.remove_tcp_client(d)

    def remove_tcp_client(self, client):
        if client in self.tcp_clients:
            self.tcp_clients.remove(client)
        if client in self.tcp_rx_buffers:
            del self.tcp_rx_buffers[client]
        try:
            client.close()
        except Exception:
            pass
        print("[DAEMON] TCP Client disconnected. Active clients: %d" % len(self.tcp_clients))

    def handle_at_command(self, client, cmd_line):
        cmd = cmd_line.strip()
        cmd_upper = cmd.upper()
        print("[DAEMON] Intercepted AT Command: %s" % cmd)

        if cmd_upper == "AT":
            client.sendall(b"OK\r\n")
            return

        node = self.state["nodes"].get(str(self.active_node_id), {})

        if cmd_upper == "AT+STATUS?":
            # Format comprehensive status report
            resp = (
                "\r\n+STATUS: NODE %d (%s)\r\n"
                "STATE: %s\r\n"
                "LINK: SF%d | RSSI: %.1f dBm | SNR: %.1f dB\r\n"
                "SUPPLY: %d mV | TEMP: %.1f C\r\n"
                "SCHED: WAKE %02d:00 UTC | DUR: %dm | HOLDOFF: %dm (REM: %ds)\r\n"
                "MAINT_LOCK: %s | FALLBACK: %s\r\n"
                "OK\r\n"
            ) % (
                self.active_node_id,
                node.get("name", "Unknown"),
                node.get("power_state_desc", "UNKNOWN"),
                node.get("current_sf", self.current_sf),
                node.get("link", {}).get("rssi_dbm", 0.0),
                node.get("link", {}).get("snr_db", 0.0),
                node.get("telemetry", {}).get("vbat_mv", 0),
                node.get("telemetry", {}).get("temp_c", 0.0),
                node.get("schedule_config", {}).get("wake_hour_utc", 0),
                node.get("schedule_config", {}).get("duration_min", 60),
                node.get("schedule_config", {}).get("holdoff_min", 10),
                node.get("holdoff_rem_sec", 0),
                "ENABLED" if node.get("maintenance_lock") else "DISABLED",
                "ACTIVE (TRIAL)" if node.get("sf_fallback_active") else "INACTIVE"
            )
            client.sendall(resp.encode('utf-8'))

        elif cmd_upper.startswith("AT+AWAKE="):
            val = cmd_upper[9:].strip()
            lock = (val == "1")
            self.send_control_packet(self.active_node_id, CMD_SET_LOCK, p1=(1 if lock else 0),
                                     summary="AT+AWAKE=%d -> Node %d" % (1 if lock else 0, self.active_node_id))
            node["maintenance_lock"] = lock
            self.save_state()
            client.sendall(b"OK: STAY_AWAKE_LOCK=" + (b"1" if lock else b"0") + b"\r\n")

        elif cmd_upper.startswith("AT+SF="):
            val = cmd_upper[6:].strip()
            if val in ("9", "11"):
                target_sf = int(val)
                success = self.initiate_sf_switch(target_sf, self.active_node_id)
                if success:
                    client.sendall(b"OK: SWITCHING_TO_SF" + val.encode('ascii') + b"\r\n")
                else:
                    client.sendall(b"ERROR: SF_SWITCH_BUSY\r\n")
            else:
                client.sendall(b"ERROR: INVALID_SF (Use 9 or 11)\r\n")

        elif cmd_upper.startswith("AT+SCHED="):
            # Format: AT+SCHED=HH,DUR[,HOLD]
            parts = cmd_upper[9:].split(',')
            if len(parts) >= 2:
                try:
                    hh = int(parts[0])
                    dur = int(parts[1])
                    hold = int(parts[2]) if len(parts) > 2 else 10
                    self.send_control_packet(self.active_node_id, CMD_SET_SCHED, p1=hh, p2=dur, p3=hold,
                                             summary="AT+SCHED=%02d,%d,%d -> Node %d" % (hh, dur, hold, self.active_node_id))
                    node["schedule_config"]["wake_hour_utc"] = hh
                    node["schedule_config"]["duration_min"] = dur
                    node["schedule_config"]["holdoff_min"] = hold
                    self.save_state()
                    client.sendall(b"OK: SCHEDULE_UPDATED\r\n")
                except ValueError:
                    client.sendall(b"ERROR: INVALID_PARAMS\r\n")
            else:
                client.sendall(b"ERROR: SYNTAX (AT+SCHED=HH,DUR[,HOLD])\r\n")
        else:
            client.sendall(b"ERROR: UNKNOWN_AT_CMD\r\n")

    def process_tcp_client_data(self, client):
        try:
            chunk = client.recv(1024)
            if not chunk:
                self.remove_tcp_client(client)
                return
        except Exception:
            self.remove_tcp_client(client)
            return

        buf = self.tcp_rx_buffers.get(client, b"") + chunk
        
        # Check if client is issuing Hayes AT commands
        # An AT command starts with AT or at followed by + or \r or \n
        while b'\n' in buf or b'\r' in buf:
            # Handle \r\n or \n or \r
            if b'\r\n' in buf:
                idx = buf.find(b'\r\n')
                line = buf[:idx].decode('ascii', errors='replace').strip()
                buf = buf[idx + 2:]
            elif b'\n' in buf:
                idx = buf.find(b'\n')
                line = buf[:idx].decode('ascii', errors='replace').strip()
                buf = buf[idx + 1:]
            else:
                idx = buf.find(b'\r')
                line = buf[:idx].decode('ascii', errors='replace').strip()
                buf = buf[idx + 1:]

            if line.upper().startswith("AT") or line == "+++":
                self.handle_at_command(client, line)
            elif line:
                # Normal transparent line with newline
                self.send_data_packet((line + "\r\n").encode('ascii'))
            else:
                # User pressed Enter alone in terminal
                self.send_data_packet(b"\r\n")

        # Any remaining non-newline transparent serial bytes buffered for idle-flush
        self.tcp_rx_buffers[client] = buf

    def check_cmd_queue(self):
        if not os.path.exists(CMD_QUEUE_FILE):
            return
        try:
            with open(CMD_QUEUE_FILE, 'r') as f:
                cmds = json.load(f)
            os.remove(CMD_QUEUE_FILE)
        except Exception:
            return

        for cmd in cmds:
            action = cmd.get("action")
            node_id = int(cmd.get("node_id", self.active_node_id))
            params = cmd.get("params", {})

            if action == "set_maintenance_lock":
                lock = bool(params.get("enabled", False))
                self.send_control_packet(node_id, CMD_SET_LOCK, p1=(1 if lock else 0),
                                         summary="WEB_CMD: StayAwake=%d -> Node %d" % (1 if lock else 0, node_id))
                self.state["nodes"][str(node_id)]["maintenance_lock"] = lock

            elif action == "set_sf":
                target_sf = int(params.get("sf", 9))
                self.initiate_sf_switch(target_sf, node_id)

            elif action == "set_schedule":
                hh = int(params.get("wake_hour_utc", 0))
                dur = int(params.get("duration_min", 60))
                hold = int(params.get("holdoff_min", 10))
                self.send_control_packet(node_id, CMD_SET_SCHED, p1=hh, p2=dur, p3=hold,
                                         summary="WEB_CMD: Sched %02d:00 (%dm) -> Node %d" % (hh, dur, node_id))
                self.state["nodes"][str(node_id)]["schedule_config"]["wake_hour_utc"] = hh
                self.state["nodes"][str(node_id)]["schedule_config"]["duration_min"] = dur
                self.state["nodes"][str(node_id)]["schedule_config"]["holdoff_min"] = hold

            elif action == "select_node":
                self.active_node_id = node_id

        self.save_state()

    def run(self):
        self.open_serial()
        self.start_tcp_server()
        self.save_state()

        print("[DAEMON] Mawson MX921 LoRa Gateway daemon active.")
        serial_buf = ""
        last_save_time = time.time()
        last_queue_check = time.time()

        while True:
            now = time.time()
            if now - last_save_time >= 2.0:
                last_save_time = now
                self.save_state()

            if now - last_queue_check >= 0.5:
                last_queue_check = now
                self.check_cmd_queue()

            # Flush any pending transparent serial buffers from TCP clients (15ms idle coalescing)
            for client, buf in list(self.tcp_rx_buffers.items()):
                if buf and not (buf.startswith(b"AT") or buf.startswith(b"at")):
                    self.send_data_packet(buf)
                    self.tcp_rx_buffers[client] = b""

            # Select multiplexing
            read_sockets = [self.tcp_server] + self.tcp_clients
            read_fds = read_sockets[:]
            if self.fd is not None:
                read_fds.append(self.fd)

            try:
                readable, _, _ = select.select(read_fds, [], [], 0.05)
            except Exception:
                continue

            for s in readable:
                if s is self.tcp_server:
                    client_sock, addr = self.tcp_server.accept()
                    client_sock.setblocking(0)
                    self.tcp_clients.append(client_sock)
                    self.tcp_rx_buffers[client_sock] = b""
                    print("[DAEMON] New TCP Bridge connection from %s:%d (Active: %d)" % (addr[0], addr[1], len(self.tcp_clients)))
                    # Welcome banner
                    welcome = (
                        "\r\n=== Mawson MX921 RS-232-over-LoRa Virtual COM Bridge ===\r\n"
                        "Connected to Node %d (Mt Henderson). 57600 baud transparent stream active.\r\n"
                        "Type AT+STATUS? for RF/sleep diagnostics, AT+AWAKE=1 to lock awake.\r\n\r\n"
                    ) % self.active_node_id
                    try:
                        client_sock.sendall(welcome.encode('utf-8'))
                    except Exception:
                        pass
                elif s in self.tcp_clients:
                    self.process_tcp_client_data(s)
                elif s == self.fd:
                    try:
                        chunk = os.read(self.fd, 512)
                        if chunk:
                            text = chunk.decode('ascii', errors='replace') if sys.version_info[0] >= 3 else chunk
                            serial_buf += text
                            while '\n' in serial_buf:
                                line, serial_buf = serial_buf.split('\n', 1)
                                line = line.strip()
                                if not line:
                                    continue
                                if line.startswith('+RX:'):
                                    parts = line[4:].split(',')
                                    if len(parts) >= 4:
                                        self.handle_incoming_lora(parts[0], parts[1], parts[2], parts[3])
                    except OSError:
                        pass

if __name__ == '__main__':
    daemon = Mx921GatewayDaemon()
    daemon.run()

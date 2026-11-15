#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mawson MX921 LoRa Repeater Bridge — OpenWrt Gateway Daemon
Runs on Duinotech XC-4394 (OpenWrt Chaos Calmer, Python 2.7)
Manages serial communication with ATmega328P LoRa bridge over /dev/ttyATH0,
maintains /tmp/mx921_state.json, and handles the command queue.
"""

import os
import sys
import time
import termios
import select
import struct
import json

SERIAL_PORT = '/dev/ttyATH0'
SERIAL_BAUD = termios.B115200
STATE_FILE = '/tmp/mx921_state.json'
CMD_QUEUE_FILE = '/tmp/mx921_cmd_queue.json'
LOG_MAX_ENTRIES = 60

# Node definitions
NODE_NAMES = {
    0x00: "Mawson Base Gateway (XC-4394)",
    0x01: "Mount Henderson (~13 km SSE)",
    0x02: "Mount Parsons"
}

def crc16_ccitt(data):
    crc = 0xFFFF
    for byte in bytearray(data):
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc

class Mx921GatewayDaemon(object):
    def __init__(self):
        self.fd = None
        self.ack_seq = 0
        self.state = {
            "gateway": {
                "uptime_sec": 0,
                "utc_time": "",
                "lora_freq_mhz": 916.8,
                "sf": 10,
                "bw_khz": 125.0,
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
                    "repeater_inhibited": False,
                    "vext_enabled": False,
                    "link": {
                        "rssi_dbm": 0.0,
                        "snr_db": 0.0,
                        "packet_count": 0,
                        "crc_errors": 0
                    },
                    "telemetry": {
                        "vbat_mv": 0,
                        "temp_c": 0.0,
                        "analog_rssi_raw": 0,
                        "analog_rssi_dbm": -120.0,
                        "active_channel": 1,
                        "rx_freq_mhz": 149.825,
                        "tx_freq_mhz": 149.225,
                        "spacing_khz": 12.5,
                        "ctcss_tone_hz": 123.0
                    },
                    "alarms": {
                        "alarm1": False,
                        "alarm2": False,
                        "cor_active": False,
                        "vswr_fault": False,
                        "low_fwd_power": False,
                        "pa_overtemp": False,
                        "pll_unlock": False
                    },
                    "schedule_config": {
                        "wake_hour_utc": 0,
                        "duration_min": 60,
                        "holdoff_min": 10
                    }
                },
                "2": {
                    "node_id": 2,
                    "name": "Mount Parsons",
                    "last_seen_epoch": 0,
                    "last_seen_str": "Standby (Offline)",
                    "seconds_ago": -1,
                    "power_state_raw": 0,
                    "power_state_desc": "OFFLINE / UNREACHABLE",
                    "maintenance_lock": False,
                    "scheduled_window": False,
                    "activity_hold": False,
                    "repeater_inhibited": False,
                    "vext_enabled": False,
                    "link": {
                        "rssi_dbm": 0.0,
                        "snr_db": 0.0,
                        "packet_count": 0,
                        "crc_errors": 0
                    },
                    "telemetry": {
                        "vbat_mv": 0,
                        "temp_c": 0.0,
                        "analog_rssi_raw": 0,
                        "analog_rssi_dbm": -120.0,
                        "active_channel": 2,
                        "rx_freq_mhz": 150.150,
                        "tx_freq_mhz": 149.550,
                        "spacing_khz": 12.5,
                        "ctcss_tone_hz": 123.0
                    },
                    "alarms": {
                        "alarm1": False,
                        "alarm2": False,
                        "cor_active": False,
                        "vswr_fault": False,
                        "low_fwd_power": False,
                        "pa_overtemp": False,
                        "pll_unlock": False
                    },
                    "schedule_config": {
                        "wake_hour_utc": 0,
                        "duration_min": 60,
                        "holdoff_min": 10
                    }
                }
            },
            "command_queue": [],
            "packet_log": [],
            "rmenu_history": []
        }
        self.load_state()

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r') as f:
                    saved = json.load(f)
                    if "nodes" in saved:
                        self.state["nodes"] = saved["nodes"]
                    if "command_queue" in saved:
                        self.state["command_queue"] = saved["command_queue"]
                    if "packet_log" in saved:
                        self.state["packet_log"] = saved["packet_log"]
                    if "rmenu_history" in saved:
                        self.state["rmenu_history"] = saved["rmenu_history"]
            except Exception as e:
                print("[DAEMON] Error loading state: %s" % e)

    def save_state(self):
        now = time.time()
        self.state["gateway"]["uptime_sec"] = int(time.time())
        self.state["gateway"]["utc_time"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now))
        
        # Update seconds_ago for all nodes
        for nid, node in self.state["nodes"].items():
            if node["last_seen_epoch"] > 0:
                node["seconds_ago"] = int(now - node["last_seen_epoch"])
            else:
                node["seconds_ago"] = -1

        tmp_path = STATE_FILE + ".tmp"
        try:
            with open(tmp_path, 'w') as f:
                json.dump(self.state, f, indent=2)
            os.rename(tmp_path, STATE_FILE)
        except Exception as e:
            print("[DAEMON] Error saving state: %s" % e)

    def log_packet(self, direction, hex_str, summary, rssi=None, snr=None):
        entry = {
            "time": time.strftime("%H:%M:%S", time.gmtime()),
            "dir": direction, # "TX" or "RX"
            "hex": hex_str,
            "summary": summary,
            "rssi": rssi,
            "snr": snr
        }
        self.state["packet_log"].insert(0, entry)
        if len(self.state["packet_log"]) > LOG_MAX_ENTRIES:
            self.state["packet_log"] = self.state["packet_log"][:LOG_MAX_ENTRIES]

    def open_serial(self):
        print("[DAEMON] Opening %s at 115200..." % SERIAL_PORT)
        self.fd = os.open(SERIAL_PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(self.fd)
        attrs[0] = 0 # iflag
        attrs[1] = 0 # oflag
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL # cflag
        attrs[3] = 0 # lflag
        attrs[4] = SERIAL_BAUD # ispeed
        attrs[5] = SERIAL_BAUD # ospeed
        termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        termios.tcflush(self.fd, termios.TCIOFLUSH)
        print("[DAEMON] Serial port ready.")

    def check_cmd_queue(self, node_id):
        # Check if external API deposited commands in CMD_QUEUE_FILE
        if os.path.exists(CMD_QUEUE_FILE):
            try:
                with open(CMD_QUEUE_FILE, 'r') as f:
                    new_cmds = json.load(f)
                if new_cmds:
                    self.state["command_queue"].extend(new_cmds)
                os.remove(CMD_QUEUE_FILE)
            except Exception as e:
                print("[DAEMON] Error reading cmd queue: %s" % e)

        # Find pending commands for this node
        pending = [cmd for cmd in self.state["command_queue"] if cmd.get("node_id") == node_id and cmd.get("status") == "QUEUED"]
        return pending

    def build_ack_packet(self, node_id, flags=0):
        self.ack_seq += 1
        # Header (7B): Magic 'MX', NodeID=0x00, MsgType=0x02 (ACK), Seq(2B), PowerState=0
        hdr = struct.pack('<2sBBHB', 'MX', 0x00, 0x02, self.ack_seq, 0x00)
        
        # Pending commands check
        pending = self.check_cmd_queue(node_id)
        pending_count = len(pending)
        
        node_str = str(node_id)
        maint_lock = self.state["nodes"].get(node_str, {}).get("maintenance_lock", False)
        if maint_lock:
            flags |= (1 << 0) # Bit 0: stay awake lock active

        # Payload (8B): epoch_utc (4B), wake_window_sec (2B), pending_cmds (1B), flags (1B)
        epoch = int(time.time())
        payload = struct.pack('<IHBB', epoch, 3600, pending_count, flags)
        
        # Trailer (6B): AuthTag (4B) + CRC16 (2B)
        auth = b'\xaa\xbb\xcc\xdd'
        body = hdr + payload + auth
        crc = crc16_ccitt(body)
        trailer = auth + struct.pack('<H', crc)
        
        return hdr + payload + trailer

    def send_tx_packet(self, pkt_bytes, summary=""):
        hex_data = "".join(["%02X" % ord(b) for b in pkt_bytes])
        cmd = "+TX:%s\r\n" % hex_data
        try:
            os.write(self.fd, cmd)
            self.log_packet("TX", hex_data, summary)
        except Exception as e:
            print("[DAEMON] Error writing serial TX: %s" % e)

    def handle_incoming_lora(self, rssi, snr, length, hex_str):
        if len(hex_str) % 2 != 0:
            return
        
        try:
            raw_bytes = bytearray.fromhex(hex_str)
        except Exception:
            return

        # Minimum valid packet is Header (7) + Trailer (6) = 13 bytes
        if len(raw_bytes) < 13:
            return

        # Verify Magic bytes
        if raw_bytes[0] != 0x4D or raw_bytes[1] != 0x58:
            return

        # Verify CRC-16
        body = raw_bytes[:-2]
        expected_crc = struct.unpack('<H', bytes(raw_bytes[-2:]))[0]
        calc_crc = crc16_ccitt(body)
        
        node_id = raw_bytes[2]
        msg_type = raw_bytes[3]
        seq = struct.unpack('<H', bytes(raw_bytes[4:6]))[0]
        power_state = raw_bytes[6]

        node_str = str(node_id)
        if node_str not in self.state["nodes"]:
            # Auto-register node if unknown
            self.state["nodes"][node_str] = {
                "node_id": node_id,
                "name": NODE_NAMES.get(node_id, "Unknown Node %d" % node_id),
                "last_seen_epoch": 0,
                "link": {"packet_count": 0, "crc_errors": 0},
                "telemetry": {},
                "alarms": {},
                "schedule_config": {"wake_hour_utc": 0, "duration_min": 60, "holdoff_min": 10}
            }

        node = self.state["nodes"][node_str]
        node["link"]["packet_count"] += 1
        node["link"]["rssi_dbm"] = float(rssi)
        node["link"]["snr_db"] = float(snr)

        if expected_crc != calc_crc:
            node["link"]["crc_errors"] += 1
            self.log_packet("RX", hex_str, "CRC ERROR (Calc 0x%04X != Exp 0x%04X)" % (calc_crc, expected_crc), rssi, snr)
            return

        # Update last seen
        node["last_seen_epoch"] = int(time.time())
        node["last_seen_str"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(node["last_seen_epoch"]))
        node["power_state_raw"] = power_state

        # Decode power state flags
        node["scheduled_window"] = bool(power_state & (1 << 0))
        node["activity_hold"] = bool(power_state & (1 << 1))
        node["maintenance_lock"] = bool(power_state & (1 << 2))
        node["repeater_inhibited"] = bool(power_state & (1 << 4))
        node["vext_enabled"] = bool(power_state & (1 << 5))

        if node["maintenance_lock"]:
            node["power_state_desc"] = "MAINTENANCE LOCK (STAY AWAKE)"
        elif node["activity_hold"]:
            node["power_state_desc"] = "AWAKE - ACTIVITY HOLD"
        elif node["scheduled_window"]:
            node["power_state_desc"] = "AWAKE - SCHEDULED WINDOW"
        else:
            node["power_state_desc"] = "AWAKE (INTERRUPT/UNSCHEDULED)"

        # Parse message types
        summary = "Node %d MsgType=0x%02X Seq=%d" % (node_id, msg_type, seq)

        if msg_type == 0x01: # PING / HEARTBEAT
            # Payload begins at index 7: vbat(2B), temp(2B), analog_rssi(2B), alarm_flags(1B), active_channel(1B)
            if len(raw_bytes) >= 15:
                vbat, temp_x10, raw_rssi, alarm_flags, ch = struct.unpack('<HhHBB', bytes(raw_bytes[7:15]))
                node["telemetry"]["vbat_mv"] = vbat
                node["telemetry"]["temp_c"] = temp_x10 / 10.0
                node["telemetry"]["analog_rssi_raw"] = raw_rssi
                scaled_dbm = -120.0 + (float(raw_rssi) / 4095.0) * 70.0
                node["telemetry"]["analog_rssi_dbm"] = round(scaled_dbm, 1)
                node["telemetry"]["active_channel"] = ch
                node["alarms"]["alarm1"] = bool(alarm_flags & (1 << 0))
                node["alarms"]["alarm2"] = bool(alarm_flags & (1 << 1))
                node["alarms"]["cor_active"] = bool(alarm_flags & (1 << 2))
                
                # Extract any optional embedded ASCII message (between telemetry and trailer)
                beacon_msg = ""
                if len(raw_bytes) > 21: # More than header(7) + telem(8) + trailer(6)
                    try:
                        beacon_msg = bytes(raw_bytes[15:-6]).decode('ascii', errors='replace')
                        node["beacon_msg"] = beacon_msg
                    except Exception:
                        beacon_msg = ""

                if beacon_msg:
                    summary = "PING #%d: \"%s\" (Ch %d, Batt %d mV)" % (seq, beacon_msg, ch, vbat)
                else:
                    summary = "PING #%d: Ch %d, Batt %d mV, RSSI %s dBm" % (seq, ch, vbat, node["telemetry"]["analog_rssi_dbm"])

            # Send immediate ACK with time synchronization
            ack_pkt = self.build_ack_packet(node_id)
            ack_sum = "ACK #%d -> Node %d (UTC epoch sync)" % (self.ack_seq, node_id)
            self.send_tx_packet(ack_pkt, ack_sum)

        elif msg_type == 0x06: # RMENU RESPONSE
            resp_str = bytes(raw_bytes[7:-6]).decode('ascii', errors='replace')
            summary = "RMENU RESP: %s" % resp_str
            self.state["rmenu_history"].insert(0, {
                "time": time.strftime("%H:%M:%S", time.gmtime()),
                "dir": "RESP",
                "text": resp_str
            })

        self.log_packet("RX", hex_str, summary, rssi, snr)
        self.save_state()

    def run(self):
        self.open_serial()
        print("[DAEMON] Mawson MX921 LoRa Gateway daemon running...")
        
        serial_buf = ""
        last_periodic_check = time.time()

        while True:
            now = time.time()
            if now - last_periodic_check >= 2.0:
                last_periodic_check = now
                self.save_state()

            r, _, _ = select.select([self.fd], [], [], 1.0)
            if r:
                try:
                    chunk = os.read(self.fd, 256)
                    if chunk:
                        serial_buf += chunk
                        while '\n' in serial_buf:
                            line, serial_buf = serial_buf.split('\n', 1)
                            line = line.strip()
                            if not line:
                                continue
                            if line.startswith('+RX:'):
                                # Format: +RX:<RSSI>,<SNR>,<LEN>,<HEX>
                                parts = line[4:].split(',')
                                if len(parts) >= 4:
                                    self.handle_incoming_lora(parts[0], parts[1], parts[2], parts[3])
                except OSError:
                    pass

if __name__ == '__main__':
    daemon = Mx921GatewayDaemon()
    daemon.run()

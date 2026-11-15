#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mawson MX921 LoRa Repeater Bridge — Gateway LoRa Test & ACK responder
Target: Duinotech XC-4394 OpenWrt
"""
import os
import sys
import termios
import time
import struct
import select

SERIAL_PORT = '/dev/ttyATH0'

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

def open_serial():
    fd = os.open(SERIAL_PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0 # iflag
    attrs[1] = 0 # oflag
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL # cflag
    attrs[3] = 0 # lflag
    attrs[4] = termios.B115200 # ispeed
    attrs[5] = termios.B115200 # ospeed
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd

def build_ack_packet(node_id, ack_seq):
    # Header: Magic(2B 'MX'), NodeID(1B=0x00 Gateway), MsgType(1B=0x02 ACK), Seq(2B), PowerState(1B=0)
    hdr = struct.pack('<2sBBHB', 'MX', 0x00, 0x02, ack_seq, 0x00)
    # Payload: epoch_utc(4B uint32), wake_window_sec(2B uint16=3600), pending_cmds(1B=0), flags(1B=0)
    epoch = int(time.time())
    payload = struct.pack('<IHBB', epoch, 3600, 0, 0)
    # Auth tag (4B)
    auth = b'\xaa\xbb\xcc\xdd'
    
    body = hdr + payload + auth
    crc = crc16_ccitt(body)
    trailer = auth + struct.pack('<H', crc)
    return hdr + payload + trailer

def run():
    print("[XC-4394] Opening /dev/ttyATH0 at 115200 baud...")
    fd = open_serial()
    print("[XC-4394] Listening for incoming LoRa packets from Heltec...")

    buf = ""
    start_time = time.time()
    ack_seq = 0

    while time.time() - start_time < 30:
        r, _, _ = select.select([fd], [], [], 1.0)
        if r:
            try:
                data = os.read(fd, 256)
                if data:
                    buf += data
                    while '\n' in buf:
                        line, buf = buf.split('\n', 1)
                        line = line.strip()
                        if not line:
                            continue
                        print("[XC-4394 RECV] " + line)
                        if line.startswith('+RX:'):
                            # Format: +RX:<RSSI>,<SNR>,<LEN>,<HEX>
                            parts = line[4:].split(',')
                            if len(parts) >= 4:
                                rssi, snr, pkt_len, hex_data = parts[0], parts[1], parts[2], parts[3]
                                print("  -> Node Ping Received! RSSI=%s dBm, SNR=%s dB, Len=%s" % (rssi, snr, pkt_len))
                                
                                # Send immediate ACK
                                ack_seq += 1
                                ack_pkt = build_ack_packet(0x01, ack_seq)
                                ack_hex = "".join(["%02X" % ord(b) for b in ack_pkt])
                                tx_cmd = "+TX:%s\r\n" % ack_hex
                                os.write(fd, tx_cmd)
                                print("  <- [TX ACK] Seq=%d, Payload=%s" % (ack_seq, ack_hex))
            except OSError:
                pass

    os.close(fd)
    print("[XC-4394] Test complete.")

if __name__ == '__main__':
    run()

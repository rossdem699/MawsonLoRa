# Mawson MX921 Transparent RS-232-over-LoRa Wireless Bridge

[![Platform](https://img.shields.io/badge/Platform-ESP32--S3%20%7C%20OpenWrt-blue.svg)](https://github.com/rossdem699/MawsonLoRa)
[![RF](https://img.shields.io/badge/LoRa-916.8%20MHz%20SF9%2FSF11-emerald.svg)](https://github.com/rossdem699/MawsonLoRa)
[![Baud](https://img.shields.io/badge/Serial-57600%20baud%208--N--1-purple.svg)](https://github.com/rossdem699/MawsonLoRa)

A bidirectional 915 MHz transparent RS-232-over-LoRa serial bridge connecting a **Spectra Engineering MX921 repeater's front RJ45 `MIC` port** (Phase 1: Mawson Comms-room bench testing $\rightarrow$ Mount Henderson ~13 km SSE, extensible to Mount Parsons) to a **Duinotech XC-4394 OpenWrt LoRa gateway** over Mawson Station Ethernet.

Provides:
- **Virtual Serial Port (TCP Port 9001):** Transparent pipe for Spectra `900Tools` and `PuTTY` / `RMENU` via `socat` on Linux Mint (`/dev/ttyV0`) or `com0com`/`HW VSP3` on Windows.
- **Hayes-style `+++` AT Command Control:** Local command interception on Port 9001 (`AT+STATUS?`, `AT+AWAKE=1|0`, `AT+SF=9|11`, `AT+SCHED=HH,DUR`).
- **Lightweight OpenWrt Web Control Bar (`http://<gateway-ip>/`):** Single compact responsive status & control bar.
- **Remote Spreading Factor Switching with Dead-Man Fallback:** Default **`SF9`** (~120 ms airtime, <500 ms RTT for `900Tools`) with one-click remote switch to **`SF11`** ("Storm Mode" for extreme Antarctic blizzards) and an automatic **60-second dead-man fallback**.
- **Scheduled Deep-Sleep Power Management:** Switched `Vext` (`GPIO 36`, active LOW) completely cuts power to the MAX3232 transceiver during deep sleep (~1 mA), with scheduled daily wake windows, 10-minute activity watchdog, and persistent NVS Maintenance Lock ("Stay Awake").

---

## 1. System Architecture

```text
[Spectra MX921 Repeater]
  └─ Front RJ45 MIC Port (Pin 4 RXD, Pin 5 TXD, Pin 7 GND @ 57,600 baud)
       │
[MAX3232 3.3V Transceiver] (Powered via switched Vext GPIO 36)
       │ (UART1 TX=48, RX=47)
[Heltec Wireless Stick Lite V3] (ESP32-S3FN8 + SX1262 @ 916.8 MHz)
       │
       ▼ (915 MHz LoRa Link: SF9 Normal / SF11 Storm, 125 kHz BW, CR 4/5)
       │
[Duinotech XC-4394 Base Gateway]
  ├── ATmega328P + SX1276 Bridge MCU (LoRa <-> /dev/ttyATH0 @ 115200 baud)
  └── Atheros AR9331 (OpenWrt Chaos Calmer, 64 MB RAM)
        ├── TCP Port 9001: Virtual COM Bridge & +++ AT Parser
        └── uhttpd Web Server: Lightweight Web Control Bar & CGI REST API
               │
               ▼ (Mawson Station Ethernet LAN / Wi-Fi)
[Operator Laptops (Linux Mint / Windows)]
  ├── Spectra 900Tools (Virtual COM /dev/ttyV0 or COM3)
  └── Web Browser (http://10.130.1.1/)
```

---

## 2. Hardware Interfaces & Pinout

### Remote Repeater Node (`node_mx921/`)
- **Microcontroller:** Heltec Wireless Stick Lite V3 (`HTIT-WB32LAF`, ESP32-S3FN8).
- **LoRa Transceiver:** Semtech SX1262 (Internal SPI: `NSS=8`, `SCK=9`, `MOSI=10`, `MISO=11`, `RST=12`, `BUSY=13`, `DIO1=14`).
- **Power Rail Switching:** `Vext` (`GPIO 36`, active LOW) powers the MAX3232 during wake windows and cuts it completely during deep sleep.
- **External Serial:** `UART1` (`TX=GPIO 48`, `RX=GPIO 47`) at `57600 baud` (8-N-1). Uses accessible through-hole header pins 48 & 47.
- **Repeater Front RJ45 MIC Pinout (Strictly 3-Wire Interface):**
  - `RJ45 Pin 4` (MX921 `RXD`) $\leftrightarrow$ MAX3232 `T1OUT`
  - `RJ45 Pin 5` (MX921 `TXD`) $\leftrightarrow$ MAX3232 `R1IN`
  - `RJ45 Pin 7` (`GND`) $\leftrightarrow$ MAX3232 `GND`
  - *(Pins 1, 2, 3, 6, 8 left completely disconnected to isolate +12V mic bias, PTT, and audio lines).*

### FreeRTOS Dual-Core Firmware Architecture
- **`RadioTask` (Core 0):** Manages SX1262 via `RadioLib`, handles `DIO1` packet RX interrupts, validates CCITT CRC-16 and 4-byte auth tags on control packets, executes synchronized SF switching with 60s dead-man fallback, and transmits queued serial frames.
- **`UartBridgeTask` (Core 1):** Reads/writes `UART1` (57,600 baud) with **15 ms idle-gap packet coalescing** or **180-byte max payload flush** so `900Tools` requests and `RMENU` screens transmit cleanly without byte fragmentation.
- **`PowerManagerTask` (Core 1):** Tracks `last_activity_ms`, syncs UTC epoch from gateway ACKs, manages 10-minute activity hold-off, NVS `stay_awake_lock`, and enters deep sleep (~1 mA) between scheduled windows.

### Mawson Base Gateway (`gateway_mcu/` & `gateway_openwrt/`)
- **Gateway Hardware:** Duinotech XC-4394 (Atheros AR9331 + ATmega328P + SX1276).
- **ATmega328P Bridge MCU:** Minimal `arduino-LoRa` sketch passing framed packets between SX1276 SPI and `/dev/ttyATH0` (`115200 baud`), executing local `+SF:<9|11>` commands. Memory footprint is strictly **706 bytes SRAM (34.5%)**.
- **OpenWrt Daemon (`mx921_daemon.py`):**
  - Listens on **`TCP Port 9001`** for incoming virtual serial connections.
  - Intercepts Hayes-style `+++` AT commands or forwards transparent serial frames.
  - Manages synchronized SF switching and NTP epoch time synchronization.
  - Maintains atomic state file `/tmp/mx921_state.json`.

---

## 3. Radio Settings & Packet Framing Layer

| Parameter | Value | Details |
| :--- | :--- | :--- |
| **Frequency** | `916.8 MHz` | AU915 ISM Band |
| **Bandwidth** | `125 kHz` | Standard narrowband LoRa |
| **Spreading Factor (Default)** | **`SF9`** | ~120 ms air-time, <500 ms RTT for Spectra `900Tools` |
| **Spreading Factor (Storm)** | **`SF11`** | Storm Mode (extreme Antarctic weather / blizzard link margin) |
| **Fallback Watchdog** | **`60 s`** | Automatically reverts node to `SF9` if no valid packets received on new SF |
| **Coding Rate** | `4/5` | Robust forward error correction |
| **Sync Word** | `0x12` | Private network identifier |
| **CRC** | Enabled | CCITT CRC-16 hardware verification |

### Packet Structure
```text
[Magic 2B][NodeID 1B][Type 1B][Seq 2B][Flags 1B][Payload <=180B][CRC16 2B]
```
- **Magic:** `0x4D 0x58` ("MX")
- **Node IDs:** `0x00` = Base Gateway, `0x01` = Mount Henderson (~13 km SSE), `0x02` = Mount Parsons
- **Packet Types:** `0x01` DATA (Transparent Serial), `0x02` CTRL (Command), `0x03` ACK (Telemetry Sync), `0x04` PING

---

## 4. Virtual COM Port Quick-Connect

Connect your laptop to the Duinotech XC-4394 via Ethernet or Wi-Fi (`duinotech-18094a` at `10.130.1.1`):

### Linux Mint (`socat`)
```bash
# Create local virtual serial port /dev/ttyV0 mapped to TCP Port 9001
socat pty,link=/dev/ttyV0,raw,echo=0 tcp:10.130.1.1:9001 &

# Open minicom or launch Spectra 900Tools targeting /dev/ttyV0 at 57600 baud
minicom -D /dev/ttyV0 -b 57600
```

### Windows Laptop
1. Open **HW VSP3** (HW Virtual Serial Port) or **com0com**.
2. Configure TCP Client connection:
   - **IP Address:** `10.130.1.1`
   - **Port:** `9001`
   - **Virtual COM Port:** `COM3`
3. Launch Spectra `900Tools` or `PuTTY` (Serial `COM3`, 57600 baud, 8-N-1).

### Hayes-style `+++` AT Commands (on Port 9001)
- `AT+STATUS?` — Prints current RF metrics, RSSI, SNR, battery, SF, and sleep state.
- `AT+AWAKE=1` / `AT+AWAKE=0` — Enable/disable 24/7 Stay Awake Maintenance Lock.
- `AT+SF=9` / `AT+SF=11` — Remotely switch spreading factor (with 60s fallback).
- `AT+SCHED=HH,DUR` — Update daily wake hour (UTC) and duration (min).

---

## 5. Lightweight Web Control Bar (`http://10.130.1.1/`)

Served directly by `uhttpd` on the OpenWrt gateway:
- **Node Selector:** Switch between `Node 1: Mount Henderson` and `Node 2: Mount Parsons`.
- **Live State Badge:** Real-time visual status (`AWAKE - SCHEDULED`, `AWAKE - IDLE HOLD (mm:ss)`, `MAINTENANCE LOCK`, `SLEEPING - NEXT WAKE HH:MM`).
- **LoRa Link Metrics:** Real-time RSSI (dBm), SNR (dB), and visual signal strength bar.
- **One-Click Stay Awake Toggle:** Instantly toggle Maintenance Lock ON/OFF.
- **One-Click SF Switcher:** Switch between `SF9 Normal` and `SF11 Storm Mode`.
- **Daily Wake Schedule Form:** Adjust wake hour, window duration, and inactivity hold-off.
- **Live Packet Log:** Rolling table of recent LoRa packets with timestamps and raw hex preview.

---

## 6. Build & Deployment Instructions

### Compile Remote Node Firmware (Heltec WSL V3)
```bash
cd node_mx921
pio run
# To flash:
pio run -t upload
```

### Compile Gateway MCU Firmware (ATmega328P)
```bash
cd gateway_mcu
pio run
# To flash via Duinotech internal AVR programmer:
/usr/bin/run-avrdude /root/gateway_mcu.hex
```

### Deploy OpenWrt Gateway Components
```bash
# Copy files to Duinotech XC-4394:
scp gateway_openwrt/mx921_daemon.py root@10.130.1.1:/root/
scp gateway_openwrt/mx921_api.py root@10.130.1.1:/www/cgi-bin/
scp -r gateway_openwrt/www/* root@10.130.1.1:/www/
ssh root@10.130.1.1 "/etc/init.d/mx921_daemon restart"
```

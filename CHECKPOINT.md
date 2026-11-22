# Mawson MX921 LoRa Repeater Bridge — Session Checkpoint

**Checkpoint Date:** 20 November 2026  
**Status:** SPEC.md Updated & Entire Codebase Refactored to Full Transparent RS-232-over-LoRa Spec (**100% BUILD VERIFIED**).

---

## 1. Architecture Alignment with SPEC.md

| Subsystem | Target Hardware | Role & Architecture | Status |
| :--- | :--- | :--- | :--- |
| **Remote Node** | Heltec Wireless Stick Lite V3 (`ESP32-S3FN8` + `SX1262`) | **FreeRTOS Dual-Core Firmware (`node_mx921/`):**<br>• Core 0: `RadioTask` (SX1262 RadioLib, DIO1 RX interrupts, CRC16 + 4B HMAC auth tag validation, SF9/SF11 switching with 60s dead-man fallback)<br>• Core 1: `UartBridgeTask` (`UART1` TX=48, RX=47 @ 57600 baud, 15ms idle-gap packet coalescing / 180B flush to front RJ45 MIC socket)<br>• Core 1: `PowerManagerTask` (10-min activity holdoff watchdog, scheduled RTC wake, deep sleep ~1 mA with switched `Vext` GPIO 36) | **Compiled & Verified:**<br>RAM: 7.2% (23,480 B)<br>Flash: 11.8% (394,605 B) |
| **Gateway MCU Bridge** | Duinotech XC-4394 (`ATmega328P` + `SX1276`) | Minimal `arduino-LoRa` bridge sketch passing framed packets between SX1276 SPI and `/dev/ttyATH0` (`115200 baud`), executing local `+SF:<9\|11>` commands. | **Compiled & Verified:**<br>RAM: 34.5% (706 B)<br>Flash: 20.2% (6,528 B) |
| **Gateway Daemon & TCP Bridge** | Duinotech XC-4394 (`Atheros AR9331` OpenWrt Chaos Calmer) | **Python 2.7/3 Daemon (`mx921_daemon.py`):**<br>• Listens on `TCP Port 9001` for virtual COM clients (`socat`, `HW VSP3`, `com0com`, `PuTTY`)<br>• Intercepts Hayes `+++` AT commands (`AT+STATUS?`, `AT+AWAKE=1\|0`, `AT+SF=9\|11`, `AT+SCHED=HH,DUR`)<br>• Frames transparent serial data (<=180B) with 15ms idle-gap coalescing<br>• Synchronized SF switching & NTP epoch time injection | **Tested & Verified:**<br>TCP Port 9001 server & AT command parser operational |
| **Lightweight Web Control Bar** | Duinotech XC-4394 (`uhttpd`) | **Responsive Single-Bar Control Dashboard (`gateway_openwrt/www/`):**<br>• Target Node Selector (`Node 1: Mt Henderson` / `Node 2: Mt Parsons`)<br>• Live State Badge (`AWAKE - SCHEDULED`, `AWAKE - IDLE HOLD`, `MAINTENANCE LOCK`, `SLEEPING`)<br>• LoRa link RSSI / SNR & current Spreading Factor (`SF9` / `SF11`)<br>• One-click **"Stay Awake"** toggle switch<br>• One-click **SF selector** (`SF9 Normal` / `SF11 Storm`)<br>• Daily Wake Schedule inputs (Hour, Duration, Hold-off)<br>• Virtual COM Quick-Connect instructions & Live Packet Log | **Tested & Verified:**<br>Fast asynchronous polling & dynamic IP detection |

---

## 2. Key Files & Locations

- **Specification:** [`SPEC.md`](./SPEC.md)
- **Shared Packet Definitions:** [`shared/mx921_packet.h`](./shared/mx921_packet.h)
- **Heltec WSL V3 Node Firmware:** [`node_mx921/src/main.cpp`](./node_mx921/src/main.cpp)
- **Gateway MCU Bridge Firmware:** [`gateway_mcu/src/main.cpp`](./gateway_mcu/src/main.cpp) & [`gateway_mcu/gateway_mcu.ino`](./gateway_mcu/gateway_mcu.ino)
- **Gateway OpenWrt Daemon:** [`gateway_openwrt/mx921_daemon.py`](./gateway_openwrt/mx921_daemon.py)
- **Gateway CGI REST API:** [`gateway_openwrt/mx921_api.py`](./gateway_openwrt/mx921_api.py)
- **Web Control Bar Assets:**
  - HTML: [`gateway_openwrt/www/index.html`](./gateway_openwrt/www/index.html)
  - CSS: [`gateway_openwrt/www/style.css`](./gateway_openwrt/www/style.css)
  - JS: [`gateway_openwrt/www/app.js`](./gateway_openwrt/www/app.js)
- **Project Documentation:** [`README.md`](./README.md)

---

## 3. MX921 Front RJ45 MIC Socket Wiring Reference

Strictly 3-wire RS-232 interface at 57,600 baud, 8-N-1:
```text
RJ45 Pin 4 (MX921 RXD) <---> MAX3232 T1OUT
RJ45 Pin 5 (MX921 TXD) <---> MAX3232 R1IN
RJ45 Pin 7 (GND)       <---> MAX3232 GND

Pins 1, 2, 3, 6, 8 are left completely disconnected (isolates +12V mic bias, PTT, audio lines).
MAX3232 VCC/GND is powered via Heltec WSL V3 switched Vext (GPIO 36, active LOW) and GND.
```

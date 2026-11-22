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

## 3. MX921 Front RJ45 MIC Socket Wiring Reference (VERIFIED)

Official Spectra Engineering MX921 Technical Manual pinout (T568B wire colours):
```text
RJ45 Pin 5 (GND)       <---> MAX232 Pin 15 (GND)    [White/Blue stripe]
RJ45 Pin 7 (Radio TXD) <---> MAX232 Pin 13 (R1IN)   [Solid Brown]
RJ45 Pin 8 (Radio RXD) <---> MAX232 Pin 14 (T1OUT)  [White/Brown stripe]

Pins 1, 2, 3, 4, 6 are completely disconnected and isolated (protects against +8V mic bias, PTT, audio).
Heltec WSL V3: GPIO 48 (TX) -> MAX232 Pin 11 (T1IN); GPIO 47 (RX) <- 10k resistor <- MAX232 Pin 12 (R1OUT).
```

---

## 4. Hardware Bench Verification Summary (22 Nov 2026)

- **RF Link:** 916.8 MHz LoRa link fully active between Gateway (SX1276) and Heltec WSL V3 (SX1262). RSSI -61 dBm, SNR +9 to +14 dB at SF9.
- **Electrical Verification:** MAX232 voltage rails (+9.4V V+, -8.6V V-, -8.42V T1OUT) and physical loopback (Pins 7 & 8) confirmed with multimeter.
- **Live Repeater Interrogation:** Connected directly to MX921 front MIC socket at 57,600 baud, 8-N-1. Command `RMENU` transmitted over LoRa, successfully returned:
  - **Repeater Model:** `MX921D3D3H`
  - **Serial Number:** `91D300004`
  - **Firmware Version:** `0.2.22`
  - Interactive menus (`Configuration Menu`, `Channel Edit Menu`, `Alignment Menu`) fully responsive over LoRa!

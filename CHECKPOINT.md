# Mawson MX921 LoRa Repeater Bridge — Session Checkpoint

**Checkpoint Date:** 15 November 2026  
**Status:** Phase 1 (Bench RF Link) & Phase 3 (OpenWrt Gateway Daemon & 4-Tab Web UI) **100% COMPLETE & HARDWARE VERIFIED**.  
**Next Objective:** Phase 2 (MX921 RS-232 Interface, RMENU Parser State Machine, and Deep Sleep Task).

---

## 1. Verified Working Hardware & Link

| Subsystem | Hardware | Configuration / Pins | Live Status |
| :--- | :--- | :--- | :--- |
| **Remote Node** | Heltec Wireless Stick Lite V3 (`ESP32-S3FN8` + `SX1262`) | `916.8 MHz`, `BW 125 kHz`, `SF10`, `CR 4/5`, `Sync 0x12`, `2 dBm` bench power | **Flashing & Transmitting:** 5-second heartbeats broadcasting `"Hello from Mt Henderson Repeater!"` |
| **Gateway Bridge** | Duinotech XC-4394 MCU (`ATmega328P` + `SX1276`) | Transparent UART-to-LoRa bridge on `/dev/ttyATH0` at `115200` baud | **Flashed & Active:** Consumes only 427 bytes SRAM (20.8%). |
| **Gateway OS & UI** | Duinotech XC-4394 (`Atheros AR9331` OpenWrt Chaos Calmer) | Auto-start daemon `/root/mx921_daemon.py`, CGI REST API, 4-Tab Dashboard `/www/mx921/` | **Live & Serving:** Bridge URL `http://10.130.1.1/mx921/`. |

---

## 2. Reconnecting After Power Cycle

Both devices are configured with persistent storage in flash memory. When powering off and resuming:

1. **Power Heltec:** Connect 5V USB-C cable (PC or USB power pack).
2. **Power Duinotech:** Plug in 12V DC adapter. Wait ~30 seconds until the green Heartbeat LED pulses.
3. **Access Dashboard from ANY device (PC, Laptop, Tablet, Phone):**
   - **Option A (Wi-Fi):** Connect to open Wi-Fi network **`duinotech-18094a`**, open browser to:  
     👉 **`http://10.130.1.1/mx921/`** (or `http://192.168.240.1/mx921/`)
   - **Option B (Ethernet):** Plug RJ45 cable into Duinotech **LAN** port, open browser to:  
     👉 **`http://10.130.1.1/mx921/`**
4. **SSH Access (Host alias pre-configured on this development laptop):**
   ```bash
   ssh duinotech
   ```
   *(Root password: `duinotech`, passwordless RSA key is installed)*

---

## 3. Key Project Files & Code Locations

- **Shared Packet Definitions:** [`shared/mx921_packet.h`](./shared/mx921_packet.h)
- **Gateway MCU Firmware:** [`gateway_mcu/src/main.cpp`](./gateway_mcu/src/main.cpp) (Hex: `gateway_mcu/.pio/build/uno/firmware.hex`)
- **Remote Node Firmware:** [`node_mx921/src/main.cpp`](./node_mx921/src/main.cpp) (Bin: `node_mx921/.pio/build/heltec_wireless_stick_lite_v3/firmware.bin`)
- **Gateway Daemon:** [`gateway_openwrt/mx921_daemon.py`](./gateway_openwrt/mx921_daemon.py) (Deployed at `/root/mx921_daemon.py`)
- **Daemon Init Script:** [`gateway_openwrt/init_mx921_daemon`](./gateway_openwrt/init_mx921_daemon) (Deployed at `/etc/init.d/mx921_daemon`)
- **CGI REST API:** [`gateway_openwrt/mx921_api.py`](./gateway_openwrt/mx921_api.py) (Deployed at `/www/cgi-bin/mx921_api`)
- **4-Tab Web Dashboard:**
  - HTML: [`gateway_openwrt/www/index.html`](./gateway_openwrt/www/index.html)
  - CSS: [`gateway_openwrt/www/style.css`](./gateway_openwrt/www/style.css)
  - JS: [`gateway_openwrt/www/app.js`](./gateway_openwrt/www/app.js)
  - *(Deployed at `/www/mx921/` on the Duinotech)*
- **Master Plan & Spec:** [`MawsonLoRaRepeaterESP32.md`](./MawsonLoRaRepeaterESP32.md) & [`WORKFLOW.md`](./WORKFLOW.md)

---

## 4. Next Step When Resuming: Phase 2 (MX921 Hardware & Serial Interface)

When resuming this project, proceed directly with **Phase 2**:
1. **Task 2.1 — Wiring the Physical Interface Harness:**
   - `Vext` (`GPIO 36`, active LOW) $\rightarrow$ Switched 3.3V rail powering `MAX3232` RS-232 module and optos.
   - `UART1` (`TX=17`, `RX=18`) $\rightarrow$ `MAX3232` $\rightarrow$ MX921 rear `CN4` DB9-M (Pin 2 RX, Pin 3 TX, Pin 5 GND).
   - `CN3` (DB25-F): `10 kΩ / 18 kΩ` divider to `GPIO 1` (analog RSSI), optos to `GPIO 5/6` (Alarms 1/2) and `GPIO 7` (COR), and `GPIO 4` (Repeater Inhibit output).
2. **Task 2.2 — `Mx921SerialTask` (FreeRTOS `RMENU` Parser):**
   - Implement the non-blocking state machine on the Heltec ESP32-S3 to send/receive MX921 ASCII `RMENU` commands (`STATUS`, `CHAN`, `FREQ`, `TONE`, `BATT`).
3. **Task 2.3 — `PowerAndHwTask` (Sleep & Wake State Machine):**
   - Implement true deep-sleep (~1 mA), RTC scheduled wakeup, 10-minute activity hold-off countdown, NVS `stay_awake_lock`, and `EXT1` emergency wake on `GPIO 5/6` hardware alarms.

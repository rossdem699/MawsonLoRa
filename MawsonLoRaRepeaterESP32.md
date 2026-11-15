# SPEC.md — Mawson MX921 Repeater LoRa Telemetry & Remote Programming Bridge

## Build Spec

- **Goal:** Build a bidirectional 915 MHz LoRa telemetry, alarm, and full remote-programming bridge between a Spectra Engineering MX921 repeater (Phase 1: Mount Henderson, extensible to Mount Parsons) and a tabbed local web dashboard hosted on a Duinotech XC-4394 OpenWrt LoRa gateway at Mawson Station, featuring intelligent deep-sleep scheduling with activity hold-off and web-controlled maintenance lock.

- **Users / Environment:**
  - **Operators:** Communications Technical Officers at Mawson Station, Antarctica.
  - **Phase 1 (Bench Test):** Local bench testing on the Mawson comms-room MX921 units using 5 V USB-C power before field deployment to Mount Henderson (~13 km SSE).
  - **Remote Repeater Node Hardware:** Heltec Wireless Stick Lite V3 (`HTIT-WB32LAF`, ESP32-S3FN8 + SX1262, 863–928 MHz HF) running FreeRTOS (PlatformIO / Arduino-ESP32 or ESP-IDF).
  - **Repeater Electrical Interface:**
    - **RS-232 Serial:** MAX3232 3.3 V transceiver (powered via switched `Vext` GPIO 36 so it shuts down completely during deep sleep) wired to MX921 rear DB9-M `CN4` (Pin 2 RXD, Pin 3 TXD, Pin 5 GND — 9600 baud default, configurable to 57600 baud for front RJ45 `MIC` test cable).
    - **Parallel Hardware I/O (`CN3` DB25-F):** Optocoupled GPIO output for hardware repeater knockdown/inhibit; optocoupled RTC-GPIO inputs for open-collector `Alarm #1` / `Alarm #2` and `COR/Mute`; resistive voltage divider (`10 kΩ` / `18 kΩ`) scaling `0–5 V` analog RSSI to `0–3.3 V` ADC.
  - **Base Gateway Hardware:** Duinotech XC-4394 (Atheros AR9331 OpenWrt + ATmega328P + SX1276 915 MHz) connected to Mawson Station LAN via RJ45 Ethernet, powered by 12 V DC.

- **Must Have:**
  1. **Heltec Wireless Stick Lite V3 FreeRTOS Firmware (`node_mx921/`):**
     - **Pinout (Fixed Heltec WSL V3 Internal SPI):** SX1262 `NSS=8`, `SCK=9`, `MOSI=10`, `MISO=11`, `RST=12`, `BUSY=13`, `DIO1=14`; `Vext_Ctrl=36` (LOW = ON, HIGH = OFF before sleep); User LED=`35`.
     - **External Interface Pins (All RTC-GPIO capable for deep-sleep wakeup/hold):** `UART1` (`TX=17`, `RX=18`) -> MAX3232; `ADC1_CH0` (`GPIO 1`) -> Scaled RSSI (`0–3.3 V`); `GPIO 4` -> Opto Repeater Inhibit Out (RTC GPIO hold enabled during sleep); `GPIO 5` & `GPIO 6` -> Opto Alarm 1 & Alarm 2 In (`EXT1` wakeup); `GPIO 7` -> Opto COR/Mute In.
     - **FreeRTOS Task Architecture:**
       - `RadioTask` (Core 0): Manages SX1262 via `RadioLib`, handles `DIO1` packet RX interrupts, validates HMAC-SHA256 truncated auth tags + CRC-16, and sends queued telemetry/ACK frames.
       - `Mx921SerialTask` (Core 1): State-machine parser for MX921 `RMENU` ASCII console (supports `9600` and `57600` baud, character pacing, timeout recovery) to read and write channel tables, frequencies, spacing (`12.5/25 kHz`), CTCSS/DCS tones, repeater enable state, and diagnostic status.
       - `PowerAndHwTask` (Core 1): Samples analog RSSI, monitors hardware alarm pins, syncs UTC/Mawson epoch from gateway ACKs, and governs the sleep state machine.
     - **Power-Saving State Machine:**
       - **Deep Sleep (~1 mA):** SX1262 put into sleep (`radio.sleep()`), `Vext` (GPIO 36) set HIGH to cut power to MAX3232/optos, `GPIO 4` state latched via `rtc_gpio_hold_en()`, wakes on RTC timer (scheduled window) OR `EXT1` hardware alarm (`GPIO 5`/`6`).
       - **Scheduled Window + 10-Min Inactivity Hold-Off:** Wakes daily at configured hour/duration (e.g., midnight for 60 min, configurable via web UI). Any valid LoRa packet received resets a 10-minute (`600 s`) inactivity countdown; node only returns to deep sleep once both the scheduled window AND the 10-minute post-traffic timer expire.
       - **Maintenance Lock ("Stay Awake" Web Toggle):** Remote command sets `stay_awake_lock` in NVS flash—keeps node awake 24/7 until explicitly toggled off from the web UI.
       - **Emergency Alarm Wake:** If woken by `EXT1` outside a scheduled window, polls MX921 alarms, transmits `ALARM_CRITICAL` burst, waits 30 s for any operator response/lock command, and returns to deep sleep if idle.
  2. **Locked 915 MHz Physical & Framing Layer (SX1262 <-> SX1276 Compatible):**
     - Frequency: `916.8 MHz` (AU915 ISM, configurable in NVS), Bandwidth: `125 kHz`, Spreading Factor: `SF10`, Coding Rate: `4/5`, Sync Word: `0x12`, Explicit Header, CRC ON.
     - Binary/Compact Packet Header: `[Magic 2B][NodeID 1B: 0x01=MtHendo, 0x02=MtParsons][MsgType 1B][Seq 2B][PowerState 1B][Payload N B][AuthTag 4B][CRC16 2B]`.
     - Gateway ACK frames embed `unix_epoch_utc` (4B) and pending command flags (`stay_awake_lock`, schedule updates, queued `RMENU` commands) so queued web actions execute immediately upon node wake.
  3. **Duinotech XC-4394 ATmega328P Bridge Sketch (`gateway_mcu/`):**
     - Lightweight `arduino-LoRa` firmware strictly acting as a transparent binary/hex frame forwarder between SX1276 SPI and `/dev/ttyATH0` (`115200 baud`), staying well within `2 KB SRAM` / `32 KB Flash`.
  4. **OpenWrt Web GUI & Serial Daemon (`gateway_openwrt/`):**
     - Python/Lua background daemon reading `/dev/ttyATH0`, validating packets, persisting latest node state + command queue + rolling event log to `/tmp/mx921_state.json`, and injecting NTP wall-clock time into every ACK.
     - Lightweight responsive HTML/JS/CSS web app served via OpenWrt `uhttpd` (`/www/mx921/`) with **4 tabs**:
       - **Tab 1 — Live Status & Alarms:** Node selector (`Node 1: Mt Henderson`, `Node 2: Mt Parsons`), current power state badge (`AWAKE - SCHEDULED`, `AWAKE - ACTIVITY HOLD (8m left)`, `MAINTENANCE LOCK`, `SLEEPING - NEXT WAKE 00:00`), last-seen timestamp, LoRa RSSI/SNR, active channel, RX/TX MHz, CTCSS/DCS, Repeater Enabled/Knockdown state, live RX RSSI meter, and alarm badges (High Reflected Power / VSWR, Low Fwd Power, High PA Temp, Supply Voltage, PLL Unlock).
       - **Tab 2 — Quick Control & Power Schedule:** Prominent **"Stay Awake (Maintenance Lock)"** toggle switch, Wake Schedule configurator (wake hour, window duration, inactivity hold-off minutes), Channel selector (`1–99`), Repeater Enable / Knockdown toggle, Hardware Inhibit override toggle, and Force Telemetry Poll button—with queued/ACK status indicators.
       - **Tab 3 — Channel & Radio Programming (Advanced):** Read/write channel memory slots (RX freq, TX freq, `12.5/25 kHz` bandwidth, CTCSS/DCS encode & decode, alarm routing, T45 audio bridge mode) + raw `RMENU` terminal console pass-through.
       - **Tab 4 — Diagnostics & Packet Log:** Rolling table of TX commands, queued commands waiting for wake, RX ACKs, raw hex/JSON frames, and parser error logs.

- **Out of Scope:**
  - Voice or Codec2 audio transmission over LoRa.
  - Permanent 12 V / 24 V solar-wind battery power regulation hardware (using 5 V USB-C for bench testing in Phase 1).
  - External internet or cloud telemetry forwarding (strictly local Mawson LAN).

- **Constraints & Latencies:**
  - ATmega328P on XC-4394 is limited to `2 KB SRAM`; zero JSON parsing or string buffering on the MCU—all application logic resides on the OpenWrt AR9331 (`64 MB RAM`).
  - Commands queued on the web UI while the node is in deep sleep persist in `/tmp/mx921_state.json` and dispatch automatically within `< 1 s` of the node's next wake heartbeat.
  - Hardware alarm interrupts (`EXT1`) wake the node and transmit to Mawson within `< 3 s` even during deep sleep.

- **Assumptions:**
  - Both Heltec V3 boards are confirmed `902–928 MHz` (`HF`) hardware matching the XC-4394 (`915 MHz`).
  - `Vext` (GPIO 36) powers the MAX3232 transceiver so RS-232 driver quiescent current drops to zero during deep sleep.
  - Bench testing uses a MAX3232 module, two PC817/4N35 optocouplers, and a `10 kΩ / 18 kΩ` voltage divider wired to a test MX921 port at Mawson before field deployment.
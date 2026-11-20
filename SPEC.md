# SPEC.md — Mawson MX921 Transparent RS-232-over-LoRa Wireless Bridge

## Build Spec

- **Goal:** Build a bidirectional 915 MHz transparent RS-232-over-LoRa serial bridge connecting a Spectra Engineering MX921 repeater's front RJ45 `MIC` port (Phase 1: Mount Henderson, extensible to Mount Parsons) to a Duinotech XC-4394 OpenWrt LoRa gateway over Mawson Station Ethernet, providing a virtual serial port for Spectra `900Tools` / `RMENU`, a lightweight OpenWrt web control bar, `+++` AT command control, remote Spreading Factor switching with dead-man fallback, and scheduled deep-sleep power management.

- **Users / Environment:**
  - **Operators:** Communications Technical Officers at Mawson Station, Antarctica (running Linux Mint or Windows laptops connected via Ethernet to the Duinotech XC-4394).
  - **Phase 1 (Bench Test):** Local comms-room bench testing at Mawson Station using 5 V USB-C power on the Heltec node and a short (`0.5–2 m`) shielded Cat5e/Cat6 patch cable to the MX921 front RJ45 `MIC` socket before field deployment to Mount Henderson (~13 km SSE).
  - **Remote Repeater Node Hardware:** White Heltec Wireless Stick Lite V3 (`HTIT-WB32LAF`, ESP32-S3FN8 + SX1262, 863–928 MHz HF) running FreeRTOS (PlatformIO / Arduino-ESP32).
  - **Repeater Electrical Interface:**
    - **MAX3232 3.3 V RS-232 Transceiver** powered via switched `Vext` (`GPIO 36`, active LOW) so it powers down completely during deep sleep.
    - **MX921 Front RJ45 (`MIC`) Pinout (57,600 baud, 8-N-1, strictly 3-wire — all other pins isolated):**
      - `RJ45 Pin 4` (MX921 `RXD`) <-> MAX3232 `T1OUT`
      - `RJ45 Pin 5` (MX921 `TXD`) <-> MAX3232 `R1IN`
      - `RJ45 Pin 7` (`GND`) <-> MAX3232 `GND`
      - *(Pins 1, 2, 3, 6, 8 left completely disconnected to isolate +12 V mic bias, PTT, and audio lines).*
  - **Base Gateway Hardware:** Duinotech XC-4394 (Atheros AR9331 OpenWrt + ATmega328P + SX1276 915 MHz) connected to laptop/Mawson LAN via RJ45 Ethernet, powered by 12 V DC.

- **Must Have:**
  1. **Heltec Wireless Stick Lite V3 FreeRTOS Firmware (`node_mx921/`):**
     - **Pinout (Fixed Heltec WSL V3 Internal SPI):** SX1262 `NSS=8`, `SCK=9`, `MOSI=10`, `MISO=11`, `RST=12`, `BUSY=13`, `DIO1=14`; `Vext_Ctrl=36` (LOW = ON, HIGH = OFF before sleep); User LED=`35`.
     - **External UART Pinout:** `UART1` (`TX=17`, `RX=18`) -> MAX3232 `T1IN` / `R1OUT` (`57600 baud` default, configurable in NVS).
     - **FreeRTOS Dual-Core Task Architecture:**
       - `RadioTask` (Core 0): Manages SX1262 via `RadioLib`, handles `DIO1` packet RX interrupts, validates CRC-16 + 4-byte HMAC/shared-secret auth tags on control packets, manages SF switching and dead-man fallback, and sends coalesced serial frames.
       - `UartBridgeTask` (Core 1): Reads/writes `UART1` (`57600 baud`) with `15 ms` idle-gap packet coalescing (or `180-byte` max payload flush) so `900Tools` requests and `RMENU` screens transmit cleanly without byte fragmentation.
       - `PowerManagerTask` (Core 1): Tracks `last_activity_ms`, syncs UTC/Mawson epoch from gateway ACKs, and governs the sleep state machine.
     - **Power-Saving State Machine:**
       - **Deep Sleep (~1 mA):** Calls `radio.sleep()`, sets `Vext` (`GPIO 36`) HIGH to cut power to the MAX3232, and wakes on RTC timer for the scheduled daily window (default `00:00` local/UTC configurable, `60 min` duration).
       - **10-Minute Activity Watchdog:** Any valid data or control packet received over LoRa resets a `600 s` (`10 min`) inactivity countdown; the node stays awake past the scheduled window until `10 min` after the last received traffic.
       - **Maintenance Lock ("Stay Awake" Toggle):** Persistent NVS flag `stay_awake_lock`—when enabled via web control bar or `+++` AT command, keeps the node awake 24/7 until explicitly toggled back off.
  2. **Locked 915 MHz Physical Layer & Remote SF Switch with Dead-Man Fallback:**
     - **Base Radio Settings (SX1262 <-> SX1276 Compatible):** Frequency `916.8 MHz` (AU915 ISM, configurable), Bandwidth `125 kHz`, Coding Rate `4/5`, Sync Word `0x12`, Explicit Header, CRC ON.
     - **Default & Storm Spreading Factors:** Defaults to **`SF9`** (~120 ms air-time, ~250–450 ms RTT for Spectra `900Tools` compatibility with >45 dB link margin on the 13 km Mt Henderson hop). Supports remote switch to **`SF11`** ("Storm Mode" for `RMENU` terminal use in extreme weather).
     - **Synchronized SF Switch + 60 s Fallback:** Gateway sends `CMD_SET_SF` at current SF; node replies `ACK` at current SF and switches to target SF; gateway switches upon receiving `ACK`. If the node receives zero valid packets on the new SF within `60 s`, it automatically reverts to `SF9` so neither end can get stranded out of sync.
     - **Packet Framing:** `[Magic 2B][NodeID 1B: 0x01=MtHendo, 0x02=MtParsons][Type 1B: DATA|CTRL|ACK][Seq 2B][Flags 1B][Payload <=180B][CRC16 2B]`.
  3. **Duinotech XC-4394 ATmega328P Bridge Sketch (`gateway_mcu/`):**
     - Minimal `arduino-LoRa` firmware strictly forwarding framed packets between SX1276 SPI and `/dev/ttyATH0` (`115200 baud`), plus executing local `SET_SF` commands from the OpenWrt daemon, staying well within `2 KB SRAM` / `32 KB Flash`.
  4. **OpenWrt Daemon, TCP Virtual COM Port & Lightweight Control Bar (`gateway_openwrt/`):**
     - **TCP Serial Bridge (`TCP Port 9001`):** Exposes the transparent LoRa serial stream to your laptop (`socat` on Linux Mint `/dev/ttyV0` or `com0com`/`HW VSP3` on Windows) for `900Tools` and `PuTTY`/`RMENU`, while intercepting local `+++` Hayes-style AT commands (`AT+STATUS?`, `AT+AWAKE=1|0`, `AT+SF=9|11`, `AT+SCHED=HH,DUR`).
     - **Lightweight Web Control Bar (`http://<gateway-ip>/` via `uhttpd`):** Single compact responsive status & control bar showing:
       - Node selector (`Node 1: Mt Henderson` / future `Node 2: Mt Parsons`)
       - Live state badge (`AWAKE - SCHEDULED`, `AWAKE - IDLE HOLD (mm:ss)`, `MAINTENANCE LOCK`, `SLEEPING - NEXT WAKE HH:MM`)
       - Last packet LoRa RSSI / SNR & current Spreading Factor (`SF9` / `SF11`)
       - One-click **"Stay Awake (Maintenance Lock)"** toggle switch
       - One-click **Spreading Factor (`SF9 Normal` / `SF11 Storm`)** selector
       - Wake schedule inputs (Wake Hour, Window Minutes, Idle Hold Minutes) + queued command indicator.

- **Out of Scope:**
  - Custom `RMENU` command parsing or full channel-table editing inside the web GUI (handled natively by Spectra's PC software or `RMENU` over the virtual COM port).
  - Hardware DB25 `CN3` alarm/RSSI wiring (using strictly the 3-wire front RJ45 `MIC` RS-232 port).
  - Voice/Codec2 bridging and permanent 12 V / 24 V solar-wind power wiring (5 V USB-C used in Phase 1).

- **Constraints & Latencies:**
  - ATmega328P on XC-4394 has only `2 KB SRAM`; zero string/JSON buffering on the MCU—all TCP socket handling, `+++` command parsing, NTP time injection, and web UI state live on the OpenWrt AR9331 (`64 MB RAM`).
  - Keep front RJ45 patch cable between MX921 and MAX3232 short (`0.5–2 m` shielded Cat5e/Cat6 with ferrite choke) to prevent VHF RF pickup at `57600 baud`.
  - Round-trip serial latency at `SF9 / 125 kHz` must stay under `500 ms` so Spectra's PC software does not time out.

- **Assumptions:**
  - Both Heltec V3 boards operate in the `902–928 MHz` (`HF`) band matching the Duinotech XC-4394 (`915 MHz`).
  - During bench testing and field installation, the front RJ45 `MIC` socket is available (or tapped via a passive pass-through splitter passing Pins 1–3 & 6–8 untouched).

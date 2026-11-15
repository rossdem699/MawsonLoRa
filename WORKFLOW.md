# Mawson MX921 LoRa Repeater Bridge — Summary & Project Workflow

> Reference Spec: [MawsonLoRaRepeaterESP32.md](./MawsonLoRaRepeaterESP32.md)  
> Latest Saved Checkpoint: [CHECKPOINT.md](./CHECKPOINT.md) (Phase 1 & Phase 3 Complete, Resuming at Phase 2)

## 1. How the Project Works

This project builds a **915 MHz LoRa telemetry, alarm, and remote-programming bridge** between an off-site **Spectra Engineering MX921 repeater** (Phase 1: Mawson comms bench POC -> Mount Henderson ~13 km SSE, extensible to Mount Parsons) and a **4-tab local web dashboard** hosted on the Mawson Station LAN.

```
[MX921 Repeater] <--RS-232 (CN4) & Opto/ADC (CN3)--> [Heltec ESP32-S3 + SX1262]
                                                              |
                                                 916.8 MHz LoRa (SF10 / 125 kHz)
                                                 ~13 km LOS (Mt Henderson <-> Mawson)
                                                              |
[Mawson LAN Browser] <--HTTP (4-Tab UI)-- [OpenWrt AR9331 <--UART--> ATmega328P + SX1276]
                                                    (Duinotech XC-4394 Gateway)
```

### Key Subsystems

1. **Remote Repeater Node (`node_mx921/`)**
   - **Hardware:** Heltec Wireless Stick Lite V3 (`HTIT-WB32LAF`, `ESP32-S3FN8` + `SX1262` 863–928 MHz), powered via 5 V USB-C for the Mawson Station bench POC.
   - **Serial Control (`CN4` DB9-M / Front RJ45 `MIC`):** Uses a `MAX3232` 3.3 V RS-232 transceiver (`UART1` on `GPIO 17/18`) to drive the MX921 `RMENU` ASCII console (`9600` or `57600` baud) for reading/writing channels, frequencies, CTCSS/DCS tones, `12.5/25 kHz` bandwidth, repeater enable/knockdown, and diagnostic status.
   - **Parallel Hardware I/O (`CN3` DB25-F):**
     - **Alarms & COR:** Optocouplers on RTC-capable `GPIO 5` & `6` (`Alarm #1` & `#2`, `EXT1` wakeup) and `GPIO 7` (`COR/Mute`).
     - **Hardware Knockdown/Inhibit:** Optocoupler on `GPIO 4` (state held during deep sleep via `rtc_gpio_hold_en()`).
     - **Analog RSSI:** `10 kΩ / 18 kΩ` resistive divider scaling `0–5 V` analog RSSI to `0–3.3 V` on `GPIO 1` (`ADC1_CH0`).

2. **Intelligent Power & Sleep State Machine**
   - **Deep Sleep (~1 mA):** Sleeps the SX1262 (`radio.sleep()`) and sets `Vext` (`GPIO 36 = HIGH`) to cut power to the `MAX3232` and optocouplers.
   - **Scheduled Window + 10-Min Activity Hold-Off:** Wakes daily on an RTC schedule (e.g., midnight for 60 mins). Every valid LoRa packet received resets a **10-minute (`600 s`) inactivity countdown**, ensuring the node never goes to sleep mid-session.
   - **Maintenance Lock ("Stay Awake"):** Web toggle persisted in NVS flash forces the node to stay awake 24/7 until explicitly turned off.
   - **Emergency Alarm Wake (`< 3 s`):** Hardware alarm on `GPIO 5/6` (`EXT1`) immediately wakes the ESP32 from deep sleep, polls the MX921 fault state, transmits an `ALARM_CRITICAL` burst to Mawson, and listens for 30 seconds for operator commands before returning to sleep.

3. **Locked 915 MHz LoRa Physical & Framing Layer**
   - **RF Parameters (SX1262 <-> SX1276 compatible):** `916.8 MHz` (AU915 ISM), Bandwidth `125 kHz`, Spreading Factor `SF10`, Coding Rate `4/5`, Sync Word `0x12`, Explicit Header, CRC ON.
   - **Compact Binary Frame:** `[Magic 2B][NodeID 1B][MsgType 1B][Seq 2B][PowerState 1B][Payload N B][AuthTag 4B (HMAC-SHA256)][CRC16 2B]`.
   - **Store-and-Forward Command Queue:** Commands queued on the web UI while the node is asleep are persisted on the gateway and dispatched automatically in the ACK packet within `< 1 s` of the node's next wake heartbeat (along with UTC epoch time sync).

4. **Mawson Base Gateway & Web Dashboard (`gateway_mcu/` & `gateway_openwrt/`)**
   - **ATmega328P Bridge (`gateway_mcu/`):** Ultra-lightweight `arduino-LoRa` sketch forwarding raw binary/hex frames between the onboard SX1276 and `/dev/ttyATH0` (`115200` baud) within the `2 KB SRAM` limit.
   - **OpenWrt Daemon & 4-Tab Web UI (`gateway_openwrt/`):** Background service managing `/tmp/mx921_state.json` and serving a 4-tab web app via `uhttpd` (`/www/mx921/`):
     - **Tab 1 — Live Status & Alarms:** Node selector, sleep/wake badge, LoRa RSSI/SNR, active channel/frequencies/tones, live RX RSSI meter, and alarm badges (VSWR, Low Fwd Power, PA Temp, Supply Voltage, PLL Unlock).
     - **Tab 2 — Quick Control & Power Schedule:** "Stay Awake (Maintenance Lock)" toggle, wake schedule configurator, channel selector (`1–99`), repeater knockdown toggle, hardware inhibit override, and force poll button.
     - **Tab 3 — Channel & Radio Programming (Advanced):** Read/write channel memory table + raw `RMENU` terminal console pass-through.
     - **Tab 4 — Diagnostics & Packet Log:** Rolling TX/RX packet table, queued commands waiting for wake, raw hex/JSON frames, and parser errors.

---

## 2. Phased Workflow & To-Do Checklist

### Phase 1: RF Bench Prep & Basic LoRa Link (Small Bench Antennas) — COMPLETE & VERIFIED
- [x] **1.1. Verify Hardware & Attach Bench Test Antennas**
  - Confirmed hardware is 915 MHz (863–928 MHz HF).
  - Attached bench antennas / U.FL-to-SMA pigtail.
  - Safe for initial power-on and bench testing.
- [x] **1.2. Define Shared Packet & Crypto Header**
  - Created shared binary frame definitions (`shared/mx921_packet.h`) with fixed RF params (`916.8 MHz`, `125 kHz`, `SF10`, `CR 4/5`, `Sync 0x12`), header `[Magic 2B][NodeID 1B][MsgType 1B][Seq 2B][PowerState 1B]`, payloads (`mx921_ack_payload_t`, `mx921_ping_payload_t`), trailer `[AuthTag 4B][CRC16 2B]`, and inline CCITT-CRC16 computation.
- [x] **1.3. Flash the Gateway MCU Bridge (`gateway_mcu/`)**
  - Built ATmega328P transparent serial-to-LoRa bridge (`gateway_mcu/src/main.cpp`).
  - Flashed onto Duinotech XC-4394 ATmega328P using internal OpenWrt `run-avrdude`.
  - Memory verified: 427 bytes SRAM (20.8%), 6.1 KB Flash (19.2%).
  - Configured `/dev/ttyATH0` at 115200 baud with ASCII Hex frames (`+RX:` and `+TX:`).
- [x] **1.4. Build & Flash Initial Heltec Ping Firmware (`node_mx921/`)**
  - Scaffolded PlatformIO project (`node_mx921/src/main.cpp`) using `RadioLib` for Heltec V3 SX1262 (`NSS=8, SCK=9, MOSI=10, MISO=11, RST=12, BUSY=13, DIO1=14`, `Vext=36`).
  - Successfully flashed to physical Heltec WSL V3 on `/dev/ttyUSB0`.
  - **LIVE BENCH VERIFICATION COMPLETE:** Confirmed full bidirectional LoRa ping/ACK exchange between Heltec WSL V3 (SX1262) and Duinotech XC-4394 (SX1276) at 916.8 MHz SF10:
    - Heltec TX PING: `[TX] Sending PING Seq=452 (len=21 bytes)... Done.`
    - Duinotech RX PING: `+RX:-66,10.3,21,4D580101...`
    - Duinotech TX ACK: `+TX:4D5800020100...` -> `+OK:TX_DONE,21`
    - Heltec RX ACK: `[RX] Valid Frame: Node=0x00 MsgType=0x02 Seq=1 RSSI=-45.0 dBm SNR=8.5 dB`

### Phase 2: MX921 Bench Interface in Mawson Comms Room
- [ ] **2.1. Assemble the Prototype Interface Board/Harness**
  - Wire `Vext` (`GPIO 36`, active LOW) to switch power to the `MAX3232` RS-232 module and optocoupler bias rails.
  - Wire `UART1` (`TX=17, RX=18`) via `MAX3232` to MX921 rear `CN4` (DB9-M: Pin 2 RXD, Pin 3 TXD, Pin 5 GND) and/or front RJ45 `MIC` cable.
  - Wire `CN3` (DB25-F): `10 kΩ / 18 kΩ` divider to `GPIO 1` (analog RSSI), optocoupled `Alarm 1/2` to `GPIO 5/6`, `COR/Mute` to `GPIO 7`, and Repeater Inhibit opto output to `GPIO 4`.
- [ ] **2.2. Implement `Mx921SerialTask` (`RMENU` Parser)**
  - Build the non-blocking state machine supporting `9600` and `57600` baud to read/write channels, frequencies, CTCSS/DCS, repeater enable state, and diagnostic flags on the bench MX921.
- [ ] **2.3. Implement `PowerAndHwTask` (Sleep & Wake State Machine)**
  - Implement deep sleep (`radio.sleep()`, `Vext` HIGH, `rtc_gpio_hold_en(GPIO_NUM_4)`), scheduled RTC wakeup, 10-minute post-activity hold-off timer, NVS `stay_awake_lock`, and `EXT1` hardware alarm emergency wake (`< 3 s`).
  - Verify deep-sleep current draw on the bench (`~1 mA`).

### Phase 3: OpenWrt Gateway Daemon & 4-Tab Web Dashboard — COMPLETE & ACTIVE
- [x] **3.1. Develop the OpenWrt Serial Daemon (`gateway_openwrt/`)**
  - Created `/root/mx921_daemon.py` on Duinotech XC-4394 OpenWrt.
  - Interacts continuously with `/dev/ttyATH0` at 115200 baud, verifying binary packets, CRC16, and calculating rolling metrics.
  - Automatically maintains `/tmp/mx921_state.json` and synchronizes UTC time into outgoing ACKs.
  - Installed and enabled background procd service `/etc/init.d/mx921_daemon` (auto-start on boot).
- [x] **3.2. Build the 4-Tab Web GUI (`/www/mx921/`)**
  - Built high-contrast responsive dark dashboard served via OpenWrt `uhttpd` at `http://10.130.1.1/mx921/`:
    - **Tab 1 — Live Status & Alarms:** Real-time power state badges, live LoRa RSSI/SNR bar, active channel/frequencies, animated needle meter for receiver analog RSSI, and 5 hardware alarm badges.
    - **Tab 2 — Quick Control & Power Schedule:** Maintenance Lock ("Stay Awake") switch, channel selector (1–99), repeater knockdown/inhibit buttons, force telemetry poll, and wake schedule configurator.
    - **Tab 3 — Channel & Radio Programming (Advanced):** Memory slot table + interactive `RMENU` pass-through console with quick macros.
    - **Tab 4 — Diagnostics & Packet Log:** Live rolling table of TX/RX packets with direction badges, RSSI, SNR, summaries, raw hex inspector, and raw state JSON viewer.
  - Created CGI backend `/www/cgi-bin/mx921_api` handling REST GET state requests and POST control commands.
- [ ] **3.3. End-to-End Comms Room POC Sign-Off**
  - Test full workflow on the Mawson comms bench: queue a channel change while the Heltec is in deep sleep, verify execution within `< 1 s` of wake, trigger simulated hardware alarms on `CN3`, and test the "Stay Awake" lock.

### Phase 4: Progressive Antenna Testing & RF Validation (Mawson Station)
- [ ] **4.1. Feedline, Adapter & VSWR Checks**
  - Inspect all coax jumpers (`U.FL-to-N-type` / `SMA-to-N-type`), surge arrestors, and tuned **915 MHz outdoor antennas** (collinear omni / directional Yagi for `902–928 MHz`).
  - Sweep antennas and feedlines with a VNA / antenna analyzer at `916.8 MHz` to confirm VSWR `< 1.5:1`.
- [ ] **4.2. Medium-Range Outdoor Station Walk/Drive Test (Intermediate Antennas)**
  - Raise TX power in NVS to operational levels (`14–22 dBm`).
  - Connect the XC-4394 gateway to an indoor or temporary roof 915 MHz whip/dipole antenna and walk the Heltec node (on USB power bank) around Mawson Station (`500 m – 2 km`).
  - Check **Tab 1 (RSSI / SNR)** and **Tab 4 (Packet Log)** across station buildings and terrain obstacles.
- [ ] **4.3. Mawson Base Antenna Installation & 24-Hour Burn-In**
  - Mount the permanent **915 MHz base antenna** on the Mawson comms building/mast with low-loss coax (e.g., LMR-400) connected to the XC-4394.
  - Temporarily mount the remote **915 MHz field antenna** outside at Mawson connected to the Heltec node and run a 24-hour burn-in cycling through scheduled wakes, deep sleep, and remote `RMENU` operations.

### Phase 5: Mount Henderson (~13 km) Field Deployment
- [ ] **5.1. Repeater Hut Installation at Mount Henderson**
  - Install and align the outdoor **915 MHz directional Yagi / collinear antenna** at Mount Henderson pointing back to Mawson Station (~13 km LOS), weatherproofing all coax connections with self-amalgamating tape.
  - Connect the Heltec interface enclosure to the Mount Henderson MX921 (`CN4` RS-232 and `CN3` DB25) and power supply.
- [ ] **5.2. Live Link Margin & Commissioning Sign-Off**
  - Enable **"Stay Awake (Maintenance Lock)"** from the Mawson web UI during field commissioning.
  - Verify 13 km link metrics at `SF10 / 125 kHz` (target RSSI better than `-115 dBm`, SNR `> -5 dB`; `SF10` sensitivity floor is `~ -132 dBm`).
  - Confirm live channel readback, analog RSSI telemetry, and hardware alarm triggers from Mawson Station, then disable **Maintenance Lock** to begin normal scheduled deep-sleep operation.

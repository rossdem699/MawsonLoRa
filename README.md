# Mawson MX921 LoRa Repeater Telemetry & Remote Programming Bridge

[![Platform](https://img.shields.io/badge/Platform-ESP32--S3%20%7C%20OpenWrt-blue.svg)](https://github.com/rossdem699/MawsonLoRa)
[![RF](https://img.shields.io/badge/LoRa-916.8%20MHz%20SF10-emerald.svg)](https://github.com/rossdem699/MawsonLoRa)
[![Status](https://img.shields.io/badge/Status-Phase%201%20%26%203%20Verified-brightgreen.svg)](https://github.com/rossdem699/MawsonLoRa)

A 915 MHz bidirectional LoRa telemetry, alarm monitoring, and remote-programming bridge connecting an off-site **Spectra Engineering MX921 VHF repeater** (Phase 1: Mawson Comms bench POC $\rightarrow$ Mount Henderson ~13 km SSE, extensible to Mount Parsons) with an interactive **4-tab web dashboard** hosted on a **Duinotech XC-4394 OpenWrt LoRa gateway** on the Mawson Station LAN in Antarctica.

---

## 1. System Architecture

```text
[MX921 Repeater] <---RS-232 (CN4) & Opto/ADC (CN3)---> [Heltec ESP32-S3 WSL V3 + SX1262]
(Mount Henderson Hut)                                             |
                                                     916.8 MHz LoRa (SF10 / 125 kHz)
                                                     ~13 km LOS Link (Mt Hendo <-> Mawson)
                                                                  |
[Mawson Comms Techs] <--HTTP (4-Tab UI)-- [OpenWrt AR9331 <---UART---> ATmega328P + SX1276]
 (Mawson Station LAN)                                   (Duinotech XC-4394 Gateway)
```

### Key Capabilities
- **Remote ASCII `RMENU` Programming:** Read and reprogram MX921 channel memory slots, TX/RX frequencies, 12.5/25 kHz channel spacing, and CTCSS/DCS tones remotely without traveling to the mountain site.
- **Critical Alarm Bursts:** Instant alerts on high reflected power (VSWR / antenna icing), low forward power, PA over-temperature, and PLL frequency unlock.
- **Live Analog RSSI Meter:** Real-time visual dial showing incoming field radio signal strengths scaled from the MX921 `CN3` 0–5 V analog pin.
- **Hardware Repeater Knockdown:** Optocoupled line asserting repeater inhibit (`GPIO 4`) to silence stuck field transmitters or runaway repeaters remotely.
- **Intelligent Sleep State Machine:** Remote node sleeps at ~1 mA to conserve mountain battery banks, waking daily on an RTC schedule or hardware alarm interrupt (`EXT1`), with a 10-minute activity hold-off timer and web-controlled Maintenance Lock ("Stay Awake").

---

## 2. Hardware Specifications

### Remote Repeater Node (`node_mx921/`)
- **Microcontroller:** Heltec Wireless Stick Lite V3 (`HTIT-WB32LAF`, ESP32-S3FN8).
- **LoRa Transceiver:** Semtech SX1262 (863–928 MHz HF).
- **Power Rail Switching:** `Vext` (`GPIO 36`, active LOW) completely cuts power to the RS-232 transceiver and optocouplers during deep sleep.
- **Serial Interface:** `UART1` (`TX=GPIO 17`, `RX=GPIO 18`) via `MAX3232` 3.3V RS-232 transceiver wired to MX921 rear `CN4` DB9-M (Pin 2 RX, Pin 3 TX, Pin 5 GND) at 9600 / 57600 baud.
- **Parallel I/O (`CN3` DB25-F):**
  - Optocoupled `Alarm 1` & `Alarm 2` on RTC-capable `GPIO 5` & `6` (`EXT1` wakeup).
  - Optocoupled `COR / Mute` on `GPIO 7`.
  - Optocoupled Repeater Inhibit output on `GPIO 4` (held during deep sleep via `rtc_gpio_hold_en`).
  - Resistive voltage divider (`10 kΩ / 18 kΩ`) scaling 0–5 V analog RSSI to 0–3.3 V on `GPIO 1` (`ADC1_CH0`).

### Mawson Base Gateway (`gateway_mcu/` & `gateway_openwrt/`)
- **Gateway Hardware:** Duinotech XC-4394 (Dragino LG01 / Yun compatible).
- **Embedded Linux SoC:** Atheros AR9331 (400 MHz MIPS, 64 MB RAM, OpenWrt Chaos Calmer).
- **LoRa Microcontroller Bridge:** Microchip ATmega328P connected via internal UART to AR9331 (`/dev/ttyATH0` at 115200 baud) and SPI to an SX1276 transceiver. Memory footprint is strictly **427 bytes SRAM (20.8%)**, preventing buffer overflow.
- **Local Network Access:** Both physical Ethernet LAN (`eth0`) and Wi-Fi AP (`duinotech-18094a`) are bridged together at static IP **`10.130.1.1`**.

---

## 3. LoRa Physical & Packet Framing Layer

| Parameter | Value | Details |
| :--- | :--- | :--- |
| **Frequency** | `916.8 MHz` | AU915 ISM (Ch 0 centre frequency) |
| **Bandwidth** | `125 kHz` | Standard narrowband LoRa |
| **Spreading Factor** | `SF10` | ~ -132 dBm receiver sensitivity floor |
| **Coding Rate** | `4/5` | Robust forward error correction |
| **Sync Word** | `0x12` | Private LoRa network identifier |
| **CRC** | Enabled | Hardware CCITT-16 verification |

### Compact Binary Packet Framing
```text
[Magic 2B][NodeID 1B][MsgType 1B][Seq 2B][PowerState 1B][Payload N B][AuthTag 4B][CRC16 2B]
```
- **Magic:** `0x4D 0x58` ("MX")
- **Node IDs:** `0x00` = Mawson Base Gateway, `0x01` = Mount Henderson (~13 km SSE), `0x02` = Mount Parsons
- **Message Types:** `0x01` PING / Heartbeat, `0x02` Gateway ACK (with UTC time sync), `0x05` RMENU Req, `0x06` RMENU Resp, `0x07` Critical Alarm Burst.

---

## 4. 4-Tab Web Dashboard (`http://10.130.1.1/mx921/`)

Served directly by `uhttpd` on the Duinotech gateway:

1. **Tab 1 — Live Status & Alarms:**
   - Power State badge (`AWAKE - SCHEDULED`, `MAINTENANCE LOCK`, `SLEEPING`).
   - Live LoRa link metrics (RSSI in dBm with signal bar, SNR in dB).
   - Active VHF Channel, frequencies, repeater knockdown state.
   - **Animated Analog RSSI Needle Dial** calibrated to the MX921 CN3 receiver line.
   - Hardware Alarm Badges (VSWR, Low Forward Power, PA Over-Temp, Voltage, PLL Unlock, COR).
   - **Live Repeater Announcement Banner** (e.g. `"Hello from Mt Henderson Repeater!"`).
2. **Tab 2 — Quick Control & Power Schedule:**
   - **"Stay Awake (Maintenance Lock)"** safety toggle switch.
   - Channel Selector dropdown (1–99) with single-click remote change.
   - Hardware Repeater Knockdown / Inhibit override buttons.
   - Wake schedule configurator (Daily UTC hour, window duration, hold-off timer).
   - Pending Command Queue status inspector.
3. **Tab 3 — Channel Programming & Console:**
   - Channel memory slot read/write table.
   - **Interactive ASCII `RMENU` Terminal Console** pass-through with quick macros (`STATUS`, `VER`, `CHAN`, `BATT`, `HELP`).
4. **Tab 4 — Diagnostics & Packet Log:**
   - Live rolling table of every TX and RX LoRa packet.
   - **Plain English Decoded Payloads** showing message text, node name, battery voltage, temperature, and channel.
   - Raw state JSON viewer.

---

## 5. Repository Structure

```text
MawsonLoRa/
├── CHECKPOINT.md               # Current hardware state & resume instructions
├── WORKFLOW.md                 # Master phased roadmap and progress checklist
├── MawsonLoRaRepeaterESP32.md  # Detailed system architecture specification
├── shared/
│   └── mx921_packet.h          # Shared binary packet structs, RF constants & CRC16
├── node_mx921/                 # Heltec Wireless Stick Lite V3 firmware (PlatformIO)
│   ├── platformio.ini
│   └── src/
│       └── main.cpp            # SX1262 RadioLib driver, telemetry & beacon logic
├── gateway_mcu/                # Duinotech XC-4394 ATmega328P firmware (PlatformIO)
│   ├── platformio.ini
│   └── src/
│       └── main.cpp            # Lightweight transparent UART-to-LoRa bridge (< 430B RAM)
└── gateway_openwrt/            # OpenWrt background service and web dashboard
    ├── mx921_daemon.py         # Serial daemon managing state & ACK dispatching
    ├── init_mx921_daemon       # /etc/init.d/ procd auto-start service script
    ├── mx921_api.py            # CGI REST API for the dashboard (/www/cgi-bin/mx921_api)
    └── www/                    # Web frontend (/www/mx921/)
        ├── index.html          # Accessible 4-tab WAI-ARIA layout
        ├── style.css           # High-contrast Antarctic dark mode theme
        └── app.js              # Real-time polling & English payload decoder
```

---

## 6. Access & Deployment Reference

### Connecting to the Gateway
- **IP Address:** `10.130.1.1`
- **Dashboard URL:** `http://10.130.1.1/mx921/`
- **Wi-Fi Access:** Connect to open SSID `duinotech-18094a`
- **Ethernet Access:** Plug into the Duinotech **LAN** port (DHCP gives client `10.130.1.x`)
- **SSH Access:** `ssh root@10.130.1.1` (Password: `duinotech` / passwordless RSA key)

---

## 7. Phased Implementation Roadmap

- [x] **Phase 1: RF Bench Prep & Basic LoRa Link (Small Bench Antennas)** — *COMPLETE & VERIFIED ON HARDWARE*
- [ ] **Phase 2: MX921 Bench Interface in Mawson Comms Room** — *NEXT UP*
  - [ ] 2.1 Assemble MAX3232 & optocoupler interface harness.
  - [ ] 2.2 Implement FreeRTOS `Mx921SerialTask` (RMENU ASCII parser).
  - [ ] 2.3 Implement `PowerAndHwTask` (sleep & wake state machine, ~1 mA deep sleep).
- [x] **Phase 3: OpenWrt Gateway Daemon & 4-Tab Web Dashboard** — *COMPLETE & LIVE*
- [ ] **Phase 4: Progressive Antenna Testing & RF Validation (Mawson Station)**
- [ ] **Phase 5: Mount Henderson (~13 km) Field Deployment**

---

**Australian Antarctic Division — Mawson Station Comms Team**

# Mawson MX921 LoRa Repeater Bridge — Session Checkpoint

**Checkpoint Date:** 22 November 2026  
**Status:** Bench Testing 100% Completed & Verified. Live Spectra MX921 Interrogation Proved Over 916.8 MHz LoRa. Web GUI & Virtual COM Operational.

---

## 1. Executive Summary & Accomplishments

1. **Hardware Loopback & Electrical Verification Proved:**
   - Designed and assembled the level-shifting interface using a **MAX232/MAX3232** IC between the Heltec Wireless Stick Lite V3 (3.3V TTL) and the Spectra MX921 repeater's front RJ45 `MIC` port (true RS-232 $\pm 8\text{V}$ to $\pm 12\text{V}$).
   - Bench measured voltage charge pump rails: $V_{CC} = +3.3\text{V}$, $V+ = +9.41\text{V}$, $V- = -8.62\text{V}$, $T1OUT = -8.42\text{V}$ (RS-232 mark state).
   - Conducted physical hardware loopback (bridging Pins 7 & 8 on the RJ45 socket): verified 0 CRC errors, zero packet corruption, and clean round-trip echo over LoRa at 57,600 baud.
2. **Live Spectra Engineering MX921 Interrogation Proved:**
   - Connected directly to the MX921 front RJ45 `MIC` port at Mawson Station.
   - Successfully queried the repeater over the 916.8 MHz LoRa link:
     - **Model Number:** `MX921D3D3H`
     - **Serial Number:** `91D300004`
     - **Firmware Version:** `0.2.22`
     - **Channel 1 Data:** Tx `0.00000 MHz`, Rx `0.00000 MHz`, Tx Power `50.0 W`, Bandwidth `WIDE`, Subtone `CTCSS`.
     - **Submenus Traversed:** Configuration Menu, Channel Edit Menu, Option T45 System Interface Menu.
3. **Responsive Web GUI & REST API Deployed to Gateway (`10.130.1.1`):**
   - Built and deployed a mobile-friendly dashboard on the Duinotech XC-4394 (`/www/`).
   - Integrated real-time visual progress bar (`TX ➔ LoRa ➔ MX921 ➔ RX`), smart atomic query burst routing (bypassing the 30-second menu timeout), and a live serial terminal.
4. **RF Link Health:**
   - 916.8 MHz, SF9, 125 kHz BW, CR 4/5, RSSI `-61 dBm`, SNR `+10 dB` to `+14 dB`, 0 CRC errors.

---

## 2. Hardware Wiring Reference (Bench Verified)

```text
========================================================================================
MX921 Front RJ45 MIC Socket        Signal Function     MAX232 / Level Shifter
========================================================================================
RJ45 Pin 5 (GND)          <----->  Signal Ground  <---> MAX232 Pin 15 (GND)
RJ45 Pin 7 (Radio TXD)    ------>  RS-232 TX Data --->  MAX232 Pin 13 (R1IN)
RJ45 Pin 8 (Radio RXD)    <------  RS-232 RX Data <---  MAX232 Pin 14 (T1OUT)

RJ45 Pin 1 (Mic Audio)     ------> ISOLATED / DISCONNECTED
RJ45 Pin 2 (PTT Line)      ------> ISOLATED / DISCONNECTED
RJ45 Pin 3 (+8V/+12V Bias) ------> ISOLATED / DISCONNECTED
RJ45 Pin 4 (Aux Logic/NC)  ------> ISOLATED / DISCONNECTED
RJ45 Pin 6 (Hook/Monitor)  ------> ISOLATED / DISCONNECTED
========================================================================================

MAX232 to Heltec Wireless Stick Lite V3 (ESP32-S3):
• MAX232 Pin 11 (T1IN)  <---> Heltec GPIO 48 (TX1, 3.3V TTL)
• MAX232 Pin 12 (R1OUT) <---> 10k Resistor <---> Heltec GPIO 47 (RX1, 3.3V TTL)
• MAX232 Pin 15 (GND)   <---> Heltec GND
• MAX232 Pin 16 (VCC)   <---> Heltec 3.3V / Switched Vext (GPIO 36)
```

---

## 3. Why We Run FreeRTOS

The firmware running on the Heltec WSL V3 (`node_mx921/`) relies on **FreeRTOS on the ESP32-S3FN8**:

1. **Decoupling Asynchronous UART from Half-Duplex LoRa Airtime:**
   * Transmitting a 180-byte packet at `SF9` takes **~200–300 ms** of radio airtime, during which the SPI bus and SX1262 driver are locked.
   * At 57,600 baud, incoming serial data arrives at 5.76 kB/s. The hardware UART FIFO (128 bytes) would overflow in ~22 ms if the system were single-threaded.
   * FreeRTOS isolates `RadioTask` on **Core 0** and `UartBridgeTask` on **Core 1**. Core 1 continuously empties the hardware UART into FreeRTOS queues while Core 0 handles RF transmission.
2. **Deterministic Packet Framing:**
   * `UartBridgeTask` enforces a **15 ms inter-byte silence detector** to group incoming serial streams into coherent atomic packets.
3. **Power Management & RTC Wake:**
   * `PowerManagerTask` monitors a 10-minute inactivity countdown, toggles switched $V_{ext}$ (`GPIO 36`) to cut power to the MAX232 during deep sleep, and synchronizes UTC time from gateway ACKs.

---

## 4. Key Engineering Findings: `RMENU` vs. `900Tools`

### The Challenges with `RMENU` (Terminal Menu)
* **Strict Carriage Return Terminator (`\r` vs `\r\n`):**
  The MX921 strictly requires `\r` (ASCII 0x0D). Sending CRLF (`\r\n`) causes the trailing `\n` to be evaluated as a second empty command, immediately terminating menus with `ERROR: Invalid command -> `.
* **30-Second Inactivity Watchdog:**
  The MX921 firmware automatically closes `rmenu` after 30 seconds of inactivity to protect against unattended terminal lockouts. Once closed, typing menu choices returns `ERROR: Invalid command -> E`.
* **Latency & Half-Duplex Collisions:**
  Over LoRa, interactive human typing feels sluggish (~500 ms round trip). Sending background keep-alive pings over half-duplex LoRa is dangerous because pings collide with manual keystrokes.

### Why `900Tools` is Superior
* **Stateless Machine Protocol:** Uses direct, atomic command-response frames (memory block dumps and parameter writes).
* **Zero Timeout Risk:** Never enters `rmenu`; therefore, the 30-second watchdog never activates.
* **Full Access:** Programs all 16 channels, alignment, squelch thresholds, and CTCSS matrices.

---

## 5. Virtual COM Port & Linux Considerations

* **Architecture:** `900Tools` is a Windows 32-bit application expecting a hardware COM port (`COM1`–`COMx`).
* **Linux Mint Setup (Wine + `socat`):**
  1. `socat pty,link=/tmp/ttyMX921,raw,echo=0 tcp:10.130.1.1:9001 &`
  2. `ln -s /tmp/ttyMX921 ~/.wine/dosdevices/com1`
  3. Run `900Tools` under Wine, selecting `COM1`.
* **Linux Challenges:**
  * **Timing/Latency:** Wine serial emulation needs relaxed timeout thresholds. Set `900Tools` communications timeout to **1500–2000 ms** to accommodate the ~500 ms LoRa wireless round-trip.
  * **PTY Persistence:** If `socat` terminates, the PTY symlink must be refreshed before restarting Wine.
  * **Windows Alternative:** Running `900Tools` on Windows with **HW VSP3** or **com0com** creates a native driver-level virtual COM port that transparently bridges to `10.130.1.1:9001`.

---

## 6. Deployment Roadmap (Field Implementation)

With bench testing 100% complete, the remaining steps are:

1. **Full `900Tools` Archive Backup:**
   - Execute a full "Read All Channels" and save the `.mx9` codeplug file across the LoRa bridge.
2. **Environmental Ruggedization:**
   - Enclose Heltec WSL V3 and MAX232 PCB in an IP67 sealed weatherproof enclosure with desiccant packs.
   - Install snap-on ferrite chokes on the RJ45 serial patch cable to eliminate VHF transmitter RF pickup.
3. **Mount Henderson Site Installation (~13 km SSE):**
   - Connect to the hut's 12V/24V battery bank using an isolated DC-DC step-down converter (12V $\rightarrow$ 5V).
   - Mount and align a high-gain 915 MHz directional Yagi or collinear antenna toward Mawson Station.
4. **Cold-Boot & Power-Cycle Fail-Safe Test:**
   - Simulate complete battery cut/restore to verify both ends auto-boot and re-establish the LoRa bridge without human intervention.

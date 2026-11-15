#include <Arduino.h>
#include <SPI.h>
#include <RadioLib.h>
#include "mx921_packet.h"

// Heltec Wireless Stick Lite V3 Hardware Pinout
#define PIN_LORA_NSS     8
#define PIN_LORA_SCK     9
#define PIN_LORA_MOSI    10
#define PIN_LORA_MISO    11
#define PIN_LORA_RST     12
#define PIN_LORA_BUSY    13
#define PIN_LORA_DIO1    14

#define PIN_VEXT_CTRL    36  // Active LOW to enable external Vext rail
#define PIN_USER_LED     35  // Active HIGH LED

// Module setup: SX1262(cs, irq, rst, busy)
SX1262 radio = new Module(PIN_LORA_NSS, PIN_LORA_DIO1, PIN_LORA_RST, PIN_LORA_BUSY);

// Packet sequence tracking
static uint16_t packet_seq = 0;
static uint32_t last_tx_ms = 0;
const uint32_t TX_INTERVAL_BENCH_MS = 5000; // 5-second ping interval for bench testing

// Flag indicating packet received via DIO1 interrupt
static volatile bool rx_flag = false;

#if defined(ESP8266) || defined(ESP32)
  ICACHE_RAM_ATTR
#endif
void setFlag(void) {
  rx_flag = true;
}

void enableVext(bool enable) {
  pinMode(PIN_VEXT_CTRL, OUTPUT);
  // Heltec WSL V3 Vext is enabled by writing LOW, disabled by writing HIGH
  digitalWrite(PIN_VEXT_CTRL, enable ? LOW : HIGH);
}

void blinkLed(uint16_t ms) {
  digitalWrite(PIN_USER_LED, HIGH);
  delay(ms);
  digitalWrite(PIN_USER_LED, LOW);
}

void setupRadio() {
  Serial.print(F("[SX1262] Initializing RadioLib... "));
  
  // Custom SPI instance for Heltec V3
  SPI.begin(PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_NSS);

  // Initialize SX1262 at 916.8 MHz, BW 125, SF10, CR 4/5, sync 0x12, power 2 dBm (bench)
  int state = radio.begin(
    916.8,                      // Carrier frequency (MHz)
    125.0,                      // Bandwidth (kHz)
    10,                         // Spreading factor (SF10)
    5,                          // Coding rate (4/5)
    0x12,                       // Sync word
    LORA_TX_POWER_BENCH_DBM,    // Output power in dBm (low for bench testing)
    8,                          // Preamble length
    1.6,                        // TCXO voltage
    false                       // Regulator: DCDC
  );

  if (state == RADIOLIB_ERR_NONE) {
    Serial.println(F("OK!"));
  } else {
    Serial.print(F("FAILED, code: "));
    Serial.println(state);
    while (true) {
      blinkLed(100);
      delay(100);
    }
  }

  radio.setDio1Action(setFlag);
  radio.startReceive();
}

void sendHeartbeatPing() {
  const char* beacon_msg = "Hello from Mt Henderson Repeater!";
  size_t msg_len = strlen(beacon_msg);

  size_t total_len = sizeof(mx921_header_t) + sizeof(mx921_ping_payload_t) + msg_len + sizeof(mx921_trailer_t);
  uint8_t tx_buf[128];
  
  mx921_header_t *hdr = (mx921_header_t *)tx_buf;
  hdr->magic[0] = MX921_PKT_MAGIC_0;
  hdr->magic[1] = MX921_PKT_MAGIC_1;
  hdr->node_id = NODE_ID_MT_HENDERSON;
  hdr->msg_type = MSG_TYPE_PING;
  hdr->seq = ++packet_seq;
  hdr->power_state = PWR_STATE_SCHEDULED_WINDOW | PWR_STATE_VEXT_ENABLED;

  mx921_ping_payload_t *payload = (mx921_ping_payload_t *)(tx_buf + sizeof(mx921_header_t));
  payload->vbat_mv = 5000;         // Bench 5V USB
  payload->temp_c_x10 = 215;       // 21.5 C
  payload->rx_rssi_raw = 0;        // Initial bench raw
  payload->alarm_flags = 0;        // No alarms active
  payload->active_channel = 1;     // Default channel 1

  // Append ASCII text greeting into payload
  uint8_t *msg_ptr = tx_buf + sizeof(mx921_header_t) + sizeof(mx921_ping_payload_t);
  memcpy(msg_ptr, beacon_msg, msg_len);

  mx921_trailer_t *trl = (mx921_trailer_t *)(msg_ptr + msg_len);
  // Bench mock auth tag
  trl->auth_tag[0] = 0xAA;
  trl->auth_tag[1] = 0xBB;
  trl->auth_tag[2] = 0xCC;
  trl->auth_tag[3] = 0xDD;

  // CRC-16 over Header + Payload + Text + AuthTag
  size_t data_len = sizeof(mx921_header_t) + sizeof(mx921_ping_payload_t) + msg_len + sizeof(trl->auth_tag);
  trl->crc16 = mx921_crc16_ccitt(tx_buf, data_len);

  Serial.printf("[TX] Sending PING Seq=%u: \"%s\" (len=%u bytes)... ", hdr->seq, beacon_msg, (unsigned)total_len);
  
  digitalWrite(PIN_USER_LED, HIGH);
  int state = radio.transmit(tx_buf, total_len);
  digitalWrite(PIN_USER_LED, LOW);

  if (state == RADIOLIB_ERR_NONE) {
    Serial.println(F("Done."));
  } else {
    Serial.printf("Error: %d\n", state);
  }

  // Put SX1262 back into continuous receive mode for Gateway ACK
  radio.startReceive();
}

void processIncomingPacket() {
  rx_flag = false;
  uint8_t rx_buf[128];
  size_t len = radio.getPacketLength();

  int state = radio.readData(rx_buf, len);
  if (state == RADIOLIB_ERR_NONE && len >= (sizeof(mx921_header_t) + sizeof(mx921_trailer_t))) {
    mx921_header_t *hdr = (mx921_header_t *)rx_buf;
    if (hdr->magic[0] == MX921_PKT_MAGIC_0 && hdr->magic[1] == MX921_PKT_MAGIC_1) {
      float rssi = radio.getRSSI();
      float snr = radio.getSNR();
      
      Serial.printf("[RX] Valid Frame: Node=0x%02X MsgType=0x%02X Seq=%u RSSI=%.1f dBm SNR=%.1f dB\n",
                    hdr->node_id, hdr->msg_type, hdr->seq, rssi, snr);
      
      blinkLed(50);
    }
  }

  // Resume listening
  radio.startReceive();
}

void setup() {
  Serial.begin(115200);
  delay(1500); // Wait for USB CDC to connect

  pinMode(PIN_USER_LED, OUTPUT);
  digitalWrite(PIN_USER_LED, LOW);

  Serial.println(F("\n=========================================="));
  Serial.println(F(" Mawson MX921 Bridge — Heltec V3 Node POC "));
  Serial.println(F("=========================================="));

  enableVext(true); // Turn on Vext rail
  setupRadio();
}

void loop() {
  if (rx_flag) {
    processIncomingPacket();
  }

  uint32_t now = millis();
  if (now - last_tx_ms >= TX_INTERVAL_BENCH_MS) {
    last_tx_ms = now;
    sendHeartbeatPing();
  }
}

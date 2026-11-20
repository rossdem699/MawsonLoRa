/*
 * Mawson MX921 LoRa Repeater Bridge — Gateway MCU Firmware
 * Target: Duinotech XC-4394 (ATmega328P + SX1276 LoRa Shield)
 * Serial Port: Connected to OpenWrt AR9331 (/dev/ttyATH0) at 115200 baud.
 */

#include <SPI.h>
#include <LoRa.h>

// Duinotech XC-4394 / Dragino LoRa Shield pinout on ATmega328P
#define LORA_CS_PIN      10
#define LORA_RST_PIN     9
#define LORA_IRQ_PIN     2

// Default LoRa RF Parameters
#define LORA_FREQUENCY   916800000L // 916.8 MHz (AU915 ISM)
#define LORA_DEFAULT_SF  9          // SF9 Default (~120ms airtime)
#define LORA_BW          125E3      // 125 kHz
#define LORA_CR          5          // 4/5
#define LORA_SYNC_WORD   0x12       // Private Sync Word 0x12
#define LORA_PREAMBLE    8

#define SERIAL_BAUD      115200
#define RX_BUFFER_MAX    400

static char serialBuf[RX_BUFFER_MAX];
static uint16_t serialBufIdx = 0;
static uint8_t currentSF = LORA_DEFAULT_SF;

uint8_t hexNibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return 0xFF;
}

void processIncomingLoRa() {
  int packetSize = LoRa.parsePacket();
  if (packetSize > 0) {
    int rssi = LoRa.packetRssi();
    float snr = LoRa.packetSnr();

    Serial.print(F("+RX:"));
    Serial.print(rssi);
    Serial.print(F(","));
    Serial.print(snr, 1);
    Serial.print(F(","));
    Serial.print(packetSize);
    Serial.print(F(","));

    while (LoRa.available()) {
      uint8_t b = LoRa.read();
      if (b < 0x10) Serial.print('0');
      Serial.print(b, HEX);
    }
    Serial.println();
  }
}

void handleTxCommand(const char* hexStr, uint16_t hexLen) {
  if (hexLen % 2 != 0 || hexLen == 0) {
    Serial.println(F("+ERR:INVALID_HEX_LEN"));
    return;
  }

  uint16_t byteCount = hexLen / 2;
  LoRa.beginPacket();
  for (uint16_t i = 0; i < byteCount; i++) {
    uint8_t hi = hexNibble(hexStr[i * 2]);
    uint8_t lo = hexNibble(hexStr[i * 2 + 1]);
    if (hi == 0xFF || lo == 0xFF) {
      Serial.println(F("+ERR:INVALID_HEX_CHAR"));
      return;
    }
    LoRa.write((hi << 4) | lo);
  }
  LoRa.endPacket();
  Serial.print(F("+OK:TX_DONE,"));
  Serial.println(byteCount);
}

void handleSetSf(uint8_t newSf) {
  if (newSf < 6 || newSf > 12) {
    Serial.println(F("+ERR:INVALID_SF"));
    return;
  }
  LoRa.setSpreadingFactor(newSf);
  currentSF = newSf;
  Serial.print(F("+OK:SF="));
  Serial.println(currentSF);
}

void processSerialInput() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r' || c == '\n') {
      if (serialBufIdx > 0) {
        serialBuf[serialBufIdx] = '\0';
        if (strncmp(serialBuf, "+TX:", 4) == 0) {
          handleTxCommand(serialBuf + 4, serialBufIdx - 4);
        } else if (strncmp(serialBuf, "+SF:", 4) == 0) {
          uint8_t sf = (uint8_t)atoi(serialBuf + 4);
          handleSetSf(sf);
        } else if (strcmp(serialBuf, "+PING") == 0) {
          Serial.print(F("+PONG,SF="));
          Serial.println(currentSF);
        } else {
          Serial.println(F("+ERR:UNKNOWN_CMD"));
        }
        serialBufIdx = 0;
      }
    } else {
      if (serialBufIdx < (RX_BUFFER_MAX - 1)) {
        serialBuf[serialBufIdx++] = c;
      }
    }
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(100);
  Serial.println(F("+INIT:Mawson_XC4394_Bridge_v2.0"));

  LoRa.setPins(LORA_CS_PIN, LORA_RST_PIN, LORA_IRQ_PIN);

  if (!LoRa.begin(LORA_FREQUENCY)) {
    Serial.println(F("+ERR:LORA_INIT_FAILED"));
    while (1) {
      delay(1000);
      Serial.println(F("+ERR:LORA_INIT_RETRY"));
      if (LoRa.begin(LORA_FREQUENCY)) break;
    }
  }

  LoRa.setSpreadingFactor(currentSF);
  LoRa.setSignalBandwidth(LORA_BW);
  LoRa.setCodingRate4(LORA_CR);
  LoRa.setSyncWord(LORA_SYNC_WORD);
  LoRa.setPreambleLength(LORA_PREAMBLE);
  LoRa.enableCrc();

  Serial.print(F("+OK:LORA_READY,SF="));
  Serial.println(currentSF);
}

void loop() {
  processIncomingLoRa();
  processSerialInput();
}

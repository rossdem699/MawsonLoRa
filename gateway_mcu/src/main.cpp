/*
 * Mawson MX921 LoRa Repeater Bridge — Gateway MCU Firmware
 * Target: Duinotech XC-4394 (ATmega328P + SX1276 LoRa Shield)
 * Serial Port: Connected to OpenWrt AR9331 (/dev/ttyATH0) at 115200 baud.
 *
 * Description:
 * Transparent, ultra-lightweight LoRa-to-UART bridge.
 * Formats incoming LoRa packets as ASCII Hex frames:
 *   +RX:<RSSI>,<SNR>,<LEN>,<HEX_STRING>\r\n
 * Transmits outgoing ASCII Hex frames from OpenWrt:
 *   +TX:<HEX_STRING>\r\n
 *
 * Keeps SRAM consumption minimal (< 800 bytes) to guarantee stability on ATmega328P (2KB SRAM).
 */

#include <SPI.h>
#include <LoRa.h>

// Duinotech XC-4394 / Dragino LoRa Shield pinout on ATmega328P
#define LORA_CS_PIN      10
#define LORA_RST_PIN     9
#define LORA_IRQ_PIN     2

// Fixed RF Parameters (Interoperable with SX1262)
#define LORA_FREQUENCY   916800000L // 916.8 MHz
#define LORA_SF          10
#define LORA_BW          125E3
#define LORA_CR          5          // 4/5
#define LORA_SYNC_WORD   0x12       // Sync word 0x12
#define LORA_PREAMBLE    8

#define SERIAL_BAUD      115200
#define RX_BUFFER_MAX    128

char serialBuf[RX_BUFFER_MAX];
uint8_t serialBufIdx = 0;

void setup() {
  Serial.begin(SERIAL_BAUD);
  // Wait brief period for UART stabilisation
  delay(100);
  Serial.println(F("+INIT:Mawson_XC4394_Bridge_v1.0"));

  LoRa.setPins(LORA_CS_PIN, LORA_RST_PIN, LORA_IRQ_PIN);

  if (!LoRa.begin(LORA_FREQUENCY)) {
    Serial.println(F("+ERR:LORA_INIT_FAILED"));
    while (1) {
      delay(1000);
      Serial.println(F("+ERR:LORA_INIT_RETRY"));
      if (LoRa.begin(LORA_FREQUENCY)) break;
    }
  }

  LoRa.setSpreadingFactor(LORA_SF);
  LoRa.setSignalBandwidth(LORA_BW);
  LoRa.setCodingRate4(LORA_CR);
  LoRa.setSyncWord(LORA_SYNC_WORD);
  LoRa.setPreambleLength(LORA_PREAMBLE);
  LoRa.enableCrc();

  Serial.println(F("+OK:LORA_READY"));
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

uint8_t hexNibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return 0xFF;
}

void handleTxCommand(const char* hexStr, uint8_t hexLen) {
  if (hexLen % 2 != 0 || hexLen == 0) {
    Serial.println(F("+ERR:INVALID_HEX_LEN"));
    return;
  }

  uint8_t byteCount = hexLen / 2;
  LoRa.beginPacket();
  for (uint8_t i = 0; i < byteCount; i++) {
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

void processSerialInput() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r' || c == '\n') {
      if (serialBufIdx > 0) {
        serialBuf[serialBufIdx] = '\0';
        // Command check
        if (strncmp(serialBuf, "+TX:", 4) == 0) {
          handleTxCommand(serialBuf + 4, serialBufIdx - 4);
        } else if (strcmp(serialBuf, "+PING") == 0) {
          Serial.println(F("+PONG"));
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

void loop() {
  processIncomingLoRa();
  processSerialInput();
}

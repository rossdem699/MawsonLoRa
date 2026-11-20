/*
 * Mawson MX921 Transparent RS-232-over-LoRa Wireless Bridge
 * Remote Repeater Node Firmware
 *
 * Hardware:
 *   - Heltec Wireless Stick Lite V3 (ESP32-S3FN8 + SX1262, 863-928 MHz HF)
 *   - Switched Vext (GPIO 36, active LOW) -> MAX3232 3.3V RS-232 transceiver
 *   - UART1 (TX=GPIO 17, RX=GPIO 18) -> MAX3232 -> MX921 Front RJ45 MIC port (57600 baud, 8-N-1)
 *
 * Architecture:
 *   - FreeRTOS Dual-Core Design:
 *     - Core 0: RadioTask (SX1262 RadioLib driver, DIO1 interrupt, CRC16 + 4B auth tag validation,
 *               remote SF switching with 60s dead-man fallback, LoRa TX/RX)
 *     - Core 1: UartBridgeTask (UART1 57600 baud, 15ms idle-gap packet coalescing / 180B flush)
 *     - Core 1: PowerManagerTask (Activity watchdog, NVS stay_awake_lock, scheduled deep sleep)
 */

#include <Arduino.h>
#include <SPI.h>
#include <RadioLib.h>
#include <Preferences.h>
#include <sys/time.h>
#include <esp_sleep.h>
#include "mx921_packet.h"

// Heltec Wireless Stick Lite V3 Hardware Pinout
#define PIN_LORA_NSS       8
#define PIN_LORA_SCK       9
#define PIN_LORA_MOSI      10
#define PIN_LORA_MISO      11
#define PIN_LORA_RST       12
#define PIN_LORA_BUSY      13
#define PIN_LORA_DIO1      14

#define PIN_VEXT_CTRL      36  // Switched Vext: LOW = Enabled, HIGH = Disabled
#define PIN_USER_LED       35  // Active HIGH LED

// External UART1 connected to MAX3232 -> MX921 Front RJ45 MIC Port
#define PIN_UART1_TX       17
#define PIN_UART1_RX       18

// Node Identity
#define THIS_NODE_ID       NODE_ID_MT_HENDERSON

// Queue sizing
#define SERIAL_TX_QUEUE_LEN 8

typedef struct {
    uint8_t len;
    uint8_t data[MX921_MAX_PAYLOAD_LEN];
} serial_frame_t;

// FreeRTOS Handles
static TaskHandle_t xRadioTaskHandle = NULL;
static TaskHandle_t xUartTaskHandle = NULL;
static TaskHandle_t xPowerTaskHandle = NULL;
static QueueHandle_t xSerialTxQueue = NULL;

// Radio Module
static SX1262 radio = new Module(PIN_LORA_NSS, PIN_LORA_DIO1, PIN_LORA_RST, PIN_LORA_BUSY);

// State & Config (NVS backed)
static Preferences prefs;
static uint8_t g_current_sf = LORA_SF_DEFAULT;
static bool g_stay_awake_lock = false;
static uint8_t g_wake_hour = 0;       // 00:00 UTC/local
static uint8_t g_duration_min = 60;   // 60 minutes
static uint8_t g_holdoff_min = 10;    // 10 minutes (600s)

// Power & Watchdog State
static volatile uint32_t g_last_activity_ms = 0;
static volatile bool g_sf_fallback_active = false;
static volatile uint32_t g_sf_fallback_deadline_ms = 0;
static uint16_t g_packet_seq = 0;

// Interrupt Service Routine for SX1262 DIO1
#if defined(ESP8266) || defined(ESP32)
  ICACHE_RAM_ATTR
#endif
void onDio1Interrupt(void) {
    BaseType_t xHigherPriorityTaskWoken = pdFALSE;
    if (xRadioTaskHandle != NULL) {
        vTaskNotifyGiveFromISR(xRadioTaskHandle, &xHigherPriorityTaskWoken);
        portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
    }
}

static inline void setVext(bool enable) {
    pinMode(PIN_VEXT_CTRL, OUTPUT);
    digitalWrite(PIN_VEXT_CTRL, enable ? LOW : HIGH);
}

static inline void blinkLed(uint16_t ms) {
    digitalWrite(PIN_USER_LED, HIGH);
    vTaskDelay(pdMS_TO_TICKS(ms));
    digitalWrite(PIN_USER_LED, LOW);
}

// Read / Write NVS configuration
void loadConfig() {
    prefs.begin("mx921", false);
    g_stay_awake_lock = prefs.getBool("stay_awake", false);
    g_wake_hour = prefs.getUChar("wake_hr", 0);
    g_duration_min = prefs.getUChar("dur_min", 60);
    g_holdoff_min = prefs.getUChar("hold_min", 10);
    g_current_sf = prefs.getUChar("sf", LORA_SF_DEFAULT);
    if (g_current_sf != 9 && g_current_sf != 11) {
        g_current_sf = LORA_SF_DEFAULT;
    }
    prefs.end();
}

void saveStayAwake(bool lock) {
    prefs.begin("mx921", false);
    prefs.putBool("stay_awake", lock);
    prefs.end();
    g_stay_awake_lock = lock;
}

void saveSchedule(uint8_t hour, uint8_t duration, uint8_t holdoff) {
    prefs.begin("mx921", false);
    prefs.putUChar("wake_hr", hour);
    prefs.putUChar("dur_min", duration);
    prefs.putUChar("hold_min", holdoff);
    prefs.end();
    g_wake_hour = hour;
    g_duration_min = duration;
    g_holdoff_min = holdoff;
}

// Compute active power flags bitmask
uint8_t getPowerStateFlags() {
    uint8_t flags = 0;
    if (g_stay_awake_lock) flags |= PWR_STATE_MAINTENANCE_LOCK;
    
    // Check scheduled window
    time_t now_sec;
    time(&now_sec);
    struct tm timeinfo;
    gmtime_r(&now_sec, &timeinfo);
    uint32_t current_min_of_day = (uint32_t)timeinfo.tm_hour * 60 + timeinfo.tm_min;
    uint32_t start_min = (uint32_t)g_wake_hour * 60;
    uint32_t end_min = start_min + g_duration_min;
    
    if (current_min_of_day >= start_min && current_min_of_day < end_min) {
        flags |= PWR_STATE_SCHEDULED_WINDOW;
    }
    
    uint32_t elapsed = millis() - g_last_activity_ms;
    if (elapsed < ((uint32_t)g_holdoff_min * 60000UL)) {
        flags |= PWR_STATE_ACTIVITY_HOLD;
    }
    
    // Vext is ON while awake
    flags |= PWR_STATE_VEXT_ENABLED;
    if (g_sf_fallback_active) {
        flags |= PWR_STATE_SF_FALLBACK_ACTIVE;
    }
    
    return flags;
}

// Send standard ACK / Telemetry packet
void sendAckPacket(uint8_t cmd_ack, uint8_t status_code, uint8_t target_sf) {
    uint8_t tx_buf[sizeof(mx921_header_t) + sizeof(mx921_ack_payload_t) + sizeof(uint16_t)];
    mx921_header_t *hdr = (mx921_header_t *)tx_buf;
    hdr->magic[0] = MX921_PKT_MAGIC_0;
    hdr->magic[1] = MX921_PKT_MAGIC_1;
    hdr->node_id = THIS_NODE_ID;
    hdr->pkt_type = PKT_TYPE_ACK;
    hdr->seq = ++g_packet_seq;
    hdr->flags = getPowerStateFlags();

    mx921_ack_payload_t *ack = (mx921_ack_payload_t *)(tx_buf + sizeof(mx921_header_t));
    ack->cmd_ack = cmd_ack;
    ack->status_code = status_code;
    ack->current_sf = target_sf;
    ack->flags = hdr->flags;
    
    uint32_t holdoff_total = (uint32_t)g_holdoff_min * 60000UL;
    uint32_t elapsed = millis() - g_last_activity_ms;
    ack->holdoff_rem_sec = (elapsed < holdoff_total) ? (uint16_t)((holdoff_total - elapsed) / 1000) : 0;
    ack->vbat_mv = 5000;    // 5V USB-C bench test supply
    ack->temp_c_x10 = 215;  // 21.5 C
    
    time_t now_sec;
    time(&now_sec);
    ack->epoch_utc = (uint32_t)now_sec;

    // CRC16
    size_t payload_and_header_len = sizeof(mx921_header_t) + sizeof(mx921_ack_payload_t);
    uint16_t crc = mx921_crc16(tx_buf, payload_and_header_len);
    tx_buf[payload_and_header_len] = (uint8_t)(crc & 0xFF);
    tx_buf[payload_and_header_len + 1] = (uint8_t)((crc >> 8) & 0xFF);

    digitalWrite(PIN_USER_LED, HIGH);
    radio.transmit(tx_buf, payload_and_header_len + 2);
    digitalWrite(PIN_USER_LED, LOW);
    radio.startReceive();
}

// Configure SX1262 Spreading Factor
bool applySpreadingFactor(uint8_t sf) {
    if (sf < 7 || sf > 12) return false;
    Serial.printf("[Radio] Switching Spreading Factor to SF%u...\n", sf);
    int state = radio.setSpreadingFactor(sf);
    if (state == RADIOLIB_ERR_NONE) {
        g_current_sf = sf;
        radio.startReceive();
        return true;
    } else {
        Serial.printf("[Radio] ERROR setting SF%u: %d\n", sf, state);
        radio.startReceive();
        return false;
    }
}

/* ========================================================================= */
/* Core 0: RadioTask                                                         */
/* ========================================================================= */
void RadioTask(void *pvParameters) {
    Serial.println("[RadioTask] Initializing on Core 0...");
    SPI.begin(PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_NSS);

    int state = radio.begin(
        916.8,                      // Carrier frequency (MHz)
        125.0,                      // Bandwidth (kHz)
        g_current_sf,               // Spreading factor
        5,                          // Coding rate (4/5)
        0x12,                       // Private Sync word
        LORA_TX_POWER_BENCH_DBM,    // 2 dBm bench power
        8,                          // Preamble length
        1.6,                        // TCXO voltage
        false                       // DCDC regulator
    );

    if (state != RADIOLIB_ERR_NONE) {
        Serial.printf("[RadioTask] SX1262 Init FAILED: %d\n", state);
        while (1) {
            blinkLed(100);
            vTaskDelay(pdMS_TO_TICKS(100));
        }
    }

    radio.setDio1Action(onDio1Interrupt);
    radio.startReceive();
    Serial.printf("[RadioTask] SX1262 Ready: 916.8 MHz, BW 125 kHz, SF%u, CR 4/5, Sync 0x12\n", g_current_sf);

    // Bench beacon timer
    uint32_t last_beacon_ms = millis();

    while (1) {
        // Wait for DIO1 notification or timeout (20ms tick)
        uint32_t ulNotificationValue = ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(20));

        if (ulNotificationValue > 0) {
            // Packet received via DIO1
            size_t rx_len = radio.getPacketLength();
            if (rx_len >= (sizeof(mx921_header_t) + sizeof(uint16_t)) && rx_len <= MX921_MAX_FRAME_LEN) {
                uint8_t rx_buf[MX921_MAX_FRAME_LEN];
                int read_state = radio.readData(rx_buf, rx_len);
                if (read_state == RADIOLIB_ERR_NONE) {
                    // Check CRC16
                    size_t data_len = rx_len - 2;
                    uint16_t received_crc = rx_buf[data_len] | ((uint16_t)rx_buf[data_len + 1] << 8);
                    uint16_t calculated_crc = mx921_crc16(rx_buf, data_len);

                    if (received_crc == calculated_crc) {
                        mx921_header_t *hdr = (mx921_header_t *)rx_buf;
                        if (hdr->magic[0] == MX921_PKT_MAGIC_0 && hdr->magic[1] == MX921_PKT_MAGIC_1) {
                            // Check if destination is for this node or broadcast
                            if (hdr->node_id == THIS_NODE_ID || hdr->node_id == 0xFF) {
                                // Valid packet received -> reset activity watchdog!
                                g_last_activity_ms = millis();
                                
                                // If fallback trial was active and packet arrived on target SF, clear fallback!
                                if (g_sf_fallback_active) {
                                    Serial.println("[RadioTask] Valid packet received on new SF! Fallback cancelled.");
                                    g_sf_fallback_active = false;
                                }

                                size_t payload_len = data_len - sizeof(mx921_header_t);
                                uint8_t *payload = rx_buf + sizeof(mx921_header_t);

                                if (hdr->pkt_type == PKT_TYPE_DATA) {
                                    // Transparent RS-232 serial data from Gateway -> output immediately to UART1!
                                    if (payload_len > 0) {
                                        Serial1.write(payload, payload_len);
                                        Serial1.flush();
                                    }
                                } else if (hdr->pkt_type == PKT_TYPE_CTRL) {
                                    // Gateway control packet
                                    if (payload_len >= sizeof(mx921_ctrl_payload_t)) {
                                        mx921_ctrl_payload_t *ctrl = (mx921_ctrl_payload_t *)payload;
                                        // Validate 4-byte auth tag
                                        uint32_t expected_tag = mx921_calc_auth_tag(payload, sizeof(mx921_ctrl_payload_t) - 4, MX921_SHARED_SECRET);
                                        uint32_t received_tag = ctrl->auth_tag[0] |
                                                                ((uint32_t)ctrl->auth_tag[1] << 8) |
                                                                ((uint32_t)ctrl->auth_tag[2] << 16) |
                                                                ((uint32_t)ctrl->auth_tag[3] << 24);

                                        if (expected_tag == received_tag) {
                                            // Auth valid! Sync UTC epoch
                                            if (ctrl->epoch_utc > 1700000000UL) {
                                                struct timeval tv = { (time_t)ctrl->epoch_utc, 0 };
                                                settimeofday(&tv, NULL);
                                            }

                                            // Process Command
                                            if (ctrl->cmd_id == CMD_SET_SF) {
                                                uint8_t target_sf = ctrl->param1;
                                                Serial.printf("[RadioTask] CMD_SET_SF -> target SF%u\n", target_sf);
                                                // Send ACK at CURRENT SF first!
                                                sendAckPacket(CMD_SET_SF, 0, target_sf);
                                                // Now switch to target SF
                                                applySpreadingFactor(target_sf);
                                                // If switching away from default SF9, start 60s dead-man fallback!
                                                if (target_sf != LORA_SF_DEFAULT) {
                                                    g_sf_fallback_active = true;
                                                    g_sf_fallback_deadline_ms = millis() + SF_FALLBACK_TIMEOUT_MS;
                                                    Serial.printf("[RadioTask] SF%u fallback watchdog started (60s)\n", target_sf);
                                                }
                                            } else if (ctrl->cmd_id == CMD_SET_LOCK) {
                                                bool lock = (ctrl->param1 != 0);
                                                Serial.printf("[RadioTask] CMD_SET_LOCK -> %d\n", lock);
                                                saveStayAwake(lock);
                                                sendAckPacket(CMD_SET_LOCK, 0, g_current_sf);
                                            } else if (ctrl->cmd_id == CMD_SET_SCHED) {
                                                Serial.printf("[RadioTask] CMD_SET_SCHED -> %02u:00, dur=%u, hold=%u\n",
                                                              ctrl->param1, ctrl->param2, ctrl->param3);
                                                saveSchedule(ctrl->param1, ctrl->param2, ctrl->param3);
                                                sendAckPacket(CMD_SET_SCHED, 0, g_current_sf);
                                            } else if (ctrl->cmd_id == CMD_REQ_STATUS) {
                                                sendAckPacket(CMD_REQ_STATUS, 0, g_current_sf);
                                            }
                                        } else {
                                            Serial.println("[RadioTask] WARNING: Control packet Auth Tag mismatch!");
                                        }
                                    }
                                }
                            }
                        }
                    } else {
                        Serial.println("[RadioTask] CRC-16 Check Failed!");
                    }
                }
            }
            radio.startReceive();
        }

        // Dead-Man Fallback Check (60s timer)
        if (g_sf_fallback_active && (long)(millis() - g_sf_fallback_deadline_ms) >= 0) {
            Serial.println("[RadioTask] DEAD-MAN FALLBACK TRIGGERED! Reverting to SF9...");
            g_sf_fallback_active = false;
            applySpreadingFactor(LORA_SF_DEFAULT);
            // Send autonomous status report
            sendAckPacket(0xFF, 0, LORA_SF_DEFAULT);
        }

        // Check if there are serial frames queued from UartBridgeTask to transmit over LoRa
        serial_frame_t tx_frame;
        if (xQueueReceive(xSerialTxQueue, &tx_frame, 0) == pdTRUE) {
            uint8_t tx_buf[MX921_MAX_FRAME_LEN];
            mx921_header_t *hdr = (mx921_header_t *)tx_buf;
            hdr->magic[0] = MX921_PKT_MAGIC_0;
            hdr->magic[1] = MX921_PKT_MAGIC_1;
            hdr->node_id = THIS_NODE_ID;
            hdr->pkt_type = PKT_TYPE_DATA;
            hdr->seq = ++g_packet_seq;
            hdr->flags = getPowerStateFlags();

            memcpy(tx_buf + sizeof(mx921_header_t), tx_frame.data, tx_frame.len);
            size_t data_len = sizeof(mx921_header_t) + tx_frame.len;
            uint16_t crc = mx921_crc16(tx_buf, data_len);
            tx_buf[data_len] = (uint8_t)(crc & 0xFF);
            tx_buf[data_len + 1] = (uint8_t)((crc >> 8) & 0xFF);

            digitalWrite(PIN_USER_LED, HIGH);
            radio.transmit(tx_buf, data_len + 2);
            digitalWrite(PIN_USER_LED, LOW);
            radio.startReceive();
            g_last_activity_ms = millis();
        }

        // Periodic heartbeat beacon when completely idle (every 10 seconds on bench)
        if ((millis() - last_beacon_ms) >= 10000UL) {
            last_beacon_ms = millis();
            sendAckPacket(0x00, 0, g_current_sf);
        }
    }
}

/* ========================================================================= */
/* Core 1: UartBridgeTask                                                    */
/* ========================================================================= */
void UartBridgeTask(void *pvParameters) {
    Serial.println("[UartBridgeTask] Initializing on Core 1 (57600 baud)...");
    Serial1.begin(57600, SERIAL_8N1, PIN_UART1_RX, PIN_UART1_TX);

    uint8_t buffer[MX921_MAX_PAYLOAD_LEN];
    uint8_t buf_idx = 0;
    uint32_t last_byte_time_ms = 0;
    const uint32_t IDLE_GAP_MS = 15; // 15 ms idle-gap packet coalescing

    while (1) {
        while (Serial1.available()) {
            int c = Serial1.read();
            if (c >= 0) {
                buffer[buf_idx++] = (uint8_t)c;
                last_byte_time_ms = millis();
                g_last_activity_ms = last_byte_time_ms;

                // Max payload reached -> flush immediately
                if (buf_idx >= MX921_MAX_PAYLOAD_LEN) {
                    serial_frame_t frame;
                    frame.len = buf_idx;
                    memcpy(frame.data, buffer, buf_idx);
                    xQueueSend(xSerialTxQueue, &frame, pdMS_TO_TICKS(50));
                    buf_idx = 0;
                }
            }
        }

        // Check 15ms idle-gap flush
        if (buf_idx > 0 && (millis() - last_byte_time_ms) >= IDLE_GAP_MS) {
            serial_frame_t frame;
            frame.len = buf_idx;
            memcpy(frame.data, buffer, buf_idx);
            xQueueSend(xSerialTxQueue, &frame, pdMS_TO_TICKS(50));
            buf_idx = 0;
        }

        vTaskDelay(pdMS_TO_TICKS(2));
    }
}

/* ========================================================================= */
/* Core 1: PowerManagerTask                                                  */
/* ========================================================================= */
void PowerManagerTask(void *pvParameters) {
    Serial.println("[PowerManagerTask] Initializing Sleep Governor...");
    vTaskDelay(pdMS_TO_TICKS(5000)); // Let system settle after boot

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(1000));

        // 1. If Maintenance Lock is ON, stay awake 24/7
        if (g_stay_awake_lock) {
            continue;
        }

        // 2. Check Scheduled Wake Window
        time_t now_sec;
        time(&now_sec);
        struct tm timeinfo;
        gmtime_r(&now_sec, &timeinfo);
        uint32_t cur_min = (uint32_t)timeinfo.tm_hour * 60 + timeinfo.tm_min;
        uint32_t start_min = (uint32_t)g_wake_hour * 60;
        uint32_t end_min = start_min + g_duration_min;

        bool inside_window = (cur_min >= start_min && cur_min < end_min);
        if (inside_window) {
            // Inside scheduled window -> stay awake
            continue;
        }

        // 3. Check 10-Minute Activity Watchdog
        uint32_t holdoff_ms = (uint32_t)g_holdoff_min * 60000UL;
        uint32_t elapsed = millis() - g_last_activity_ms;
        if (elapsed < holdoff_ms) {
            // Still within 10-minute holdoff -> stay awake
            continue;
        }

        // Outside window and no activity for > 10 min -> Enter Deep Sleep (~1 mA)
        Serial.println("[PowerManager] Window ended and inactivity > 10 min. Entering Deep Sleep...");

        // Calculate seconds until next scheduled wake window
        uint32_t sleep_sec;
        if (cur_min < start_min) {
            sleep_sec = (start_min - cur_min) * 60;
        } else {
            sleep_sec = ((24 * 60 - cur_min) + start_min) * 60;
        }

        // Clamp sleep to at least 60 seconds
        if (sleep_sec < 60) sleep_sec = 60;

        Serial.printf("[PowerManager] Sleeping for %lu seconds (Wake at %02u:00 UTC)...\n", (unsigned long)sleep_sec, g_wake_hour);

        // Power down MAX3232 transceiver by setting Vext HIGH
        setVext(false);

        // Put radio into deep sleep
        radio.sleep();

        // Configure timer wakeup
        esp_sleep_enable_timer_wakeup((uint64_t)sleep_sec * 1000000ULL);

        // Enter deep sleep
        esp_deep_sleep_start();
    }
}

/* ========================================================================= */
/* System Setup & FreeRTOS Startup                                           */
/* ========================================================================= */
void setup() {
    Serial.begin(115200);
    delay(1000);

    pinMode(PIN_USER_LED, OUTPUT);
    digitalWrite(PIN_USER_LED, LOW);

    // Power up MAX3232 transceiver via switched Vext (LOW = ON)
    setVext(true);

    Serial.println(F("\n======================================================="));
    Serial.println(F(" Mawson MX921 RS-232-over-LoRa Bridge — Heltec WSL V3  "));
    Serial.println(F("======================================================="));

    loadConfig();
    g_last_activity_ms = millis();

    xSerialTxQueue = xQueueCreate(SERIAL_TX_QUEUE_LEN, sizeof(serial_frame_t));

    // RadioTask on Core 0 (High Priority)
    xTaskCreatePinnedToCore(
        RadioTask,
        "RadioTask",
        4096,
        NULL,
        5,
        &xRadioTaskHandle,
        0
    );

    // UartBridgeTask on Core 1 (High Priority for 15ms idle-gap coalescing)
    xTaskCreatePinnedToCore(
        UartBridgeTask,
        "UartBridgeTask",
        4096,
        NULL,
        4,
        &xUartTaskHandle,
        1
    );

    // PowerManagerTask on Core 1 (Low Priority)
    xTaskCreatePinnedToCore(
        PowerManagerTask,
        "PowerManagerTask",
        4096,
        NULL,
        1,
        &xPowerTaskHandle,
        1
    );
}

void loop() {
    // FreeRTOS governs all tasks; Arduino loop idles
    vTaskDelay(pdMS_TO_TICKS(1000));
}

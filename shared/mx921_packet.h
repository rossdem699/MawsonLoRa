#ifndef MX921_PACKET_H
#define MX921_PACKET_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

// Packet Magic Identifier ("MX")
#define MX921_PKT_MAGIC_0        0x4D // 'M'
#define MX921_PKT_MAGIC_1        0x58 // 'X'
#define MX921_PKT_MAGIC          0x4D58

// Node IDs
#define NODE_ID_GATEWAY          0x00 // Mawson Base Gateway (XC-4394)
#define NODE_ID_MT_HENDERSON     0x01 // Node 1: Mt Henderson (~13 km SSE)
#define NODE_ID_MT_PARSONS       0x02 // Node 2: Mt Parsons

// Message Types
typedef enum {
    MSG_TYPE_PING                = 0x01, // Node periodic wake heartbeat / ping
    MSG_TYPE_ACK                 = 0x02, // Gateway ACK (with UTC epoch time sync + flags)
    MSG_TYPE_STATUS_TELEMETRY    = 0x03, // Full telemetry report (RF, alarms, analog RSSI)
    MSG_TYPE_COMMAND_QUEUE       = 0x04, // Gateway queued command to node
    MSG_TYPE_RMENU_CMD           = 0x05, // MX921 RMENU command payload
    MSG_TYPE_RMENU_RESP          = 0x06, // MX921 RMENU response payload
    MSG_TYPE_ALARM_CRITICAL      = 0x07, // Emergency EXT1 alarm wake broadcast
    MSG_TYPE_CONFIG_SYNC         = 0x08  // Schedule / Maintenance Lock update
} mx921_msg_type_t;

// Power & Sleep State Flags (1 Byte Bitfield)
#define PWR_STATE_SCHEDULED_WINDOW   (1 << 0) // Currently inside scheduled wake window
#define PWR_STATE_ACTIVITY_HOLD      (1 << 1) // 10-minute activity hold-off active
#define PWR_STATE_MAINTENANCE_LOCK   (1 << 2) // "Stay Awake" maintenance lock active
#define PWR_STATE_ALARM_EXT1_WAKE    (1 << 3) // Woken by EXT1 hardware alarm
#define PWR_STATE_REPEATER_INHIBITED (1 << 4) // Hardware knockdown/inhibit line asserted
#define PWR_STATE_VEXT_ENABLED       (1 << 5) // Switched 3.3V rail active (MAX3232 ON)

// Fixed LoRa Radio Parameters (SX1262 <-> SX1276 Interoperable)
#define LORA_FREQ_DEFAULT_HZ         916800000UL // 916.8 MHz (AU915 ISM)
#define LORA_BANDWIDTH_KHZ           125.0       // 125 kHz
#define LORA_SPREADING_FACTOR        10          // SF10
#define LORA_CODING_RATE             5           // 4/5
#define LORA_SYNC_WORD               0x12        // Standard LoRa private sync word
#define LORA_PREAMBLE_LEN            8
#define LORA_TX_POWER_BENCH_DBM      2           // Initial bench test low power
#define LORA_TX_POWER_MAX_DBM        22          // Max PA boost for field deploy

// Maximum payload buffer size (to stay within ATmega328P 2KB SRAM limits)
#define MX921_MAX_PAYLOAD_LEN        64

// Packet Header (7 Bytes)
// [Magic 2B][NodeID 1B][MsgType 1B][Seq 2B][PowerState 1B]
#pragma pack(push, 1)
typedef struct {
    uint8_t  magic[2];    // 'M', 'X' (0x4D, 0x58)
    uint8_t  node_id;     // Source / Dest node ID
    uint8_t  msg_type;    // mx921_msg_type_t
    uint16_t seq;         // Sequence counter
    uint8_t  power_state; // Bitmask of PWR_STATE_*
} mx921_header_t;

// Standard Gateway ACK Payload
typedef struct {
    uint32_t epoch_utc;        // NTP synchronized UTC epoch from OpenWrt
    uint16_t wake_window_sec;  // Remaining scheduled wake window or config
    uint8_t  pending_cmds;     // Number of queued commands waiting for node
    uint8_t  flags;            // Bit 0: Set Stay Awake Lock, Bit 1: Force Poll
} mx921_ack_payload_t;

// Periodic Heartbeat / Ping Payload
typedef struct {
    uint16_t vbat_mv;          // Battery / supply voltage in mV
    int16_t  temp_c_x10;       // Temperature in 0.1 deg C
    uint16_t rx_rssi_raw;      // Raw 12-bit ADC reading from MX921 CN3 RSSI divider
    uint8_t  alarm_flags;      // Bit 0: Alarm1, Bit 1: Alarm2, Bit 2: COR/Mute
    uint8_t  active_channel;   // Active MX921 channel number (1-99)
} mx921_ping_payload_t;

// Packet Trailer (6 Bytes: 4B Truncated HMAC + 2B CRC16)
typedef struct {
    uint8_t  auth_tag[4]; // Truncated HMAC-SHA256 (or keyed lightweight hash)
    uint16_t crc16;       // CCITT CRC-16 over Header + Payload + AuthTag
} mx921_trailer_t;
#pragma pack(pop)

// CRC-16 (CCITT: polynomial 0x1021, init 0xFFFF) calculation
static inline uint16_t mx921_crc16_ccitt(const uint8_t *data, size_t length) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < length; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (uint8_t bit = 0; bit < 8; bit++) {
            if (crc & 0x8000) {
                crc = (crc << 1) ^ 0x1021;
            } else {
                crc = crc << 1;
            }
        }
    }
    return crc;
}

#ifdef __cplusplus
}
#endif

#endif // MX921_PACKET_H

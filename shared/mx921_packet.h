#ifndef MX921_PACKET_H
#define MX921_PACKET_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// Packet Magic Identifier ("MX" = 0x4D, 0x58)
#define MX921_PKT_MAGIC_0            0x4D // 'M'
#define MX921_PKT_MAGIC_1            0x58 // 'X'
#define MX921_PKT_MAGIC              0x4D58

// Node IDs
#define NODE_ID_GATEWAY              0x00 // Mawson Base Gateway (XC-4394)
#define NODE_ID_MT_HENDERSON         0x01 // Node 1: Mt Henderson (~13 km SSE)
#define NODE_ID_MT_PARSONS           0x02 // Node 2: Mt Parsons

// Packet Types (1 Byte)
#define PKT_TYPE_DATA                0x01 // Transparent RS-232 serial payload (900Tools / RMENU)
#define PKT_TYPE_CTRL                0x02 // Gateway control command to node
#define PKT_TYPE_ACK                 0x03 // Node ACK / status / telemetry sync to gateway
#define PKT_TYPE_PING                0x04 // Periodic heartbeat / keepalive

// Control Command IDs (Inside PKT_TYPE_CTRL payload)
#define CMD_SET_SF                   0x10 // Change Spreading Factor (Payload: [TargetSF 1B])
#define CMD_SET_LOCK                 0x11 // Set/Clear Stay Awake Maintenance Lock (Payload: [Lock 1B: 0/1])
#define CMD_SET_SCHED                0x12 // Update Schedule (Payload: [Hour 1B][DurationMin 1B][HoldMin 1B])
#define CMD_REQ_STATUS               0x13 // Request immediate status / telemetry report
#define CMD_REBOOT_NODE              0x14 // Reboot node (Payload: [DelaySec 1B])

// Power & Sleep State Flags (1 Byte Bitfield in Header Flags)
#define PWR_STATE_SCHEDULED_WINDOW   (1 << 0) // Currently inside scheduled daily wake window
#define PWR_STATE_ACTIVITY_HOLD      (1 << 1) // 10-minute activity hold-off active
#define PWR_STATE_MAINTENANCE_LOCK   (1 << 2) // "Stay Awake" maintenance lock active
#define PWR_STATE_VEXT_ENABLED       (1 << 3) // Switched 3.3V rail active (MAX3232 ON)
#define PWR_STATE_SF_FALLBACK_ACTIVE (1 << 4) // Currently in 60s dead-man SF fallback trial

// Fixed LoRa Radio Parameters (SX1262 <-> SX1276 Interoperable)
#define LORA_FREQ_DEFAULT_HZ         916800000UL // 916.8 MHz (AU915 ISM)
#define LORA_BANDWIDTH_KHZ           125.0       // 125 kHz
#define LORA_SF_DEFAULT              9           // SF9 Default (~120ms airtime, <500ms RTT)
#define LORA_SF_STORM                11          // SF11 Storm Mode (robust long-range in extreme blizzards)
#define LORA_CODING_RATE             5           // 4/5
#define LORA_SYNC_WORD               0x12        // Standard LoRa private sync word
#define LORA_PREAMBLE_LEN            8
#define LORA_TX_POWER_BENCH_DBM      2           // Bench test low power (2 dBm)
#define LORA_TX_POWER_MAX_DBM        22          // Max PA boost for field deploy

// Maximum payload buffer sizes
#define MX921_MAX_PAYLOAD_LEN        180
#define MX921_MAX_FRAME_LEN          (sizeof(mx921_header_t) + MX921_MAX_PAYLOAD_LEN + sizeof(uint16_t))

// Dead-Man Fallback Timeout
#define SF_FALLBACK_TIMEOUT_MS       60000UL     // 60 seconds fallback timeout
#define ACTIVITY_WATCHDOG_MS         600000UL    // 10 minutes (600s) activity holdoff

// Shared Secret Key for Control Authentication (8 bytes)
#define MX921_SHARED_SECRET          "MWS921LK"

// Packet Header (7 Bytes)
// [Magic 2B][NodeID 1B][Type 1B][Seq 2B][Flags 1B]
#pragma pack(push, 1)
typedef struct {
    uint8_t  magic[2];    // 'M', 'X' (0x4D, 0x58)
    uint8_t  node_id;     // Source (if node) or Dest (if gateway) Node ID
    uint8_t  pkt_type;    // PKT_TYPE_DATA, PKT_TYPE_CTRL, PKT_TYPE_ACK, PKT_TYPE_PING
    uint16_t seq;         // Sequence counter
    uint8_t  flags;       // Bitmask of PWR_STATE_*
} mx921_header_t;

// Control Packet Payload Format (Header + Payload <= 180B + CRC16 2B)
typedef struct {
    uint8_t  cmd_id;           // CMD_SET_SF, CMD_SET_LOCK, etc.
    uint8_t  param1;           // e.g. target_sf or lock_val or wake_hour
    uint8_t  param2;           // e.g. duration_min
    uint8_t  param3;           // e.g. hold_min
    uint32_t epoch_utc;        // Current gateway UTC epoch timestamp
    uint8_t  auth_tag[4];      // 4-byte HMAC / keyed auth tag
} mx921_ctrl_payload_t;

// Standard ACK / Telemetry Payload Format
typedef struct {
    uint8_t  cmd_ack;          // Command ID being acknowledged (or 0 for periodic ping)
    uint8_t  status_code;      // 0 = OK, non-zero = error
    uint8_t  current_sf;       // Current active SF (9 or 11)
    uint8_t  flags;            // Node state flags (PWR_STATE_*)
    uint16_t holdoff_rem_sec;  // Remaining seconds on 10-minute activity hold-off
    uint16_t vbat_mv;          // Battery / supply voltage in mV (5000 on bench)
    int16_t  temp_c_x10;       // Temperature in 0.1 deg C
    uint32_t epoch_utc;        // Last synchronized node epoch
} mx921_ack_payload_t;
#pragma pack(pop)

// CCITT CRC-16 (Polynomial 0x1021, init 0xFFFF)
static inline uint16_t mx921_crc16(const uint8_t *data, size_t length) {
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

// 4-byte keyed HMAC / auth tag calculation (FNV-1a 32-bit keyed hash)
// Computes a deterministic, lightweight 4-byte auth tag over data using a shared secret
static inline uint32_t mx921_calc_auth_tag(const uint8_t *data, size_t length, const char *secret) {
    uint32_t hash = 2166136261UL; // FNV-1a 32-bit offset basis
    
    // Mix in secret first
    if (secret) {
        while (*secret) {
            hash ^= (uint8_t)(*secret++);
            hash *= 16777619UL;
        }
    }
    
    // Mix in data
    for (size_t i = 0; i < length; i++) {
        hash ^= data[i];
        hash *= 16777619UL;
    }
    
    return hash;
}

#ifdef __cplusplus
}
#endif

#endif // MX921_PACKET_H

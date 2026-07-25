#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace petcare {

constexpr std::size_t max_provisioning_frame_bytes = 768;
constexpr std::uint64_t provisioning_frame_idle_timeout_ms = 1'000;
constexpr std::size_t max_product_id_bytes = 11;
constexpr std::size_t max_ssid_bytes = 32;
constexpr std::size_t max_wifi_password_bytes = 63;
constexpr std::size_t max_mqtt_host_bytes = 253;
constexpr std::size_t max_mqtt_ipv4_bytes = 15;
constexpr std::size_t max_mqtt_username_bytes = 64;
constexpr std::size_t max_mqtt_password_bytes = 128;
constexpr std::uint32_t provisioning_record_magic = 0x31544550U;

enum class ProvisioningKind : std::uint8_t {
    hello = 1,
    config = 2,
    ack = 3,
    error = 4,
    diagnostics = 5,
};

enum class ProvisioningError {
    none,
    too_large,
    bad_magic,
    bad_version,
    bad_kind,
    bad_length,
    bad_crc,
    embedded_nul,
    invalid_utf8,
    invalid_field_length,
    wrong_product,
    invalid_port,
    generation_overflow,
};

enum class RuntimePhase : std::uint8_t {
    awaiting_provisioning = 0,
    wifi_connecting = 1,
    time_syncing = 2,
    mqtt_connecting = 3,
    online = 4,
    backoff = 5,
};

enum class RuntimeError : std::uint8_t {
    none = 0,
    wifi = 1,
    time_sync = 2,
    mqtt = 3,
    publish = 4,
    sensor = 5,
};

struct RuntimeDiagnostics {
    RuntimePhase phase = RuntimePhase::awaiting_provisioning;
    RuntimeError error = RuntimeError::none;
    std::int8_t wifi_link_status = 0;
    bool watchdog_reboot = false;
};

struct ProvisioningConfig {
    std::array<char, max_product_id_bytes + 1> product_id{};
    std::size_t product_id_size = 0;
    std::array<char, max_ssid_bytes + 1> ssid{};
    std::size_t ssid_size = 0;
    std::array<char, max_wifi_password_bytes + 1> wifi_password{};
    std::size_t wifi_password_size = 0;
    std::array<char, max_mqtt_host_bytes + 1> mqtt_host{};
    std::size_t mqtt_host_size = 0;
    std::array<char, max_mqtt_username_bytes + 1> mqtt_username{};
    std::size_t mqtt_username_size = 0;
    std::array<char, max_mqtt_password_bytes + 1> mqtt_password{};
    std::size_t mqtt_password_size = 0;
    std::uint16_t mqtt_port = 0;
};

struct ProvisioningResult {
    ProvisioningError error = ProvisioningError::bad_length;
    ProvisioningConfig config{};
    std::uint32_t configuration_crc = 0;
};

struct ProvisioningRecord {
    std::uint32_t magic = 0;
    std::uint32_t generation = 0;
    ProvisioningConfig config{};
    std::uint32_t crc = 0;
};

std::uint32_t crc32(const std::uint8_t* data, std::size_t size);

bool expire_idle_provisioning_frame(
    std::uint8_t* data,
    std::size_t capacity,
    std::size_t& size,
    std::uint64_t last_byte_ms,
    std::uint64_t now_ms);

ProvisioningResult parse_provisioning_frame(
    const std::uint8_t* data,
    std::size_t size,
    std::string_view expected_device_id);

std::size_t encode_provisioning_frame(
    ProvisioningKind kind,
    const std::uint8_t* payload,
    std::size_t payload_size,
    std::uint8_t* output,
    std::size_t output_capacity);

std::size_t encode_ack_frame(
    std::string_view product_id,
    std::uint32_t configuration_crc,
    std::uint8_t* output,
    std::size_t output_capacity);

std::size_t encode_diagnostics_frame(
    const RuntimeDiagnostics& diagnostics,
    std::uint8_t* output,
    std::size_t output_capacity);

std::uint32_t provisioning_record_crc(const ProvisioningRecord& record);
ProvisioningError build_next_provisioning_record(
    const ProvisioningRecord* current,
    const ProvisioningConfig& config,
    ProvisioningRecord& output);
const ProvisioningRecord* newest_valid_record(
    const ProvisioningRecord& a,
    const ProvisioningRecord& b);

}

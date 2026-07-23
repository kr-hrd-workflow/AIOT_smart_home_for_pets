#include "provisioning.hpp"

#include <algorithm>
#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <new>
#include <string>
#include <string_view>
#include <vector>

namespace {

std::size_t allocations = 0;

void append_u16(std::vector<std::uint8_t>& output, std::uint16_t value) {
    output.push_back(static_cast<std::uint8_t>(value));
    output.push_back(static_cast<std::uint8_t>(value >> 8U));
}

void append_string(std::vector<std::uint8_t>& output, std::string_view value) {
    append_u16(output, static_cast<std::uint16_t>(value.size()));
    output.insert(output.end(), value.begin(), value.end());
}

std::vector<std::uint8_t> frame(
    std::string_view product_id = "entrance-01",
    std::string_view ssid = "Home WiFi",
    std::string_view wifi_password = "wifi-secret",
    std::string_view mqtt_host = "192.168.1.20",
    std::string_view mqtt_username = "entrance-01",
    std::string_view mqtt_password = "mqtt-secret",
    std::uint16_t mqtt_port = 18883) {
    std::vector<std::uint8_t> payload;
    append_string(payload, product_id);
    append_string(payload, ssid);
    append_string(payload, wifi_password);
    append_string(payload, mqtt_host);
    append_string(payload, mqtt_username);
    append_string(payload, mqtt_password);
    append_u16(payload, mqtt_port);

    std::vector<std::uint8_t> output{'P', 'E', 'T', '1', 1, 2};
    append_u16(output, static_cast<std::uint16_t>(payload.size()));
    output.insert(output.end(), payload.begin(), payload.end());
    const auto checksum = petcare::crc32(output.data(), output.size());
    output.push_back(static_cast<std::uint8_t>(checksum));
    output.push_back(static_cast<std::uint8_t>(checksum >> 8U));
    output.push_back(static_cast<std::uint8_t>(checksum >> 16U));
    output.push_back(static_cast<std::uint8_t>(checksum >> 24U));
    return output;
}

bool contains(const std::uint8_t* data, std::size_t size, std::string_view needle) {
    return std::search(data, data + size, needle.begin(), needle.end()) != data + size;
}

petcare::ProvisioningConfig valid_config(std::string_view product_id = "entrance-01") {
    petcare::ProvisioningConfig config{};
    std::copy(product_id.begin(), product_id.end(), config.product_id.begin());
    config.product_id_size = product_id.size();
    constexpr std::string_view ssid = "Home WiFi";
    std::copy(ssid.begin(), ssid.end(), config.ssid.begin());
    config.ssid_size = ssid.size();
    constexpr std::string_view wifi_password = "wifi-secret";
    std::copy(wifi_password.begin(), wifi_password.end(), config.wifi_password.begin());
    config.wifi_password_size = wifi_password.size();
    constexpr std::string_view mqtt_host = "192.168.1.20";
    std::copy(mqtt_host.begin(), mqtt_host.end(), config.mqtt_host.begin());
    config.mqtt_host_size = mqtt_host.size();
    std::copy(product_id.begin(), product_id.end(), config.mqtt_username.begin());
    config.mqtt_username_size = product_id.size();
    constexpr std::string_view mqtt_password = "mqtt-secret";
    std::copy(mqtt_password.begin(), mqtt_password.end(), config.mqtt_password.begin());
    config.mqtt_password_size = mqtt_password.size();
    config.mqtt_port = 18883;
    return config;
}

petcare::ProvisioningRecord record(std::uint32_t generation, bool valid = true) {
    petcare::ProvisioningRecord output{};
    output.magic = petcare::provisioning_record_magic;
    output.generation = generation;
    output.config = valid_config();
    output.crc = petcare::provisioning_record_crc(output);
    if (!valid) {
        ++output.crc;
    }
    return output;
}

void test_crc_and_little_endian_encoder() {
    constexpr std::array<std::uint8_t, 9> input{'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    assert(petcare::crc32(input.data(), input.size()) == 0xCBF43926U);

    const std::array<std::uint8_t, 2> payload{0x34, 0x12};
    std::array<std::uint8_t, petcare::max_provisioning_frame_bytes> output{};
    const auto size = petcare::encode_provisioning_frame(
        petcare::ProvisioningKind::hello, payload.data(), payload.size(), output.data(), output.size());
    assert(size == 14);
    assert((std::array<std::uint8_t, 8>{
                output[0], output[1], output[2], output[3],
                output[4], output[5], output[6], output[7]} ==
            std::array<std::uint8_t, 8>{'P', 'E', 'T', '1', 1, 1, 2, 0}));
    const auto checksum = petcare::crc32(output.data(), 10);
    assert(output[10] == static_cast<std::uint8_t>(checksum));
    assert(output[11] == static_cast<std::uint8_t>(checksum >> 8U));
    assert(output[12] == static_cast<std::uint8_t>(checksum >> 16U));
    assert(output[13] == static_cast<std::uint8_t>(checksum >> 24U));

    const std::uint8_t byte = 0;
    assert(petcare::encode_provisioning_frame(
               petcare::ProvisioningKind::config,
               &byte,
               std::numeric_limits<std::size_t>::max(),
               output.data(),
               output.size()) == 0);
}

void test_idle_partial_frame_expiry_zeroes_and_resets() {
    std::array<std::uint8_t, 64> buffered{};
    std::fill(buffered.begin(), buffered.end(), 0xA5U);
    std::size_t size = 32;
    constexpr std::uint64_t last_byte_ms = 4'000;

    assert(!petcare::expire_idle_provisioning_frame(
        buffered.data(), buffered.size(), size, last_byte_ms,
        last_byte_ms + petcare::provisioning_frame_idle_timeout_ms - 1));
    assert(size == 32);
    assert(std::all_of(
        buffered.begin(), buffered.begin() + 32,
        [](std::uint8_t value) { return value == 0xA5U; }));

    assert(petcare::expire_idle_provisioning_frame(
        buffered.data(), buffered.size(), size, last_byte_ms,
        last_byte_ms + petcare::provisioning_frame_idle_timeout_ms));
    assert(size == 0);
    assert(std::all_of(
        buffered.begin(), buffered.begin() + 32,
        [](std::uint8_t value) { return value == 0; }));
    assert(buffered[32] == 0xA5U);
}

void test_parse_valid_config_without_allocating() {
    const auto input = frame();
    const auto before = allocations;
    const auto result =
        petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01");
    assert(allocations == before);
    assert(result.error == petcare::ProvisioningError::none);
    assert((std::string_view{result.config.product_id.data(), result.config.product_id_size} ==
            "entrance-01"));
    assert((std::string_view{result.config.ssid.data(), result.config.ssid_size} ==
            "Home WiFi"));
    assert((std::string_view{
                result.config.wifi_password.data(), result.config.wifi_password_size} ==
            "wifi-secret"));
    assert((std::string_view{result.config.mqtt_host.data(), result.config.mqtt_host_size} ==
            "192.168.1.20"));
    assert((std::string_view{
                result.config.mqtt_username.data(), result.config.mqtt_username_size} ==
            "entrance-01"));
    assert((std::string_view{
                result.config.mqtt_password.data(), result.config.mqtt_password_size} ==
            "mqtt-secret"));
    assert(result.config.mqtt_port == 18883);
    assert(result.configuration_crc == petcare::crc32(input.data(), input.size() - 4));
}

void test_reject_wrong_profile_crc_and_oversize() {
    auto input = frame();
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "petzone-01").error ==
           petcare::ProvisioningError::wrong_product);
    input.back() ^= 0x01U;
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::bad_crc);

    std::array<std::uint8_t, petcare::max_provisioning_frame_bytes + 1> oversized{};
    assert(petcare::parse_provisioning_frame(
               oversized.data(), oversized.size(), "entrance-01").error ==
           petcare::ProvisioningError::too_large);
}

void test_reject_bad_header_length_and_trailing_bytes() {
    auto input = frame();
    input[0] = 'X';
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::bad_magic);

    input = frame();
    input[4] = 2;
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::bad_version);

    input = frame();
    input[5] = 1;
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::bad_kind);

    input = frame();
    ++input[6];
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::bad_length);

    input = frame();
    input.push_back(0);
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::bad_length);

    const std::array<std::uint8_t, 11> short_input{};
    assert(petcare::parse_provisioning_frame(
               short_input.data(), short_input.size(), "entrance-01").error ==
           petcare::ProvisioningError::bad_length);
}

void test_reject_embedded_nul_and_invalid_utf8() {
    auto embedded_nul = frame("entrance-01", std::string_view{"a\0b", 3});
    assert(petcare::parse_provisioning_frame(
               embedded_nul.data(), embedded_nul.size(), "entrance-01").error ==
           petcare::ProvisioningError::embedded_nul);

    const std::array<char, 2> invalid_bytes{
        static_cast<char>(0xC0), static_cast<char>(0xAF)};
    auto invalid_utf8 = frame(
        "entrance-01", std::string_view{invalid_bytes.data(), invalid_bytes.size()});
    assert(petcare::parse_provisioning_frame(
               invalid_utf8.data(), invalid_utf8.size(), "entrance-01").error ==
           petcare::ProvisioningError::invalid_utf8);
}

void test_reject_invalid_field_bounds_and_port() {
    auto input = frame("entrance-01", "");
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::invalid_field_length);

    const std::string_view too_long_ssid = "123456789012345678901234567890123";
    input = frame("entrance-01", too_long_ssid);
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::invalid_field_length);

    input = frame("entrance-01", "x", "1234567");
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::invalid_field_length);

    input = frame("entrance-01", "x", "12345678", "");
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::invalid_field_length);

    input = frame("entrance-01", "x", "12345678", "host", "", "password");
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::invalid_field_length);

    input = frame("entrance-01", "x", "12345678", "host", "user", "");
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::invalid_field_length);

    input = frame("entrance-01", "x", "12345678", "host", "user", "password", 0);
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::invalid_port);
}

void test_accept_maximums_and_reject_each_oversize_field() {
    const std::string ssid(32, 's');
    const std::string wifi_password(63, 'w');
    const std::string mqtt_host(253, 'h');
    const std::string mqtt_username(64, 'u');
    const std::string mqtt_password(128, 'm');
    auto input = frame(
        "entrance-01", ssid, wifi_password, mqtt_host, mqtt_username,
        mqtt_password, 65535);
    assert(input.size() <= petcare::max_provisioning_frame_bytes);
    assert(petcare::parse_provisioning_frame(
               input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::none);
    const auto maximums =
        petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01");
    assert(maximums.config.product_id[maximums.config.product_id_size] == '\0');
    assert(maximums.config.ssid[maximums.config.ssid_size] == '\0');
    assert(maximums.config.wifi_password[maximums.config.wifi_password_size] == '\0');
    assert(maximums.config.mqtt_host[maximums.config.mqtt_host_size] == '\0');
    assert(maximums.config.mqtt_username[maximums.config.mqtt_username_size] == '\0');
    assert(maximums.config.mqtt_password[maximums.config.mqtt_password_size] == '\0');

    input = frame("entrance-01", "x", std::string(64, 'w'));
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::invalid_field_length);
    input = frame("entrance-01", "x", "12345678", std::string(254, 'h'));
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::invalid_field_length);
    input = frame(
        "entrance-01", "x", "12345678", "host", std::string(65, 'u'));
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::invalid_field_length);
    input = frame(
        "entrance-01", "x", "12345678", "host", "user",
        std::string(129, 'm'));
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01").error ==
           petcare::ProvisioningError::invalid_field_length);
}

void test_reject_unknown_fixed_product() {
    auto input = frame("garage-01");
    assert(petcare::parse_provisioning_frame(input.data(), input.size(), "garage-01").error ==
           petcare::ProvisioningError::wrong_product);
}

void test_ack_is_secret_free_and_uses_config_crc() {
    const auto input = frame();
    const auto parsed =
        petcare::parse_provisioning_frame(input.data(), input.size(), "entrance-01");
    assert(parsed.error == petcare::ProvisioningError::none);

    std::array<std::uint8_t, petcare::max_provisioning_frame_bytes> ack{};
    const auto size = petcare::encode_ack_frame(
        "entrance-01", parsed.configuration_crc, ack.data(), ack.size());
    assert(size == 28);
    assert(ack[5] == static_cast<std::uint8_t>(petcare::ProvisioningKind::ack));
    assert(contains(ack.data(), size, "entrance-01"));
    assert(!contains(ack.data(), size, "wifi-secret"));
    assert(!contains(ack.data(), size, "mqtt-secret"));
    assert(ack[20] == static_cast<std::uint8_t>(parsed.configuration_crc));
    assert(ack[21] == static_cast<std::uint8_t>(parsed.configuration_crc >> 8U));
    assert(ack[22] == static_cast<std::uint8_t>(parsed.configuration_crc >> 16U));
    assert(ack[23] == static_cast<std::uint8_t>(parsed.configuration_crc >> 24U));
}

void test_select_newest_valid_slot_and_preserve_previous() {
    const auto older = record(8);
    const auto newer = record(9);
    const auto interrupted = record(9, false);
    const auto invalid = record(7, false);
    assert(petcare::newest_valid_record(older, newer) == &newer);
    assert(petcare::newest_valid_record(older, interrupted) == &older);
    assert(petcare::newest_valid_record(invalid, newer) == &newer);
    assert(petcare::newest_valid_record(invalid, interrupted) == nullptr);
}

void test_build_next_record_for_initial_and_successful_slot_transition() {
    petcare::ProvisioningRecord initial{};
    assert(petcare::build_next_provisioning_record(
               nullptr, valid_config("petzone-01"), initial) ==
           petcare::ProvisioningError::none);
    assert(initial.magic == petcare::provisioning_record_magic);
    assert(initial.generation == 1);
    assert(initial.crc == petcare::provisioning_record_crc(initial));

    const auto active = record(8);
    petcare::ProvisioningRecord inactive{};
    assert(petcare::build_next_provisioning_record(
               &active, valid_config(), inactive) ==
           petcare::ProvisioningError::none);
    assert(inactive.generation == 9);
    assert(inactive.crc == petcare::provisioning_record_crc(inactive));
    assert(petcare::newest_valid_record(active, inactive) == &inactive);
}

void test_interrupted_slot_write_retains_previous_record() {
    const auto active = record(8);
    petcare::ProvisioningRecord inactive{};
    assert(petcare::build_next_provisioning_record(
               &active, valid_config(), inactive) ==
           petcare::ProvisioningError::none);

    ++inactive.crc;
    assert(petcare::newest_valid_record(active, inactive) == &active);
}

void test_reject_corrupt_current_record() {
    const auto corrupt = record(8, false);
    auto next = record(9);
    assert(petcare::build_next_provisioning_record(
               &corrupt, valid_config(), next) ==
           petcare::ProvisioningError::bad_crc);
    assert(next.magic == 0);
    assert(next.generation == 0);
    assert(next.crc == 0);
}

void test_reject_invalid_new_config() {
    auto invalid = valid_config();
    invalid.mqtt_port = 0;
    auto next = record(1);
    assert(petcare::build_next_provisioning_record(nullptr, invalid, next) ==
           petcare::ProvisioningError::invalid_port);
    assert(next.magic == 0);
    assert(next.generation == 0);
    assert(next.crc == 0);
}

void test_build_next_record_zeroes_unused_config_tails() {
    auto config = valid_config();
    std::fill(
        config.product_id.begin() + config.product_id_size + 1,
        config.product_id.end(), 'x');
    std::fill(
        config.ssid.begin() + config.ssid_size + 1, config.ssid.end(), 'x');
    std::fill(
        config.wifi_password.begin() + config.wifi_password_size + 1,
        config.wifi_password.end(), 'x');
    std::fill(
        config.mqtt_host.begin() + config.mqtt_host_size + 1,
        config.mqtt_host.end(), 'x');
    std::fill(
        config.mqtt_username.begin() + config.mqtt_username_size + 1,
        config.mqtt_username.end(), 'x');
    std::fill(
        config.mqtt_password.begin() + config.mqtt_password_size + 1,
        config.mqtt_password.end(), 'x');

    petcare::ProvisioningRecord next{};
    assert(petcare::build_next_provisioning_record(nullptr, config, next) ==
           petcare::ProvisioningError::none);
    assert(std::all_of(
        next.config.product_id.begin() + next.config.product_id_size,
        next.config.product_id.end(), [](char value) { return value == '\0'; }));
    assert(std::all_of(
        next.config.ssid.begin() + next.config.ssid_size,
        next.config.ssid.end(), [](char value) { return value == '\0'; }));
    assert(std::all_of(
        next.config.wifi_password.begin() + next.config.wifi_password_size,
        next.config.wifi_password.end(), [](char value) { return value == '\0'; }));
    assert(std::all_of(
        next.config.mqtt_host.begin() + next.config.mqtt_host_size,
        next.config.mqtt_host.end(), [](char value) { return value == '\0'; }));
    assert(std::all_of(
        next.config.mqtt_username.begin() + next.config.mqtt_username_size,
        next.config.mqtt_username.end(), [](char value) { return value == '\0'; }));
    assert(std::all_of(
        next.config.mqtt_password.begin() + next.config.mqtt_password_size,
        next.config.mqtt_password.end(), [](char value) { return value == '\0'; }));
}

void test_generation_overflow_is_rejected() {
    const auto current = record(std::numeric_limits<std::uint32_t>::max() - 1U);
    petcare::ProvisioningRecord next{};
    assert(petcare::build_next_provisioning_record(
               &current, valid_config(), next) ==
           petcare::ProvisioningError::generation_overflow);
    assert(next.magic == 0);
    assert(next.generation == 0);
    assert(next.crc == 0);
}

void test_generation_zero_record_is_rejected() {
    const auto zero = record(0);
    const petcare::ProvisioningRecord empty{};
    assert(petcare::newest_valid_record(zero, empty) == nullptr);
}

void test_corrupt_record_sizes_are_rejected_without_reading_fields() {
    const auto valid = record(8);
    auto corrupt = record(9);

    corrupt.config.product_id_size = std::numeric_limits<std::size_t>::max();
    assert(petcare::newest_valid_record(valid, corrupt) == &valid);
    assert(petcare::provisioning_record_crc(corrupt) == 0);

    corrupt = record(9);
    corrupt.config.ssid_size = std::numeric_limits<std::size_t>::max();
    assert(petcare::newest_valid_record(valid, corrupt) == &valid);
    assert(petcare::provisioning_record_crc(corrupt) == 0);

    corrupt = record(9);
    corrupt.config.wifi_password_size = std::numeric_limits<std::size_t>::max();
    assert(petcare::newest_valid_record(valid, corrupt) == &valid);
    assert(petcare::provisioning_record_crc(corrupt) == 0);

    corrupt = record(9);
    corrupt.config.mqtt_host_size = std::numeric_limits<std::size_t>::max();
    assert(petcare::newest_valid_record(valid, corrupt) == &valid);
    assert(petcare::provisioning_record_crc(corrupt) == 0);

    corrupt = record(9);
    corrupt.config.mqtt_username_size = std::numeric_limits<std::size_t>::max();
    assert(petcare::newest_valid_record(valid, corrupt) == &valid);
    assert(petcare::provisioning_record_crc(corrupt) == 0);

    corrupt = record(9);
    corrupt.config.mqtt_password_size = std::numeric_limits<std::size_t>::max();
    assert(petcare::newest_valid_record(valid, corrupt) == &valid);
    assert(petcare::provisioning_record_crc(corrupt) == 0);
}

}

void* operator new(std::size_t size) {
    ++allocations;
    if (void* value = std::malloc(size)) {
        return value;
    }
    throw std::bad_alloc{};
}

void* operator new[](std::size_t size) {
    return ::operator new(size);
}

void operator delete(void* value) noexcept { std::free(value); }
void operator delete[](void* value) noexcept { std::free(value); }
void operator delete(void* value, std::size_t) noexcept { std::free(value); }
void operator delete[](void* value, std::size_t) noexcept { std::free(value); }

int main() {
    static_assert(petcare::max_provisioning_frame_bytes <= 768);
    static_assert(petcare::ProvisioningConfig{}.product_id.size() ==
                  petcare::max_product_id_bytes + 1);
    static_assert(petcare::ProvisioningConfig{}.ssid.size() ==
                  petcare::max_ssid_bytes + 1);
    static_assert(petcare::ProvisioningConfig{}.wifi_password.size() ==
                  petcare::max_wifi_password_bytes + 1);
    static_assert(petcare::ProvisioningConfig{}.mqtt_host.size() ==
                  petcare::max_mqtt_host_bytes + 1);
    static_assert(petcare::ProvisioningConfig{}.mqtt_username.size() ==
                  petcare::max_mqtt_username_bytes + 1);
    static_assert(petcare::ProvisioningConfig{}.mqtt_password.size() ==
                  petcare::max_mqtt_password_bytes + 1);
    test_crc_and_little_endian_encoder();
    test_idle_partial_frame_expiry_zeroes_and_resets();
    test_parse_valid_config_without_allocating();
    test_reject_wrong_profile_crc_and_oversize();
    test_reject_bad_header_length_and_trailing_bytes();
    test_reject_embedded_nul_and_invalid_utf8();
    test_reject_invalid_field_bounds_and_port();
    test_accept_maximums_and_reject_each_oversize_field();
    test_reject_unknown_fixed_product();
    test_ack_is_secret_free_and_uses_config_crc();
    test_select_newest_valid_slot_and_preserve_previous();
    test_build_next_record_for_initial_and_successful_slot_transition();
    test_interrupted_slot_write_retains_previous_record();
    test_reject_corrupt_current_record();
    test_reject_invalid_new_config();
    test_build_next_record_zeroes_unused_config_tails();
    test_generation_overflow_is_rejected();
    test_generation_zero_record_is_rejected();
    test_corrupt_record_sizes_are_rejected_without_reading_fields();
}

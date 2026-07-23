#include "provisioning.hpp"

#include <algorithm>
#include <cstring>
#include <limits>

namespace petcare {
namespace {

constexpr std::size_t header_bytes = 8;
constexpr std::size_t checksum_bytes = 4;

std::uint16_t read_u16(const std::uint8_t* data) {
    return static_cast<std::uint16_t>(
        data[0] | static_cast<std::uint16_t>(data[1]) << 8U);
}

std::uint32_t read_u32(const std::uint8_t* data) {
    return static_cast<std::uint32_t>(data[0]) |
           static_cast<std::uint32_t>(data[1]) << 8U |
           static_cast<std::uint32_t>(data[2]) << 16U |
           static_cast<std::uint32_t>(data[3]) << 24U;
}

void write_u16(std::uint8_t* output, std::uint16_t value) {
    output[0] = static_cast<std::uint8_t>(value);
    output[1] = static_cast<std::uint8_t>(value >> 8U);
}

void write_u32(std::uint8_t* output, std::uint32_t value) {
    output[0] = static_cast<std::uint8_t>(value);
    output[1] = static_cast<std::uint8_t>(value >> 8U);
    output[2] = static_cast<std::uint8_t>(value >> 16U);
    output[3] = static_cast<std::uint8_t>(value >> 24U);
}

bool fixed_product(std::string_view value) {
    return value == "entrance-01" || value == "petzone-01";
}

ProvisioningError validate_utf8(const std::uint8_t* data, std::size_t size) {
    for (std::size_t i = 0; i < size;) {
        const auto first = data[i];
        if (first == 0) {
            return ProvisioningError::embedded_nul;
        }
        if (first < 0x80U) {
            ++i;
            continue;
        }

        std::size_t continuation = 0;
        std::uint32_t code_point = 0;
        std::uint32_t minimum = 0;
        if ((first & 0xE0U) == 0xC0U) {
            continuation = 1;
            code_point = first & 0x1FU;
            minimum = 0x80U;
        } else if ((first & 0xF0U) == 0xE0U) {
            continuation = 2;
            code_point = first & 0x0FU;
            minimum = 0x800U;
        } else if ((first & 0xF8U) == 0xF0U) {
            continuation = 3;
            code_point = first & 0x07U;
            minimum = 0x10000U;
        } else {
            return ProvisioningError::invalid_utf8;
        }
        if (continuation > size - i - 1) {
            return ProvisioningError::invalid_utf8;
        }
        for (std::size_t offset = 1; offset <= continuation; ++offset) {
            const auto next = data[i + offset];
            if ((next & 0xC0U) != 0x80U) {
                return ProvisioningError::invalid_utf8;
            }
            code_point = code_point << 6U | (next & 0x3FU);
        }
        if (code_point < minimum ||
            (code_point >= 0xD800U && code_point <= 0xDFFFU) ||
            code_point > 0x10FFFFU) {
            return ProvisioningError::invalid_utf8;
        }
        i += continuation + 1;
    }
    return ProvisioningError::none;
}

ProvisioningError read_string(
    const std::uint8_t*& cursor,
    const std::uint8_t* end,
    char* output,
    std::size_t& output_size,
    std::size_t minimum,
    std::size_t maximum) {
    if (static_cast<std::size_t>(end - cursor) < 2) {
        return ProvisioningError::bad_length;
    }
    const auto size = static_cast<std::size_t>(read_u16(cursor));
    cursor += 2;
    if (size > static_cast<std::size_t>(end - cursor)) {
        return ProvisioningError::bad_length;
    }
    if (size < minimum || size > maximum) {
        return ProvisioningError::invalid_field_length;
    }
    const auto utf8_error = validate_utf8(cursor, size);
    if (utf8_error != ProvisioningError::none) {
        return utf8_error;
    }
    std::copy_n(cursor, size, output);
    output[size] = '\0';
    output_size = size;
    cursor += size;
    return ProvisioningError::none;
}

bool config_sizes_valid(const ProvisioningConfig& config) {
    return config.product_id_size >= 1 &&
           config.product_id_size <= max_product_id_bytes &&
           config.ssid_size >= 1 && config.ssid_size <= max_ssid_bytes &&
           config.wifi_password_size >= 8 &&
           config.wifi_password_size <= max_wifi_password_bytes &&
           config.mqtt_host_size >= 1 &&
           config.mqtt_host_size <= max_mqtt_host_bytes &&
           config.mqtt_username_size >= 1 &&
           config.mqtt_username_size <= max_mqtt_username_bytes &&
           config.mqtt_password_size >= 1 &&
           config.mqtt_password_size <= max_mqtt_password_bytes;
}

bool valid_config(const ProvisioningConfig& config) {
    if (!config_sizes_valid(config)) {
        return false;
    }
    const auto product =
        std::string_view{config.product_id.data(), config.product_id_size};
    return fixed_product(product) &&
           config.product_id[config.product_id_size] == '\0' &&
           config.ssid[config.ssid_size] == '\0' &&
           config.wifi_password[config.wifi_password_size] == '\0' &&
           config.mqtt_host[config.mqtt_host_size] == '\0' &&
           config.mqtt_username[config.mqtt_username_size] == '\0' &&
           config.mqtt_password[config.mqtt_password_size] == '\0' &&
           config.mqtt_port != 0;
}

void append_u16(
    std::array<std::uint8_t, max_provisioning_frame_bytes>& output,
    std::size_t& size,
    std::uint16_t value) {
    write_u16(output.data() + size, value);
    size += 2;
}

void append_u32(
    std::array<std::uint8_t, max_provisioning_frame_bytes>& output,
    std::size_t& size,
    std::uint32_t value) {
    write_u32(output.data() + size, value);
    size += 4;
}

void append_field(
    std::array<std::uint8_t, max_provisioning_frame_bytes>& output,
    std::size_t& size,
    const char* data,
    std::size_t data_size) {
    append_u16(output, size, static_cast<std::uint16_t>(data_size));
    std::copy_n(
        reinterpret_cast<const std::uint8_t*>(data),
        data_size,
        output.data() + size);
    size += data_size;
}

bool valid_record(const ProvisioningRecord& record) {
    return record.magic == provisioning_record_magic &&
           record.generation != 0 &&
           record.generation != std::numeric_limits<std::uint32_t>::max() &&
           valid_config(record.config) &&
           record.crc == provisioning_record_crc(record);
}

}

std::uint32_t crc32(const std::uint8_t* data, std::size_t size) {
    std::uint32_t crc = 0xFFFFFFFFU;
    for (std::size_t index = 0; index < size; ++index) {
        crc ^= data[index];
        for (int bit = 0; bit < 8; ++bit) {
            crc = crc & 1U ? crc >> 1U ^ 0xEDB88320U : crc >> 1U;
        }
    }
    return crc ^ 0xFFFFFFFFU;
}

bool expire_idle_provisioning_frame(
    std::uint8_t* data,
    std::size_t capacity,
    std::size_t& size,
    std::uint64_t last_byte_ms,
    std::uint64_t now_ms) {
    if (size == 0 ||
        now_ms < last_byte_ms ||
        now_ms - last_byte_ms < provisioning_frame_idle_timeout_ms) {
        return false;
    }
    if (data != nullptr) {
        std::fill_n(data, std::min(size, capacity), 0);
    }
    size = 0;
    return true;
}

ProvisioningResult parse_provisioning_frame(
    const std::uint8_t* data,
    std::size_t size,
    std::string_view expected_device_id) {
    ProvisioningResult result{};
    if (size > max_provisioning_frame_bytes) {
        result.error = ProvisioningError::too_large;
        return result;
    }
    if (data == nullptr || size < header_bytes + checksum_bytes) {
        result.error = ProvisioningError::bad_length;
        return result;
    }
    if (!std::equal(data, data + 4, "PET1")) {
        result.error = ProvisioningError::bad_magic;
        return result;
    }
    if (data[4] != 1) {
        result.error = ProvisioningError::bad_version;
        return result;
    }
    if (data[5] != static_cast<std::uint8_t>(ProvisioningKind::config)) {
        result.error = ProvisioningError::bad_kind;
        return result;
    }

    const auto payload_size = static_cast<std::size_t>(read_u16(data + 6));
    if (payload_size != size - header_bytes - checksum_bytes) {
        result.error = ProvisioningError::bad_length;
        return result;
    }
    result.configuration_crc = crc32(data, header_bytes + payload_size);
    if (result.configuration_crc != read_u32(data + header_bytes + payload_size)) {
        result.error = ProvisioningError::bad_crc;
        result.configuration_crc = 0;
        return result;
    }

    const auto* cursor = data + header_bytes;
    const auto* end = cursor + payload_size;
    ProvisioningError error = read_string(
        cursor,
        end,
        result.config.product_id.data(),
        result.config.product_id_size,
        1,
        max_product_id_bytes);
    if (error == ProvisioningError::none) {
        const auto product = std::string_view{
            result.config.product_id.data(), result.config.product_id_size};
        if (!fixed_product(product) ||
            !fixed_product(expected_device_id) ||
            product != expected_device_id) {
            error = ProvisioningError::wrong_product;
        }
    }
    if (error == ProvisioningError::none) {
        error = read_string(
            cursor, end, result.config.ssid.data(), result.config.ssid_size, 1,
            max_ssid_bytes);
    }
    if (error == ProvisioningError::none) {
        error = read_string(
            cursor, end, result.config.wifi_password.data(),
            result.config.wifi_password_size, 8,
            max_wifi_password_bytes);
    }
    if (error == ProvisioningError::none) {
        error = read_string(
            cursor, end, result.config.mqtt_host.data(),
            result.config.mqtt_host_size, 1, max_mqtt_host_bytes);
    }
    if (error == ProvisioningError::none) {
        error = read_string(
            cursor, end, result.config.mqtt_username.data(),
            result.config.mqtt_username_size, 1,
            max_mqtt_username_bytes);
    }
    if (error == ProvisioningError::none) {
        error = read_string(
            cursor, end, result.config.mqtt_password.data(),
            result.config.mqtt_password_size, 1,
            max_mqtt_password_bytes);
    }
    if (error == ProvisioningError::none) {
        if (end - cursor != 2) {
            error = ProvisioningError::bad_length;
        } else {
            result.config.mqtt_port = read_u16(cursor);
            cursor += 2;
            if (result.config.mqtt_port == 0) {
                error = ProvisioningError::invalid_port;
            }
        }
    }
    if (error == ProvisioningError::none && cursor != end) {
        error = ProvisioningError::bad_length;
    }
    result.error = error;
    if (error != ProvisioningError::none) {
        result.config = {};
        result.configuration_crc = 0;
    }
    return result;
}

std::size_t encode_provisioning_frame(
    ProvisioningKind kind,
    const std::uint8_t* payload,
    std::size_t payload_size,
    std::uint8_t* output,
    std::size_t output_capacity) {
    if (payload_size >
        max_provisioning_frame_bytes - header_bytes - checksum_bytes) {
        return 0;
    }
    const auto kind_value = static_cast<std::uint8_t>(kind);
    const auto frame_size = header_bytes + payload_size + checksum_bytes;
    if (kind_value < 1 || kind_value > 4 ||
        (payload == nullptr && payload_size != 0) ||
        output == nullptr ||
        frame_size > max_provisioning_frame_bytes ||
        frame_size > output_capacity) {
        return 0;
    }
    std::copy_n("PET1", 4, output);
    output[4] = 1;
    output[5] = kind_value;
    write_u16(output + 6, static_cast<std::uint16_t>(payload_size));
    if (payload_size != 0) {
        std::copy_n(payload, payload_size, output + header_bytes);
    }
    write_u32(
        output + header_bytes + payload_size,
        crc32(output, header_bytes + payload_size));
    return frame_size;
}

std::size_t encode_ack_frame(
    std::string_view product_id,
    std::uint32_t configuration_crc,
    std::uint8_t* output,
    std::size_t output_capacity) {
    if (!fixed_product(product_id)) {
        return 0;
    }
    std::array<std::uint8_t, 16> payload{};
    payload[0] = 1;
    std::copy(
        product_id.begin(),
        product_id.end(),
        payload.begin() + 1);
    write_u32(payload.data() + 1 + product_id.size(), configuration_crc);
    return encode_provisioning_frame(
        ProvisioningKind::ack,
        payload.data(),
        1 + product_id.size() + 4,
        output,
        output_capacity);
}

std::uint32_t provisioning_record_crc(const ProvisioningRecord& record) {
    if (!config_sizes_valid(record.config)) {
        return 0;
    }
    std::array<std::uint8_t, max_provisioning_frame_bytes> bytes{};
    std::size_t size = 0;
    append_u32(bytes, size, record.magic);
    append_u32(bytes, size, record.generation);
    append_field(
        bytes, size, record.config.product_id.data(),
        record.config.product_id_size);
    append_field(bytes, size, record.config.ssid.data(), record.config.ssid_size);
    append_field(
        bytes, size, record.config.wifi_password.data(),
        record.config.wifi_password_size);
    append_field(
        bytes, size, record.config.mqtt_host.data(),
        record.config.mqtt_host_size);
    append_field(
        bytes, size, record.config.mqtt_username.data(),
        record.config.mqtt_username_size);
    append_field(
        bytes, size, record.config.mqtt_password.data(),
        record.config.mqtt_password_size);
    append_u16(bytes, size, record.config.mqtt_port);
    return crc32(bytes.data(), size);
}

ProvisioningError build_next_provisioning_record(
    const ProvisioningRecord* current,
    const ProvisioningConfig& config,
    ProvisioningRecord& output) {
    std::memset(&output, 0, sizeof(output));
    if (current != nullptr && !valid_record(*current)) {
        return ProvisioningError::bad_crc;
    }
    if (!valid_config(config)) {
        return config.mqtt_port == 0
                   ? ProvisioningError::invalid_port
                   : ProvisioningError::invalid_field_length;
    }
    if (current != nullptr &&
        current->generation >=
            std::numeric_limits<std::uint32_t>::max() - 1U) {
        return ProvisioningError::generation_overflow;
    }
    output.magic = provisioning_record_magic;
    output.generation = current == nullptr ? 1U : current->generation + 1U;
    output.config.product_id_size = config.product_id_size;
    std::copy_n(
        config.product_id.data(), config.product_id_size,
        output.config.product_id.data());
    output.config.ssid_size = config.ssid_size;
    std::copy_n(
        config.ssid.data(), config.ssid_size, output.config.ssid.data());
    output.config.wifi_password_size = config.wifi_password_size;
    std::copy_n(
        config.wifi_password.data(), config.wifi_password_size,
        output.config.wifi_password.data());
    output.config.mqtt_host_size = config.mqtt_host_size;
    std::copy_n(
        config.mqtt_host.data(), config.mqtt_host_size,
        output.config.mqtt_host.data());
    output.config.mqtt_username_size = config.mqtt_username_size;
    std::copy_n(
        config.mqtt_username.data(), config.mqtt_username_size,
        output.config.mqtt_username.data());
    output.config.mqtt_password_size = config.mqtt_password_size;
    std::copy_n(
        config.mqtt_password.data(), config.mqtt_password_size,
        output.config.mqtt_password.data());
    output.config.mqtt_port = config.mqtt_port;
    output.crc = provisioning_record_crc(output);
    return ProvisioningError::none;
}

const ProvisioningRecord* newest_valid_record(
    const ProvisioningRecord& a,
    const ProvisioningRecord& b) {
    const bool a_valid = valid_record(a);
    const bool b_valid = valid_record(b);
    if (!a_valid) {
        return b_valid ? &b : nullptr;
    }
    if (!b_valid) {
        return &a;
    }
    return b.generation > a.generation ? &b : &a;
}

}

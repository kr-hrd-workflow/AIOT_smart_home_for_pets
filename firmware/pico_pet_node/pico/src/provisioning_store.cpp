#include "provisioning_store.hpp"

#include "hardware/flash.h"
#include "hardware/regs/addressmap.h"
#include "hardware/sync.h"
#include "hardware/watchdog.h"
#include "pico/stdio.h"
#include "pico/stdlib.h"
#include "pico/time.h"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <type_traits>

namespace petcare {
namespace {

constexpr std::uint32_t slot_a_offset =
    PICO_FLASH_SIZE_BYTES - 2U * FLASH_SECTOR_SIZE;
constexpr std::uint32_t slot_b_offset =
    PICO_FLASH_SIZE_BYTES - FLASH_SECTOR_SIZE;
constexpr std::size_t frame_header_bytes = 8;
constexpr std::size_t frame_crc_bytes = 4;
constexpr std::size_t programmed_bytes =
    (sizeof(ProvisioningRecord) + FLASH_PAGE_SIZE - 1U) /
    FLASH_PAGE_SIZE * FLASH_PAGE_SIZE;

static_assert(programmed_bytes <= FLASH_SECTOR_SIZE);
static_assert(std::is_trivially_copyable_v<ProvisioningRecord>);

const ProvisioningRecord& slot(std::uint32_t offset) {
    return *reinterpret_cast<const ProvisioningRecord*>(
        XIP_BASE + static_cast<std::uintptr_t>(offset));
}

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

void send_frame(
    ProvisioningKind kind,
    const std::uint8_t* payload,
    std::size_t payload_size) {
    std::array<std::uint8_t, max_provisioning_frame_bytes> frame{};
    const auto size = encode_provisioning_frame(
        kind, payload, payload_size, frame.data(), frame.size());
    for (std::size_t index = 0; index < size; ++index) {
        putchar_raw(frame[index]);
    }
    stdio_flush();
}

void send_error(ProvisioningError error) {
    const auto value = static_cast<std::uint8_t>(error);
    send_frame(ProvisioningKind::error, &value, 1);
}

void drain_usb_input() {
    while (getchar_timeout_us(0) >= 0) {
    }
}

ProvisioningError store_provisioning(
    const ProvisioningConfig& config,
    ProvisioningConfig& output) {
    const auto& a = slot(slot_a_offset);
    const auto& b = slot(slot_b_offset);
    const auto* current = newest_valid_record(a, b);
    const auto target_offset =
        current == &a ? slot_b_offset : slot_a_offset;

    ProvisioningRecord next{};
    const auto build_error =
        build_next_provisioning_record(current, config, next);
    if (build_error != ProvisioningError::none) {
        return build_error;
    }

    alignas(4) std::array<std::uint8_t, programmed_bytes> bytes{};
    std::fill(bytes.begin(), bytes.end(), 0xFFU);
    std::memcpy(bytes.data(), &next, sizeof(next));

    const auto interrupt_state = save_and_disable_interrupts();
    flash_range_erase(target_offset, FLASH_SECTOR_SIZE);
    flash_range_program(target_offset, bytes.data(), bytes.size());
    restore_interrupts(interrupt_state);

    const auto& written = slot(target_offset);
    const ProvisioningRecord erased{};
    if (newest_valid_record(written, erased) != &written ||
        written.generation != next.generation ||
        written.crc != next.crc) {
        return ProvisioningError::bad_crc;
    }
    output = written.config;
    return ProvisioningError::none;
}

ProvisioningError process_frame(
    const std::uint8_t* data,
    std::size_t size,
    std::string_view device_id,
    ProvisioningConfig& output) {
    const auto kind = static_cast<ProvisioningKind>(data[5]);
    if (kind == ProvisioningKind::hello) {
        if (size != frame_header_bytes + frame_crc_bytes ||
            read_u16(data + 6) != 0) {
            return ProvisioningError::bad_length;
        }
        if (crc32(data, frame_header_bytes) !=
            read_u32(data + frame_header_bytes)) {
            return ProvisioningError::bad_crc;
        }
        if (device_id != "entrance-01" && device_id != "petzone-01") {
            return ProvisioningError::wrong_product;
        }
        std::array<std::uint8_t, max_product_id_bytes + 1> payload{};
        payload[0] = 1;
        std::copy(device_id.begin(), device_id.end(), payload.begin() + 1);
        send_frame(
            ProvisioningKind::hello,
            payload.data(),
            1 + device_id.size());
        return ProvisioningError::none;
    }

    const auto parsed = parse_provisioning_frame(data, size, device_id);
    if (parsed.error != ProvisioningError::none) {
        return parsed.error;
    }
    const auto store_error = store_provisioning(parsed.config, output);
    if (store_error != ProvisioningError::none) {
        return store_error;
    }

    std::array<std::uint8_t, max_provisioning_frame_bytes> ack{};
    const auto ack_size = encode_ack_frame(
        device_id,
        parsed.configuration_crc,
        ack.data(),
        ack.size());
    for (std::size_t index = 0; index < ack_size; ++index) {
        putchar_raw(ack[index]);
    }
    stdio_flush();
    watchdog_reboot(0, 0, 50);
    return ProvisioningError::none;
}

}

bool load_provisioning(ProvisioningConfig& output) {
    const auto* current =
        newest_valid_record(slot(slot_a_offset), slot(slot_b_offset));
    if (current == nullptr) {
        output = {};
        return false;
    }
    output = current->config;
    return true;
}

ProvisioningError poll_usb_provisioning(
    std::string_view device_id,
    ProvisioningConfig& output) {
    static std::array<std::uint8_t, max_provisioning_frame_bytes> frame{};
    static std::size_t frame_size = 0;
    static std::uint64_t last_byte_ms = 0;

    const auto now_ms = to_ms_since_boot(get_absolute_time());
    if (expire_idle_provisioning_frame(
            frame.data(), frame.size(), frame_size, last_byte_ms, now_ms)) {
        last_byte_ms = 0;
    }

    for (int value = getchar_timeout_us(0);
         value >= 0;
         value = getchar_timeout_us(0)) {
        if (frame_size == frame.size()) {
            std::fill(frame.begin(), frame.end(), 0);
            frame_size = 0;
            last_byte_ms = 0;
            drain_usb_input();
            send_error(ProvisioningError::too_large);
            continue;
        }
        frame[frame_size++] = static_cast<std::uint8_t>(value);
        last_byte_ms = to_ms_since_boot(get_absolute_time());
        if (frame_size < frame_header_bytes) {
            continue;
        }

        ProvisioningError header_error = ProvisioningError::none;
        if (!std::equal(frame.begin(), frame.begin() + 4, "PET1")) {
            header_error = ProvisioningError::bad_magic;
        } else if (frame[4] != 1) {
            header_error = ProvisioningError::bad_version;
        } else if (
            frame[5] != static_cast<std::uint8_t>(ProvisioningKind::hello) &&
            frame[5] != static_cast<std::uint8_t>(ProvisioningKind::config)) {
            header_error = ProvisioningError::bad_kind;
        }
        if (header_error != ProvisioningError::none) {
            std::fill_n(frame.begin(), frame_size, 0);
            frame_size = 0;
            last_byte_ms = 0;
            drain_usb_input();
            send_error(header_error);
            return header_error;
        }

        const auto expected_size =
            frame_header_bytes +
            static_cast<std::size_t>(read_u16(frame.data() + 6)) +
            frame_crc_bytes;
        if (expected_size > frame.size()) {
            std::fill_n(frame.begin(), frame_size, 0);
            frame_size = 0;
            last_byte_ms = 0;
            drain_usb_input();
            send_error(ProvisioningError::too_large);
            return ProvisioningError::too_large;
        }
        if (frame_size < expected_size) {
            continue;
        }

        const auto result =
            process_frame(frame.data(), frame_size, device_id, output);
        std::fill_n(frame.begin(), frame_size, 0);
        frame_size = 0;
        last_byte_ms = 0;
        if (result != ProvisioningError::none) {
            drain_usb_input();
            send_error(result);
        }
        return result;
    }
    return ProvisioningError::none;
}

}

#pragma once

#include "pet_node.hpp"

#include <array>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <string_view>

#ifdef PICO_ON_DEVICE
#include "lwip/apps/mqtt.h"
#endif

namespace petcare {

struct MqttContract {
    static constexpr std::uint8_t qos = 1;
    static constexpr bool sensor_retain = false;
    static constexpr bool status_retain = true;
    static constexpr std::uint64_t heartbeat_ms = 10'000;
};

class HeartbeatSchedule {
public:
    void connected(std::uint64_t monotonic_ms) {
        last_emitted_ms_ = monotonic_ms;
        armed_ = true;
    }

    bool due(std::uint64_t monotonic_ms) const {
        return armed_ && monotonic_ms - last_emitted_ms_ >= MqttContract::heartbeat_ms;
    }

    void emitted(std::uint64_t monotonic_ms) { last_emitted_ms_ = monotonic_ms; }

private:
    std::uint64_t last_emitted_ms_ = 0;
    bool armed_ = false;
};

class UtcClock {
public:
    static constexpr std::uint64_t minimum_utc_ms = 1'704'067'200'000ULL;
    static constexpr std::uint64_t retry_ms = 15'000;
    static constexpr std::uint64_t resync_ms = 21'600'000;
    static constexpr char primary_server[] = "pool.ntp.org";
    static constexpr char fallback_server[] = "time.cloudflare.com";

    bool synchronize(std::uint64_t utc_ms, std::uint64_t monotonic_ms) {
        if (utc_ms < minimum_utc_ms || (has_last_published_ && utc_ms <= last_published_ms_)) {
            valid_ = false;
            return false;
        }
        anchor_utc_ms_ = utc_ms;
        anchor_monotonic_ms_ = monotonic_ms;
        valid_ = true;
        return true;
    }

    bool valid() const { return valid_; }

    bool timestamp(
        std::uint64_t monotonic_ms,
        std::array<char, 25>& output,
        std::uint64_t& utc_ms
    ) const {
        if (!valid_ || monotonic_ms < anchor_monotonic_ms_) {
            return false;
        }
        utc_ms = anchor_utc_ms_ + (monotonic_ms - anchor_monotonic_ms_);
        return format(utc_ms, output);
    }

    void mark_published(std::uint64_t utc_ms) {
        if (valid_ && (!has_last_published_ || utc_ms >= last_published_ms_)) {
            last_published_ms_ = utc_ms;
            has_last_published_ = true;
        }
    }

private:
    static bool format(std::uint64_t utc_ms, std::array<char, 25>& output) {
        const auto seconds = utc_ms / 1'000;
        const auto milliseconds = utc_ms % 1'000;
        std::int64_t days = static_cast<std::int64_t>(seconds / 86'400);
        const auto seconds_of_day = seconds % 86'400;

        days += 719'468;
        const auto era = (days >= 0 ? days : days - 146'096) / 146'097;
        const auto day_of_era = static_cast<unsigned>(days - era * 146'097);
        const auto year_of_era = (day_of_era - day_of_era / 1'460 + day_of_era / 36'524 - day_of_era / 146'096) / 365;
        auto year = static_cast<int>(year_of_era) + static_cast<int>(era * 400);
        const auto day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
        const auto month_prime = (5 * day_of_year + 2) / 153;
        const auto day = day_of_year - (153 * month_prime + 2) / 5 + 1;
        const auto month = month_prime + (month_prime < 10 ? 3 : -9);
        year += month <= 2;
        if (year < 0 || year > 9'999) {
            return false;
        }

        const auto written = std::snprintf(
            output.data(), output.size(), "%04d-%02u-%02uT%02llu:%02llu:%02llu.%03lluZ",
            year,
            month,
            day,
            static_cast<unsigned long long>(seconds_of_day / 3'600),
            static_cast<unsigned long long>((seconds_of_day / 60) % 60),
            static_cast<unsigned long long>(seconds_of_day % 60),
            static_cast<unsigned long long>(milliseconds)
        );
        return written == 24;
    }

    std::uint64_t anchor_utc_ms_ = 0;
    std::uint64_t anchor_monotonic_ms_ = 0;
    std::uint64_t last_published_ms_ = 0;
    bool valid_ = false;
    bool has_last_published_ = false;
};

inline bool make_offline_lwt(
    std::string_view device_id,
    std::string_view observed_at,
    TelemetryMessage& output
) {
    return serialize_status_message({device_id, DeviceState::offline, observed_at}, output);
}

#ifdef PICO_ON_DEVICE
class MqttPublisher {
public:
    MqttPublisher();
    ~MqttPublisher();

    bool connect(
        const char* host,
        std::uint16_t port,
        const char* client_id,
        const char* username,
        const char* password,
        const TelemetryMessage& offline_lwt
    );
    bool connected() const;
    bool publish_sensor(const TelemetryMessage& message);
    bool publish_status(const TelemetryMessage& message);
    bool graceful_disconnect(const TelemetryMessage& offline_status);
    void abort();

private:
    bool publish(const TelemetryMessage& message, bool retain, bool disconnect_after);
    static void connection_changed(mqtt_client_t* client, void* argument, mqtt_connection_status_t status);
    static void publication_complete(void* argument, err_t error);

    mqtt_client_t* client_ = nullptr;
    TelemetryMessage offline_lwt_{};
    std::atomic_bool connected_{false};
    std::atomic_bool disconnect_after_publish_{false};
};
#endif

}

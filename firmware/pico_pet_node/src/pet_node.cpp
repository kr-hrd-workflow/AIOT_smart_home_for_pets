#include "pet_node.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>

namespace petcare {

namespace {

struct SensorSpec {
    ValueKind kind;
    std::string_view unit;
    bool petzone_only;
};

bool sensor_spec(std::string_view sensor_type, SensorSpec& output) {
    if (sensor_type == "temperature") {
        output = {ValueKind::number, "C", false};
    } else if (sensor_type == "humidity") {
        output = {ValueKind::number, "%", false};
    } else if (sensor_type == "presence_moving" || sensor_type == "presence_stationary") {
        output = {ValueKind::boolean, "bool", false};
    } else if (sensor_type == "food_weight" || sensor_type == "water_weight") {
        output = {ValueKind::number, "g", true};
    } else if (sensor_type == "bed_pressure_left" || sensor_type == "bed_pressure_center" ||
               sensor_type == "bed_pressure_right") {
        output = {ValueKind::integer, "adc", true};
    } else {
        return false;
    }
    return true;
}

bool profile_for_device(std::string_view device_id, DeviceProfile& profile) {
    if (device_id == "entrance-01") {
        profile = DeviceProfile::entrance_01;
        return true;
    }
    if (device_id == "petzone-01") {
        profile = DeviceProfile::petzone_01;
        return true;
    }
    return false;
}

bool digit(char value) { return value >= '0' && value <= '9'; }

int two_digits(std::string_view value, std::size_t offset) {
    return (value[offset] - '0') * 10 + value[offset + 1] - '0';
}

bool valid_utc(std::string_view value) {
    if (value.size() != 24 || value[4] != '-' || value[7] != '-' || value[10] != 'T' ||
        value[13] != ':' || value[16] != ':' || value[19] != '.' || value[23] != 'Z') {
        return false;
    }
    for (const auto offset : {0U, 1U, 2U, 3U, 5U, 6U, 8U, 9U, 11U, 12U, 14U, 15U, 17U, 18U, 20U, 21U, 22U}) {
        if (!digit(value[offset])) {
            return false;
        }
    }
    const int year = (value[0] - '0') * 1000 + (value[1] - '0') * 100 + (value[2] - '0') * 10 + value[3] - '0';
    const int month = two_digits(value, 5);
    const int day = two_digits(value, 8);
    if (year < 2024 || month < 1 || month > 12 || day < 1 || two_digits(value, 11) > 23 ||
        two_digits(value, 14) > 59 || two_digits(value, 17) > 59) {
        return false;
    }
    constexpr std::array<int, 12> days_per_month{{31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}};
    int last_day = days_per_month[static_cast<std::size_t>(month - 1)];
    const bool leap_year = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    if (month == 2 && leap_year) {
        last_day = 29;
    }
    return day <= last_day;
}

bool valid_reading(const SensorReading& reading, SensorSpec& spec) {
    DeviceProfile profile{};
    if (!profile_for_device(reading.device_id, profile) || !sensor_spec(reading.sensor_type, spec) ||
        !profile_allows(profile, reading.sensor_type) || reading.unit != spec.unit || reading.value.kind != spec.kind ||
        !valid_utc(reading.observed_at)) {
        return false;
    }
    if (spec.kind == ValueKind::number) {
        return std::isfinite(reading.value.number_value);
    }
    if (spec.kind == ValueKind::integer) {
        return reading.value.integer_value <= 4095;
    }
    return true;
}

}

std::string_view profile_device_id(DeviceProfile profile) {
    switch (profile) {
    case DeviceProfile::entrance_01:
        return "entrance-01";
    case DeviceProfile::petzone_01:
        return "petzone-01";
    }
    return {};
}

bool profile_allows(DeviceProfile profile, std::string_view sensor_type) {
    SensorSpec spec{};
    if (!sensor_spec(sensor_type, spec)) {
        return false;
    }
    switch (profile) {
    case DeviceProfile::entrance_01:
        return !spec.petzone_only;
    case DeviceProfile::petzone_01:
        return true;
    }
    return false;
}

bool TelemetryMessage::assign(std::string_view new_topic, std::string_view new_payload) {
    if (new_topic.size() > topic.size() || new_payload.size() > payload.size()) {
        return false;
    }
    TelemetryMessage candidate{};
    candidate.topic_size = new_topic.size();
    candidate.payload_size = new_payload.size();
    std::copy(new_topic.begin(), new_topic.end(), candidate.topic.begin());
    std::copy(new_payload.begin(), new_payload.end(), candidate.payload.begin());
    *this = candidate;
    return true;
}

bool WeightCalibration::grams(std::int32_t raw, double& output) const {
    if (!std::isfinite(counts_per_gram) || counts_per_gram <= 0.0) {
        return false;
    }
    const double candidate = (static_cast<double>(raw) - static_cast<double>(tare_raw)) / counts_per_gram;
    if (!std::isfinite(candidate)) {
        return false;
    }
    output = candidate;
    return true;
}

std::uint32_t ReconnectBackoff::next_delay_seconds() {
    constexpr std::array<std::uint32_t, 6> delays{{1, 2, 4, 8, 16, 30}};
    const auto delay = delays[std::min<std::size_t>(index_, delays.size() - 1)];
    if (index_ < delays.size() - 1) {
        ++index_;
    }
    return delay;
}

void ReconnectBackoff::reset() { index_ = 0; }

bool serialize_sensor_message(const SensorReading& reading, TelemetryMessage& output) {
    SensorSpec spec{};
    if (!valid_reading(reading, spec)) {
        return false;
    }
    std::array<char, 65> topic{};
    std::array<char, 257> payload{};
    std::array<char, 32> value{};
    int value_written = 0;
    if (spec.kind == ValueKind::number) {
        value_written = std::snprintf(value.data(), value.size(), "%.15g", reading.value.number_value);
    } else if (spec.kind == ValueKind::boolean) {
        value_written = std::snprintf(value.data(), value.size(), "%s", reading.value.boolean_value ? "true" : "false");
    } else {
        value_written = std::snprintf(value.data(), value.size(), "%u", static_cast<unsigned>(reading.value.integer_value));
    }
    if (value_written <= 0 || value_written >= static_cast<int>(value.size())) {
        return false;
    }
    const int topic_written = std::snprintf(
        topic.data(), topic.size(), "home/pico/%.*s/sensor/%.*s",
        static_cast<int>(reading.device_id.size()), reading.device_id.data(),
        static_cast<int>(reading.sensor_type.size()), reading.sensor_type.data()
    );
    const int payload_written = std::snprintf(
        payload.data(), payload.size(),
        "{\"device_id\":\"%.*s\",\"sensor_type\":\"%.*s\",\"value\":%s,\"unit\":\"%.*s\",\"observed_at\":\"%.*s\"}",
        static_cast<int>(reading.device_id.size()), reading.device_id.data(),
        static_cast<int>(reading.sensor_type.size()), reading.sensor_type.data(), value.data(),
        static_cast<int>(reading.unit.size()), reading.unit.data(),
        static_cast<int>(reading.observed_at.size()), reading.observed_at.data()
    );
    if (topic_written < 0 || payload_written < 0) {
        return false;
    }
    return output.assign(
        {topic.data(), static_cast<std::size_t>(topic_written)},
        {payload.data(), static_cast<std::size_t>(payload_written)}
    );
}

bool serialize_status_message(const DeviceStatus& status, TelemetryMessage& output) {
    DeviceProfile profile{};
    if (!profile_for_device(status.device_id, profile) || !valid_utc(status.observed_at)) {
        return false;
    }
    const char* state = nullptr;
    switch (status.state) {
    case DeviceState::online:
        state = "online";
        break;
    case DeviceState::offline:
        state = "offline";
        break;
    default:
        return false;
    }
    std::array<char, 65> topic{};
    std::array<char, 257> payload{};
    const int topic_written = std::snprintf(
        topic.data(), topic.size(), "home/pico/%.*s/status",
        static_cast<int>(status.device_id.size()), status.device_id.data()
    );
    const int payload_written = std::snprintf(
        payload.data(), payload.size(), "{\"device_id\":\"%.*s\",\"status\":\"%s\",\"observed_at\":\"%.*s\"}",
        static_cast<int>(status.device_id.size()), status.device_id.data(), state,
        static_cast<int>(status.observed_at.size()), status.observed_at.data()
    );
    if (topic_written < 0 || payload_written < 0) {
        return false;
    }
    return output.assign(
        {topic.data(), static_cast<std::size_t>(topic_written)},
        {payload.data(), static_cast<std::size_t>(payload_written)}
    );
}

}

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace petcare {

enum class DeviceProfile { entrance_01, petzone_01 };
enum class ValueKind { number, boolean, integer };
enum class DeviceState { online, offline };

struct SensorValue {
    ValueKind kind;
    union {
        double number_value;
        bool boolean_value;
        std::uint16_t integer_value;
    };

    static constexpr SensorValue number(double value) {
        SensorValue result{};
        result.kind = ValueKind::number;
        result.number_value = value;
        return result;
    }

    static constexpr SensorValue boolean(bool value) {
        SensorValue result{};
        result.kind = ValueKind::boolean;
        result.boolean_value = value;
        return result;
    }

    static constexpr SensorValue integer(std::uint16_t value) {
        SensorValue result{};
        result.kind = ValueKind::integer;
        result.integer_value = value;
        return result;
    }
};

struct SensorReading {
    std::string_view device_id;
    std::string_view sensor_type;
    SensorValue value;
    std::string_view unit;
    std::string_view observed_at;
};

struct DeviceStatus {
    std::string_view device_id;
    DeviceState state;
    std::string_view observed_at;
};

struct TelemetryMessage {
    std::array<char, 64> topic{};
    std::size_t topic_size = 0;
    std::array<char, 256> payload{};
    std::size_t payload_size = 0;

    bool assign(std::string_view new_topic, std::string_view new_payload);
};

struct WeightCalibration {
    std::int32_t tare_raw;
    double counts_per_gram;

    bool grams(std::int32_t raw, double& output) const;
};

std::string_view profile_device_id(DeviceProfile profile);
bool profile_allows(DeviceProfile profile, std::string_view sensor_type);
bool serialize_sensor_message(const SensorReading& reading, TelemetryMessage& output);
bool serialize_status_message(const DeviceStatus& status, TelemetryMessage& output);

}

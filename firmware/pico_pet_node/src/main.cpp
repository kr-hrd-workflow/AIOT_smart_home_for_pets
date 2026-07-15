#include "pet_node.hpp"

#include <array>
#include <iostream>

namespace {

void print(const petcare::TelemetryMessage& message) {
    std::cout.write(message.topic.data(), static_cast<std::streamsize>(message.topic_size));
    std::cout << '\n';
    std::cout.write(message.payload.data(), static_cast<std::streamsize>(message.payload_size));
    std::cout << '\n';
}

bool publish(const petcare::SensorReading& reading) {
    petcare::TelemetryMessage message{};
    if (!petcare::serialize_sensor_message(reading, message)) {
        return false;
    }
    print(message);
    return true;
}

bool publish(const petcare::DeviceStatus& status) {
    petcare::TelemetryMessage message{};
    if (!petcare::serialize_status_message(status, message)) {
        return false;
    }
    print(message);
    return true;
}

}

int main() {
    using petcare::SensorReading;
    using petcare::SensorValue;
    constexpr std::string_view observed_at = "2026-07-15T07:00:00.000Z";
    const std::array<SensorReading, 4> entrance{{
        {"entrance-01", "temperature", SensorValue::number(25.5), "C", observed_at},
        {"entrance-01", "humidity", SensorValue::number(51.0), "%", observed_at},
        {"entrance-01", "presence_moving", SensorValue::boolean(true), "bool", observed_at},
        {"entrance-01", "presence_stationary", SensorValue::boolean(false), "bool", observed_at},
    }};
    const std::array<SensorReading, 9> petzone{{
        {"petzone-01", "temperature", SensorValue::number(24.25), "C", observed_at},
        {"petzone-01", "humidity", SensorValue::number(51.0), "%", observed_at},
        {"petzone-01", "presence_moving", SensorValue::boolean(true), "bool", observed_at},
        {"petzone-01", "presence_stationary", SensorValue::boolean(false), "bool", observed_at},
        {"petzone-01", "food_weight", SensorValue::number(80.0), "g", observed_at},
        {"petzone-01", "water_weight", SensorValue::number(80.0), "g", observed_at},
        {"petzone-01", "bed_pressure_left", SensorValue::integer(100), "adc", observed_at},
        {"petzone-01", "bed_pressure_center", SensorValue::integer(200), "adc", observed_at},
        {"petzone-01", "bed_pressure_right", SensorValue::integer(300), "adc", observed_at},
    }};
    for (const auto& reading : entrance) {
        if (!publish(reading)) return 1;
    }
    if (!publish({"entrance-01", petcare::DeviceState::online, observed_at})) return 1;
    for (const auto& reading : petzone) {
        if (!publish(reading)) return 1;
    }
    if (!publish({"petzone-01", petcare::DeviceState::online, observed_at})) return 1;
    return 0;
}

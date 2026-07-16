#include "pet_node.hpp"
#include "../pico/include/mqtt_publisher.hpp"

#include <array>
#include <cassert>
#include <cmath>
#include <cstdlib>
#include <new>
#include <string_view>

namespace {

std::size_t allocations = 0;

std::string_view topic(const petcare::TelemetryMessage& message) {
    return {message.topic.data(), message.topic_size};
}

std::string_view payload(const petcare::TelemetryMessage& message) {
    return {message.payload.data(), message.payload_size};
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
    using petcare::DeviceProfile;
    using petcare::DeviceState;
    using petcare::SensorReading;
    using petcare::SensorValue;

    petcare::ReconnectBackoff reconnect;
    for (const auto seconds : {1U, 2U, 4U, 8U, 16U, 30U, 30U}) {
        assert(reconnect.next_delay_seconds() == seconds);
    }
    reconnect.reset();
    assert(reconnect.next_delay_seconds() == 1U);

    static_assert(petcare::MqttContract::qos == 1);
    static_assert(!petcare::MqttContract::sensor_retain);
    static_assert(petcare::MqttContract::status_retain);
    static_assert(petcare::MqttContract::heartbeat_ms == 10'000);
    static_assert(petcare::UtcClock::minimum_utc_ms == 1'704'067'200'000ULL);
    static_assert(petcare::UtcClock::retry_ms == 15'000);
    static_assert(petcare::UtcClock::resync_ms == 21'600'000);
    static_assert(std::string_view{petcare::UtcClock::primary_server} == "pool.ntp.org");
    static_assert(std::string_view{petcare::UtcClock::fallback_server} == "time.cloudflare.com");

    petcare::UtcClock clock;
    std::array<char, 25> timestamp{};
    std::uint64_t timestamp_ms = 0;
    assert(!clock.valid());
    assert(!clock.timestamp(0, timestamp, timestamp_ms));
    assert(!clock.synchronize(petcare::UtcClock::minimum_utc_ms - 1, 100));
    assert(!clock.valid());
    assert(clock.synchronize(petcare::UtcClock::minimum_utc_ms, 100));
    assert(clock.timestamp(100, timestamp, timestamp_ms));
    assert((std::string_view{timestamp.data(), 24} == "2024-01-01T00:00:00.000Z"));
    const auto first_batch = timestamp;
    std::array<char, 25> same_batch{};
    std::uint64_t same_batch_ms = 0;
    assert(clock.timestamp(100, same_batch, same_batch_ms));
    assert(same_batch == first_batch && same_batch_ms == timestamp_ms);
    clock.mark_published(timestamp_ms);
    assert(clock.synchronize(petcare::UtcClock::minimum_utc_ms + 10'000, 5'000));
    assert(clock.timestamp(5'500, timestamp, timestamp_ms));
    assert((std::string_view{timestamp.data(), 24} == "2024-01-01T00:00:10.500Z"));
    clock.mark_published(timestamp_ms);
    assert(!clock.synchronize(timestamp_ms - 1, 6'000));
    assert(!clock.valid());
    assert(!clock.timestamp(6'000, timestamp, timestamp_ms));
    assert(!clock.synchronize(timestamp_ms, 6'100));
    assert(clock.synchronize(timestamp_ms + 1, 6'200));
    assert(clock.valid());
    petcare::UtcClock rebooted_clock;
    assert(!rebooted_clock.valid());

    petcare::HeartbeatSchedule heartbeat;
    heartbeat.connected(50);
    assert(!heartbeat.due(10'049));
    assert(heartbeat.due(10'050));
    heartbeat.emitted(10'050);
    assert(!heartbeat.due(20'049));
    assert(heartbeat.due(20'050));

    static_assert(petcare::TelemetryMessage{}.topic.size() == 64);
    static_assert(petcare::TelemetryMessage{}.payload.size() == 256);

    assert(petcare::profile_device_id(DeviceProfile::entrance_01) == "entrance-01");
    assert(petcare::profile_device_id(DeviceProfile::petzone_01) == "petzone-01");
    for (const auto sensor : {"temperature", "humidity", "presence_moving", "presence_stationary"}) {
        assert(petcare::profile_allows(DeviceProfile::entrance_01, sensor));
        assert(petcare::profile_allows(DeviceProfile::petzone_01, sensor));
    }
    for (const auto sensor : {"food_weight", "water_weight", "bed_pressure_left", "bed_pressure_center", "bed_pressure_right"}) {
        assert(!petcare::profile_allows(DeviceProfile::entrance_01, sensor));
        assert(petcare::profile_allows(DeviceProfile::petzone_01, sensor));
    }
    for (const auto retired : {"bed_weight", "light", "motion", "door_open"}) {
        assert(!petcare::profile_allows(DeviceProfile::entrance_01, retired));
        assert(!petcare::profile_allows(DeviceProfile::petzone_01, retired));
    }
    assert(!petcare::profile_allows(static_cast<DeviceProfile>(99), "temperature"));

    std::array<char, 257> boundary_bytes{};
    boundary_bytes.fill('x');
    petcare::TelemetryMessage boundary{};
    assert(boundary.assign({boundary_bytes.data(), 64}, {boundary_bytes.data(), 256}));
    assert(boundary.topic_size == 64 && boundary.payload_size == 256);
    const auto full_boundary = boundary;
    assert(!boundary.assign({boundary_bytes.data(), 65}, {boundary_bytes.data(), 256}));
    assert(boundary.topic == full_boundary.topic && boundary.payload == full_boundary.payload);
    assert(!boundary.assign({boundary_bytes.data(), 64}, {boundary_bytes.data(), 257}));
    assert(boundary.topic == full_boundary.topic && boundary.payload == full_boundary.payload);

    constexpr std::string_view observed_at = "2026-07-15T07:00:00.000Z";
    const SensorReading temperature{"entrance-01", "temperature", SensorValue::number(25.5), "C", observed_at};
    petcare::TelemetryMessage sensor_message{};
    assert(petcare::serialize_sensor_message(temperature, sensor_message));
    assert(topic(sensor_message) == "home/pico/entrance-01/sensor/temperature");
    assert(payload(sensor_message) ==
        "{\"device_id\":\"entrance-01\",\"sensor_type\":\"temperature\",\"value\":25.5,\"unit\":\"C\",\"observed_at\":\"2026-07-15T07:00:00.000Z\"}");

    const petcare::DeviceStatus online{"petzone-01", DeviceState::online, observed_at};
    petcare::TelemetryMessage status_message{};
    assert(petcare::serialize_status_message(online, status_message));
    assert(topic(status_message) == "home/pico/petzone-01/status");
    assert(payload(status_message) ==
        "{\"device_id\":\"petzone-01\",\"status\":\"online\",\"observed_at\":\"2026-07-15T07:00:00.000Z\"}");

    petcare::TelemetryMessage lwt{};
    assert(petcare::make_offline_lwt("petzone-01", observed_at, lwt));
    assert(topic(lwt) == "home/pico/petzone-01/status");
    assert(payload(lwt) ==
        "{\"device_id\":\"petzone-01\",\"status\":\"offline\",\"observed_at\":\"2026-07-15T07:00:00.000Z\"}");

    const std::array<SensorReading, 9> petzone_readings{{
        {"petzone-01", "temperature", SensorValue::number(24.25), "C", observed_at},
        {"petzone-01", "humidity", SensorValue::number(51.0), "%", observed_at},
        {"petzone-01", "presence_moving", SensorValue::boolean(true), "bool", observed_at},
        {"petzone-01", "presence_stationary", SensorValue::boolean(false), "bool", observed_at},
        {"petzone-01", "food_weight", SensorValue::number(80.0), "g", observed_at},
        {"petzone-01", "water_weight", SensorValue::number(80.0), "g", observed_at},
        {"petzone-01", "bed_pressure_left", SensorValue::integer(0), "adc", observed_at},
        {"petzone-01", "bed_pressure_center", SensorValue::integer(2048), "adc", observed_at},
        {"petzone-01", "bed_pressure_right", SensorValue::integer(4095), "adc", observed_at},
    }};
    for (const auto& reading : petzone_readings) {
        petcare::TelemetryMessage message{};
        assert(petcare::serialize_sensor_message(reading, message));
    }
    petcare::TelemetryMessage boolean_message{};
    assert(petcare::serialize_sensor_message(petzone_readings[2], boolean_message));
    assert(payload(boolean_message) ==
        "{\"device_id\":\"petzone-01\",\"sensor_type\":\"presence_moving\",\"value\":true,\"unit\":\"bool\",\"observed_at\":\"2026-07-15T07:00:00.000Z\"}");
    petcare::TelemetryMessage integer_message{};
    assert(petcare::serialize_sensor_message(petzone_readings[8], integer_message));
    assert(payload(integer_message) ==
        "{\"device_id\":\"petzone-01\",\"sensor_type\":\"bed_pressure_right\",\"value\":4095,\"unit\":\"adc\",\"observed_at\":\"2026-07-15T07:00:00.000Z\"}");

    const petcare::WeightCalibration food{100, 10.0};
    const petcare::WeightCalibration water{100, 10.0};
    double food_grams = 0.0;
    double water_grams = 0.0;
    assert(food.grams(900, food_grams) && food_grams == 80.0);
    assert(water.grams(900, water_grams) && water_grams == 80.0);
    const petcare::WeightCalibration changed_food{200, 10.0};
    assert(changed_food.grams(900, food_grams) && food_grams == 70.0);
    assert(water.grams(900, water_grams) && water_grams == 80.0);
    const double previous_grams = water_grams;
    const petcare::WeightCalibration zero_scale{100, 0.0};
    const petcare::WeightCalibration nonfinite_scale{100, NAN};
    assert(!zero_scale.grams(900, water_grams));
    assert(!nonfinite_scale.grams(900, water_grams));
    assert(water_grams == previous_grams);

    petcare::TelemetryMessage sentinel{};
    sentinel.topic.fill('T');
    sentinel.payload.fill('P');
    sentinel.topic_size = 7;
    sentinel.payload_size = 11;
    const auto unchanged = sentinel;
    const std::array<SensorReading, 11> invalid{{
        {"entrance-01", "food_weight", SensorValue::number(1.0), "g", observed_at},
        {"petzone-01", "temperature", SensorValue::boolean(true), "C", observed_at},
        {"petzone-01", "humidity", SensorValue::number(INFINITY), "%", observed_at},
        {"petzone-01", "bed_pressure_left", SensorValue::integer(4096), "adc", observed_at},
        {"petzone-01", "temperature", SensorValue::number(1.0), "F", observed_at},
        {"petzone-01", "bed_weight", SensorValue::number(1.0), "g", observed_at},
        {"petzone-01", "temperature", SensorValue::number(1.0), "C", "2026-07-15T07:00:00+09:00"},
        {"device-id-that-is-longer-than-the-fixed-sixty-four-byte-topic-buffer-boundary", "temperature", SensorValue::number(1.0), "C", observed_at},
        {"petzone-01", "temperature", SensorValue::number(1.0), "C", "2026-07-15T07:00:00.000Z-extra-payload-that-must-not-be-truncated"},
        {"petzone-01", "temperature", SensorValue::number(1.0), "C", "2026-02-29T07:00:00.000Z"},
        {"petzone-01", "temperature", SensorValue::number(1.0), "C", "2026-04-31T07:00:00.000Z"},
    }};
    for (const auto& reading : invalid) {
        assert(!petcare::serialize_sensor_message(reading, sentinel));
        assert(sentinel.topic == unchanged.topic && sentinel.payload == unchanged.payload);
        assert(sentinel.topic_size == unchanged.topic_size && sentinel.payload_size == unchanged.payload_size);
    }
    assert(!petcare::serialize_status_message({"petzone-01", static_cast<DeviceState>(99), observed_at}, sentinel));
    assert(sentinel.topic == unchanged.topic && sentinel.payload == unchanged.payload);

    petcare::TelemetryMessage warmup{};
    assert(petcare::serialize_sensor_message(petzone_readings[0], warmup));
    assert(petcare::serialize_status_message(online, warmup));
    const auto before = allocations;
    for (int second = 0; second < 120; ++second) {
        for (const auto& reading : petzone_readings) {
            assert(petcare::serialize_sensor_message(reading, warmup));
        }
        assert(petcare::serialize_status_message(online, warmup));
        assert(clock.timestamp(6'200 + static_cast<std::uint64_t>(second) * 1'000, timestamp, timestamp_ms));
        clock.mark_published(timestamp_ms);
        assert(petcare::make_offline_lwt("petzone-01", observed_at, warmup));
    }
    assert(allocations == before);
    return 0;
}

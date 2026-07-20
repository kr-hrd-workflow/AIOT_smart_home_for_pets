#include "petcare_config.hpp"
#include "sensors.hpp"

#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <new>
#include <string_view>

namespace {

std::size_t allocations = 0;

struct FakeSensors {
    bool sht_valid = true;
    bool presence_valid = true;
    bool food_valid = true;
    bool water_valid = true;
    std::array<bool, 3> fsr_valid{{true, true, true}};
    double temperature = 25.0;
    double humidity = 50.0;
    bool moving = true;
    bool stationary = false;
    double food = 80.0;
    double water = 70.0;
    std::array<std::uint16_t, 3> fsr{{111, 222, 333}};
    std::array<char, 4'096> calls{};
    std::size_t call_count = 0;

    void called(char value) { calls[call_count++] = value; }

    static bool read_sht31(void* context, double& temperature, double& humidity) {
        auto& self = *static_cast<FakeSensors*>(context);
        self.called('S');
        temperature = self.temperature;
        humidity = self.humidity;
        return self.sht_valid;
    }

    static bool read_presence(void* context, bool& moving, bool& stationary) {
        auto& self = *static_cast<FakeSensors*>(context);
        self.called('P');
        moving = self.moving;
        stationary = self.stationary;
        return self.presence_valid;
    }

    static bool read_weight(void* context, petcare::Bowl bowl, double& grams) {
        auto& self = *static_cast<FakeSensors*>(context);
        if (bowl == petcare::Bowl::food) {
            self.called('F');
            grams = self.food;
            return self.food_valid;
        }
        self.called('W');
        grams = self.water;
        return self.water_valid;
    }

    static bool read_fsr(void* context, petcare::FsrChannel channel, std::uint16_t& raw) {
        auto& self = *static_cast<FakeSensors*>(context);
        const auto index = static_cast<std::size_t>(channel);
        self.called("LCR"[index]);
        raw = self.fsr[index];
        return self.fsr_valid[index];
    }

    petcare::SensorSource source() {
        return {this, read_sht31, read_presence, read_weight, read_fsr};
    }
};

std::size_t drain(
    petcare::SensorSchedule& schedule,
    std::uint32_t now_ms,
    std::array<petcare::ScheduledOutput, 512>& outputs
) {
    std::size_t count = 0;
    while (schedule.next_due(now_ms, outputs[count])) {
        assert(++count < outputs.size());
    }
    return count;
}

}

void* operator new(std::size_t size) {
    ++allocations;
    if (void* value = std::malloc(size)) {
        return value;
    }
    throw std::bad_alloc{};
}

void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete(void* value) noexcept { std::free(value); }
void operator delete[](void* value) noexcept { std::free(value); }
void operator delete(void* value, std::size_t) noexcept { std::free(value); }
void operator delete[](void* value, std::size_t) noexcept { std::free(value); }

int main() {
    using petcare::DeviceProfile;
    using petcare::OutputKind;

    static_assert(petcare::config::sht31_i2c_index == 0);
    static_assert(petcare::config::sht31_sda_pin == 4);
    static_assert(petcare::config::sht31_scl_pin == 5);
    static_assert(petcare::config::sht31_address == 0x44);
    static_assert(petcare::config::sht31_cadence_ms == 30'000);
    static_assert(petcare::config::sensor_logic_supply_mv == 3'300);
    static_assert(petcare::config::gpio_input_max_mv == 3'300);
    static_assert(petcare::config::ld2410c_uart_index == 1);
    static_assert(petcare::config::ld2410c_rx_pin == 9);
    static_assert(petcare::config::ld2410c_baud == 256'000);
    static_assert(petcare::config::ld2410c_data_bits == 8);
    static_assert(petcare::config::ld2410c_stop_bits == 1);
    static_assert(!petcare::config::ld2410c_parity);
    static_assert(!petcare::config::ld2410c_pico_tx_connected);
    static_assert(petcare::config::ld2410c_supply_mv == 5'000);
    static_assert(petcare::config::ld2410c_uart_tx_mv == 3'300);
    static_assert(petcare::config::ld2410c_min_supply_ma == 200);
    static_assert(petcare::config::ld2410c_timeout_us > 0);
    static_assert(petcare::config::presence_cadence_ms == 1'000);
    static_assert(petcare::config::weight_cadence_ms == 1'000);
    static_assert(petcare::config::food_hx711_dout_pin == 10);
    static_assert(petcare::config::food_hx711_sck_pin == 11);
    static_assert(petcare::config::water_hx711_dout_pin == 12);
    static_assert(petcare::config::water_hx711_sck_pin == 13);
    static_assert(petcare::config::food_hx711_timeout_us > 0);
    static_assert(petcare::config::water_hx711_timeout_us > 0);
    static_assert(petcare::config::food_tare_raw == 100);
    static_assert(petcare::config::food_counts_per_gram == 10.0);
    static_assert(petcare::config::water_tare_raw == 100);
    static_assert(petcare::config::water_counts_per_gram == 10.0);
    static_assert(&petcare::config::food_calibration != &petcare::config::water_calibration);
    static_assert(petcare::config::fsr_left_pin == 26);
    static_assert(petcare::config::fsr_center_pin == 27);
    static_assert(petcare::config::fsr_right_pin == 28);
    static_assert(petcare::config::fsr_cadence_ms == 1'000);
    static_assert(petcare::config::fsr_supply_mv == 3'300);
    static_assert(petcare::config::fsr_fixed_resistor_ohms == 10'000);
    static_assert(petcare::config::fsr_adc_max == 4'095);

    double grams = 0.0;
    assert(petcare::config::food_calibration.grams(900, grams) && grams == 80.0);
    assert(petcare::config::water_calibration.grams(900, grams) && grams == 80.0);

    const std::array<std::uint8_t, 6> sht31{{0x66, 0x66, 0x93, 0x80, 0x00, 0xA2}};
    double temperature = 0.0;
    double humidity = 0.0;
    assert(petcare::decode_sht31(sht31, temperature, humidity));
    assert(std::abs(temperature - 25.0) < 0.01);
    assert(std::abs(humidity - 50.0008) < 0.01);
    auto bad_sht31 = sht31;
    bad_sht31[2] ^= 1;
    assert(!petcare::decode_sht31(bad_sht31, temperature, humidity));

    const std::array<std::uint8_t, 23> ld2410c{{
        0xF4, 0xF3, 0xF2, 0xF1, 0x0D, 0x00, 0x02, 0xAA, 0x03,
        0x64, 0x00, 0x32, 0xC8, 0x00, 0x28, 0x2C, 0x01, 0x55, 0x00,
        0xF8, 0xF7, 0xF6, 0xF5,
    }};
    bool moving = false;
    bool stationary = false;
    assert(petcare::decode_ld2410c(ld2410c, moving, stationary));
    assert(moving && stationary);
    auto bad_ld2410c = ld2410c;
    bad_ld2410c[7] = 0;
    assert(!petcare::decode_ld2410c(bad_ld2410c, moving, stationary));

    FakeSensors fake;
    petcare::SensorSchedule petzone{DeviceProfile::petzone_01, fake.source()};
    petzone.start(0);
    petcare::ScheduledOutput output{};
    assert(!petzone.next_due(999, output));

    std::array<petcare::ScheduledOutput, 512> outputs{};
    const auto count = drain(petzone, 30'000, outputs);
    assert(count == 215);
    constexpr std::array<std::string_view, 10> shared_order{{
        "temperature", "humidity", "presence_moving", "presence_stationary",
        "food_weight", "water_weight", "bed_pressure_left", "bed_pressure_center",
        "bed_pressure_right", "status",
    }};
    std::size_t shared_index = 0;
    for (std::size_t index = 0; index < count; ++index) {
        if (outputs[index].due_ms == 30'000) {
            const auto name = outputs[index].kind == OutputKind::status ? std::string_view{"status"} : outputs[index].sensor_type;
            assert(name == shared_order[shared_index++]);
        }
    }
    assert(shared_index == shared_order.size());
    assert(fake.call_count >= 7);
    assert((std::string_view{fake.calls.data() + fake.call_count - 7, 7} == "SPFWLCR"));

    FakeSensors entrance_fake;
    petcare::SensorSchedule entrance{DeviceProfile::entrance_01, entrance_fake.source()};
    entrance.start(0);
    const auto entrance_count = drain(entrance, 30'000, outputs);
    std::size_t entrance_shared = 0;
    constexpr std::array<std::string_view, 5> entrance_order{{
        "temperature", "humidity", "presence_moving", "presence_stationary", "status",
    }};
    for (std::size_t index = 0; index < entrance_count; ++index) {
        assert(outputs[index].sensor_type != "food_weight");
        assert(outputs[index].sensor_type != "water_weight");
        assert(outputs[index].sensor_type != "bed_pressure_left");
        if (outputs[index].due_ms == 30'000) {
            const auto name = outputs[index].kind == OutputKind::status ? std::string_view{"status"} : outputs[index].sensor_type;
            assert(name == entrance_order[entrance_shared++]);
        }
    }
    assert(entrance_shared == entrance_order.size());
    for (std::size_t index = 0; index < entrance_fake.call_count; ++index) {
        assert(entrance_fake.calls[index] == 'S' || entrance_fake.calls[index] == 'P');
    }

    FakeSensors invalid;
    invalid.sht_valid = false;
    invalid.presence_valid = false;
    invalid.food = std::numeric_limits<double>::quiet_NaN();
    invalid.water_valid = true;
    invalid.water = 0.0;
    invalid.fsr_valid = {{false, true, false}};
    invalid.fsr[1] = 4'096;
    petcare::SensorSchedule invalid_schedule{DeviceProfile::petzone_01, invalid.source()};
    invalid_schedule.start(0);
    const auto invalid_count = drain(invalid_schedule, 1'000, outputs);
    assert(invalid_count == 1);
    assert(outputs[0].sensor_type == "water_weight");
    assert(outputs[0].value.number_value == 0.0);

    FakeSensors rollover_fake;
    petcare::SensorSchedule rollover{DeviceProfile::entrance_01, rollover_fake.source()};
    rollover.start(std::numeric_limits<std::uint32_t>::max() - 500);
    assert(!rollover.next_due(498, output));
    assert(rollover.next_due(499, output));
    assert(output.due_ms == 499);
    assert(output.sensor_type == "presence_moving");

    FakeSensors allocation_fake;
    petcare::SensorSchedule allocation_schedule{DeviceProfile::petzone_01, allocation_fake.source()};
    allocation_schedule.start(0);
    const auto before = allocations;
    for (std::uint32_t now = 1'000; now <= 120'000; now += 1'000) {
        while (allocation_schedule.next_due(now, output)) {
        }
    }
    assert(allocations == before);
}

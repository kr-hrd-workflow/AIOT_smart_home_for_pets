#include "sensors.hpp"

#include "mqtt_publisher.hpp"
#include "petcare_config.hpp"

#include <cmath>
#include <cstdint>

#ifdef PICO_ON_DEVICE
#include "hardware/adc.h"
#include "hardware/gpio.h"
#include "hardware/i2c.h"
#include "hardware/uart.h"
#include "pico/stdlib.h"
#include "pico/time.h"
#endif

namespace petcare {

namespace {

bool crc_valid(const std::uint8_t* data, std::uint8_t expected) {
    std::uint8_t crc = 0xff;
    for (std::size_t index = 0; index < 2; ++index) {
        crc ^= data[index];
        for (int bit = 0; bit < 8; ++bit) {
            crc = static_cast<std::uint8_t>((crc & 0x80U) ? (crc << 1U) ^ 0x31U : crc << 1U);
        }
    }
    return crc == expected;
}

bool due(std::uint32_t now_ms, std::uint32_t target_ms) {
    return static_cast<std::int32_t>(now_ms - target_ms) >= 0;
}

}

bool decode_sht31(const std::array<std::uint8_t, 6>& frame, double& temperature, double& humidity) {
    if (!crc_valid(frame.data(), frame[2]) || !crc_valid(frame.data() + 3, frame[5])) {
        return false;
    }
    const auto raw_temperature = static_cast<std::uint16_t>((frame[0] << 8U) | frame[1]);
    const auto raw_humidity = static_cast<std::uint16_t>((frame[3] << 8U) | frame[4]);
    const double candidate_temperature = -45.0 + 175.0 * raw_temperature / 65'535.0;
    const double candidate_humidity = 100.0 * raw_humidity / 65'535.0;
    if (!std::isfinite(candidate_temperature) || !std::isfinite(candidate_humidity) ||
        candidate_temperature < -45.0 || candidate_temperature > 130.0 ||
        candidate_humidity < 0.0 || candidate_humidity > 100.0) {
        return false;
    }
    temperature = candidate_temperature;
    humidity = candidate_humidity;
    return true;
}

bool decode_ld2410c(const std::array<std::uint8_t, 23>& frame, bool& moving, bool& stationary) {
    constexpr std::array<std::uint8_t, 4> header{{0xf4, 0xf3, 0xf2, 0xf1}};
    constexpr std::array<std::uint8_t, 4> footer{{0xf8, 0xf7, 0xf6, 0xf5}};
    for (std::size_t index = 0; index < header.size(); ++index) {
        if (frame[index] != header[index] || frame[index + 19] != footer[index]) {
            return false;
        }
    }
    if (frame[4] != 0x0d || frame[5] != 0x00 || frame[6] != 0x02 || frame[7] != 0xaa ||
        frame[17] != 0x55 || frame[18] != 0x00 || frame[8] > 3) {
        return false;
    }
    moving = frame[8] == 1 || frame[8] == 3;
    stationary = frame[8] == 2 || frame[8] == 3;
    return true;
}

void SensorSchedule::start(std::uint32_t now_ms) {
    next_sht_ms_ = now_ms + config::sht31_cadence_ms;
    next_fast_ms_ = now_ms + config::presence_cadence_ms;
    next_status_ms_ = now_ms + static_cast<std::uint32_t>(MqttContract::heartbeat_ms);
    pending_size_ = 0;
    pending_index_ = 0;
    started_ = true;
}

void SensorSchedule::append(
    std::uint32_t due_ms,
    std::string_view type,
    SensorValue value,
    std::string_view unit
) {
    pending_[pending_size_++] = {OutputKind::sensor, due_ms, type, value, unit};
}

void SensorSchedule::prepare(std::uint32_t due_ms, bool sht_due, bool fast_due, bool status_due) {
    pending_size_ = 0;
    pending_index_ = 0;

    if (sht_due && source_.read_sht31) {
        double temperature = 0.0;
        double humidity = 0.0;
        if (source_.read_sht31(source_.context, temperature, humidity)) {
            if (std::isfinite(temperature) && temperature >= -45.0 && temperature <= 130.0) {
                append(due_ms, "temperature", SensorValue::number(temperature), "C");
            }
            if (std::isfinite(humidity) && humidity >= 0.0 && humidity <= 100.0) {
                append(due_ms, "humidity", SensorValue::number(humidity), "%");
            }
        }
    }

    if (fast_due && source_.read_presence) {
        bool moving = false;
        bool stationary = false;
        if (source_.read_presence(source_.context, moving, stationary)) {
            append(due_ms, "presence_moving", SensorValue::boolean(moving), "bool");
            append(due_ms, "presence_stationary", SensorValue::boolean(stationary), "bool");
        }
    }

    if (fast_due && profile_ == DeviceProfile::petzone_01) {
        if (source_.read_weight) {
            double grams = 0.0;
            if (source_.read_weight(source_.context, Bowl::food, grams) && std::isfinite(grams)) {
                append(due_ms, "food_weight", SensorValue::number(grams), "g");
            }
            if (source_.read_weight(source_.context, Bowl::water, grams) && std::isfinite(grams)) {
                append(due_ms, "water_weight", SensorValue::number(grams), "g");
            }
        }
        if (source_.read_fsr) {
            constexpr std::array<FsrChannel, 3> channels{{FsrChannel::left, FsrChannel::center, FsrChannel::right}};
            constexpr std::array<std::string_view, 3> names{{
                "bed_pressure_left", "bed_pressure_center", "bed_pressure_right",
            }};
            for (std::size_t index = 0; index < channels.size(); ++index) {
                std::uint16_t raw = 0;
                if (source_.read_fsr(source_.context, channels[index], raw) && raw <= config::fsr_adc_max) {
                    append(due_ms, names[index], SensorValue::integer(raw), "adc");
                }
            }
        }
    }

    if (status_due) {
        pending_[pending_size_++] = {OutputKind::status, due_ms, {}, {}, {}};
    }
}

bool SensorSchedule::next_due(std::uint32_t now_ms, ScheduledOutput& output) {
    for (;;) {
        if (pending_index_ < pending_size_) {
            output = pending_[pending_index_++];
            return true;
        }
        if (!started_) {
            return false;
        }

        bool found = false;
        std::uint32_t due_ms = 0;
        const auto choose = [&](std::uint32_t candidate) {
            if (due(now_ms, candidate) && (!found || now_ms - candidate > now_ms - due_ms)) {
                due_ms = candidate;
                found = true;
            }
        };
        choose(next_sht_ms_);
        choose(next_fast_ms_);
        choose(next_status_ms_);
        if (!found) {
            return false;
        }

        const bool sht_due = next_sht_ms_ == due_ms;
        const bool fast_due = next_fast_ms_ == due_ms;
        const bool status_due = next_status_ms_ == due_ms;
        if (sht_due) {
            next_sht_ms_ += config::sht31_cadence_ms;
        }
        if (fast_due) {
            next_fast_ms_ += config::presence_cadence_ms;
        }
        if (status_due) {
            next_status_ms_ += static_cast<std::uint32_t>(MqttContract::heartbeat_ms);
        }
        prepare(due_ms, sht_due, fast_due, status_due);
    }
}

#ifdef PICO_ON_DEVICE
bool SensorHardware::init() {
    static_assert(config::sht31_i2c_index == 0);
    static_assert(config::ld2410c_uart_index == 1);
    static_assert(config::presence_cadence_ms == config::weight_cadence_ms);
    static_assert(config::presence_cadence_ms == config::fsr_cadence_ms);

    i2c_init(i2c0, config::sht31_i2c_baud_hz);
    gpio_set_function(config::sht31_sda_pin, GPIO_FUNC_I2C);
    gpio_set_function(config::sht31_scl_pin, GPIO_FUNC_I2C);
    gpio_pull_up(config::sht31_sda_pin);
    gpio_pull_up(config::sht31_scl_pin);

    uart_init(uart1, config::ld2410c_baud);
    uart_set_format(uart1, config::ld2410c_data_bits, config::ld2410c_stop_bits, UART_PARITY_NONE);
    uart_set_fifo_enabled(uart1, true);
    gpio_set_function(config::ld2410c_rx_pin, GPIO_FUNC_UART);

#if defined(PETCARE_PROFILE_PETZONE)
    for (const auto pin : {config::food_hx711_dout_pin, config::water_hx711_dout_pin}) {
        gpio_init(pin);
        gpio_set_dir(pin, GPIO_IN);
    }
    for (const auto pin : {config::food_hx711_sck_pin, config::water_hx711_sck_pin}) {
        gpio_init(pin);
        gpio_put(pin, false);
        gpio_set_dir(pin, GPIO_OUT);
    }
    adc_init();
    for (const auto pin : {config::fsr_left_pin, config::fsr_center_pin, config::fsr_right_pin}) {
        adc_gpio_init(pin);
    }
#endif
    return true;
}

SensorSource SensorHardware::source() {
    return {this, read_sht31, read_presence, read_weight, read_fsr};
}

bool SensorHardware::read_sht31(void*, double& temperature, double& humidity) {
    constexpr std::array<std::uint8_t, 2> command{{0x24, 0x00}};
    std::array<std::uint8_t, 6> frame{};
    if (i2c_write_timeout_us(
            i2c0, config::sht31_address, command.data(), command.size(), false, config::sht31_timeout_us
        ) != static_cast<int>(command.size())) {
        return false;
    }
    sleep_ms(15);
    if (i2c_read_timeout_us(
            i2c0, config::sht31_address, frame.data(), frame.size(), false, config::sht31_timeout_us
        ) != static_cast<int>(frame.size())) {
        return false;
    }
    return decode_sht31(frame, temperature, humidity);
}

bool SensorHardware::read_presence(void* context, bool& moving, bool& stationary) {
    auto& self = *static_cast<SensorHardware*>(context);
    constexpr std::array<std::uint8_t, 4> header{{0xf4, 0xf3, 0xf2, 0xf1}};
    const auto started = time_us_64();
    while (time_us_64() - started < config::ld2410c_timeout_us) {
        if (!uart_is_readable(uart1)) {
            tight_loop_contents();
            continue;
        }
        const auto value = static_cast<std::uint8_t>(uart_getc(uart1));
        if (self.ld2410c_size_ < header.size()) {
            if (value == header[self.ld2410c_size_]) {
                self.ld2410c_frame_[self.ld2410c_size_++] = value;
            } else {
                self.ld2410c_size_ = value == header[0] ? 1 : 0;
                if (self.ld2410c_size_) {
                    self.ld2410c_frame_[0] = value;
                }
            }
            continue;
        }
        self.ld2410c_frame_[self.ld2410c_size_++] = value;
        if (self.ld2410c_size_ == self.ld2410c_frame_.size()) {
            self.ld2410c_size_ = 0;
            if (decode_ld2410c(self.ld2410c_frame_, moving, stationary)) {
                return true;
            }
        }
    }
    return false;
}

bool SensorHardware::read_hx711(
    std::uint8_t dout_pin,
    std::uint8_t sck_pin,
    std::uint32_t timeout_us,
    const WeightCalibration& calibration,
    double& grams
) {
    const auto started = time_us_64();
    while (gpio_get(dout_pin)) {
        if (time_us_64() - started >= timeout_us) {
            return false;
        }
        tight_loop_contents();
    }

    std::uint32_t raw = 0;
    for (int bit = 0; bit < 24; ++bit) {
        gpio_put(sck_pin, true);
        sleep_us(1);
        raw = (raw << 1U) | (gpio_get(dout_pin) ? 1U : 0U);
        gpio_put(sck_pin, false);
        sleep_us(1);
    }
    gpio_put(sck_pin, true);
    sleep_us(1);
    gpio_put(sck_pin, false);
    sleep_us(1);

    const auto signed_raw = static_cast<std::int32_t>(raw) - ((raw & 0x800000U) ? 0x01000000 : 0);
    return calibration.grams(signed_raw, grams) && std::isfinite(grams);
}

bool SensorHardware::read_weight(void*, Bowl bowl, double& grams) {
    if (bowl == Bowl::food) {
        return read_hx711(
            config::food_hx711_dout_pin,
            config::food_hx711_sck_pin,
            config::food_hx711_timeout_us,
            config::food_calibration,
            grams
        );
    }
    return read_hx711(
        config::water_hx711_dout_pin,
        config::water_hx711_sck_pin,
        config::water_hx711_timeout_us,
        config::water_calibration,
        grams
    );
}

bool SensorHardware::read_fsr(void*, FsrChannel channel, std::uint16_t& raw) {
    constexpr std::array<std::uint8_t, 3> pins{{
        config::fsr_left_pin, config::fsr_center_pin, config::fsr_right_pin,
    }};
    const auto pin = pins[static_cast<std::size_t>(channel)];
    adc_select_input(pin - 26U);
    sleep_us(5);
    const auto candidate = adc_read();
    if (candidate > config::fsr_adc_max) {
        return false;
    }
    raw = candidate;
    return true;
}
#endif

}

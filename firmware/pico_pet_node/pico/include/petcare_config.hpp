#pragma once

#include "pet_node.hpp"

#include <cstdint>

namespace petcare::config {

#if defined(PETCARE_PROFILE_ENTRANCE)
inline constexpr char device_id[] = "entrance-01";
inline constexpr DeviceProfile device_profile = DeviceProfile::entrance_01;
#elif defined(PETCARE_PROFILE_PETZONE)
inline constexpr char device_id[] = "petzone-01";
inline constexpr DeviceProfile device_profile = DeviceProfile::petzone_01;
#else
#error "A PetCare Pico profile must be selected"
#endif

inline constexpr auto client_id = device_id;
inline constexpr std::uint32_t wifi_timeout_ms = 10'000;
inline constexpr std::uint32_t mqtt_timeout_ms = 10'000;

inline constexpr std::uint8_t sht31_i2c_index = 0;
inline constexpr std::uint8_t sht31_sda_pin = 4;
inline constexpr std::uint8_t sht31_scl_pin = 5;
inline constexpr std::uint8_t sht31_address = 0x44;
inline constexpr std::uint32_t sht31_i2c_baud_hz = 100'000;
inline constexpr std::uint32_t sht31_timeout_us = 20'000;
inline constexpr std::uint32_t sht31_cadence_ms = 30'000;

inline constexpr std::uint16_t sensor_logic_supply_mv = 3'300;
inline constexpr std::uint16_t gpio_input_max_mv = 3'300;
inline constexpr std::uint8_t ld2410c_uart_index = 1;
inline constexpr std::uint8_t ld2410c_rx_pin = 9;
inline constexpr std::uint32_t ld2410c_baud = 256'000;
inline constexpr std::uint8_t ld2410c_data_bits = 8;
inline constexpr std::uint8_t ld2410c_stop_bits = 1;
inline constexpr bool ld2410c_parity = false;
inline constexpr bool ld2410c_pico_tx_connected = false;
inline constexpr std::uint16_t ld2410c_supply_mv = 5'000;
inline constexpr std::uint16_t ld2410c_uart_tx_mv = 3'300;
inline constexpr std::uint16_t ld2410c_min_supply_ma = 200;
inline constexpr std::uint32_t ld2410c_timeout_us = 50'000;

inline constexpr std::uint32_t presence_cadence_ms = 1'000;
inline constexpr std::uint32_t weight_cadence_ms = 1'000;
inline constexpr std::uint8_t food_hx711_dout_pin = 10;
inline constexpr std::uint8_t food_hx711_sck_pin = 11;
inline constexpr std::uint8_t water_hx711_dout_pin = 12;
inline constexpr std::uint8_t water_hx711_sck_pin = 13;
inline constexpr std::uint32_t food_hx711_timeout_us = 100'000;
inline constexpr std::uint32_t water_hx711_timeout_us = 100'000;
inline constexpr std::int32_t food_tare_raw = 100;
inline constexpr double food_counts_per_gram = 10.0;
inline constexpr std::int32_t water_tare_raw = 100;
inline constexpr double water_counts_per_gram = 10.0;
inline constexpr WeightCalibration food_calibration{food_tare_raw, food_counts_per_gram};
inline constexpr WeightCalibration water_calibration{water_tare_raw, water_counts_per_gram};

inline constexpr std::uint8_t fsr_left_pin = 26;
inline constexpr std::uint8_t fsr_center_pin = 27;
inline constexpr std::uint8_t fsr_right_pin = 28;
inline constexpr std::uint32_t fsr_cadence_ms = 1'000;
inline constexpr std::uint16_t fsr_supply_mv = 3'300;
inline constexpr std::uint32_t fsr_fixed_resistor_ohms = 10'000;
inline constexpr std::uint16_t fsr_adc_max = 4'095;

}

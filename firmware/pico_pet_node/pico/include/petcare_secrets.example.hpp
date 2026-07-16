#pragma once

#include <cstdint>

namespace petcare::secrets {

inline constexpr char wifi_ssid[] = "petcare-contract";
inline constexpr char wifi_password[] = "not-a-real-secret";
inline constexpr char mqtt_host[] = "192.0.2.1";
inline constexpr std::uint16_t mqtt_port = 18'883;
inline constexpr char mqtt_username[] = "petcare-contract";
inline constexpr char mqtt_password[] = "not-a-real-secret";

}

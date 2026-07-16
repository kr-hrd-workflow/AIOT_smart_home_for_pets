#pragma once

#include <cstdint>

namespace petcare::config {

#if defined(PETCARE_PROFILE_ENTRANCE)
inline constexpr char device_id[] = "entrance-01";
#elif defined(PETCARE_PROFILE_PETZONE)
inline constexpr char device_id[] = "petzone-01";
#else
#error "A PetCare Pico profile must be selected"
#endif

inline constexpr auto client_id = device_id;
inline constexpr std::uint32_t wifi_timeout_ms = 10'000;
inline constexpr std::uint32_t mqtt_timeout_ms = 10'000;

}

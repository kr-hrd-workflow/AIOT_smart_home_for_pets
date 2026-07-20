#pragma once

#include "pet_node.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace petcare {

enum class Bowl : std::uint8_t { food, water };
enum class FsrChannel : std::uint8_t { left, center, right };
enum class OutputKind : std::uint8_t { sensor, status };

struct SensorSource {
    void* context;
    bool (*read_sht31)(void*, double&, double&);
    bool (*read_presence)(void*, bool&, bool&);
    bool (*read_weight)(void*, Bowl, double&);
    bool (*read_fsr)(void*, FsrChannel, std::uint16_t&);
};

struct ScheduledOutput {
    OutputKind kind = OutputKind::sensor;
    std::uint32_t due_ms = 0;
    std::string_view sensor_type{};
    SensorValue value{};
    std::string_view unit{};
};

bool decode_sht31(const std::array<std::uint8_t, 6>& frame, double& temperature, double& humidity);
bool decode_ld2410c(const std::array<std::uint8_t, 23>& frame, bool& moving, bool& stationary);

class Ld2410cStream {
public:
    void push(std::uint8_t value);
    bool take_latest(bool& moving, bool& stationary);

private:
    std::array<std::uint8_t, 23> frame_{};
    std::size_t size_ = 0;
    std::size_t nested_header_size_ = 0;
    bool latest_ready_ = false;
    bool latest_moving_ = false;
    bool latest_stationary_ = false;
};

class SensorSchedule {
public:
    SensorSchedule(DeviceProfile profile, SensorSource source) : profile_(profile), source_(source) {}

    void start(std::uint32_t now_ms);
    bool next_due(std::uint32_t now_ms, ScheduledOutput& output);

private:
    void prepare(std::uint32_t due_ms, bool sht_due, bool fast_due, bool status_due);
    void append(std::uint32_t due_ms, std::string_view type, SensorValue value, std::string_view unit);

    DeviceProfile profile_;
    SensorSource source_;
    std::array<ScheduledOutput, 10> pending_{};
    std::size_t pending_size_ = 0;
    std::size_t pending_index_ = 0;
    std::uint32_t next_sht_ms_ = 0;
    std::uint32_t next_fast_ms_ = 0;
    std::uint32_t next_status_ms_ = 0;
    bool started_ = false;
};

#ifdef PICO_ON_DEVICE
class SensorHardware {
public:
    bool init();
    void poll();
    SensorSource source();

private:
    void drain_presence();
    static bool read_sht31(void* context, double& temperature, double& humidity);
    static bool read_presence(void* context, bool& moving, bool& stationary);
    static bool read_weight(void* context, Bowl bowl, double& grams);
    static bool read_fsr(void* context, FsrChannel channel, std::uint16_t& raw);
    static bool read_hx711(
        std::uint8_t dout_pin,
        std::uint8_t sck_pin,
        std::uint32_t timeout_us,
        const WeightCalibration& calibration,
        double& grams
    );

    Ld2410cStream ld2410c_stream_{};
};
#endif

}

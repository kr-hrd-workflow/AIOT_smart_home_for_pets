#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace petcare {

struct SensorSnapshot {
    std::string_view device_id;
    std::string_view zone;
    std::uint64_t timestamp_ms;
    float temperature_c;
    float humidity_pct;
    float light_lux;
    bool motion;
    bool door_open;
    float food_weight_g;
    float water_weight_g;
    float bed_weight_g;
};

struct CameraTrigger {
    bool active;
    std::string_view reason;
};

struct TelemetryMessage {
    std::string topic;
    std::string payload;
};

struct SensorReading {
    std::string_view device_id;
    std::string_view sensor_type;
    float value;
    std::string_view unit;
    float battery;
    int rssi;
    std::string_view timestamp;
};

struct DeviceStatus {
    std::string_view device_id;
    std::string_view status;
    std::string_view firmware_version;
    std::string_view ip;
    std::uint32_t uptime_sec;
    std::string_view timestamp;
};

struct BoundingBox {
    int x;
    int y;
    int w;
    int h;
};

struct CameraDetection {
    std::string_view camera_id;
    std::string_view detected_type;
    float confidence;
    BoundingBox bbox;
    std::string_view zone;
    std::string_view track_id;
    std::string_view timestamp;
};

struct RoiZone {
    std::string_view zone_id;
    int x1;
    int y1;
    int x2;
    int y2;
};

struct BehaviorEvent {
    std::string_view subject_type;
    std::string_view subject_id;
    std::string_view behavior_type;
    std::string_view zone_id;
    float confidence;
    std::uint32_t duration_sec;
    std::string_view message;
    std::string_view timestamp;
};

struct AnomalyEvent {
    std::string_view subject_type;
    std::string_view subject_id;
    std::string_view anomaly_type;
    std::string_view severity;
    float score;
    std::string_view message;
    std::string_view timestamp;
};

std::string telemetry_topic(std::string_view device_id);
std::string sensor_topic(std::string_view device_id, std::string_view sensor_type);
std::string status_topic(std::string_view device_id);
std::string camera_detection_topic(std::string_view camera_id);
std::string camera_behavior_topic(std::string_view camera_id);
std::string camera_anomaly_topic(std::string_view camera_id);
std::string camera_trigger_topic(std::string_view device_id);
CameraTrigger evaluate_camera_trigger(
    const SensorSnapshot& current,
    const SensorSnapshot& previous,
    float min_weight_delta_g = 5.0f
);
std::string serialize_telemetry(
    const SensorSnapshot& snapshot,
    const CameraTrigger& trigger
);
std::string serialize_camera_trigger(
    const SensorSnapshot& snapshot,
    const CameraTrigger& trigger
);
TelemetryMessage make_telemetry_message(
    const SensorSnapshot& snapshot,
    const CameraTrigger& trigger
);
TelemetryMessage make_camera_trigger_message(
    const SensorSnapshot& snapshot,
    const CameraTrigger& trigger
);
TelemetryMessage make_sensor_message(const SensorReading& reading);
TelemetryMessage make_status_message(const DeviceStatus& status);
TelemetryMessage make_detection_message(const CameraDetection& detection);
TelemetryMessage make_behavior_message(std::string_view camera_id, const BehaviorEvent& event);
TelemetryMessage make_anomaly_message(std::string_view camera_id, const AnomalyEvent& event);
AnomalyEvent make_no_meal_anomaly(
    std::string_view subject_type,
    std::string_view subject_id,
    std::string_view timestamp
);
AnomalyEvent make_fall_suspected_anomaly(
    std::string_view subject_type,
    std::string_view subject_id,
    std::string_view timestamp
);
bool detection_in_roi(const CameraDetection& detection, const RoiZone& zone);
BehaviorEvent infer_eating_behavior(
    const CameraDetection& detection,
    const RoiZone& food_zone,
    float previous_food_weight_g,
    float current_food_weight_g,
    std::string_view timestamp
);
AnomalyEvent infer_entrance_risk(
    const CameraDetection& detection,
    const RoiZone& entrance_zone,
    bool door_open,
    std::string_view timestamp
);

}

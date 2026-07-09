#include "pet_node.hpp"

#include <cstddef>
#include <cmath>
#include <cstdio>

namespace petcare {

namespace {

const char* bool_json(bool value) {
    return value ? "true" : "false";
}

bool changed_by(float current, float previous, float threshold) {
    return std::fabs(current - previous) >= threshold;
}

std::string json_buffer(const char* buffer, std::size_t capacity, int written) {
    if (written <= 0) {
        return "{}";
    }
    const auto length = static_cast<std::size_t>(written);
    return std::string(buffer, length < capacity ? length : capacity - 1);
}

std::string format(const char* fmt, const SensorSnapshot& snapshot, const CameraTrigger& trigger) {
    char buffer[768];
    const int written = std::snprintf(
        buffer,
        sizeof(buffer),
        fmt,
        static_cast<int>(snapshot.device_id.size()),
        snapshot.device_id.data(),
        static_cast<int>(snapshot.zone.size()),
        snapshot.zone.data(),
        static_cast<unsigned long long>(snapshot.timestamp_ms),
        snapshot.temperature_c,
        snapshot.humidity_pct,
        snapshot.light_lux,
        bool_json(snapshot.motion),
        bool_json(snapshot.door_open),
        snapshot.food_weight_g,
        snapshot.water_weight_g,
        snapshot.bed_weight_g,
        bool_json(trigger.active),
        static_cast<int>(trigger.reason.size()),
        trigger.reason.data()
    );

    return json_buffer(buffer, sizeof(buffer), written);
}

}

std::string telemetry_topic(std::string_view device_id) {
    return "home/pico/" + std::string(device_id) + "/telemetry";
}

std::string sensor_topic(std::string_view device_id, std::string_view sensor_type) {
    return "home/pico/" + std::string(device_id) + "/sensor/" + std::string(sensor_type);
}

std::string status_topic(std::string_view device_id) {
    return "home/pico/" + std::string(device_id) + "/status";
}

std::string camera_detection_topic(std::string_view camera_id) {
    return "home/camera/" + std::string(camera_id) + "/detection";
}

std::string camera_behavior_topic(std::string_view camera_id) {
    return "home/camera/" + std::string(camera_id) + "/behavior";
}

std::string camera_anomaly_topic(std::string_view camera_id) {
    return "home/camera/" + std::string(camera_id) + "/anomaly";
}

std::string camera_trigger_topic(std::string_view device_id) {
    return "home/pico/" + std::string(device_id) + "/camera_trigger";
}

CameraTrigger evaluate_camera_trigger(
    const SensorSnapshot& current,
    const SensorSnapshot& previous,
    float min_weight_delta_g
) {
    if (current.motion) {
        return {true, "motion"};
    }
    if (current.door_open && !previous.door_open) {
        return {true, "door_open"};
    }
    if (changed_by(current.food_weight_g, previous.food_weight_g, min_weight_delta_g)) {
        return {true, "food_weight_change"};
    }
    if (changed_by(current.water_weight_g, previous.water_weight_g, min_weight_delta_g)) {
        return {true, "water_weight_change"};
    }
    if (changed_by(current.bed_weight_g, previous.bed_weight_g, min_weight_delta_g)) {
        return {true, "bed_weight_change"};
    }
    return {false, "none"};
}

std::string serialize_telemetry(
    const SensorSnapshot& snapshot,
    const CameraTrigger& trigger
) {
    return format(
        "{\"device_id\":\"%.*s\",\"zone\":\"%.*s\",\"timestamp_ms\":%llu,"
        "\"temperature_c\":%.2f,\"humidity_pct\":%.2f,\"light_lux\":%.2f,"
        "\"motion\":%s,\"door_open\":%s,\"food_weight_g\":%.2f,"
        "\"water_weight_g\":%.2f,\"bed_weight_g\":%.2f,"
        "\"trigger_camera\":%s,\"reason\":\"%.*s\"}",
        snapshot,
        trigger
    );
}

std::string serialize_camera_trigger(
    const SensorSnapshot& snapshot,
    const CameraTrigger& trigger
) {
    return format(
        "{\"device_id\":\"%.*s\",\"zone\":\"%.*s\",\"timestamp_ms\":%llu,"
        "\"temperature_c\":%.2f,\"humidity_pct\":%.2f,\"light_lux\":%.2f,"
        "\"motion\":%s,\"door_open\":%s,\"food_weight_g\":%.2f,"
        "\"water_weight_g\":%.2f,\"bed_weight_g\":%.2f,"
        "\"trigger_camera\":%s,\"reason\":\"%.*s\"}",
        snapshot,
        trigger
    );
}

TelemetryMessage make_telemetry_message(
    const SensorSnapshot& snapshot,
    const CameraTrigger& trigger
) {
    return {
        telemetry_topic(snapshot.device_id),
        serialize_telemetry(snapshot, trigger),
    };
}

TelemetryMessage make_camera_trigger_message(
    const SensorSnapshot& snapshot,
    const CameraTrigger& trigger
) {
    return {
        camera_trigger_topic(snapshot.device_id),
        serialize_camera_trigger(snapshot, trigger),
    };
}

TelemetryMessage make_sensor_message(const SensorReading& reading) {
    char buffer[512];
    const int written = std::snprintf(
        buffer,
        sizeof(buffer),
        "{\"device_id\":\"%.*s\",\"sensor_type\":\"%.*s\",\"value\":%.2f,"
        "\"unit\":\"%.*s\",\"battery\":%.2f,\"rssi\":%d,\"timestamp\":\"%.*s\"}",
        static_cast<int>(reading.device_id.size()),
        reading.device_id.data(),
        static_cast<int>(reading.sensor_type.size()),
        reading.sensor_type.data(),
        reading.value,
        static_cast<int>(reading.unit.size()),
        reading.unit.data(),
        reading.battery,
        reading.rssi,
        static_cast<int>(reading.timestamp.size()),
        reading.timestamp.data()
    );
    return {
        sensor_topic(reading.device_id, reading.sensor_type),
        json_buffer(buffer, sizeof(buffer), written),
    };
}

TelemetryMessage make_status_message(const DeviceStatus& status) {
    char buffer[512];
    const int written = std::snprintf(
        buffer,
        sizeof(buffer),
        "{\"device_id\":\"%.*s\",\"status\":\"%.*s\",\"firmware_version\":\"%.*s\","
        "\"ip\":\"%.*s\",\"uptime_sec\":%u,\"timestamp\":\"%.*s\"}",
        static_cast<int>(status.device_id.size()),
        status.device_id.data(),
        static_cast<int>(status.status.size()),
        status.status.data(),
        static_cast<int>(status.firmware_version.size()),
        status.firmware_version.data(),
        static_cast<int>(status.ip.size()),
        status.ip.data(),
        static_cast<unsigned>(status.uptime_sec),
        static_cast<int>(status.timestamp.size()),
        status.timestamp.data()
    );
    return {
        status_topic(status.device_id),
        json_buffer(buffer, sizeof(buffer), written),
    };
}

TelemetryMessage make_detection_message(const CameraDetection& detection) {
    char buffer[768];
    const int written = std::snprintf(
        buffer,
        sizeof(buffer),
        "{\"camera_id\":\"%.*s\",\"detected_type\":\"%.*s\",\"confidence\":%.2f,"
        "\"bbox\":{\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d},"
        "\"zone\":\"%.*s\",\"track_id\":\"%.*s\",\"timestamp\":\"%.*s\"}",
        static_cast<int>(detection.camera_id.size()),
        detection.camera_id.data(),
        static_cast<int>(detection.detected_type.size()),
        detection.detected_type.data(),
        detection.confidence,
        detection.bbox.x,
        detection.bbox.y,
        detection.bbox.w,
        detection.bbox.h,
        static_cast<int>(detection.zone.size()),
        detection.zone.data(),
        static_cast<int>(detection.track_id.size()),
        detection.track_id.data(),
        static_cast<int>(detection.timestamp.size()),
        detection.timestamp.data()
    );
    return {
        camera_detection_topic(detection.camera_id),
        json_buffer(buffer, sizeof(buffer), written),
    };
}

TelemetryMessage make_behavior_message(std::string_view camera_id, const BehaviorEvent& event) {
    char buffer[768];
    const int written = std::snprintf(
        buffer,
        sizeof(buffer),
        "{\"type\":\"dashboard_update\",\"payload\":{\"subject_type\":\"%.*s\","
        "\"subject_id\":\"%.*s\",\"behavior_type\":\"%.*s\",\"zone_id\":\"%.*s\","
        "\"confidence\":%.2f,\"duration_sec\":%u,\"message\":\"%.*s\","
        "\"created_at\":\"%.*s\"}}",
        static_cast<int>(event.subject_type.size()),
        event.subject_type.data(),
        static_cast<int>(event.subject_id.size()),
        event.subject_id.data(),
        static_cast<int>(event.behavior_type.size()),
        event.behavior_type.data(),
        static_cast<int>(event.zone_id.size()),
        event.zone_id.data(),
        event.confidence,
        static_cast<unsigned>(event.duration_sec),
        static_cast<int>(event.message.size()),
        event.message.data(),
        static_cast<int>(event.timestamp.size()),
        event.timestamp.data()
    );
    return {
        camera_behavior_topic(camera_id),
        json_buffer(buffer, sizeof(buffer), written),
    };
}

TelemetryMessage make_anomaly_message(std::string_view camera_id, const AnomalyEvent& event) {
    char buffer[768];
    const int written = std::snprintf(
        buffer,
        sizeof(buffer),
        "{\"type\":\"anomaly_alert\",\"payload\":{\"severity\":\"%.*s\","
        "\"subject_type\":\"%.*s\",\"subject_id\":\"%.*s\",\"anomaly_type\":\"%.*s\","
        "\"score\":%.2f,\"message\":\"%.*s\",\"created_at\":\"%.*s\"}}",
        static_cast<int>(event.severity.size()),
        event.severity.data(),
        static_cast<int>(event.subject_type.size()),
        event.subject_type.data(),
        static_cast<int>(event.subject_id.size()),
        event.subject_id.data(),
        static_cast<int>(event.anomaly_type.size()),
        event.anomaly_type.data(),
        event.score,
        static_cast<int>(event.message.size()),
        event.message.data(),
        static_cast<int>(event.timestamp.size()),
        event.timestamp.data()
    );
    return {
        camera_anomaly_topic(camera_id),
        json_buffer(buffer, sizeof(buffer), written),
    };
}

AnomalyEvent make_no_meal_anomaly(
    std::string_view subject_type,
    std::string_view subject_id,
    std::string_view timestamp
) {
    return {
        subject_type,
        subject_id,
        "no_meal_12h",
        "warning",
        0.40f,
        "No eating event has been recorded for 12 hours",
        timestamp,
    };
}

AnomalyEvent make_fall_suspected_anomaly(
    std::string_view subject_type,
    std::string_view subject_id,
    std::string_view timestamp
) {
    return {
        subject_type,
        subject_id,
        "fall_suspected",
        "danger",
        0.80f,
        "Possible fall or immobility pattern detected",
        timestamp,
    };
}

bool detection_in_roi(const CameraDetection& detection, const RoiZone& zone) {
    const int center_x = detection.bbox.x + detection.bbox.w / 2;
    const int center_y = detection.bbox.y + detection.bbox.h / 2;
    return center_x >= zone.x1 && center_x <= zone.x2 && center_y >= zone.y1 && center_y <= zone.y2;
}

BehaviorEvent infer_eating_behavior(
    const CameraDetection& detection,
    const RoiZone& food_zone,
    float previous_food_weight_g,
    float current_food_weight_g,
    std::string_view timestamp
) {
    const bool is_pet = detection.detected_type == "dog" || detection.detected_type == "cat";
    const bool food_decreased = previous_food_weight_g - current_food_weight_g >= 5.0f;
    if (is_pet && detection_in_roi(detection, food_zone) && food_decreased) {
        return {
            detection.detected_type,
            detection.track_id,
            "eating",
            food_zone.zone_id,
            detection.confidence,
            30,
            "food_bowl ROI and food_weight decrease indicate eating",
            timestamp,
        };
    }
    return {
        detection.detected_type,
        detection.track_id,
        "detected",
        detection.zone,
        detection.confidence,
        0,
        "detection only",
        timestamp,
    };
}

AnomalyEvent infer_entrance_risk(
    const CameraDetection& detection,
    const RoiZone& entrance_zone,
    bool door_open,
    std::string_view timestamp
) {
    const bool is_pet = detection.detected_type == "dog" || detection.detected_type == "cat";
    if (door_open && is_pet && detection_in_roi(detection, entrance_zone)) {
        return {
            detection.detected_type,
            detection.track_id,
            "entrance_risk",
            "danger",
            0.95f,
            "Door is open and pet is in entrance ROI",
            timestamp,
        };
    }
    return {
        detection.detected_type,
        detection.track_id,
        "none",
        "warning",
        0.0f,
        "No entrance risk detected",
        timestamp,
    };
}

}

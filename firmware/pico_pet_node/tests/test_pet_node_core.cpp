#include "pet_node.hpp"

#include <cassert>
#include <string>

int main() {
    const petcare::SensorSnapshot previous{
        "pico_petzone_01",
        "petzone",
        1720000000000ULL,
        25.0f,
        48.0f,
        100.0f,
        false,
        false,
        150.0f,
        350.0f,
        4100.0f,
    };
    const petcare::SensorSnapshot current{
        "pico_petzone_01",
        "petzone",
        1720000005000ULL,
        25.1f,
        47.8f,
        112.0f,
        true,
        false,
        128.4f,
        342.9f,
        4100.0f,
    };

    const auto trigger = petcare::evaluate_camera_trigger(current, previous);
    assert(trigger.active);
    assert(trigger.reason == "motion");
    assert(petcare::telemetry_topic(current.device_id) == "home/pico/pico_petzone_01/telemetry");
    assert(petcare::camera_trigger_topic(current.device_id) == "home/pico/pico_petzone_01/camera_trigger");

    const auto message = petcare::make_telemetry_message(current, trigger);
    assert(message.topic == "home/pico/pico_petzone_01/telemetry");

    const auto telemetry = message.payload;
    assert(telemetry.find("\"device_id\":\"pico_petzone_01\"") != std::string::npos);
    assert(telemetry.find("\"zone\":\"petzone\"") != std::string::npos);
    assert(telemetry.find("\"temperature_c\":25.10") != std::string::npos);
    assert(telemetry.find("\"humidity_pct\":47.80") != std::string::npos);
    assert(telemetry.find("\"motion\":true") != std::string::npos);
    assert(telemetry.find("\"food_weight_g\":128.40") != std::string::npos);
    assert(telemetry.find("\"water_weight_g\":342.90") != std::string::npos);
    assert(telemetry.find("\"trigger_camera\":true") != std::string::npos);
    assert(telemetry.find("\"reason\":\"motion\"") != std::string::npos);

    const petcare::SensorSnapshot quiet = previous;
    const auto no_trigger = petcare::evaluate_camera_trigger(quiet, previous);
    assert(!no_trigger.active);
    assert(no_trigger.reason == "none");

    const petcare::SensorSnapshot door_open{
        "pico_entry_01",
        "entry",
        1720000006000ULL,
        24.0f,
        45.0f,
        80.0f,
        false,
        true,
        0.0f,
        0.0f,
        0.0f,
    };
    const auto door_trigger = petcare::evaluate_camera_trigger(door_open, previous);
    assert(door_trigger.active);
    assert(door_trigger.reason == "door_open");
    const auto trigger_message = petcare::make_camera_trigger_message(door_open, door_trigger);
    assert(trigger_message.topic == "home/pico/pico_entry_01/camera_trigger");
    assert(trigger_message.payload.find("\"reason\":\"door_open\"") != std::string::npos);

    const petcare::SensorReading food_weight{
        "pico_petzone_01",
        "food_weight",
        128.4f,
        "g",
        92.0f,
        -51,
        "2026-07-09T17:05:00+09:00",
    };
    const auto sensor = petcare::make_sensor_message(food_weight);
    assert(sensor.topic == "home/pico/pico_petzone_01/sensor/food_weight");
    assert(sensor.payload.find("\"sensor_type\":\"food_weight\"") != std::string::npos);
    assert(sensor.payload.find("\"battery\":92.00") != std::string::npos);
    assert(sensor.payload.find("\"rssi\":-51") != std::string::npos);

    const petcare::DeviceStatus status{
        "pico_petzone_01",
        "online",
        "0.1.0",
        "192.168.0.23",
        3600,
        "2026-07-09T17:05:00+09:00",
    };
    const auto status_message = petcare::make_status_message(status);
    assert(status_message.topic == "home/pico/pico_petzone_01/status");
    assert(status_message.payload.find("\"status\":\"online\"") != std::string::npos);
    assert(status_message.payload.find("\"firmware_version\":\"0.1.0\"") != std::string::npos);

    const petcare::CameraDetection detection{
        "pc_webcam_01",
        "dog",
        0.87f,
        {214, 352, 80, 130},
        "food_bowl",
        "dog_001",
        "2026-07-09T17:05:00+09:00",
    };
    const auto detection_message = petcare::make_detection_message(detection);
    assert(detection_message.topic == "home/camera/pc_webcam_01/detection");
    assert(detection_message.payload.find("\"camera_id\":\"pc_webcam_01\"") != std::string::npos);
    assert(detection_message.payload.find("\"detected_type\":\"dog\"") != std::string::npos);
    assert(detection_message.payload.find("\"bbox\":{\"x\":214,\"y\":352,\"w\":80,\"h\":130}") != std::string::npos);

    const petcare::RoiZone food_zone{"food_bowl", 100, 350, 300, 520};
    assert(petcare::detection_in_roi(detection, food_zone));
    const auto eating = petcare::infer_eating_behavior(
        detection,
        food_zone,
        150.0f,
        128.4f,
        "2026-07-09T17:05:00+09:00"
    );
    assert(eating.behavior_type == "eating");
    const auto behavior_message = petcare::make_behavior_message("pc_webcam_01", eating);
    assert(behavior_message.topic == "home/camera/pc_webcam_01/behavior");
    assert(behavior_message.payload.find("\"type\":\"dashboard_update\"") != std::string::npos);
    assert(behavior_message.payload.find("\"behavior_type\":\"eating\"") != std::string::npos);

    const petcare::RoiZone entrance_zone{"entrance", 0, 0, 250, 250};
    const petcare::CameraDetection entrance_detection{
        "pc_webcam_01",
        "dog",
        0.91f,
        {40, 40, 90, 110},
        "entrance",
        "dog_001",
        "2026-07-09T17:06:00+09:00",
    };
    const auto entrance_risk = petcare::infer_entrance_risk(
        entrance_detection,
        entrance_zone,
        true,
        "2026-07-09T17:06:00+09:00"
    );
    assert(entrance_risk.anomaly_type == "entrance_risk");
    assert(entrance_risk.severity == "danger");
    const auto anomaly_message = petcare::make_anomaly_message("pc_webcam_01", entrance_risk);
    assert(anomaly_message.topic == "home/camera/pc_webcam_01/anomaly");
    assert(anomaly_message.payload.find("\"type\":\"anomaly_alert\"") != std::string::npos);
    assert(anomaly_message.payload.find("\"severity\":\"danger\"") != std::string::npos);

    const auto no_risk = petcare::infer_entrance_risk(
        detection,
        entrance_zone,
        false,
        "2026-07-09T17:06:00+09:00"
    );
    assert(no_risk.anomaly_type == "none");

    const auto no_meal = petcare::make_no_meal_anomaly(
        "dog",
        "dog_001",
        "2026-07-09T17:06:00+09:00"
    );
    assert(no_meal.anomaly_type == "no_meal_12h");
    assert(no_meal.severity == "warning");
    assert(no_meal.message == "No eating event has been recorded for 12 hours");
    const auto no_meal_message = petcare::make_anomaly_message("pc_webcam_01", no_meal);
    assert(no_meal_message.payload.find("\"anomaly_type\":\"no_meal_12h\"") != std::string::npos);

    const auto fall = petcare::make_fall_suspected_anomaly(
        "person",
        "person_001",
        "2026-07-09T17:06:30+09:00"
    );
    assert(fall.anomaly_type == "fall_suspected");
    assert(fall.severity == "danger");
    assert(fall.message == "Possible fall or immobility pattern detected");
    const auto fall_message = petcare::make_anomaly_message("pc_webcam_01", fall);
    assert(fall_message.payload.find("\"anomaly_type\":\"fall_suspected\"") != std::string::npos);

    return 0;
}

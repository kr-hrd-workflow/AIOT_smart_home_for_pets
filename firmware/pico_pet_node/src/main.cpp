#include "pet_node.hpp"

#include <iostream>
#include <string_view>

namespace {

void print_usage(std::ostream& out) {
    out << "usage: pet_node_demo [--help]\n";
    out << "prints Pico sensor, webcam detection, behavior, anomaly, and camera trigger sample payloads\n";
}

}

int main(int argc, char* argv[]) {
    if (argc > 1) {
        const std::string_view option{argv[1]};
        if (option == "--help" || option == "-h") {
            print_usage(std::cout);
            return 0;
        }
        std::cerr << "unknown argument: " << option << '\n';
        print_usage(std::cerr);
        return 2;
    }

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
    const auto telemetry = petcare::make_telemetry_message(current, trigger);
    const petcare::SensorReading food_weight{
        "pico_petzone_01",
        "food_weight",
        128.4f,
        "g",
        92.0f,
        -51,
        "2026-07-09T17:05:00+09:00",
    };
    const petcare::DeviceStatus status{
        "pico_petzone_01",
        "online",
        "0.1.0",
        "192.168.0.23",
        3600,
        "2026-07-09T17:05:00+09:00",
    };
    const petcare::RoiZone food_zone{"food_bowl", 100, 350, 300, 520};
    const petcare::RoiZone entrance_zone{"entrance", 0, 0, 250, 250};
    const petcare::CameraDetection dog_detection{
        "pc_webcam_01",
        "dog",
        0.87f,
        {214, 352, 80, 130},
        "food_bowl",
        "dog_001",
        "2026-07-09T17:05:00+09:00",
    };
    const petcare::CameraDetection entrance_detection{
        "pc_webcam_01",
        "dog",
        0.91f,
        {40, 40, 90, 110},
        "entrance",
        "dog_001",
        "2026-07-09T17:06:00+09:00",
    };
    const auto sensor = petcare::make_sensor_message(food_weight);
    const auto device_status = petcare::make_status_message(status);
    const auto detection = petcare::make_detection_message(dog_detection);
    const auto eating = petcare::infer_eating_behavior(
        dog_detection,
        food_zone,
        150.0f,
        128.4f,
        "2026-07-09T17:05:00+09:00"
    );
    const auto behavior = petcare::make_behavior_message("pc_webcam_01", eating);
    const auto entrance_risk = petcare::infer_entrance_risk(
        entrance_detection,
        entrance_zone,
        true,
        "2026-07-09T17:06:00+09:00"
    );
    const auto anomaly = petcare::make_anomaly_message("pc_webcam_01", entrance_risk);
    const auto no_meal = petcare::make_anomaly_message(
        "pc_webcam_01",
        petcare::make_no_meal_anomaly("dog", "dog_001", "2026-07-09T17:07:00+09:00")
    );
    const auto fall = petcare::make_anomaly_message(
        "pc_webcam_01",
        petcare::make_fall_suspected_anomaly("person", "person_001", "2026-07-09T17:08:00+09:00")
    );

    std::cout << telemetry.topic << '\n';
    std::cout << telemetry.payload << '\n';
    std::cout << sensor.topic << '\n';
    std::cout << sensor.payload << '\n';
    std::cout << device_status.topic << '\n';
    std::cout << device_status.payload << '\n';
    std::cout << detection.topic << '\n';
    std::cout << detection.payload << '\n';
    std::cout << behavior.topic << '\n';
    std::cout << behavior.payload << '\n';
    std::cout << anomaly.topic << '\n';
    std::cout << anomaly.payload << '\n';
    std::cout << no_meal.topic << '\n';
    std::cout << no_meal.payload << '\n';
    std::cout << fall.topic << '\n';
    std::cout << fall.payload << '\n';

    if (trigger.active) {
        const auto camera_trigger = petcare::make_camera_trigger_message(current, trigger);
        std::cout << camera_trigger.topic << '\n';
        std::cout << camera_trigger.payload << '\n';
    }

    return 0;
}

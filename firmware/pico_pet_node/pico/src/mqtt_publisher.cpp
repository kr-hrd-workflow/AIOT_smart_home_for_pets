#include "mqtt_publisher.hpp"

#include "pico/cyw43_arch.h"

#include "lwip/ip_addr.h"

#include <algorithm>
#include <cstdio>
#include <cstring>

namespace petcare {

MqttPublisher::MqttPublisher() = default;

MqttPublisher::~MqttPublisher() { abort(); }

bool MqttPublisher::connect(
    const char* host,
    std::uint16_t port,
    const char* client_id,
    const char* username,
    const char* password,
    const TelemetryMessage* offline_lwt
) {
    if (!host || !client_id || !username || !password ||
        (offline_lwt != nullptr &&
         (offline_lwt->topic_size >= offline_lwt->topic.size() ||
          offline_lwt->payload_size >= offline_lwt->payload.size()))) {
        return false;
    }
    ip_addr_t address{};
    if (!ipaddr_aton(host, &address)) {
        return false;
    }
    cyw43_arch_lwip_begin();
    if (!client_) {
        client_ = mqtt_client_new();
    }
    cyw43_arch_lwip_end();
    if (!client_) {
        return false;
    }
    connected_.store(false);
    publish_failed_.store(false);
    publication_pending_.store(false);
    disconnect_after_publish_.store(false);
    mqtt_connect_client_info_t info{};
    info.client_id = client_id;
    info.client_user = username;
    info.client_pass = password;
    info.keep_alive = 30;
    if (offline_lwt != nullptr) {
        offline_lwt_ = *offline_lwt;
        info.will_topic = offline_lwt_.topic.data();
        info.will_msg = offline_lwt_.payload.data();
        info.will_qos = MqttContract::qos;
        info.will_retain = MqttContract::status_retain;
    }

    cyw43_arch_lwip_begin();
    const auto error = mqtt_client_connect(client_, &address, port, connection_changed, this, &info);
    cyw43_arch_lwip_end();
    return error == ERR_OK;
}

bool MqttPublisher::connected() const { return connected_.load(); }

bool MqttPublisher::publish_failed() const {
    return publish_failed_.load();
}

bool MqttPublisher::publication_pending() const {
    return publication_pending_.load();
}

bool MqttPublisher::publish_sensor(const TelemetryMessage& message) {
    return publish(message, MqttContract::sensor_retain, false);
}

bool MqttPublisher::publish_status(const TelemetryMessage& message) {
    return publish(message, MqttContract::status_retain, false);
}

bool MqttPublisher::request_time(std::string_view device_id) {
    if (!connected_.load() ||
        (device_id != "entrance-01" && device_id != "petzone-01")) {
        return false;
    }
    std::array<char, 64> request_topic{};
    std::array<char, 64> response_topic{};
    const auto request_size = std::snprintf(
        request_topic.data(), request_topic.size(),
        "home/pico/%.*s%s",
        static_cast<int>(device_id.size()), device_id.data(),
        MqttContract::time_request_suffix);
    const auto response_size = std::snprintf(
        response_topic.data(), response_topic.size(),
        "home/pico/%.*s%s",
        static_cast<int>(device_id.size()), device_id.data(),
        MqttContract::time_response_suffix);
    if (request_size <= 0 ||
        static_cast<std::size_t>(request_size) >= request_topic.size() ||
        response_size <= 0 ||
        static_cast<std::size_t>(response_size) >=
            response_topic.size()) {
        return false;
    }
    cyw43_arch_lwip_begin();
    time_response_topic_ = response_topic;
    mqtt_set_inpub_callback(client_, time_publish, time_data, this);
    const auto subscribe_error = mqtt_subscribe(
        client_, time_response_topic_.data(), MqttContract::qos,
        time_request_complete, this);
    const auto publish_error = subscribe_error == ERR_OK
        ? mqtt_publish(
              client_, request_topic.data(), "", 0,
              MqttContract::qos, false, time_request_complete, this)
        : subscribe_error;
    cyw43_arch_lwip_end();
    return subscribe_error == ERR_OK && publish_error == ERR_OK;
}

bool MqttPublisher::take_time(
    std::uint64_t monotonic_ms,
    UtcClock& clock
) {
    std::array<std::uint8_t, 13> payload{};
    std::size_t payload_size = 0;
    cyw43_arch_lwip_begin();
    if (time_ready_.exchange(false)) {
        payload = time_payload_;
        payload_size = time_payload_size_;
        std::fill(time_payload_.begin(), time_payload_.end(), 0);
        time_payload_size_ = 0;
    }
    cyw43_arch_lwip_end();
    if (payload_size == 0) {
        return false;
    }
    return clock.synchronize(
        payload.data(), payload_size, monotonic_ms);
}

bool MqttPublisher::graceful_disconnect(const TelemetryMessage& offline_status) {
    return publish(offline_status, MqttContract::status_retain, true);
}

bool MqttPublisher::publish(const TelemetryMessage& message, bool retain, bool disconnect_after) {
    if (!connected_.load() || !client_ || message.topic_size >= message.topic.size() ||
        message.payload_size >= message.payload.size()) {
        return false;
    }
    bool expected = false;
    if (!publication_pending_.compare_exchange_strong(expected, true)) {
        return false;
    }
    disconnect_after_publish_.store(disconnect_after);
    cyw43_arch_lwip_begin();
    const auto error = mqtt_publish(
        client_, message.topic.data(), message.payload.data(), static_cast<u16_t>(message.payload_size),
        MqttContract::qos, retain, publication_complete, this
    );
    cyw43_arch_lwip_end();
    if (error != ERR_OK) {
        publication_pending_.store(false);
        disconnect_after_publish_.store(false);
        publish_failed_.store(true);
        connected_.store(false);
        return false;
    }
    return true;
}

void MqttPublisher::abort() {
    if (client_) {
        cyw43_arch_lwip_begin();
        mqtt_disconnect(client_);
        mqtt_client_free(client_);
        cyw43_arch_lwip_end();
        client_ = nullptr;
    }
    connected_.store(false);
    publish_failed_.store(false);
    publication_pending_.store(false);
    disconnect_after_publish_.store(false);
    std::fill(time_payload_.begin(), time_payload_.end(), 0);
    std::fill(
        time_response_topic_.begin(),
        time_response_topic_.end(),
        '\0');
    time_payload_size_ = 0;
    accept_time_payload_ = false;
    time_ready_.store(false);
}

void MqttPublisher::connection_changed(
    mqtt_client_t*,
    void* argument,
    mqtt_connection_status_t status
) {
    auto* self = static_cast<MqttPublisher*>(argument);
    const bool accepted = status == MQTT_CONNECT_ACCEPTED;
    self->connected_.store(accepted);
    if (!accepted && self->publication_pending_.exchange(false)) {
        self->publish_failed_.store(true);
    }
}

void MqttPublisher::publication_complete(void* argument, err_t error) {
    auto* self = static_cast<MqttPublisher*>(argument);
    if (error != ERR_OK) {
        self->publish_failed_.store(true);
        self->connected_.store(false);
        self->disconnect_after_publish_.store(false);
        self->publication_pending_.store(false);
        return;
    }
    if (!self->disconnect_after_publish_.exchange(false)) {
        self->publication_pending_.store(false);
        return;
    }
    if (self->client_) {
        mqtt_disconnect(self->client_);
        self->connected_.store(false);
    }
    self->publication_pending_.store(false);
}

void MqttPublisher::time_request_complete(void* argument, err_t error) {
    if (error == ERR_OK) {
        return;
    }
    auto* self = static_cast<MqttPublisher*>(argument);
    self->publish_failed_.store(true);
    self->connected_.store(false);
}

void MqttPublisher::time_publish(
    void* argument,
    const char* topic,
    u32_t size
) {
    auto* self = static_cast<MqttPublisher*>(argument);
    self->time_payload_size_ = 0;
    self->accept_time_payload_ =
        topic != nullptr &&
        std::strcmp(topic, self->time_response_topic_.data()) == 0 &&
        size == self->time_payload_.size();
}

void MqttPublisher::time_data(
    void* argument,
    const u8_t* data,
    u16_t size,
    u8_t flags
) {
    auto* self = static_cast<MqttPublisher*>(argument);
    if (!self->accept_time_payload_ ||
        (data == nullptr && size != 0) ||
        size > self->time_payload_.size() - self->time_payload_size_) {
        self->accept_time_payload_ = false;
        return;
    }
    if (size != 0) {
        std::copy_n(
            data, size,
            self->time_payload_.begin() + self->time_payload_size_);
    }
    self->time_payload_size_ += size;
    if (flags & MQTT_DATA_FLAG_LAST) {
        if (self->time_payload_size_ == self->time_payload_.size()) {
            self->time_ready_.store(true);
        }
        self->accept_time_payload_ = false;
    }
}

}

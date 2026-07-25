#include "mqtt_publisher.hpp"

#include "pico/cyw43_arch.h"

#include "lwip/ip_addr.h"

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

}

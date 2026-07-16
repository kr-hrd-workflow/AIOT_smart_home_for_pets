#include "mqtt_publisher.hpp"
#include "petcare_config.hpp"
#include "petcare_secrets.hpp"

#include "lwip/apps/sntp.h"
#include "pico/cyw43_arch.h"
#include "pico/stdlib.h"
#include "pico/time.h"

#include <array>
#include <cstdint>
#include <sys/time.h>

extern "C" int settimeofday(const timeval* value, const struct timezone* zone);

namespace {

std::uint64_t monotonic_ms() {
    return to_ms_since_boot(get_absolute_time());
}

std::uint64_t wall_clock_ms() {
    timeval value{};
    gettimeofday(&value, nullptr);
    return static_cast<std::uint64_t>(value.tv_sec) * 1'000 + static_cast<std::uint64_t>(value.tv_usec / 1'000);
}

void start_sntp() {
    cyw43_arch_lwip_begin();
    sntp_stop();
    sntp_setoperatingmode(SNTP_OPMODE_POLL);
    sntp_setservername(0, petcare::UtcClock::primary_server);
    sntp_setservername(1, petcare::UtcClock::fallback_server);
    sntp_init();
    cyw43_arch_lwip_end();
}

bool make_status(
    petcare::UtcClock& clock,
    petcare::DeviceState state,
    std::uint64_t now_ms,
    petcare::TelemetryMessage& message,
    std::uint64_t& utc_ms
) {
    std::array<char, 25> observed_at{};
    return clock.timestamp(now_ms, observed_at, utc_ms) && petcare::serialize_status_message(
        {petcare::config::device_id, state, {observed_at.data(), 24}}, message
    );
}

}

extern "C" void petcare_sntp_set_system_time_us(std::uint32_t seconds, std::uint32_t microseconds) {
    timeval value{};
    value.tv_sec = seconds;
    value.tv_usec = microseconds;
    settimeofday(&value, nullptr);
}

int main() {
    stdio_init_all();
    if (cyw43_arch_init()) {
        return 1;
    }
    cyw43_arch_enable_sta_mode();

    petcare::ReconnectBackoff wifi_backoff;
    petcare::ReconnectBackoff mqtt_backoff;
    petcare::UtcClock clock;

    for (;;) {
        if (cyw43_arch_wifi_connect_timeout_ms(
                petcare::secrets::wifi_ssid,
                petcare::secrets::wifi_password,
                CYW43_AUTH_WPA2_AES_PSK,
                petcare::config::wifi_timeout_ms
            ) != 0) {
            sleep_ms(wifi_backoff.next_delay_seconds() * 1'000);
            continue;
        }
        wifi_backoff.reset();
        start_sntp();

        std::uint64_t next_sync_attempt_ms = 0;
        while (cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA) == CYW43_LINK_UP && !clock.valid()) {
            const auto now_ms = monotonic_ms();
            if (now_ms >= next_sync_attempt_ms) {
                clock.synchronize(wall_clock_ms(), now_ms);
                next_sync_attempt_ms = now_ms + petcare::UtcClock::retry_ms;
            }
            sleep_ms(50);
        }
        if (!clock.valid()) {
            sleep_ms(wifi_backoff.next_delay_seconds() * 1'000);
            continue;
        }

        petcare::MqttPublisher publisher;
        petcare::TelemetryMessage lwt{};
        std::uint64_t lwt_utc_ms = 0;
        std::array<char, 25> lwt_observed_at{};
        const auto connect_started_ms = monotonic_ms();
        if (!clock.timestamp(connect_started_ms, lwt_observed_at, lwt_utc_ms) ||
            !petcare::make_offline_lwt(petcare::config::device_id, {lwt_observed_at.data(), 24}, lwt) ||
            !publisher.connect(
                petcare::secrets::mqtt_host,
                petcare::secrets::mqtt_port,
                petcare::config::client_id,
                petcare::secrets::mqtt_username,
                petcare::secrets::mqtt_password,
                lwt
            )) {
            publisher.abort();
            sleep_ms(mqtt_backoff.next_delay_seconds() * 1'000);
            continue;
        }

        while (!publisher.connected() &&
               monotonic_ms() - connect_started_ms < petcare::config::mqtt_timeout_ms &&
               cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA) == CYW43_LINK_UP) {
            sleep_ms(10);
        }
        if (!publisher.connected()) {
            publisher.abort();
            sleep_ms(mqtt_backoff.next_delay_seconds() * 1'000);
            continue;
        }
        mqtt_backoff.reset();

        petcare::HeartbeatSchedule heartbeat;
        petcare::TelemetryMessage status{};
        std::uint64_t status_utc_ms = 0;
        auto now_ms = monotonic_ms();
        if (!make_status(clock, petcare::DeviceState::online, now_ms, status, status_utc_ms) ||
            !publisher.publish_status(status)) {
            publisher.abort();
            sleep_ms(mqtt_backoff.next_delay_seconds() * 1'000);
            continue;
        }
        clock.mark_published(status_utc_ms);
        heartbeat.connected(now_ms);
        auto next_resync_ms = now_ms + petcare::UtcClock::resync_ms;
        next_sync_attempt_ms = 0;

        while (publisher.connected() &&
               cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA) == CYW43_LINK_UP) {
            now_ms = monotonic_ms();
            if (now_ms >= next_resync_ms) {
                clock.synchronize(wall_clock_ms(), now_ms);
                next_resync_ms = now_ms + petcare::UtcClock::resync_ms;
                next_sync_attempt_ms = now_ms + petcare::UtcClock::retry_ms;
            } else if (!clock.valid() && now_ms >= next_sync_attempt_ms) {
                clock.synchronize(wall_clock_ms(), now_ms);
                next_sync_attempt_ms = now_ms + petcare::UtcClock::retry_ms;
            }
            if (heartbeat.due(now_ms)) {
                heartbeat.emitted(now_ms);
                if (make_status(clock, petcare::DeviceState::online, now_ms, status, status_utc_ms) &&
                    publisher.publish_status(status)) {
                    clock.mark_published(status_utc_ms);
                }
            }
            sleep_ms(10);
        }
        publisher.abort();
        sleep_ms(mqtt_backoff.next_delay_seconds() * 1'000);
    }
}

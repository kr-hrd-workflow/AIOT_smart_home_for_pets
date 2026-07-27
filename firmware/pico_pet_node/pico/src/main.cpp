#include "mqtt_publisher.hpp"
#include "petcare_config.hpp"
#include "provisioning_store.hpp"
#include "sensors.hpp"

#include "lwip/apps/sntp.h"
#include "hardware/watchdog.h"
#include "pico/cyw43_arch.h"
#include "pico/stdlib.h"
#include "pico/time.h"

#include <array>
#include <algorithm>
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

void wait_with_usb(
    petcare::ProvisioningConfig& runtime,
    petcare::RuntimeDiagnostics& diagnostics,
    std::uint32_t duration_ms) {
    while (duration_ms != 0) {
        watchdog_update();
        petcare::poll_usb_provisioning(
            petcare::config::device_id,
            runtime,
            diagnostics);
        const auto slice = std::min<std::uint32_t>(duration_ms, 10);
        sleep_ms(slice);
        duration_ms -= slice;
    }
}

bool connect_wifi_with_usb(
    petcare::ProvisioningConfig& runtime,
    petcare::RuntimeDiagnostics& diagnostics,
    bool& established_wifi_association) {
    established_wifi_association = false;
    const auto link_status =
        cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA);
    diagnostics.wifi_link_status = static_cast<std::int8_t>(link_status);
    if (link_status == CYW43_LINK_UP) {
        return true;
    }

    // A failed or just-powered CYW43 association can leave transient state
    // behind. Both production boards reproduced CYW43_LINK_FAIL on a direct
    // retry, while leave + a 2 s settle connected repeatedly. Reset the STA
    // association only when it is not already linked; wait_with_usb services
    // watchdog and USB provisioning during the settle period.
    cyw43_wifi_leave(&cyw43_state, CYW43_ITF_STA);
    wait_with_usb(runtime, diagnostics, 2'000);

    if (cyw43_arch_wifi_connect_async(
            runtime.ssid.data(),
            runtime.wifi_password.data(),
            CYW43_AUTH_WPA2_AES_PSK) != 0) {
        return false;
    }

    const auto deadline =
        make_timeout_time_ms(petcare::config::wifi_timeout_ms);
    while (!time_reached(deadline)) {
        const auto status =
            cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA);
        diagnostics.wifi_link_status =
            static_cast<std::int8_t>(status);
        if (status == CYW43_LINK_UP) {
            established_wifi_association = true;
            return true;
        }
        if (status == CYW43_LINK_NONET) {
            if (cyw43_arch_wifi_connect_async(
                    runtime.ssid.data(),
                    runtime.wifi_password.data(),
                    CYW43_AUTH_WPA2_AES_PSK) != 0) {
                return false;
            }
        } else if (status == CYW43_LINK_BADAUTH ||
                   status == CYW43_LINK_FAIL) {
            return false;
        }
        wait_with_usb(runtime, diagnostics, 10);
    }
    return false;
}

bool synchronize_clock_with_usb(
    petcare::UtcClock& clock,
    petcare::ProvisioningConfig& runtime,
    petcare::RuntimeDiagnostics& diagnostics) {
    const auto deadline =
        make_timeout_time_ms(petcare::config::sntp_timeout_ms);
    while (cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA) ==
               CYW43_LINK_UP &&
           !time_reached(deadline)) {
        const auto now_ms = monotonic_ms();
        if (clock.synchronize(wall_clock_ms(), now_ms)) {
            return true;
        }
        wait_with_usb(runtime, diagnostics, 100);
    }
    return false;
}

bool await_publication(
    petcare::MqttPublisher& publisher,
    petcare::ProvisioningConfig& runtime,
    petcare::RuntimeDiagnostics& diagnostics
) {
    const auto started_ms = monotonic_ms();
    while (publisher.publication_pending() &&
           publisher.connected() &&
           monotonic_ms() - started_ms < petcare::config::mqtt_timeout_ms &&
           cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA) ==
               CYW43_LINK_UP) {
        wait_with_usb(runtime, diagnostics, 10);
    }
    diagnostics.wifi_link_status = static_cast<std::int8_t>(
        cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA));
    return !publisher.publication_pending() &&
           publisher.connected() &&
           !publisher.publish_failed() &&
           diagnostics.wifi_link_status == CYW43_LINK_UP;
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
    const bool watchdog_reboot = watchdog_caused_reboot();
    watchdog_enable(8'000, true);

    petcare::ProvisioningConfig runtime{};
    petcare::RuntimeDiagnostics diagnostics{
        petcare::RuntimePhase::awaiting_provisioning,
        petcare::RuntimeError::none,
        0,
        watchdog_reboot,
    };
    while (!petcare::load_provisioning(runtime)) {
        watchdog_update();
        petcare::poll_usb_provisioning(
            petcare::config::device_id,
            runtime,
            diagnostics);
        sleep_ms(10);
    }

    if (cyw43_arch_init_with_country(CYW43_COUNTRY_SOUTH_KOREA)) {
        diagnostics.phase = petcare::RuntimePhase::backoff;
        diagnostics.error = petcare::RuntimeError::wifi;
        for (;;) {
            wait_with_usb(runtime, diagnostics, 1'000);
        }
    }
    cyw43_arch_enable_sta_mode();

    petcare::SensorHardware sensor_hardware;
    if (!sensor_hardware.init()) {
        diagnostics.phase = petcare::RuntimePhase::backoff;
        diagnostics.error = petcare::RuntimeError::sensor;
        for (;;) {
            wait_with_usb(runtime, diagnostics, 1'000);
        }
    }

    petcare::ReconnectBackoff wifi_backoff;
    petcare::ReconnectBackoff mqtt_backoff;
    petcare::UtcClock clock;

    for (;;) {
        diagnostics.phase = petcare::RuntimePhase::wifi_connecting;
        diagnostics.error = petcare::RuntimeError::none;
        bool established_wifi_association = false;
        if (!connect_wifi_with_usb(
                runtime, diagnostics, established_wifi_association)) {
            diagnostics.phase = petcare::RuntimePhase::backoff;
            diagnostics.error = petcare::RuntimeError::wifi;
            wait_with_usb(
                runtime,
                diagnostics,
                wifi_backoff.next_delay_seconds() * 1'000U);
            continue;
        }
        diagnostics.wifi_link_status = static_cast<std::int8_t>(
            cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA));
        wifi_backoff.reset();
        if (established_wifi_association) {
            start_sntp();
        }

        diagnostics.phase = petcare::RuntimePhase::time_syncing;
        diagnostics.error = petcare::RuntimeError::none;
        if (!synchronize_clock_with_usb(clock, runtime, diagnostics)) {
            diagnostics.error = petcare::RuntimeError::time_sync;
        }

        petcare::MqttPublisher publisher;
        petcare::TelemetryMessage lwt{};
        std::uint64_t lwt_utc_ms = 0;
        std::array<char, 25> lwt_observed_at{};
        const auto connect_started_ms = monotonic_ms();
        const bool lwt_ready =
            clock.valid() &&
            clock.timestamp(
                connect_started_ms, lwt_observed_at, lwt_utc_ms) &&
            petcare::make_offline_lwt(
                petcare::config::device_id,
                {lwt_observed_at.data(), 24},
                lwt);
        diagnostics.phase = petcare::RuntimePhase::mqtt_connecting;
        if ((clock.valid() && !lwt_ready) ||
            !publisher.connect(
                runtime.mqtt_host.data(),
                runtime.mqtt_port,
                petcare::config::client_id,
                runtime.mqtt_username.data(),
                runtime.mqtt_password.data(),
                lwt_ready ? &lwt : nullptr
            )) {
            publisher.abort();
            diagnostics.phase = petcare::RuntimePhase::backoff;
            diagnostics.error = petcare::RuntimeError::mqtt;
            wait_with_usb(
                runtime,
                diagnostics,
                mqtt_backoff.next_delay_seconds() * 1'000U);
            continue;
        }

        while (!publisher.connected() &&
               monotonic_ms() - connect_started_ms < petcare::config::mqtt_timeout_ms &&
               cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA) == CYW43_LINK_UP) {
            wait_with_usb(runtime, diagnostics, 10);
        }
        if (!publisher.connected()) {
            publisher.abort();
            diagnostics.phase = petcare::RuntimePhase::backoff;
            diagnostics.error = petcare::RuntimeError::mqtt;
            wait_with_usb(
                runtime,
                diagnostics,
                mqtt_backoff.next_delay_seconds() * 1'000U);
            continue;
        }
        mqtt_backoff.reset();

        if (!clock.valid()) {
            if (!publisher.request_time(petcare::config::device_id)) {
                publisher.abort();
                diagnostics.phase = petcare::RuntimePhase::backoff;
                diagnostics.error = petcare::RuntimeError::mqtt;
                wait_with_usb(
                    runtime,
                    diagnostics,
                    mqtt_backoff.next_delay_seconds() * 1'000U);
                continue;
            }
            diagnostics.phase = petcare::RuntimePhase::time_syncing;
            diagnostics.error = petcare::RuntimeError::time_sync;
            std::uint64_t next_sync_attempt_ms = 0;
            auto next_time_request_ms =
                monotonic_ms() + petcare::UtcClock::retry_ms;
            while (publisher.connected() &&
                   cyw43_tcpip_link_status(
                       &cyw43_state, CYW43_ITF_STA) == CYW43_LINK_UP &&
                   !clock.valid()) {
                const auto now_ms = monotonic_ms();
                publisher.take_time(now_ms, clock);
                if (!clock.valid() && now_ms >= next_time_request_ms) {
                    if (!publisher.request_time(
                            petcare::config::device_id)) {
                        break;
                    }
                    next_time_request_ms =
                        now_ms + petcare::UtcClock::retry_ms;
                }
                if (now_ms >= next_sync_attempt_ms) {
                    clock.synchronize(wall_clock_ms(), now_ms);
                    next_sync_attempt_ms =
                        now_ms + petcare::UtcClock::retry_ms;
                }
                sensor_hardware.poll();
                wait_with_usb(runtime, diagnostics, 10);
            }
            const bool clock_ready = clock.valid();
            const bool link_up = cyw43_tcpip_link_status(
                &cyw43_state, CYW43_ITF_STA) == CYW43_LINK_UP;
            publisher.abort();
            if (clock_ready) {
                diagnostics.phase =
                    petcare::RuntimePhase::mqtt_connecting;
                diagnostics.error = petcare::RuntimeError::none;
                continue;
            }
            diagnostics.phase = petcare::RuntimePhase::backoff;
            diagnostics.error = link_up
                ? petcare::RuntimeError::mqtt
                : petcare::RuntimeError::wifi;
            wait_with_usb(
                runtime,
                diagnostics,
                mqtt_backoff.next_delay_seconds() * 1'000U);
            continue;
        }

        petcare::SensorSchedule sensor_schedule{petcare::config::device_profile, sensor_hardware.source()};
        petcare::TelemetryMessage status{};
        std::uint64_t status_utc_ms = 0;
        auto now_ms = monotonic_ms();
        if (!make_status(clock, petcare::DeviceState::online, now_ms, status, status_utc_ms) ||
            !publisher.publish_status(status) ||
            !await_publication(publisher, runtime, diagnostics)) {
            publisher.abort();
            diagnostics.phase = petcare::RuntimePhase::backoff;
            diagnostics.error = petcare::RuntimeError::publish;
            wait_with_usb(
                runtime,
                diagnostics,
                mqtt_backoff.next_delay_seconds() * 1'000U);
            continue;
        }
        clock.mark_published(status_utc_ms);
        diagnostics.phase = petcare::RuntimePhase::online;
        diagnostics.error = petcare::RuntimeError::none;
        sensor_schedule.start(static_cast<std::uint32_t>(now_ms));
        auto next_resync_ms = now_ms + petcare::UtcClock::resync_ms;

        while (publisher.connected() &&
               cyw43_tcpip_link_status(&cyw43_state, CYW43_ITF_STA) == CYW43_LINK_UP) {
            now_ms = monotonic_ms();
            sensor_hardware.poll();
            if (now_ms >= next_resync_ms) {
                clock.synchronize(wall_clock_ms(), now_ms);
                next_resync_ms = now_ms + petcare::UtcClock::resync_ms;
            }
            petcare::ScheduledOutput scheduled{};
            while (sensor_schedule.next_due(static_cast<std::uint32_t>(now_ms), scheduled)) {
                const auto due_ms = now_ms - static_cast<std::uint32_t>(
                    static_cast<std::uint32_t>(now_ms) - scheduled.due_ms
                );
                std::array<char, 25> observed_at{};
                std::uint64_t utc_ms = 0;
                if (!clock.timestamp(due_ms, observed_at, utc_ms)) {
                    continue;
                }
                petcare::TelemetryMessage message{};
                const bool published = scheduled.kind == petcare::OutputKind::status
                    ? petcare::serialize_status_message(
                          {petcare::config::device_id, petcare::DeviceState::online, {observed_at.data(), 24}},
                          message
                      ) && publisher.publish_status(message)
                    : petcare::serialize_sensor_message(
                          {
                              petcare::config::device_id,
                              scheduled.sensor_type,
                              scheduled.value,
                              scheduled.unit,
                              {observed_at.data(), 24},
                          },
                          message
                      ) && publisher.publish_sensor(message);
                if (published &&
                    await_publication(publisher, runtime, diagnostics)) {
                    clock.mark_published(utc_ms);
                } else {
                    diagnostics.error =
                        petcare::RuntimeError::publish;
                    break;
                }
            }
            if (diagnostics.error != petcare::RuntimeError::publish) {
                diagnostics.error = sensor_schedule.sensor_read_failed()
                    ? petcare::RuntimeError::sensor
                    : petcare::RuntimeError::none;
            }
            if (publisher.publish_failed()) {
                diagnostics.error = petcare::RuntimeError::publish;
                break;
            }
            wait_with_usb(runtime, diagnostics, 10);
        }
        const bool publish_failed =
            diagnostics.error == petcare::RuntimeError::publish ||
            publisher.publish_failed();
        const auto link_status = cyw43_tcpip_link_status(
            &cyw43_state, CYW43_ITF_STA);
        const bool link_up = link_status == CYW43_LINK_UP;
        diagnostics.wifi_link_status =
            static_cast<std::int8_t>(link_status);
        publisher.abort();
        diagnostics.phase = petcare::RuntimePhase::backoff;
        diagnostics.error = publish_failed
            ? petcare::RuntimeError::publish
            : (link_up ? petcare::RuntimeError::mqtt
                       : petcare::RuntimeError::wifi);
        wait_with_usb(
            runtime,
            diagnostics,
            mqtt_backoff.next_delay_seconds() * 1'000U);
    }
}

#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif
void petcare_sntp_set_system_time_us(uint32_t seconds, uint32_t microseconds);
#ifdef __cplusplus
}
#endif

#define NO_SYS 1
#define LWIP_SOCKET 0
#define LWIP_NETCONN 0
#define LWIP_NETIF_STATUS_CALLBACK 1
#define LWIP_NETIF_LINK_CALLBACK 1
#define MEM_ALIGNMENT 4
#define MEM_SIZE 8000
#define MEMP_NUM_TCP_SEG 32
#define MEMP_NUM_ARP_QUEUE 10
#define PBUF_POOL_SIZE 24
#define LWIP_ARP 1
#define LWIP_ETHERNET 1
#define LWIP_ICMP 1
#define LWIP_RAW 1
#define LWIP_TCP 1
#define TCP_MSS 1460
#define TCP_WND (8 * TCP_MSS)
#define TCP_SND_BUF (8 * TCP_MSS)
#define TCP_SND_QUEUELEN ((4 * TCP_SND_BUF + TCP_MSS - 1) / TCP_MSS)
#define LWIP_DHCP 1
#define LWIP_DNS 1
#define LWIP_MQTT 1
#define LWIP_SNTP 1
#define SNTP_SERVER_DNS 1
#define SNTP_MAX_SERVERS 2
#define SNTP_CHECK_RESPONSE 2
#define SNTP_RETRY_TIMEOUT 15000
#define SNTP_RETRY_TIMEOUT_MAX 15000
#define SNTP_RETRY_TIMEOUT_EXP 0
#define SNTP_UPDATE_DELAY 21600000
#define SNTP_SET_SYSTEM_TIME_US(seconds, microseconds) petcare_sntp_set_system_time_us(seconds, microseconds)

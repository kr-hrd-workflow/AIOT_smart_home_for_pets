#pragma once

#include "provisioning.hpp"

#include <string_view>

namespace petcare {

bool load_provisioning(ProvisioningConfig& output);
ProvisioningError poll_usb_provisioning(
    std::string_view device_id,
    ProvisioningConfig& output);

}

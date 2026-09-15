#pragma once

#include <cstdint>

#include "as5047p_protocol.h"

namespace as5047p {

struct ReadResult {
    uint16_t response = 0U;
    bool error_report = false;
};

// AS5047P responses are pipelined by one SPI command.  When EF is present,
// reading ERRFL returns the diagnostic bits and clears the sensor's sticky
// error register.  The current sample remains invalid; the next IMU frame gets
// a fresh angle transaction.
template <typename Transaction>
ReadResult readAngleWithErrorRecovery(Transaction transaction) {
    transaction(makeReadCommand(kAngleUncompensatedAddress));
    const uint16_t angle_response = transaction(makeNopCommand());
    if (!hasError(angle_response)) {
        return {angle_response, false};
    }

    transaction(makeReadCommand(kErrorFlagsAddress));
    const uint16_t error_response = transaction(makeNopCommand());
    return {error_response, true};
}

}  // namespace as5047p

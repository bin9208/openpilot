#include <stdbool.h>
#include <string.h>

#include "fake_stm.h"
#include "can.h"

//int safety_tx_hook(CANPacket_t *to_send) { return 1; }

#include "faults.h"
#define safety_fwd_hook safety_fwd_hook_packet
#include "safety.h"
#undef safety_fwd_hook
#include "drivers/can_common.h"

int safety_fwd_hook(int bus_num, int addr) {
  CANPacket_t packet = {0};
  packet.extended = addr >= 0x800;
  packet.addr = addr;
  packet.bus = bus_num;
  packet.data_len_code = 8U;
  return safety_fwd_hook_packet(&packet);
}

// libsafety stuff
#include "safety_helpers.h"

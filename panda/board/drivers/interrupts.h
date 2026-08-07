#include "board/drivers/drivers.h"

void unused_interrupt_handler(void) {
  // Something is wrong if this handler is called!
  print("Unused interrupt handler called!\n");
  fault_occurred(FAULT_UNUSED_INTERRUPT_HANDLED);
}

interrupt interrupts[NUM_INTERRUPTS];

static bool check_interrupt_rate = false;

static uint32_t idle_time = 0U;
static uint32_t busy_time = 0U;
float interrupt_load = 0.0f;

// CAN IRQ rate faults are temporary. Keep the normal Panda rate limit intact, but
// allow a latched transient (for example during CAN/harness initialization) to
// recover only after both IRQ lines for that FDCAN core have remained below the
// limit for several complete one-second windows.
#define CAN_INTERRUPT_RECOVERY_WINDOWS 3U
static uint8_t can_interrupt_recovery_windows[PANDA_CAN_CNT] = {0U, 0U, 0U};

void handle_interrupt(IRQn_Type irq_type){
  static uint8_t interrupt_depth = 0U;
  static uint32_t last_time = 0U;
  ENTER_CRITICAL();
  if (interrupt_depth == 0U) {
    uint32_t time = microsecond_timer_get();
    idle_time += get_ts_elapsed(time, last_time);
    last_time = time;
  }
  interrupt_depth += 1U;
  EXIT_CRITICAL();

  interrupts[irq_type].call_counter++;
  interrupts[irq_type].handler();

  // Check that the interrupts don't fire too often
  if (check_interrupt_rate && (interrupts[irq_type].call_counter > interrupts[irq_type].max_call_rate)) {
    fault_occurred(interrupts[irq_type].call_rate_fault);
  }

  ENTER_CRITICAL();
  interrupt_depth -= 1U;
  if (interrupt_depth == 0U) {
    uint32_t time = microsecond_timer_get();
    busy_time += get_ts_elapsed(time, last_time);
    last_time = time;
  }
  EXIT_CRITICAL();
}

// Every second
void interrupt_timer_handler(void) {
  if (INTERRUPT_TIMER->SR != 0U) {
    for (uint16_t i = 0U; i < NUM_INTERRUPTS; i++) {
      // Log IRQ call rate faults
      if (check_interrupt_rate && (interrupts[i].call_counter > interrupts[i].max_call_rate)) {
        print("Interrupt 0x"); puth(i); print(" fired too often (0x"); puth(interrupts[i].call_counter); print("/s)!\n");
      }

      // Reset interrupt counters
      interrupts[i].call_rate = interrupts[i].call_counter;
      interrupts[i].call_counter = 0U;
    }

    if (check_interrupt_rate) {
      const IRQn_Type can_irq[PANDA_CAN_CNT][2] = {
        {FDCAN1_IT0_IRQn, FDCAN1_IT1_IRQn},
        {FDCAN2_IT0_IRQn, FDCAN2_IT1_IRQn},
        {FDCAN3_IT0_IRQn, FDCAN3_IT1_IRQn},
      };
      const uint32_t can_fault[PANDA_CAN_CNT] = {
        FAULT_INTERRUPT_RATE_CAN_1,
        FAULT_INTERRUPT_RATE_CAN_2,
        FAULT_INTERRUPT_RATE_CAN_3,
      };

      for (uint8_t can_number = 0U; can_number < PANDA_CAN_CNT; can_number++) {
        const bool irq0_ok = interrupts[can_irq[can_number][0]].call_rate <= interrupts[can_irq[can_number][0]].max_call_rate;
        const bool irq1_ok = interrupts[can_irq[can_number][1]].call_rate <= interrupts[can_irq[can_number][1]].max_call_rate;
        const bool fault_active = (faults & can_fault[can_number]) != 0U;

        if (fault_active && irq0_ok && irq1_ok) {
          if (can_interrupt_recovery_windows[can_number] < CAN_INTERRUPT_RECOVERY_WINDOWS) {
            can_interrupt_recovery_windows[can_number] += 1U;
          }
          if (can_interrupt_recovery_windows[can_number] >= CAN_INTERRUPT_RECOVERY_WINDOWS) {
            fault_recovered(can_fault[can_number]);
            can_interrupt_recovery_windows[can_number] = 0U;
          }
        } else {
          // Any new overload immediately cancels the recovery streak. The
          // original fault remains latched until a fresh stable window streak.
          can_interrupt_recovery_windows[can_number] = 0U;
        }
      }
    }

    // Calculate interrupt load
    // The bootstub does not have the FPU enabled, so can't do float operations.
#if !defined(BOOTSTUB)
    interrupt_load = ((busy_time + idle_time) > 0U) ? ((float) (((float) busy_time) / (busy_time + idle_time))) : 0.0f;
#endif
    idle_time = 0U;
    busy_time = 0U;
  }
  INTERRUPT_TIMER->SR = 0;
}

void init_interrupts(bool check_rate_limit){
  check_interrupt_rate = check_rate_limit;

  for(uint16_t i=0U; i<NUM_INTERRUPTS; i++){
    interrupts[i].handler = unused_interrupt_handler;
  }

  // Init interrupt timer for a 1s interval
  interrupt_timer_init();
}

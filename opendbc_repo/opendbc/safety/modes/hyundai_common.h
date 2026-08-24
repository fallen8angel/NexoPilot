#pragma once

#include "opendbc/safety/declarations.h"

extern uint16_t hyundai_canfd_crc_lut[256];
uint16_t hyundai_canfd_crc_lut[256];

static const uint8_t HYUNDAI_PREV_BUTTON_SAMPLES = 8;  // roughly 160 ms

extern const uint32_t HYUNDAI_STANDSTILL_THRSLD;
const uint32_t HYUNDAI_STANDSTILL_THRSLD = 12;  // 0.375 kph

enum {
  HYUNDAI_BTN_NONE = 0,
  HYUNDAI_BTN_RESUME = 1,
  HYUNDAI_BTN_SET = 2,
  HYUNDAI_BTN_CANCEL = 4,
};

// common state
extern bool hyundai_ev_gas_signal;
bool hyundai_ev_gas_signal = false;

extern bool hyundai_hybrid_gas_signal;
bool hyundai_hybrid_gas_signal = false;

extern bool hyundai_longitudinal;
bool hyundai_longitudinal = false;

extern bool hyundai_camera_scc;
bool hyundai_camera_scc = false;

extern bool hyundai_canfd_lka_steer_msg;
bool hyundai_canfd_lka_steer_msg = false;

extern bool hyundai_alt_limits;
bool hyundai_alt_limits = false;

extern bool hyundai_fcev_gas_signal;
bool hyundai_fcev_gas_signal = false;

extern bool hyundai_alt_limits_2;
bool hyundai_alt_limits_2 = false;

extern bool hyundai_nexo_dynamic_scc;
bool hyundai_nexo_dynamic_scc = false;

static uint8_t hyundai_last_button_interaction;  // button messages since the user pressed an enable button
static bool hyundai_fcev_med_wait = false;
static bool hyundai_main_button_prev = false;

void hyundai_common_init(uint16_t param) {
  const uint16_t HYUNDAI_PARAM_EV_GAS = 1;
  const uint16_t HYUNDAI_PARAM_HYBRID_GAS = 2;
  const uint16_t HYUNDAI_PARAM_CAMERA_SCC = 8;
  const uint16_t HYUNDAI_PARAM_CANFD_LKA_STEER_MSG = 16;
  const uint16_t HYUNDAI_PARAM_ALT_LIMITS = 64; // TODO: shift this down with the rest of the common flags
  const uint16_t HYUNDAI_PARAM_FCEV_GAS = 256;
  const uint16_t HYUNDAI_PARAM_ALT_LIMITS_2 = 512;
  const uint16_t HYUNDAI_PARAM_NEXO_DYNAMIC_SCC = 1024;

  hyundai_ev_gas_signal = GET_FLAG(param, HYUNDAI_PARAM_EV_GAS);
  hyundai_hybrid_gas_signal = !hyundai_ev_gas_signal && GET_FLAG(param, HYUNDAI_PARAM_HYBRID_GAS);
  hyundai_camera_scc = GET_FLAG(param, HYUNDAI_PARAM_CAMERA_SCC);
  hyundai_canfd_lka_steer_msg = GET_FLAG(param, HYUNDAI_PARAM_CANFD_LKA_STEER_MSG);
  hyundai_alt_limits = GET_FLAG(param, HYUNDAI_PARAM_ALT_LIMITS);
  hyundai_fcev_gas_signal = GET_FLAG(param, HYUNDAI_PARAM_FCEV_GAS);
  hyundai_alt_limits_2 = GET_FLAG(param, HYUNDAI_PARAM_ALT_LIMITS_2);
  hyundai_nexo_dynamic_scc = GET_FLAG(param, HYUNDAI_PARAM_NEXO_DYNAMIC_SCC);

  hyundai_last_button_interaction = HYUNDAI_PREV_BUTTON_SAMPLES;
  hyundai_fcev_med_wait = false;
  hyundai_main_button_prev = false;
  acc_main_on = false;

  // LONG must be explicitly requested through CarParams.safetyParam. Keep all
  // Hyundai longitudinal TX allowlists and payload limit checks active.
  const uint16_t HYUNDAI_PARAM_LONGITUDINAL = 4;
  hyundai_longitudinal = GET_FLAG(param, HYUNDAI_PARAM_LONGITUDINAL);
}

void hyundai_common_cruise_state_check(const bool cruise_engaged) {
  // some newer HKG models can re-enable after spamming cancel button,
  // so keep track of user button presses to deny engagement if no interaction

  // enter controls on rising edge of ACC and recent user button press, exit controls when ACC off
  if (!hyundai_longitudinal) {
    if (cruise_engaged && !cruise_engaged_prev && (hyundai_last_button_interaction < HYUNDAI_PREV_BUTTON_SAMPLES)) {
      controls_allowed = true;
    }

    if (!cruise_engaged) {
      controls_allowed = false;
    }
    cruise_engaged_prev = cruise_engaged;
  }
}

void hyundai_common_cruise_buttons_check(const int cruise_button, const bool main_button) {
  // First-generation NEXO uses the normal Hyundai LONG safety path (LONG|FCEV,
  // safetyParam 260). MED must therefore be keyed from LONG+FCEV itself and not
  // from the separate dynamic-SCC forwarding flag, which intentionally remains off.
  const bool nexo_med = hyundai_longitudinal && hyundai_fcev_gas_signal;
  const bool main_pressed = main_button && !hyundai_main_button_prev;

  if (main_pressed && nexo_med) {
    acc_main_on = !acc_main_on;
    controls_allowed = acc_main_on;
    hyundai_fcev_med_wait = acc_main_on;
  }
  hyundai_main_button_prev = main_button;

  if ((cruise_button == HYUNDAI_BTN_RESUME) || (cruise_button == HYUNDAI_BTN_SET) || (cruise_button == HYUNDAI_BTN_CANCEL) || main_button) {
    hyundai_last_button_interaction = 0U;
  } else {
    hyundai_last_button_interaction = SAFETY_MIN(hyundai_last_button_interaction + 1U, HYUNDAI_PREV_BUTTON_SAMPLES);
  }

  if (hyundai_longitudinal) {
    // XPlus-style NEXO MED_WAIT is steering-only. Generic pedal bookkeeping can
    // clear controls_allowed, so keep lateral authorization aligned with MODE.
    if (nexo_med && acc_main_on && hyundai_fcev_med_wait) {
      controls_allowed = true;
    }

    // Braking returns NEXO speed control to MED_WAIT while leaving lateral armed.
    if (nexo_med && acc_main_on && brake_pressed) {
      hyundai_fcev_med_wait = true;
      controls_allowed = true;
    }

    // enter controls on falling edge of resume or set
    bool set = (cruise_button != HYUNDAI_BTN_SET) && (cruise_button_prev == HYUNDAI_BTN_SET);
    bool res = (cruise_button != HYUNDAI_BTN_RESUME) && (cruise_button_prev == HYUNDAI_BTN_RESUME);
    if (set || res) {
      controls_allowed = true;
      if (nexo_med) {
        acc_main_on = true;
        hyundai_fcev_med_wait = false;
      }
    }

    // NEXO uses two-stage CANCEL: SPEED_CONTROL -> MED_WAIT -> OFF.
    // Treat a held CANCEL as one press. Without the edge check the 100 Hz CLU11
    // stream can consume both stages during a single physical button hold.
    if (cruise_button == HYUNDAI_BTN_CANCEL) {
      if (nexo_med && acc_main_on) {
        const bool cancel_pressed = cruise_button_prev != HYUNDAI_BTN_CANCEL;
        if (cancel_pressed) {
          if (hyundai_fcev_med_wait) {
            controls_allowed = false;
            acc_main_on = false;
            hyundai_fcev_med_wait = false;
          } else {
            controls_allowed = true;
            hyundai_fcev_med_wait = true;
          }
        }
      } else {
        controls_allowed = false;
      }
    }

    cruise_button_prev = cruise_button;
  }
}

uint32_t hyundai_common_canfd_compute_checksum(const CANPacket_t *msg) {
  int len = GET_LEN(msg);
  uint32_t address = msg->addr;

  uint16_t crc = 0;

  for (int i = 2; i < len; i++) {
    crc = (crc << 8U) ^ hyundai_canfd_crc_lut[(crc >> 8U) ^ msg->data[i]];
  }

  // Add address to crc
  crc = (crc << 8U) ^ hyundai_canfd_crc_lut[(crc >> 8U) ^ ((address >> 0U) & 0xFFU)];
  crc = (crc << 8U) ^ hyundai_canfd_crc_lut[(crc >> 8U) ^ ((address >> 8U) & 0xFFU)];

  if (len == 24) {
    crc ^= 0x819dU;
  } else if (len == 32) {
    crc ^= 0x9f5bU;
  } else {

  }

  return crc;
}

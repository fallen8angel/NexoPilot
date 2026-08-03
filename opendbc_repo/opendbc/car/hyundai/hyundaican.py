import copy

from opendbc.car.crc import CRC8J1850, mk_crc8_fun
from opendbc.car.hyundai.values import CAR, HyundaiFlags

hyundai_checksum = mk_crc8_fun(CRC8J1850, init_crc=0xFD, xor_out=0xDF)


def create_lkas11(packer, frame, CP, apply_torque, steer_req,
                  torque_fault, lkas11, sys_warning, sys_state, enabled,
                  left_lane, right_lane,
                  left_lane_depart, right_lane_depart):
  values = {s: lkas11[s] for s in [
    "CF_Lkas_LdwsActivemode",
    "CF_Lkas_LdwsSysState",
    "CF_Lkas_SysWarning",
    "CF_Lkas_LdwsLHWarning",
    "CF_Lkas_LdwsRHWarning",
    "CF_Lkas_HbaLamp",
    "CF_Lkas_FcwBasReq",
    "CF_Lkas_HbaSysState",
    "CF_Lkas_FcwOpt",
    "CF_Lkas_HbaOpt",
    "CF_Lkas_FcwSysState",
    "CF_Lkas_FcwCollisionWarning",
    "CF_Lkas_FusionState",
    "CF_Lkas_FcwOpt_USM",
    "CF_Lkas_LdwsOpt_USM",
  ]}
  values["CF_Lkas_LdwsSysState"] = sys_state
  values["CF_Lkas_SysWarning"] = 3 if sys_warning else 0
  values["CF_Lkas_LdwsLHWarning"] = left_lane_depart
  values["CF_Lkas_LdwsRHWarning"] = right_lane_depart
  values["CR_Lkas_StrToqReq"] = apply_torque
  values["CF_Lkas_ActToi"] = steer_req
  values["CF_Lkas_ToiFlt"] = torque_fault
  values["CF_Lkas_MsgCount"] = frame % 0x10

  if CP.carFingerprint in (CAR.HYUNDAI_SONATA, CAR.HYUNDAI_PALISADE, CAR.KIA_NIRO_EV, CAR.KIA_NIRO_HEV_2021, CAR.KIA_NIRO_PHEV_2022, CAR.HYUNDAI_SANTA_FE,
                           CAR.HYUNDAI_IONIQ_EV_2020, CAR.HYUNDAI_IONIQ_PHEV, CAR.KIA_SELTOS, CAR.HYUNDAI_ELANTRA_2021, CAR.GENESIS_G70_2020,
                           CAR.HYUNDAI_ELANTRA_HEV_2021, CAR.HYUNDAI_SONATA_HYBRID, CAR.HYUNDAI_KONA_EV, CAR.HYUNDAI_KONA_HEV, CAR.HYUNDAI_KONA_EV_2022,
                           CAR.HYUNDAI_SANTA_FE_2022, CAR.KIA_K5_2021, CAR.HYUNDAI_IONIQ_HEV_2022, CAR.HYUNDAI_SANTA_FE_HEV_2022,
                           CAR.HYUNDAI_SANTA_FE_PHEV_2022, CAR.KIA_STINGER_2022, CAR.KIA_K5_HEV_2020, CAR.KIA_CEED,
                           CAR.HYUNDAI_AZERA_6TH_GEN, CAR.HYUNDAI_AZERA_HEV_6TH_GEN, CAR.HYUNDAI_CUSTIN_1ST_GEN, CAR.HYUNDAI_KONA_2022,
                           CAR.HYUNDAI_NEXO_1ST_GEN):
    values["CF_Lkas_LdwsActivemode"] = int(left_lane) + (int(right_lane) << 1)
    values["CF_Lkas_LdwsOpt_USM"] = 2
    values["CF_Lkas_FcwOpt_USM"] = 2 if enabled else 1
    values["CF_Lkas_SysWarning"] = 4 if sys_warning else 0

  elif CP.carFingerprint in (CAR.KIA_OPTIMA_G4, CAR.KIA_OPTIMA_G4_FL):
    values["CF_Lkas_SysWarning"] = 4 if sys_warning else 0
    values["CF_Lkas_LdwsSysState"] = 3 if enabled else 1
    values["CF_Lkas_LdwsOpt_USM"] = 2
    values["CF_Lkas_LdwsActivemode"] = 0
    values["CF_Lkas_FcwOpt_USM"] = 0

  elif CP.carFingerprint == CAR.HYUNDAI_GENESIS:
    values["CF_Lkas_LdwsActivemode"] = 2

  dat = packer.make_can_msg("LKAS11", 0, values)[1]

  if CP.flags & HyundaiFlags.CHECKSUM_CRC8:
    dat = dat[:6] + dat[7:8]
    checksum = hyundai_checksum(dat)
  elif CP.flags & HyundaiFlags.CHECKSUM_6B:
    checksum = sum(dat[:6]) % 256
  else:
    checksum = (sum(dat[:6]) + dat[7]) % 256

  values["CF_Lkas_Chksum"] = checksum
  return packer.make_can_msg("LKAS11", 0, values)


def create_clu11(packer, frame, clu11, button, CP):
  values = {s: clu11[s] for s in [
    "CF_Clu_CruiseSwState",
    "CF_Clu_CruiseSwMain",
    "CF_Clu_SldMainSW",
    "CF_Clu_ParityBit1",
    "CF_Clu_VanzDecimal",
    "CF_Clu_Vanz",
    "CF_Clu_SPEED_UNIT",
    "CF_Clu_DetentOut",
    "CF_Clu_RheostatLevel",
    "CF_Clu_CluInfo",
    "CF_Clu_AmpInfo",
    "CF_Clu_AliveCnt1",
  ]}
  values["CF_Clu_CruiseSwState"] = button
  values["CF_Clu_AliveCnt1"] = frame % 0x10
  bus = 2 if CP.flags & HyundaiFlags.CAMERA_SCC else 0
  return packer.make_can_msg("CLU11", bus, values)


def create_lfahda_mfc(packer, enabled):
  values = {
    "LFA_Icon_State": 2 if enabled else 0,
  }
  return packer.make_can_msg("LFAHDA_MFC", 0, values)


def create_acc_commands(packer, enabled, accel, upper_jerk, idx, hud_control, set_speed, stopping, long_override, use_fca, CP,
                        cruise_available=True, vehicle_cruise_enabled=True, stock_scc11=None, stock_scc12=None, stock_scc14=None):
  commands = []
  is_nexo = CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN
  main_mode_acc = cruise_available
  acc_enabled = enabled if is_nexo else enabled and main_mode_acc
  scc14_enabled = acc_enabled

  lead_visible = hud_control.leadVisible
  lead_distance = max(0.0, min(float(hud_control.leadDistance), 204.7)) if lead_visible else 0.0
  lead_rel_speed = max(-170.0, min(float(hud_control.leadRelSpeed), 239.5)) if lead_visible else 0.0
  obj_gap = 0 if not lead_visible else 2 if lead_distance < 25 else 3 if lead_distance < 40 else 4 if lead_distance < 70 else 5

  scc11_values = copy.copy(stock_scc11) if is_nexo and stock_scc11 else {}
  scc11_values.update({
    "MainMode_ACC": main_mode_acc,
    "TauGapSet": hud_control.leadDistanceBars,
    "VSetDis": set_speed if acc_enabled else 0,
    "AliveCounterACC": idx % 0x10,
    "SCCInfoDisplay": 0,
    # Match Carrot: object validity follows the actual selected radar lead.
    "ObjValid": 1 if lead_visible else 0,
    "ACC_ObjStatus": 1 if lead_visible else 0,
    "ACC_ObjLatPos": 0,
    "ACC_ObjRelSpd": lead_rel_speed,
    "ACC_ObjDist": lead_distance,
    "DriverAlertDisplay": 0,
  })
  commands.append(packer.make_can_msg("SCC11", 0, scc11_values))

  scc12_values = copy.copy(stock_scc12) if is_nexo and stock_scc12 else {}
  scc12_values.update({
    "ACCMode": 2 if acc_enabled and long_override else 1 if acc_enabled else 0,
    "StopReq": 1 if acc_enabled and stopping else 0,
    "aReqRaw": accel if acc_enabled else 0.0,
    "aReqValue": accel if acc_enabled else 0.0,
    "ACCFailInfo": 0,
    "TakeOverReq": 0,
    "CR_VSM_ChkSum": 0,
    "CR_VSM_Alive": idx % 0xF,
  })

  if not use_fca:
    scc12_values["CF_VSM_ConfMode"] = 1
    scc12_values["AEB_Status"] = 1

  scc12_dat = packer.make_can_msg("SCC12", 0, scc12_values)[1]
  scc12_values["CR_VSM_ChkSum"] = 0x10 - sum(sum(divmod(i, 16)) for i in scc12_dat) % 0x10
  commands.append(packer.make_can_msg("SCC12", 0, scc12_values))

  scc14_values = copy.copy(stock_scc14) if is_nexo and stock_scc14 else {}
  scc14_values.update({
    "ComfortBandUpper": 0.0,
    "ComfortBandLower": 0.0,
    "JerkUpperLimit": upper_jerk,
    "JerkLowerLimit": 5.0,
    "ACCMode": 2 if scc14_enabled and long_override else 1 if scc14_enabled else 4,
    "ObjGap": obj_gap,
  })
  commands.append(packer.make_can_msg("SCC14", 0, scc14_values))

  if use_fca and not (CP.flags & HyundaiFlags.CAMERA_SCC):
    fca11_values = {
      "CR_FCA_Alive": idx % 0xF,
      "PAINT1_Status": 1,
      "FCA_DrvSetStatus": 1,
      # Match Carrot's non-camera SCC status message.
      "FCA_Status": 1,
    }
    fca11_dat = packer.make_can_msg("FCA11", 0, fca11_values)[1]
    fca11_values["CR_FCA_ChkSum"] = hyundai_checksum(fca11_dat[:7])
    commands.append(packer.make_can_msg("FCA11", 0, fca11_values))

  return commands


def create_acc_opt(packer, CP):
  commands = []

  scc13_values = {
    "SCCDrvModeRValue": 2,
    "SCC_Equip": 1,
    "Lead_Veh_Dep_Alert_USM": 2,
  }
  commands.append(packer.make_can_msg("SCC13", 0, scc13_values))

  if not (CP.flags & HyundaiFlags.CAMERA_SCC):
    fca12_values = {
      "FCA_DrvSetState": 2,
      # Match Carrot's standard FCA user-setting status.
      "FCA_USM": 1,
    }
    commands.append(packer.make_can_msg("FCA12", 0, fca12_values))

  return commands


def create_frt_radar_opt(packer):
  frt_radar11_values = {
    "CF_FCA_Equip_Front_Radar": 1,
  }
  return packer.make_can_msg("FRT_RADAR11", 0, frt_radar11_values)

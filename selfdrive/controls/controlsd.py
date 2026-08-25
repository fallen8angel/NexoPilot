#!/usr/bin/env python3
import math
from numbers import Number

from cereal import car, log
import cereal.messaging as messaging
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, DT_CTRL, Priority, Ratekeeper
from openpilot.common.swaglog import cloudlog

from opendbc.car.car_helpers import interfaces
from opendbc.car.vehicle_model import VehicleModel
from opendbc.car.hyundai.values import CAR
from openpilot.selfdrive.controls.lib.drive_helpers import clip_curvature
from openpilot.selfdrive.controls.lib.latcontrol import LatControl
from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle, STEER_ANGLE_SATURATION_THRESHOLD
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.lib.longcontrol import LongControl
from openpilot.selfdrive.modeld.modeld import LAT_SMOOTH_SECONDS
from openpilot.selfdrive.locationd.helpers import PoseCalibrator, Pose

State = log.SelfdriveState.OpenpilotState
LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection
ButtonType = car.CarState.ButtonEvent.Type
GearShifter = car.CarState.GearShifter

ACTUATOR_FIELDS = tuple(car.CarControl.Actuators.schema.fields.keys())


def _onroad_event_name(event) -> str:
  """Return a stable short event name for NEXO MED state preservation."""
  try:
    return str(event.name).rsplit(".", 1)[-1]
  except Exception:
    return ""


class Controls:
  def __init__(self) -> None:
    self.params = Params()
    cloudlog.info("controlsd is waiting for CarParams")
    self.CP = messaging.log_from_bytes(self.params.get("CarParams", block=True), car.CarParams)
    cloudlog.info("controlsd got CarParams")

    self.CI = interfaces[self.CP.carFingerprint](self.CP)

    self.sm = messaging.SubMaster(['liveDelay', 'liveParameters', 'liveTorqueParameters', 'modelV2', 'selfdriveState',
                                   'liveCalibration', 'livePose', 'longitudinalPlan', 'lateralManeuverPlan', 'carState', 'carOutput',
                                   'driverMonitoringState', 'onroadEvents', 'driverAssistance'], poll='selfdriveState')
    self.pm = messaging.PubMaster(['carControl', 'controlsState'])

    self.steer_limited_by_safety = False
    self.curvature = 0.0
    self.desired_curvature = 0.0

    # First-gen NEXO MED keeps the driver's main selection separate from actual
    # actuation. MODE explicitly selects MED, SET/RES adds speed control, brake
    # returns to steering-only, and a real soft/immediate disable latches
    # actuation off until MODE is pressed again after the fault clears.
    self.nexo_med = self.CP.carFingerprint == CAR.HYUNDAI_NEXO_1ST_GEN and self.CP.openpilotLongitudinalControl
    self.nexo_med_lateral = False
    self.nexo_med_speed = False
    self.nexo_med_rearm_required = False

    self.pose_calibrator = PoseCalibrator()
    self.calibrated_pose: Pose | None = None

    self.LoC = LongControl(self.CP)
    self.VM = VehicleModel(self.CP)
    self.LaC: LatControl
    if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
      self.LaC = LatControlAngle(self.CP, self.CI, DT_CTRL)
    elif self.CP.lateralTuning.which() == 'pid':
      self.LaC = LatControlPID(self.CP, self.CI, DT_CTRL)
    elif self.CP.lateralTuning.which() == 'torque':
      self.LaC = LatControlTorque(self.CP, self.CI, DT_CTRL)

  def update(self):
    self.sm.update(15)
    if self.sm.updated["liveCalibration"]:
      self.pose_calibrator.feed_live_calib(self.sm['liveCalibration'])
    if self.sm.updated["livePose"]:
      device_pose = Pose.from_live_pose(self.sm['livePose'])
      self.calibrated_pose = self.pose_calibrator.build_calibrated_pose(device_pose)

  def state_control(self):
    CS = self.sm['carState']

    # Update VehicleModel
    lp = self.sm['liveParameters']
    x = max(lp.stiffnessFactor, 0.1)
    sr = max(lp.steerRatio, 0.1)
    self.VM.update_params(x, sr)

    steer_angle_without_offset = math.radians(CS.steeringAngleDeg - lp.angleOffsetDeg)
    self.curvature = -self.VM.calc_curvature(steer_angle_without_offset, CS.vEgo, lp.roll)

    # Update Torque Params
    if self.CP.lateralTuning.which() == 'torque':
      torque_params = self.sm['liveTorqueParameters']
      if self.sm.all_checks(['liveTorqueParameters']) and torque_params.useParams:
        self.LaC.update_live_torque_params(torque_params.latAccelFactorFiltered, torque_params.latAccelOffsetFiltered,
                                           torque_params.frictionCoefficientFiltered)

    long_plan = self.sm['longitudinalPlan']
    model_v2 = self.sm['modelV2']
    selfdrive_enabled = self.sm['selfdriveState'].enabled
    driving_gear = CS.gearShifter in (GearShifter.drive, GearShifter.low)
    disable_events = [e for e in self.sm['onroadEvents'] if e.immediateDisable or e.softDisable]

    if self.nexo_med:
      main_pressed = any(b.type == ButtonType.mainCruise and b.pressed for b in CS.buttonEvents)
      speed_pressed = any(b.type in (ButtonType.accelCruise, ButtonType.decelCruise, ButtonType.resumeCruise) and b.pressed
                          for b in CS.buttonEvents)
      cancel_pressed = any(b.type == ButtonType.cancel and b.pressed for b in CS.buttonEvents)

      if main_pressed:
        # After a real disable, MODE is an explicit re-arm acknowledgement.
        # It never bypasses an active disable because the latch is set again
        # below while the event remains present.
        if self.nexo_med_lateral and self.nexo_med_rearm_required:
          self.nexo_med_rearm_required = False
        else:
          self.nexo_med_lateral = not self.nexo_med_lateral
          self.nexo_med_rearm_required = False
          if not self.nexo_med_lateral:
            self.nexo_med_speed = False

      # Never accept a speed-control request outside a forward driving gear,
      # while controlsd still has a disable event, or before the driver re-arms
      # MED after a real disable.
      if speed_pressed and driving_gear and not disable_events and not self.nexo_med_rearm_required:
        self.nexo_med_lateral = True
        self.nexo_med_speed = True

      # P/N/R is a temporary MED_WAIT condition, not a reason to forget the
      # driver's MED/main selection. Drop speed control only and keep lateral
      # selection stored so D can be used again without rebooting or cycling MODE.
      if not driving_gear:
        self.nexo_med_speed = False

      # Braking is the XPlus MED transition from speed control back to steering-only.
      if CS.brakePressed and (self.nexo_med_speed or selfdrive_enabled):
        self.nexo_med_lateral = True
        self.nexo_med_speed = False

      # Two-stage CANCEL: SPEED -> MED, then MED -> OFF.
      if cancel_pressed:
        if self.nexo_med_speed:
          self.nexo_med_speed = False
          self.nexo_med_lateral = True
        elif self.nexo_med_lateral:
          self.nexo_med_lateral = False
          self.nexo_med_rearm_required = False

      # Preserve the driver's MED main selection across real system faults, but
      # drop speed control and require an explicit MODE re-arm after the fault
      # clears. The active fault still blocks every actuator below.
      non_gear_disable = [e for e in disable_events if _onroad_event_name(e) != "wrongGear"]
      if non_gear_disable:
        self.nexo_med_speed = False
        if self.nexo_med_lateral:
          self.nexo_med_rearm_required = True

    # A remembered MED selection must never actuate in P/N/R, while any disable
    # event is active, or while the post-fault re-arm latch is set.
    med_actuation_allowed = (self.nexo_med and self.nexo_med_lateral and not self.nexo_med_rearm_required and
                             driving_gear and not disable_events)

    CC = car.CarControl.new_message()
    # Keep CarControl enabled while MED owns lateral in a valid driving state.
    # Longitudinal has its own independent gate below.
    CC.enabled = selfdrive_enabled or med_actuation_allowed

    # Check which actuators can be enabled
    standstill = abs(CS.vEgo) <= max(self.CP.minSteerSpeed, 0.3) or CS.standstill
    lateral_requested = self.sm['selfdriveState'].active or med_actuation_allowed
    CC.latActive = lateral_requested and not CS.steerFaultTemporary and not CS.steerFaultPermanent and \
                   (not standstill or self.CP.steerAtStandstill)

    longitudinal_requested = selfdrive_enabled
    if self.nexo_med:
      longitudinal_requested = longitudinal_requested and self.nexo_med_speed and driving_gear and not disable_events
    CC.longActive = longitudinal_requested and not any(e.overrideLongitudinal for e in self.sm['onroadEvents']) and self.CP.openpilotLongitudinalControl

    actuators = CC.actuators
    actuators.longControlState = self.LoC.long_control_state

    # Enable blinkers while lane changing
    if model_v2.meta.laneChangeState != LaneChangeState.off:
      CC.leftBlinker = model_v2.meta.laneChangeDirection == LaneChangeDirection.left
      CC.rightBlinker = model_v2.meta.laneChangeDirection == LaneChangeDirection.right

    if not CC.latActive:
      self.LaC.reset()
    if not CC.longActive:
      self.LoC.reset()

    # accel PID loop
    pid_accel_limits = self.CI.get_pid_accel_limits(self.CP, CS.vEgo, CS.vCruise * CV.KPH_TO_MS)
    actuators.accel = float(self.LoC.update(CC.longActive, CS, long_plan.aTarget, long_plan.shouldStop, pid_accel_limits))

    # Steering PID loop and lateral MPC
    # Reset desired curvature to current to avoid violating the limits on engage
    if self.sm.valid['lateralManeuverPlan']:
      new_desired_curvature = self.sm['lateralManeuverPlan'].desiredCurvature if CC.latActive else self.curvature
    else:
      new_desired_curvature = model_v2.action.desiredCurvature if CC.latActive else self.curvature
    self.desired_curvature, curvature_limited = clip_curvature(CS.vEgo, self.desired_curvature, new_desired_curvature, lp.roll)
    lat_delay = self.sm["liveDelay"].lateralDelay + LAT_SMOOTH_SECONDS

    actuators.curvature = self.desired_curvature
    steer, steeringAngleDeg, lac_log = self.LaC.update(CC.latActive, CS, self.VM, lp,
                                                       self.steer_limited_by_safety, self.desired_curvature,
                                                       curvature_limited, lat_delay)
    actuators.torque = float(steer)
    actuators.steeringAngleDeg = float(steeringAngleDeg)
    # Ensure no NaNs/Infs
    for p in ACTUATOR_FIELDS:
      attr = getattr(actuators, p)
      if not isinstance(attr, Number):
        continue

      if not math.isfinite(attr):
        cloudlog.error(f"actuators.{p} not finite {actuators.to_dict()}")
        setattr(actuators, p, 0.0)

    return CC, lac_log

  def publish(self, CC, lac_log):
    CS = self.sm['carState']

    # Orientation and angle rates can be useful for carcontroller
    # Only calibrated (car) frame is relevant for the carcontroller
    CC.currentCurvature = self.curvature
    if self.calibrated_pose is not None:
      CC.orientationNED = self.calibrated_pose.orientation.xyz.tolist()
      CC.angularVelocity = self.calibrated_pose.angular_velocity.xyz.tolist()

    CC.cruiseControl.override = CC.enabled and not CC.longActive and self.CP.openpilotLongitudinalControl
    CC.cruiseControl.cancel = CS.cruiseState.enabled and (not CC.enabled or not self.CP.pcmCruise)
    CC.cruiseControl.resume = CC.enabled and CS.cruiseState.standstill and not self.sm['longitudinalPlan'].shouldStop

    hudControl = CC.hudControl
    hudControl.setSpeed = float(CS.vCruiseCluster * CV.KPH_TO_MS)
    hudControl.speedVisible = CC.longActive if self.nexo_med else CC.enabled
    # For NEXO this is the MED main-selection indicator, not an actuator gate.
    # carcontroller/hyundaican use it only for MainMode_ACC; CC.latActive and
    # CC.longActive remain the sole actuation gates.
    hudControl.lanesVisible = self.nexo_med_lateral if self.nexo_med else CC.enabled
    hudControl.leadVisible = self.sm['longitudinalPlan'].hasLead
    hudControl.leadDistance = self.sm['longitudinalPlan'].leadDistance
    hudControl.leadRelSpeed = self.sm['longitudinalPlan'].leadRelSpeed
    hudControl.leadDistanceBars = self.sm['selfdriveState'].personality.raw + 1
    hudControl.visualAlert = self.sm['selfdriveState'].alertHudVisual

    hudControl.rightLaneVisible = True
    hudControl.leftLaneVisible = True
    if self.sm.valid['driverAssistance']:
      hudControl.leftLaneDepart = self.sm['driverAssistance'].leftLaneDeparture
      hudControl.rightLaneDepart = self.sm['driverAssistance'].rightLaneDeparture

    if CC.latActive:
      CO = self.sm['carOutput']
      if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
        self.steer_limited_by_safety = abs(CC.actuators.steeringAngleDeg - CO.actuatorsOutput.steeringAngleDeg) > \
                                              STEER_ANGLE_SATURATION_THRESHOLD
      else:
        self.steer_limited_by_safety = abs(CC.actuators.torque - CO.actuatorsOutput.torque) > 1e-2

    # TODO: both controlsState and carControl valids should be set by
    #       sm.all_checks(), but this creates a circular dependency

    # controlsState
    dat = messaging.new_message('controlsState')
    dat.valid = CS.canValid
    cs = dat.controlsState

    cs.curvature = self.curvature
    cs.longitudinalPlanMonoTime = self.sm.logMonoTime['longitudinalPlan']
    cs.lateralPlanMonoTime = self.sm.logMonoTime['modelV2']
    cs.desiredCurvature = self.desired_curvature
    cs.longControlState = self.LoC.long_control_state
    cs.upAccelCmd = float(self.LoC.pid.p)
    cs.uiAccelCmd = float(self.LoC.pid.i)
    cs.ufAccelCmd = float(self.LoC.pid.f)
    cs.forceDecel = bool((self.sm['driverMonitoringState'].alertLevel == log.DriverMonitoringState.AlertLevel.three) or
                         (self.sm['selfdriveState'].state == State.softDisabling))

    lat_tuning = self.CP.lateralTuning.which()
    if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
      cs.lateralControlState.angleState = lac_log
    elif lat_tuning == 'pid':
      cs.lateralControlState.pidState = lac_log
    elif lat_tuning == 'torque':
      cs.lateralControlState.torqueState = lac_log

    self.pm.send('controlsState', dat)

    # carControl
    cc_send = messaging.new_message('carControl')
    cc_send.valid = CS.canValid
    cc_send.carControl = CC
    self.pm.send('carControl', cc_send)

  def run(self):
    rk = Ratekeeper(100, print_delay_threshold=None)
    while True:
      self.update()
      CC, lac_log = self.state_control()
      self.publish(CC, lac_log)
      rk.monitor_time()


def main():
  config_realtime_process(4, Priority.CTRL_HIGH)
  controls = Controls()
  controls.run()


if __name__ == "__main__":
  main()

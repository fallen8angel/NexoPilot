# NexoPilot NEXO development baseline

This document is the working baseline for Hyundai NEXO-specific development in this repository. It consolidates confirmed observations, user requirements, comparison findings, and test priorities gathered during NEXO development. Any future NEXO-specific change should be checked against this baseline before it is merged.

## 1. Vehicle baseline

- Target vehicle: Hyundai NEXO FCEV
- Device: comma 4
- Working branch: `NEXO`
- Reference vehicle parameters currently used for NEXO work:
  - mass: 1885 kg
  - wheelbase: 2.79 m
  - steer ratio: 14.19
  - tire stiffness factor: 0.385
  - flags: `FCEV | MANDO_RADAR`
- Do not blindly inherit generic Hyundai/Kia assumptions when NEXO logs show different behavior.

## 2. Reference implementations

Use these projects only as references and port changes selectively:

- NEXOdriveXPlus: preferred reference for NEXO cluster-warning suppression, MED behavior, UI behavior, NEXO-specific CAN handling, and stable vehicle recognition.
- openpilot_Carrot / carrot-wip: preferred reference for radar tracks, longitudinal control, experimental mode, navigation-related features, and recent upstream functionality.
- NEXOdriveAI / NEXOdriveS: secondary references for NEXO longitudinal behavior, radar, auto-resume, and other NEXO-specific implementations.

Never merge a reference commit solely because it is newer. Confirm that the change does not break NEXO-specific behavior first.

## 3. Radar and longitudinal control

Primary goal:

- NEXO radar tracks must be available and stable.
- openpilot longitudinal control must work without introducing cluster warnings or breaking stock cruise fallback.
- Preserve normal cruise operation when openpilot longitudinal control is not active.
- Avoid button-lock or cruise-button restrictions that are not required for safety.
- Verify SCC messages, radar tracks, longitudinal state, and stock/openpilot ownership from diagnostic logs after every major change.

Known historical failure modes that must remain regression tests:

- `openpilotLong` unexpectedly false.
- radar tracks present but not consumed correctly.
- Panda fault or unknown vehicle variant.
- stock SCC traffic still present when openpilot control expects ownership.
- cluster warning lights after longitudinal or CAN changes.

## 4. Cluster warning handling

Target behavior is the XPlus level of NEXO compatibility: enabling NexoPilot features should not unnecessarily trigger OEM cluster warnings.

Rules:

- Do not suppress legitimate safety faults just to hide a warning.
- Prefer fixing the CAN ownership/state mismatch that causes a warning rather than masking UI symptoms.
- Treat cluster-warning regressions as blocking issues for NEXO-specific merges.
- Compare message timing, counters, checksums, cruise state, LKAS/SCC state, and driver-assistance state with known-good NEXO implementations.

## 5. MED mode baseline

Desired MED behavior:

- MED mode may enter a prepared/standby state before cruise engagement.
- The driver must still use the intended cruise SET/RES action to engage control when required.
- On `R` gear entry, MED active control must stop immediately.
- While in `R`, driver-monitoring distraction accumulation related to MED use must not continue unnecessarily.
- On return to `D`, restore standby only. Do not silently re-engage driving control.
- Require a fresh SET/RES action before re-entry after reversing.
- Never bypass an already-triggered driver-monitoring lockout.
- MED behavior must not create warning chimes or cluster errors during reverse/drive transitions.

## 6. Driver monitoring and UI

- Reverse-related behavior must not unnecessarily show or accumulate MED driver-monitoring state.
- Keep the steering-wheel icon in its established position.
- Place the driver-monitoring indicator adjacent to the steering-wheel area rather than overlapping or displacing the steering icon.
- UI changes must remain readable on-device and should not alter safety-state meaning.

## 7. Parking sensor baseline

The vehicle has a separate parking-sensor activation module. Current observed activation assumptions:

- sensor system can become active around 12–13 km/h or below when enabled by the module.
- it may deactivate around approximately 20 km/h.

Development goals:

- capture all available parking-sensor CAN signals in diagnostics.
- determine the total number of independently distinguishable sensor channels.
- determine whether front-left, front-center, front-right, rear-left, rear-center, rear-right, and side/corner information can be distinguished.
- capture raw value, decoded distance/strength level, validity, activation state, gear, and vehicle speed together.
- diagnostics should show whether every expected channel was observed during a test drive.

Potential uses after signal mapping is verified:

- low-speed obstacle visualization.
- low-speed proximity warnings.
- parking-assist status display.
- additional situational information for a future low-speed joystick/remote-control concept.

Parking sensors must not be treated as sufficient input for autonomous obstacle avoidance until range, update rate, field of view, failure behavior, and CAN semantics are verified on the actual NEXO.

## 8. vNAVI baseline

Desired vNAVI behavior:

- display `vNAVI` when the stock navigation system is actively providing a recognized speed-camera signal.
- keep the indicator visible while the relevant camera guidance remains active.
- hide it automatically after the signal ends or the camera is passed.
- recognition display should remain available even when navigation-based automatic deceleration is disabled.
- fixed and mobile speed cameras may use the same `vNAVI` indicator unless reliable subtype information is available.

Navigation deceleration option concept:

- 0: disabled
- 1: speed cameras only
- 2: speed cameras + speed bumps, if a real speed-bump signal is verified
- 3: additional navigation targets only when their signal semantics are confirmed

Do not infer speed-bump support from UI labels alone. Require an actual NEXO CAN/log capture.

## 9. Stock navigation and lead-departure signals

- Treat speed-limit recognition and speed-camera recognition as separate signals unless logs prove otherwise.
- Speed-bump recognition remains unconfirmed until captured in a diagnostic log.
- Investigate whether the OEM lead-vehicle-departure notification can be exposed outside the stock HDA-only context, but do not spoof an OEM state merely to force the alert without confirming dependencies and side effects.

## 10. 7000 server and remote diagnostics

The local 7000 web server is a NEXO development tool, not just a convenience UI.

Priorities:

- keep diagnostics compact and readable.
- surface NEXO-specific signal status clearly.
- make parking-sensor capture easy to review.
- preserve remote-access reliability on the same trusted network / configured remote path.
- avoid unnecessary camera/dashcam functions when they do not contribute to NEXO debugging.
- provide enough state to diagnose radar, longitudinal control, cruise buttons, navigation signals, parking sensors, MED transitions, and warning regressions.

## 11. Dashcam/logging policy

Dashcam video is not a project goal by itself. Logging is valuable when it helps diagnose:

- radar or lead detection behavior.
- longitudinal-control anomalies.
- button/control-state errors.
- cluster warnings.
- MED transition problems.
- parking-sensor signal mapping.

Prefer compact signal logs over unnecessary continuous video storage when both provide equivalent diagnostic value.

## 12. Update and merge policy

For Carrot, XPlus, upstream openpilot, and other forks:

1. inspect the commit and affected files.
2. identify whether Hyundai/NEXO/CAN/radar/longitudinal/UI code is touched.
3. compare against current NEXO-specific modifications.
4. port only changes that are compatible or clearly beneficial.
5. run static/build tests where possible.
6. collect a short NEXO diagnostic log after installation.
7. check cluster warnings, vehicle recognition, radar tracks, SCC state, MED behavior, and cruise fallback.

A newer upstream change is not automatically a better NEXO change.

## 13. Recommended development priority

Priority A — regression safety:

- vehicle recognition stability.
- no Panda/variant regressions.
- no new cluster warnings.
- normal cruise fallback preserved.
- reverse/MED state handling stable.

Priority B — core driving functionality:

- radar tracks.
- longitudinal control.
- reliable cruise-button behavior.
- NEXO-specific CAN state correctness.

Priority C — diagnostics and convenience:

- parking-sensor channel mapping.
- vNAVI recognition.
- navigation signal diagnostics.
- lead-departure investigation.
- 7000 UI improvements.

Priority D — experimental low-speed features:

- joystick / remote low-speed operation.
- parking-sensor-assisted proximity functions.

Priority D features must remain experimental until the underlying sensors and fail-safe behavior are verified.

## 14. Test principle

For NEXO-specific vehicle-control work, a feature is not considered complete just because the code builds or the UI appears. Completion requires evidence from the actual vehicle:

- expected CAN signal observed.
- state transition confirmed.
- no unexpected OEM warning.
- no regression in normal cruise or basic driving state.
- diagnostic log supports the intended interpretation.

When uncertain, add diagnostic visibility first, capture evidence, then change control behavior.

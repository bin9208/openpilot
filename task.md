# fcamera Lane Marking Shadow Pipeline Task

## Scope

Build and verify the foundation for Hyundai Ioniq 5 PE HDA1 / openpilot fcamera lane marking detection in shadow mode.

This stage includes offline fcamera model validation, crash-safe runtime model loading, Params toggles, and an onroad UI shadow overlay. It does not include lane-change control intervention.

## Safety Boundary

The following control paths must not be modified by this task:

- `selfdrive/controls/lib/desire_helper.py`
- carcontroller implementations
- lateral control
- longitudinal control
- lane change state machine
- steering, acceleration, or brake command generation

`LaneMarkingInterventionEnabled` is a placeholder in this build. Even when set to `1`, it does not allow, block, or gate lane changes.

## Runtime Params

- `LaneMarkingModelEnabled`: default `0`
- `LaneMarkingDisplayEnabled`: default `0`
- `LaneMarkingInterventionEnabled`: default `0`
- `LaneMarkingConfidenceThreshold`: default `60`
- `LaneMarkingShowDebugOverlay`: default `0`

## Runtime Model

The runtime model path is:

`selfdrive/lanemarking/models/lane_marking_fcamera.torchscript`

The runtime loader must remain crash-safe if the model file or Python model dependency is missing.

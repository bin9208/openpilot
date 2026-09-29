# Issue 36: first-segment route horizon

The shared Tmap/Naver path slicer appended the end of the first remaining
segment even if it was beyond the 300m horizon. The following segment could
then receive a negative interpolation ratio, inventing a backwards point.
Clip the first remaining segment using the same horizon before continuing.

Interfaces: `get_path_after_distance` keeps its arguments and three return
values. Both providers use the same helper. No steering controller, curve
lookup, speed floor, ATC setting or gas override is tuned.

Tests cover start/mid-segment clipping, a single segment, the exact endpoint
and a path of short segments. Run in the real controller host suite on the
feature SHA and independently on dev after integration.

Local helper-only TDD executed the real source functions and the same test
bodies without importing native/IPC dependencies: four failures plus one
unchanged pass before correction; all five passed after. Full module imports,
controller, gas and bump regressions remain the cloud acceptance gate.

This establishes an independent geometry defect, not the cause of the user's
22km/h observation. Actual desiredSource/desiredSpeed, vehicle settings and
C3 route/GPS traces remain necessary for that diagnosis.

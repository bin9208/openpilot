# Jetlink Android model tools (experimental)

This directory implements the data contract for issue #46, under #44. It does
not enable vehicle control or establish Android performance.

Use Python 3.12 in an isolated environment:

```sh
python -m pip install -r tools/jetlink_model/requirements.lock
python -m tools.jetlink_model.prepare source.onnx new-package-directory
python -m tools.jetlink_model.export_fixtures fixture-copy
python -m tools.jetlink_model.runner --package new-package-directory --frames warped-frames.bin --output candidate-run
python -m tools.jetlink_model.compare --reference reference-run --candidate candidate-run --output parity.json
python -m pytest -c /dev/null --rootdir=. --confcutdir=tools/jetlink_model/tests tools/jetlink_model/tests
```

The source must be a genuine ONNX export with `model_checkpoint` and
`output_slices` metadata. The preparer never loads a tinygrad PKL. Slice
metadata uses a bounded unpickler that only allows `builtins.slice`.
ONNX local functions are rejected except the verified single-Identity
definition of tinygrad Contiguous used by Cinque V3, which is preserved.
A layout-only undeclared `Contiguous` becomes ONNX `Identity`; other
arithmetic is left intact. Any transformed artifact still needs numerical
comparison: algebraically equivalent graphs can take different fp16 fusion paths.

External tensor data must remain inside the source directory. Preparation
inlines it into the artifact, limits total source bytes to 4 GiB, opens an
actual CPU ONNX Runtime session and checks static I/O shapes. A new package
is published only after all checks succeed; existing destinations are not
replaced. Large source/converted models must remain outside Git.

`manifest.schema.json` describes structural constraints. `manifest.py`
additionally checks path containment, actual artifact SHA/size, bounded
tensor byte counts, nonoverlapping output slices and recurrent state pairs.
The Android loader must repeat these semantic checks and compare the
manifest with its actual runtime session before reporting readiness.

An imported graph passing these checks is not automatically a supported
driving model. The C3X adapter must additionally validate the driving-input
layout and parser contract. Real-model numerical parity, sustained Tab S9
Ultra USB performance and vehicle acceptance remain separate requirements.

Recordings contain `run.json` identities, frame IDs and output slices, plus
row-major little-endian float32 `outputs.bin`. The runner consumes each
frame as warped uint8 followed by packed float32, using the pinned Jetlink
ModelSpec and queue contract. `--source-model source.onnx` runs the verified
original instead of the prepared artifact, and records its actual hash.
Recordings are limited to 4096 frames and 4 GiB; large sustained timing
benchmarks should export aggregated timing statistics instead.

The comparator rejects mismatched source/package/input identity, frame order,
shape, missing bytes and all NaN/Inf values. It checks every output, including
unnamed gaps, with `atol=1e-5, rtol=1e-4`. A passed host recording is only
numerical evidence for that runtime/input corpus; it is not Android, USB or
vehicle acceptance. Never loosen tolerances merely to obtain a passing result.

# Jetlink Android model tools (experimental)

This directory implements the data contract for issue #46, under #44. It does
not enable vehicle control or establish Android performance.

Use Python 3.12 in an isolated environment:

```sh
python -m pip install -r tools/jetlink_model/requirements.lock
python -m tools.jetlink_model.prepare source.onnx new-package-directory
python -m pytest -c /dev/null --rootdir=. --confcutdir=tools/jetlink_model/tests tools/jetlink_model/tests
```

The source must be a genuine ONNX export with `model_checkpoint` and
`output_slices` metadata. The preparer never loads a tinygrad PKL. Slice
metadata uses a bounded unpickler that only allows `builtins.slice`.
ONNX local functions are currently rejected. A layout-only tinygrad
`Contiguous` becomes ONNX `Identity`; other arithmetic is left intact.

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

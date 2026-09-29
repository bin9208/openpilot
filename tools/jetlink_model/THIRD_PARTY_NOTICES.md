# Jetlink reference code and fixtures

`reference/` and the files named by `fixtures/index.json` originate from
[zoompilot/jetlink](https://github.com/zoompilot/jetlink) at revision
`f10f4705243812518e6441dfb06bf2178c408310`, copyright Zeph Leggett,
under the MIT license reproduced at `reference/LICENSE`.

Local adaptations to Python reference code:

- Imports use the `tools.jetlink_model.reference` namespace.
- `OnnxMeta.output_slices` uses the local bounded, restricted slice metadata
  reader instead of unrestricted `pickle.loads`.

Fixture files are copied from the original Git blobs rather than regenerated
under a different ONNX serializer. Their SHA-256 values and originating
revision are in `fixtures/index.json`. Upstream generated the server outputs
on Apple arm64 with its pinned ONNX Runtime 1.29.0; local runtime tests record
their own version/platform. These tiny synthetic models are test assets,
not validated driving models.

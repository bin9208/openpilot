# fcamera Lane Marking Shadow Pipeline Walkthrough

## Verification Summary

- `tools/lane_marking_model/config_fcamera.json` uses `camera=fcamera`.
- `manifest.csv` points usable segments at `E:\media\0\realdata\...\fcamera.hevc`.
- fcamera metadata records `1928x1208` resolution and `20.0` fps.
- `crop_metadata.csv` includes `source_video`, `source_camera`, `source_resolution`, and `source_fps`.
- Sampled crop metadata rows use `source_camera=fcamera` and `C:\tmp\lane_marking_model_pipeline_fcamera\crops\...` paths.
- `split_check.json` reports zero train/val/test segment overlap.
- `metrics.json` and `qcamera_vs_fcamera_comparison.md` agree on the headline metrics.

## Metrics

- Overall accuracy: 79.38%
- white_dashed precision: 48.69%
- white_dashed recall: 62.58%
- Argmax false allow rate: 7.51%
- Argmax block recall: 92.49%
- Threshold 0.60 false allow rate: 4.77%
- Threshold 0.90 false allow rate: 1.09%

## Runtime Integration

- Params keys are registered in `common/params_keys.h` with safe OFF defaults.
- Settings toggles are exposed in `selfdrive/ui/qt/offroad/settings.cc`.
- The onroad UI overlay is only drawn when `LaneMarkingDisplayEnabled` is enabled.
- `lanemarkingd` is only started by manager when `LaneMarkingModelEnabled` is enabled and the device is onroad.
- `lanemarkingd` loads the committed TorchScript model for shadow-mode smoke testing and remains disabled/log-only if loading fails.
- No control command path reads lane marking predictions in this change.

## Commands Run

- `python -m py_compile tools/lane_marking_model/*.py tools/lane_marking_labeler/*.py selfdrive/lanemarking/*.py system/manager/process_config.py`: passed.
- `python tools/lane_marking_model/prepare_dataset.py --config tools/lane_marking_model/config_fcamera.json`: passed.
- `python tools/lane_marking_model/evaluate.py --config tools/lane_marking_model/config_fcamera.json`: passed.
- `python tools/lane_marking_model/infer.py --config tools/lane_marking_model/config_fcamera.json --image <existing_fcamera_crop>`: passed using the committed TorchScript runtime model.
- `python -m openpilot.selfdrive.lanemarking.lanemarkingd --smoke-test`: passed.
- `python tools/lane_marking_model/visualize_camera.py --config tools/lane_marking_model/config_fcamera.json --camera fcamera --segment 00000004--b98a75d224--12 --limit-frames 5 ...`: passed.
- `python tools/lane_marking_model/compare_pipelines.py --config tools/lane_marking_model/config_fcamera.json`: passed.
- `python tools/lane_marking_model/export_model.py --config tools/lane_marking_model/config_fcamera.json`: skipped by command approval because it loads a local `.pth` checkpoint with `torch.load`; existing `export_check.json` reports TorchScript and ONNX matches.

## Excluded Artifacts

The following remain out of git:

- `best_model.pth`
- crops and overlay frame directories
- overlay videos
- misclassified and false-allow crop dumps
- raw `qcamera.ts`, `fcamera.hevc`, `rlog.zst`, and `qlog.zst`
- the `C:\tmp\lane_marking_model_pipeline_fcamera` directory

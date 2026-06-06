# fcamera Lane Marking Classifier Pipeline

This pipeline builds an offline fcamera-based lane marking classifier for openpilot shadow-mode validation. It uses `fcamera.hevc` for crop extraction, keeps original `labels.csv` untouched, and expects corrected/trusted labels from `C:\tmp\lane_marking_labels_full\corrected_labels.csv`.

## Files

- `config_fcamera.json`: fcamera configuration, crop size 224, output root `C:\tmp\lane_marking_model_pipeline_fcamera`.
- `prepare_dataset.py`: creates the fcamera/qcamera manifest and segment-level train/val/test split.
- `extract_crops.py`: extracts lane marking crops from `fcamera.hevc` while using qcamera/log timing metadata for alignment.
- `train.py`: trains the classifier and writes outputs under the configured output directory.
- `evaluate.py`: computes test accuracy, per-class precision/recall, false allow rate, block recall, and threshold sweeps.
- `infer.py`: runs single-crop or batch crop inference.
- `export_model.py`: exports one runtime model candidate as TorchScript and ONNX.
- `visualize_camera.py`: renders qcamera or fcamera shadow overlays.
- `visualize_qcamera.py`: backward-compatible wrapper for older qcamera command lines.

## Commands

```bash
python tools/lane_marking_model/prepare_dataset.py --config tools/lane_marking_model/config_fcamera.json
python tools/lane_marking_model/extract_crops.py --config tools/lane_marking_model/config_fcamera.json
python tools/lane_marking_model/train.py --config tools/lane_marking_model/config_fcamera.json
python tools/lane_marking_model/evaluate.py --config tools/lane_marking_model/config_fcamera.json
python tools/lane_marking_model/infer.py --config tools/lane_marking_model/config_fcamera.json --image <existing_fcamera_crop.jpg>
python tools/lane_marking_model/export_model.py --config tools/lane_marking_model/config_fcamera.json
python tools/lane_marking_model/visualize_camera.py --config tools/lane_marking_model/config_fcamera.json --camera fcamera --segment <segment> --output-video C:\tmp\lane_marking_model_pipeline_fcamera\outputs\overlay_video_check.mp4 --output-images-dir C:\tmp\lane_marking_model_pipeline_fcamera\outputs\overlay_frames_check
```

## Verified fcamera Artifacts

The current fcamera run writes:

- `C:\tmp\lane_marking_model_pipeline_fcamera\manifest.csv`
- `C:\tmp\lane_marking_model_pipeline_fcamera\crop_metadata.csv`
- `C:\tmp\lane_marking_model_pipeline_fcamera\split_check.json`
- `C:\tmp\lane_marking_model_pipeline_fcamera\outputs\metrics.json`
- `C:\tmp\lane_marking_model_pipeline_fcamera\outputs\qcamera_vs_fcamera_comparison.md`

`manifest.csv` points each usable segment at `E:\media\0\realdata\...\fcamera.hevc`, records fcamera resolution `1928x1208`, and records fps `20.0`. `crop_metadata.csv` includes `source_video`, `source_camera`, `source_resolution`, and `source_fps`; sampled rows use `source_camera=fcamera` and crop paths under `C:\tmp\lane_marking_model_pipeline_fcamera\crops`.

`split_check.json` reports 225 train segments, 48 val segments, 49 test segments, and zero train/val/test segment overlap.

## Metrics Summary

The current `metrics.json` reports:

- Overall accuracy: 79.38%
- white_dashed precision: 48.69%
- white_dashed recall: 62.58%
- Argmax false allow rate: 7.51%
- Argmax block recall: 92.49%
- Threshold 0.60 false allow rate: 4.77%
- Threshold 0.90 false allow rate: 1.09%

Compared with the qcamera baseline:

- Overall accuracy: 58.56% -> 79.38%
- False allow rate: 18.45% -> 7.51%
- Block recall: 81.55% -> 92.49%
- white_dashed precision: 20.19% -> 48.69%
- white_dashed recall: 41.02% -> 62.58%
- white_solid recall: 53.27% -> 86.52%

## Decision Rules

- `white_dashed` with confidence greater than or equal to `LaneMarkingConfidenceThreshold` and temporal consistency becomes `allow_candidate`.
- `white_solid`, `yellow_solid`, `yellow_double`, and `road_edge_or_barrier` become `block`.
- `unknown` or low confidence becomes `uncertain_or_block`.

`allow_candidate` is a shadow-mode label only. It is not a lane-change permission command, and this pipeline must not write `lane_change_allowed` or modify openpilot control behavior.

## Runtime Model Policy

Only one final runtime model should be committed under `selfdrive/lanemarking/models/`. Training checkpoints such as `best_model.pth`, crop directories, videos, overlay frames, misclassified crops, and raw route logs/videos stay out of git.

# Cat detection refinement — controller 1.2.19

Updated 2026-10-15. Requested examples: Dixie in the lower-right corner of clip 26046 and the manually confirmed unnamed Cat inside the wire cage in 25912.

The normal 16 regularly spaced frames are retained. Two additional frames cover the midpoint of the previously omitted tail and the final frame. This is sampled analysis, not a detector run on every video frame.

The existing detectors remain the primary path. A CPU OpenCV DNN YOLO11s model supplies additional small animal locations. Its Cat/Dog labels never bypass household Type references, background checks, quality, independent-support, temporal, or contrary-species guards. Existing supported Dog/Person results and original Person crops remain unchanged. There is no motion-only Cat detection path.

Type references gain cached grayscale, wire-obscured and rotated variants. Variants retain their original clip/image provenance and cannot manufacture independent support. Current human-confirmed manual Cat crops can teach other views in the same clip. Dan's explicitly identified Dixie end-frame crop is a pinned Cat-Type example; it neither assigns nor trains the individual Dixie identity. Same-clip/manual/static teaching examples are excluded from the independent other-clip support count. A Cat result still requires support from at least two other confirmed clips/images. Strong original Dog/Person evidence vetoes supplemental Cat promotion. Generic Unknown tracks require repeated, distinct high-confidence Cat-model views and original native features favoring Cat; one ambiguous crop cannot change their Type.

Native Cat features are preserved when a matching reviewed example supports them. An overlapping proposal in the immediately following sampled frame may continue a reviewed Cat view when native-feature cosine similarity is at least .75, original native references favor Cat, and independent Cat support, quality, background and species-veto checks pass. This continuation cannot create another continuation anchor; only a fresh strict reviewed-example match can do that. Its native crop hash, timestamp and model are bound to a separate Cat Type vote, and the final track still needs its existing strong-single-view or distinct-two-view evidence. Wider surrounding context must not replace supported native Cat pixels. Reviewed background features reject foliage/container lookalikes. All features, caches, recorded videos and catalogs remain on the Pi. Human confirmations, identity profiles and rejected detections are preserved during reanalysis.

## Required Pi assets

The production runtime uses the existing `.venv-ai` OpenCV, NumPy and Torch dependencies; Ultralytics is not a production dependency. Assets live in `ai-models/` and are provisioned separately from source control, like the existing detector weights. Source modules verify static fingerprints. The SQLite feature cache is mutable and validates each entry using source-image, model and transformation identifiers.

| Asset | SHA-256 |
|---|---|
| `yolo11s-cat-v1.onnx` | `4adb6d0246798aebadda6fd62ee1d909f5ab522d735d1067592f7d4e5a5cd4a6` |
| `cat-background-v1.json` | `9e1bcd5ca9610404f99ca51760fd32bc3438f2da4705171a85ae93422026664f` |
| `cat-reviewed-examples-v1.json` | `5c45ee3eb9292f2620c40eeff1e2f4e02a759bcfe6bacac910c1dccd1741e875` |
| `cat-reviewed-26046-v1.jpg` | `1c23ffa5b1a44d6129f956ce4b673913ed7b69551aa89c4dd2035b4a08e44743` |
| `cat-model-source-v1.json` | `fb0d3bede135da16427c5c7c022a34ce0473fefcad325ffc856d61d5e65a4507` |

The ONNX model comes from official [Ultralytics YOLO11s COCO weights](https://huggingface.co/Ultralytics/YOLO11/resolve/main/yolo11s.pt), source SHA-256 `85a76fe86dd8afe384648546b56a7a78580c7cb7b404fc595f97969322d502d5`, exported with opset 12, static 640×640 input, no simplification or embedded NMS. Input `[1,3,640,640]`, output `[1,84,8400]`. Upstream license: AGPL-3.0; [official model documentation](https://docs.ultralytics.com/models/yolo11/). Export tooling was confined to the isolated study environment. The private background and reviewed-example assets must be retained on the authorized Pi; they are not public model downloads.

## Verification scope

The isolated study is `/home/dan/BlinkDRS-Controller/.cat-improve-20261015`. It retains source, test logs, original recording hashes, frozen reference catalog, complete sampled replay observations, native crops, and paired control results. Private feature caches and observations remain there. The deployment manifest and backup guard against unexpected source changes; installation checks Live View/recording/saving, preserves the interrupted analysis queue, backs up the catalog and affected files, and verifies service health and About metadata.

These two clips provide human-supervised teaching examples and are replay tests of those examples. They are not independent unseen-clip accuracy measurements. Scene/crop controls check for specific regressions, not a general accuracy guarantee. A Cat Type result does not confirm an individual identity; Dan remains the reviewer.

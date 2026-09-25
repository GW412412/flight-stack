# Weed labeling workflow (rosette ODM tiles)

Concrete workflow so spring-paddock **OpenDroneMap tiles** can be labeled for
**rosette weeds** in a **visual UI**, with a first model trained and then used to
**pre-label** more tiles for review. Decisions are locked below (no open choices).

Companion notes: [WEED-ID.md](WEED-ID.md), [SYSTEM-SPEC.md](SYSTEM-SPEC.md).

---

## Tool choice: **CVAT** (self-hosted)

| Option | Verdict |
|--------|---------|
| **CVAT** (chosen) | Open-source; strong **polygon / mask** UI; grid + filter views so you see **all targets on a tile**; export YOLO / COCO; runs on the project ground station (40-core / 256 GB) without sending paddock imagery to a SaaS |
| Label Studio | Fine for boxes; mask tooling is weaker for dense rosette canopies |
| Roboflow | Fast cloud UX, but imagery leaves the farm network; skip for this project |

**Install (ground station):** Docker Compose from upstream CVAT docs —
https://docs.cvat.ai/docs/administration/basics/installation/  
Use the **latest stable** CVAT server + UI on `localhost` (or LAN-only). Do not
expose it to the public internet without auth hardening.

**Why CVAT fits the three user needs:** (1) polygon/brush instance labels on ODM
tiles, (2) canvas shows every annotation at once (not blind CSV labeling),
(3) import model predictions as shapes for human review (auto-annotation /
import job).

---

## Classes (locked)

Use **DeepWeeds species names** plus an explicit negative / background class.
DeepWeeds: 8 weed species + negatives (paper/repo in [WEED-ID.md](WEED-ID.md)).

| ID | Class name (CVAT / YOLO) | DeepWeeds species string |
|----|--------------------------|---------------------------|
| 0 | `chinee_apple` | Chinee apple |
| 1 | `lantana` | Lantana |
| 2 | `snake_weed` | Snake weed |
| 3 | `prickly_acacia` | Prickly acacia |
| 4 | `siam_weed` | Siam weed |
| 5 | `parthenium` | Parthenium |
| 6 | `rubber_vine` | Rubber vine |
| 7 | `parkinsonia` | Parkinsonia |
| 8 | `negative` | Negative — use for **empty tiles only** (no polygons); do not paint fake shapes on bare pasture |

Label IDs **0–7** match DeepWeeds weed species; **8** is DeepWeeds Negative.
Keep export order identical to this table so heads transfer cleanly.

**Practical rule for paddock orthos:** if a rosette species cannot be told apart at
ortho GSD, still pick the closest DeepWeeds class for v1; collapse to a single
`rosette` class only after a review cycle if confusion stays high.

Instance labels: draw a **polygon (or brush mask)** around each rosette canopy.
One instance = one plant/clump. Do not merge separate plants into one shape.

---

## Directory layout (repo / GS disk)

```text
data/
  odm_tiles/           # RGB GeoTIFF or PNG tiles cut from ODM orthophoto
  odm_tiles_index.csv  # tile_id, path, crs, bounds (optional)
  labeled/             # CVAT exports (YOLO-seg)
  prelabels/           # model predictions before review
  models/              # .pt / .onnx checkpoints
```

Create these under `/workspace/precision-drone-spraying/data/` (or a larger GS
disk mount). Keep large tiles **out of git**; gitignore `data/`.

---

## Exact steps

### Step 1 — Cut and ingest ODM tiles into CVAT

1. From ODM `odm_orthophoto.tif`, cut **sliding-window tiles** at the training
   `imgsz` (default **640×640** px) with **25–50% overlap** (see WEED-ID tiling).
2. Export tiles as **PNG or JPEG** for CVAT (or GeoTIFF if your CVAT build
   accepts them; PNG is the safe default). Keep a sidecar CSV of georef bounds
   so predictions can map back to waypoints later.
3. In CVAT: create project **`paddock-rosettes`**, task **`odm-tiles-batch-001`**.
4. Upload the tile images. Set labels to the class list above (polygon type).
5. Open the job in the UI — you should see the full tile and can place/edit all
   polygons on one canvas (requirement: **see all targets at once**).

### Step 2 — Define classes and labeling rules in CVAT

1. Project → Labels: add DeepWeeds species as **polygon** labels; colours distinct.
2. Hotkeys: polygon tool, next/prev frame (tile).
3. Rules card (pin in task description):
   - Label **every visible rosette** in the tile.
   - Tight canopy outline; include shadow only if it is part of the crown.
   - Skip grass clumps that are clearly not weeds.
   - Empty weed-free tiles: save with **no shapes** (negatives).

### Step 3 — Seed training with DeepWeeds, then fine-tune on labeled tiles

1. Download **DeepWeeds** (https://github.com/AlexOlsen/DeepWeeds) for
   classification/pretrain diversity. For **YOLO-seg**, either:
   - convert any available masks/boxes you produce from DeepWeeds images into
     YOLO-seg format, **or**
   - start from an **Ultralytics YOLO-seg checkpoint** (e.g. `yolo11n-seg.pt` or
     `yolov8n-seg.pt` — **nano** size locked for the K80/CPU GS) pretrained on
     COCO-seg, then fine-tune on your first paddock labels; use DeepWeeds images
     as **extra classification / hard-negative** material where helpful.
2. After **≥ 200–400 labeled instances** across tiles (batch-001), export from
   CVAT as **YOLO** / segmentation format into `data/labeled/batch-001/`.
3. Train (on a modern GPU if available; else CPU overnight):

```bash
yolo segment train \
  model=yolov8n-seg.pt \
  data=data/labeled/batch-001/data.yaml \
  imgsz=640 epochs=100 batch=8 \
  project=data/models name=rosette_seg_v1
```

4. Export ONNX for the ground station: `yolo export model=.../best.pt format=onnx`.

**Locked model for this workflow:** **YOLOv8n-seg** (Ultralytics nano segmentation).
Small enough for ONNX/CPU pre-label passes; upgrade to `s` only if recall is poor
after two review cycles.

### Step 4 — Pre-label more tiles with the first model

1. Cut the next tile batch (`odm-tiles-batch-002`) the same way; **do not** hand
   label from scratch.
2. Run tiled inference → YOLO-seg polygons / masks:

```bash
yolo segment predict \
  model=data/models/rosette_seg_v1/weights/best.pt \
  source=data/odm_tiles/batch-002/ \
  imgsz=640 conf=0.25 iou=0.5 \
  save_txt=True project=data/prelabels name=batch-002
```

3. Convert prediction txt/masks into a CVAT import package (CVAT “import
   annotations” / Datumaro). Create task **`odm-tiles-batch-002-prelabel`** with
   images + imported shapes.

### Step 5 — Review and correct in the visual UI

1. Open each pre-labeled tile in CVAT — **all proposed targets visible at once**.
2. Reviewer actions: **accept**, **reshape**, **reclassify**, **delete false
   positives**, **add misses**.
3. Prefer correcting in place over deleting the whole layer.
4. When a batch is clean, export → merge into `data/labeled/`, bump version
   (`rosette_seg_v2`), retrain, and repeat Steps 4–5 (active learning loop).

### Step 6 — Hand-off to spray pipeline

1. Final reviewed masks → centroids (+ area) in map CRS → CSV/GeoJSON targets.
2. Feed [WAYPOINT-OPT.md](WAYPOINT-OPT.md) / mission tools.
3. Live video ID (SYSTEM-SPEC) can later load the same `best.pt` / ONNX weights
   on stream ROIs; labeling remains ortho-first for map quality.

---

## Quality bar before trusting pre-labels

| Check | Pass criterion |
|-------|----------------|
| Per-tile review | No obvious false positives on bare pasture |
| Miss rate | Spot-check 10% of tiles for missed rosettes |
| Class confusion | Species errors corrected or collapsed to a single `rosette` class if taxonomy is unreliable in ortho GSD |
| Export | `data.yaml` class order matches CVAT label order |

---

## Summary

**CVAT** on the ground station; DeepWeeds-aligned polygon classes; tile ODM →
label batch-001 → train **YOLOv8n-seg** → pre-label batch-002 → visual review →
retrain loop.

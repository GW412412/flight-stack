# Weed identification from ODM orthomosaics

Research notes for spotting **rosette-stage weeds in spring-grazed paddocks** from **OpenDroneMap (ODM)** orthomosaics on a ground station. Compiled 2026-09-26. Links are to real papers, repos, and docs only.

---

## 1. Detection vs segmentation on orthomosaics

| Approach | Best when | Output for spray pipeline |
|----------|-----------|---------------------------|
| **Object detection** (YOLO boxes) | Discrete plants / clumps; need centroids fast | Point / bbox → waypoint |
| **Instance segmentation** (YOLO-seg, Mask R-CNN) | Need plant outline + area for dose | Mask → centroid + canopy area |
| **Semantic segmentation** (U-Net, SegFormer, SegNet-style) | Dense canopy, ambiguous boundaries, early rosettes blending into pasture | Pixel map → connected components → targets |

**For rosettes on grazed pasture orthos:** prefer **instance or semantic segmentation**. Boxes alone under-estimate canopy for dose and struggle when many small plants share a tile. Detection is a fine *bootstrap* (labeling aid) or fallback if masks are too slow.

WeedMap showed that **tiling large orthomosaics** (tile = network input size, sliding window, write predictions back) is the right pattern for hectare-scale maps without downsampling GSD ([MDPI](https://www.mdpi.com/2072-4292/10/9/1423), [arXiv](https://arxiv.org/abs/1808.00100)).

---

## 2. Suitable architectures

| Family | Notes for aerial weeds |
|--------|-------------------------|
| **YOLO-seg** (Ultralytics) | Strong default for *instance* masks + boxes; easy train/export; tile large GeoTIFFs externally. Docs: [Instance segmentation](https://docs.ultralytics.com/tasks/segment/). |
| **U-Net / encoder–decoder** | Classic dense crop/weed maps; good with small tiles and class imbalance weighting. |
| **SegFormer** (Hugging Face Transformers) | Efficient transformer semantic seg; good for tiled ortho patches. Docs: [SegFormer](https://huggingface.co/docs/transformers/en/model_doc/segformer). |
| **Mask R-CNN** | Instance masks; heavier than YOLO-seg; still used in UAV weed papers (e.g. bioRxiv 2024.08.13.607729). |
| **SegNet / weedNet lineage** | Used in WeedMap for multispectral ortho tiles; baseline for aerial sugar beet/weed. |

**Recommendation:** start with **Ultralytics YOLO*-seg** on RGB (or RGB+index) tiles for instance masks → centroids; if pasture background is noisy and labels are pixel-wise only, fine-tune **SegFormer-B0/B2** or a **U-Net**. Keep a pure **detect** YOLO only if you accept bbox→centroid dose error.

---

## 3. Training data & augmentation

### Real datasets (verify before download)

| Dataset | What it is | Link |
|---------|------------|------|
| **DeepWeeds** | 17,509 in-situ AU rangeland weed images (8 species + negatives); *ground-level*, not ortho — good for transfer / species heads | Paper: https://www.nature.com/articles/s41598-018-38343-3 · Repo: https://github.com/AlexOlsen/DeepWeeds |
| **WeedMap** | Multispectral **UAV orthomosaic** sugar beet/weed semantic labels; tiled sliding-window paradigm | Paper: https://www.mdpi.com/2072-4292/10/9/1423 · arXiv: https://arxiv.org/abs/1808.00100 |
| **CropAndWeed** | High-res field images; boxes + semantic masks; many crop/weed species (WACV 2023) | https://github.com/cropandweed/cropandweed-dataset |

**Domain note:** DeepWeeds and CropAndWeed are not spring-grazed paddock orthos. Expect to **collect/label local ODM tiles** (rosettes on pasture) and use public sets for pretrain / augmentation diversity only.

### Ortho / aerial augmentation (practical)

- **Mosaic tiling:** fixed tile size = model `imgsz` (e.g. 640 or 512); **25–50% overlap**; stitch with max/avg logits or NMS on instances.
- **Geometry:** random rotations (0–360° — nadir has no preferred up), flips, mild scale / crop (rosette size varies with GSD and growth).
- **Colour / radiometry:** brightness, contrast, HSV jitter; optional CLAHE; if multispectral, channel dropout / NDVI as extra channel (WeedMap found NDVI helpful).
- **Resolution:** match flight GSD in training (ODM ortho resolution docs: https://docs.opendronemap.org/). Avoid training only on close-up ground photos then testing on 1–3 cm GSD orthos without scale jitter.
- **Class imbalance:** weight rare weed pixels; or oversample tiles that contain positives.

ODM output (orthophoto GeoTIFF, optional DSM/DTM) is the inference source of truth: https://docs.opendronemap.org/

---

## 4. Ground-station inference (40-core CPU / 256 GB RAM / Tesla K80)

### K80 constraints (Kepler, CC 3.7)

- Official **driver branch for Kepler datacenter GPUs is R470**; CUDA toolkit development support for Kepler is through **CUDA 11.x**, not modern CUDA 12 stacks.
- Discussion / matrix references: https://forums.developer.nvidia.com/t/tesla-k80-and-discontinued-driver-support-for-kepler/193903 and NVIDIA datacenter driver docs linked from that thread: https://docs.nvidia.com/datacenter/tesla/drivers/index.html
- **Modern PyTorch / Ultralytics wheels** typically target newer SM; K80 often needs **pinned older CUDA 11 + matching torch**, or **CPU / ONNX Runtime** fallback.

### Practical options

| Path | Pros | Cons |
|------|------|------|
| **CPU + ONNX Runtime** (or OpenVINO) | Stable on this box; uses 40 cores; no Kepler drama | Slower than GPU |
| **Pinned CUDA 11 + old torch** that still builds/ships sm_37 | Uses K80 if drivers allow | Fragile; unsupported going forward |
| **Export ONNX from training machine; infer on station** | Train elsewhere (newer GPU); deploy portable | Extra export step |

**Recommendation for this station:** train elsewhere if possible; **export ONNX**; run **tiled inference on CPU (OR K80 only if a verified CUDA 11 stack is already working)**. With 256 GB RAM, keep large orthos memory-mapped (`rasterio`/`GDAL` windowed reads) — do **not** load the full GeoTIFF as one array into the model.

### Tiling & batch sizing (sketch)

1. Open ODM `odm_orthophoto.tif` with windowed reads.
2. Slide tiles of size `imgsz` with overlap; skip empty/nodata.
3. Batch size: start **batch=1–4 on K80**; on CPU try **batch=8–16** and raise until RAM/CPU saturate.
4. Merge: for detection/instance — NMS across overlaps in map CRS; for semantic — average overlapping logits then connected components → weed polygons → centroids for waypoints.
5. Write GeoJSON / CSV of targets for the waypoint optimiser (`docs/WAYPOINT-OPT.md`).

---

## 5. Concise recommendation

1. **Label** local spring-paddock ODM tiles (rosettes) in instance-mask or semantic format.
2. **Fine-tune YOLO*-seg** (Ultralytics) as primary; keep SegFormer/U-Net as semantic backup.
3. Use **WeedMap-style tiling** on the orthomosaic; optional NDVI channel if you have red/NIR.
4. Pretrain / augment with **DeepWeeds** + **CropAndWeed** carefully (domain gap).
5. On the **K80 ground station**, prefer **ONNX + CPU** (or verified CUDA 11) with windowed tiles; export spray targets as georeferenced points/polygons.

---

## Key links

- Ultralytics segmentation: https://docs.ultralytics.com/tasks/segment/
- SegFormer (HF): https://huggingface.co/docs/transformers/en/model_doc/segformer
- DeepWeeds paper: https://www.nature.com/articles/s41598-018-38343-3
- DeepWeeds repo: https://github.com/AlexOlsen/DeepWeeds
- WeedMap (MDPI): https://www.mdpi.com/2072-4292/10/9/1423
- WeedMap (arXiv): https://arxiv.org/abs/1808.00100
- CropAndWeed: https://github.com/cropandweed/cropandweed-dataset
- OpenDroneMap docs: https://docs.opendronemap.org/
- K80 / Kepler driver thread: https://forums.developer.nvidia.com/t/tesla-k80-and-discontinued-driver-support-for-kepler/193903
- NVIDIA Tesla drivers: https://docs.nvidia.com/datacenter/tesla/drivers/index.html

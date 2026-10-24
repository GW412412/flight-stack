# Map to route: OpenDroneMap survey to targets to waypoint sorties

*Draft / experimental. Contributed by Maya (AI agent, Sesame), 25 Oct 2026. Tested only against a synthetic ODM project and the pretend drone (`fake_vehicle.py`). Not yet run on a real ODM project, in SITL, or in the air. Open questions for the group are O-14 to O-23 in [DISCUSSION.md](DISCUSSION.md).*

This prototype takes the outputs of an OpenDroneMap (ODM) survey of a paddock, finds candidate weeds on the orthomosaic, places each one in latitude, longitude and ground height, and plans waypoint sorties that bring the drone to a hover above each target, where the visual centering loop ([VISUAL_CENTERING.md](VISUAL_CENTERING.md), PR #1) takes over. It produces files only: it never connects to a drone, never uploads, never arms, and never emits a sprayer command.

The bottom line: the pipeline works end to end on synthetic data and every sortie it writes passes the real flight safety check and uploads cleanly to the pretend drone. The biggest real-world gap is map accuracy. A survey flown without ground control points or RTK can put a weed several metres from where it really is, which on current placeholder numbers may be outside the live camera's view when the drone arrives.

## Where it fits

1. Survey flight over the paddock (camera and GPS still to be decided, O-14).
2. ODM builds the orthomosaic, DSM and DTM.
3. **This prototype:** tiles, detector, georeferencing, route planning, safety pre-check, output files.
4. Operator review of `mission_plan.json`, then upload of one sortie through the existing `upload_mission` tool (safety gate, confirmation code).
5. In flight, at each target: the visual centering loop finds and centres the weed using the hand-off data written here.
6. Spraying: not connected yet (O-11).

## Code

All in `drone_stack/middleware/`:

| File | Job |
|---|---|
| `map_rasters.py` | Opens the ODM rasters, checks their map grid, converts coordinates, samples heights |
| `map_tiles.py` | Cuts the orthomosaic into overlapping tiles; each weed belongs to exactly one tile |
| `map_detect.py` | Swappable detectors: colour stand-in, YOLO-seg adapter |
| `map_georef.py` | Detections to targets with position, heights and area; GeoJSON output |
| `map_route.py` | Terrain-relative heights, ordering, sorties, obstacles, zones, safety pre-check, hand-off |
| `map_to_route.py` | Command line and library entry point |

Example settings: [../config/map_to_route.example.toml](../config/map_to_route.example.toml). Tests: `tests/test_map_inputs.py`, `tests/test_map_route.py`, `tests/test_map_pipeline_e2e.py`, with the synthetic paddock in `tests/map_synth.py`. New dependencies: `rasterio`, `pyproj` (in `requirements.txt`); `shapely` was already used.

Run it from `drone_stack/middleware`:

```bash
python map_to_route.py --config ../../config/map_to_route.example.toml \
    --project /path/to/odm/project --home=-38.4012,146.3120 --out plan/
```

Write `--home` with `=`: a negative latitude otherwise reads as an option.

## ODM outputs used, and why

ODM writes the elevation models only when asked (`--dsm`, `--dtm`); see the [ODM outputs docs](https://docs.opendronemap.org/outputs/).

- **Orthomosaic** (`odm_orthophoto/odm_orthophoto.tif`, RGB plus alpha): the picture the detector looks at. Used at full resolution, in tiles, so small rosettes keep their pixels. Alpha 0 (no data) is masked out.
- **DTM** (`odm_dem/dtm.tif`, bare ground): required. It gives the ground height under each target and under home, which sets every flight height.
- **DSM** (`odm_dem/dsm.tif`, tops of everything): optional but strongly advised. It lets the planner see trees, sheds and fences along each leg. Without it the planner warns and clears the DTM only, which means obstacles are invisible to it.
- **Height above ground** (DSM minus DTM): built on the fly and resampled onto the exact pixel grid of each orthomosaic tile, so it can be a fourth channel for the detector and a height filter.

Rules the code enforces: all three rasters must share one projected map grid in metres (ODM writes UTM), and degree-based rasters are refused rather than mis-measured. No-data becomes "unknown", never a number that looks like a height. Heights are only ever used as differences within one ODM project, never mixed between runs.

## Classifier pick

**Recommendation: stay with the Ultralytics YOLO-seg family that [LABELING-WORKFLOW.md](LABELING-WORKFLOW.md) already uses, run on orthomosaic tiles on the GPU computer.**

1. **Plain colour first.** Train and judge a 3-channel model on labelled orthomosaic tiles. This is the baseline.
2. **Then a 4-channel version** with height above ground as the fourth channel, compared on the same validation tiles. Ultralytics supports inputs with more than three channels (its [COCO8-Multispectral](https://docs.ultralytics.com/datasets/detect/coco8-multispectral/) example dataset uses 10). Height helps reject shrubs, trees and stock. It will not separate flat rosettes from pasture, because both are near zero height. The tests show both sides: without the height filter the 1.5 m shrub in the synthetic paddock is a false hit; with it the shrub drops out.
3. **Licence fallback.** Ultralytics is licensed [AGPL-3.0](https://github.com/ultralytics/ultralytics/blob/main/LICENSE) and this repo is MIT (O-19). If that matters, the fallback is [RF-DETR](https://github.com/roboflow/rf-detr)'s segmentation model. Its `rfdetr` package and Apache-designated models are Apache 2.0, but its README says the "Plus" components (including the Atto, Femto, Pico, XL and 2XL detection models) are under a different licence (PML 1.0), so check each model before use.

The adapter in `map_detect.py` (`UltralyticsSegAdapter`) loads a model only from a weights file that already exists on disk, because Ultralytics downloads official weights when given a bare model name, and a stock model knows no weeds. The fourth channel's order and height scaling (`input_order`, `chm_full_scale_m`, placeholder 2.0 m to 255) must match however the training images were built (O-18). The colour detector (`ColourBlobDetector`) is a test and bench stand-in only: on real pasture almost everything is green.

## Georeferencing

Each detection's outline is converted from tile pixels to map metres to latitude and longitude. Each target gets:

- position (centroid of the outline), with the outline kept as a GeoJSON polygon
- ground height from the DTM and plant height from the height-above-ground layer
- area in cm² from the outline, which drives the existing dose formula in `spray_mission.py`
- confidence, label, detector name, map date and the map's horizontal error estimate

Tiles overlap so a weed on a tile edge is seen whole, and each weed is counted by exactly one tile (the one whose core holds its centre), so overlaps give no duplicates. Output is in the GeoJSON shape `spray_mission.load_detections` already reads, and the tests check that `generate_spray_plan` accepts every sortie's file.

## Terrain-relative heights

ArduPilot mission altitudes here are relative to home. The planner sets each target's approach height as:

> approach height = (target ground − home ground) + 6 m

with both ground heights from the same DTM. Taking a difference within one map cancels the unknown height reference (ellipsoid or geoid) and most of a survey's absolute vertical error. The 6 m approach height above the weed is a placeholder (O-22).

- A target whose approach would exceed the gate's 20 m ceiling is **excluded with a reason**, never pushed down towards the ground.
- A target downhill of home is raised to the gate's minimum height if that still leaves a usable camera height, otherwise excluded.
- Legs are sampled along a corridor against the DSM. Where a tree or shed is in the way, the planner adds climb-over waypoints, including before return to home if the Pixhawk's RTL height would not clear an obstacle.
- If the drone's real home differs from the planned home by more than 3 m, every height is wrong by the ground difference. `check_home_matches` reports this; whether `upload_mission` should refuse in that case is O-21.

## Route ordering and sorties

- **Cost:** flying time between points, counting horizontal distance at cruise speed plus climbs and descents at their own (different) rates, so uphill and downhill legs cost differently. Legs that cross a no-fly zone or cannot clear obstacles under the ceiling are infeasible.
- **Order:** nearest neighbour, then 2-opt improvement. Deterministic; tested never to be worse than nearest neighbour.
- **Sorties:** the tour is split when the next target would exceed the tank (0.5 L, from the dose formula), the battery budget (placeholder 600 s usable with a 30% reserve), or the gate's mission item limit.
- **Filters:** targets outside the spray zone, inside 3 m of the geofence edge, beyond the geofence, below the confidence threshold, or unreachable alone within one battery are excluded, each with its reason in `mission_plan.json`.
- **Map age:** a warning when the map is older than 14 days (placeholder, O-16).

## Hand-off to the visual centering loop

For every target, each sortie records what the centering loop needs on arrival: expected position, expected ground height relative to home, approach height above the weed, label, area, and a **search radius**:

> search radius = 2 × √(map error² + drone GPS error²)

With a camera field of view set, it also records the camera's half footprint at the approach height and whether the target is probably in view. A future step is to have the centering loop read this directly; today it is metadata in `mission_plan.json`.

## Safety gate integration

- Hard limits are not duplicated. Height ceiling, minimum height, geofence radius, speed and mission item count come from the stack config (`config/llm_stack.pixhawk.toml`: 20 m, 60 m, 3 m/s), the same file the MCP server and safety gate use.
- Every sortie is pre-checked with the real `FlightGate.check_mission` before it is written, and the result is stored with the sortie.
- Output waypoints are in the exact `upload_mission` format (`lat`, `lon`, `alt_m`, return home at the end). Uploading still goes through the normal tool: safety gate, confirmation code, pilot override rules.
- No sprayer commands. The plan says so (`no_sprayer_commands: true`) and the tests check both the plan and the mission read back from the pretend drone.
- The pretend-drone test uploads every planned sortie through `DroneTools → FlightGate → MavlinkHub → FakeVehicle` with the real Pixhawk envelope and compares the downloaded mission item by item. A second test puts the drone's home about 110 m from the planned home: `check_home_matches` flags it and the gate refuses the upload because the waypoints fall outside its 60 m geofence.

## Honest gaps

**1. Map accuracy versus the camera's view (the biggest gap).** ODM's own [map accuracy page](https://docs.opendronemap.org/map-accuracy/) says the horizontal accuracy of a standard GPS is usually 2 to 6 m, with vertical error 3 to 4 times that, and that ground control points bring it down to about 2.5 times the ground sample distance. With a 3 m map error and a 1.5 m drone GPS error, the search radius is about 6.7 m. At 6 m above the weed with a 90° camera, the view reaches only about 6 m each side, so the weed may start outside the picture. The 90° field of view is a placeholder, not a measurement; a narrower real lens makes it worse. The tests flag this case (`target_probably_in_view: false`). This is the strongest argument for RTK-tagged survey photos or ground control points (O-14), or for a search pattern on arrival.

**2. Not run on real data.** Only a synthetic 50 m paddock: a plane rising to the east, flat green rosettes, a shrub, a tree and gaps in the map. Real orthomosaics have seams, shadows, blur and mixed vegetation.

**3. No trained weed model yet.** The colour detector is a stand-in. The YOLO adapter is tested with a mock model only.

**4. Placeholders throughout.** Approach height, camera field of view, climb and descent rates, dwell time, battery time, drone GPS error and the height scaling of channel 4 all need measuring (O-22).

**5. Geofence versus paddock size.** The 60 m geofence covers a circle of about 1.1 ha around home; a 5-acre (about 2 ha) paddock does not fit from one home point (O-15).

**6. One uploaded mission versus target-by-target flight.** The plan is shaped as one mission per sortie. Flying target to target in GUIDED with the safety check on each move is the alternative (O-20).

**7. Spraying not connected.** Doses are computed and carried as metadata only; no pump command exists in the plan (O-11).

**8. Static obstacles only.** The DSM is as old as the survey. Stock, vehicles, people and new structures are invisible to the planner; the pilot's override remains the defence.

## Open questions for the group

See O-14 to O-23 in [DISCUSSION.md](DISCUSSION.md): survey camera and GPS, geofence versus paddock size, maximum map age, one model or two, channel order and height scaling, the AGPL question, mission versus target-by-target flight, home mismatch refusal, real airframe and camera numbers, and no-fly zones.

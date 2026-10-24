"""Map-to-route prototype: rasters, tiles and detectors.

Contributed by Maya (AI agent, Sesame). Uses the synthetic ODM project in
map_synth.py; no real survey data, no model weights, no network.
"""
import math

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
pytest.importorskip("pyproj")

from map_detect import (ColourBlobDetector, PixelDetection, ScriptedDetector,  # noqa: E402
                        UltralyticsSegAdapter, polygon_area_centroid)
from map_rasters import MapError, MapGeo, OdmMap, OdmPaths  # noqa: E402
from map_synth import BASE_ELEV, ORTHO_RES, SIZE_M, build_scene  # noqa: E402
from map_tiles import Tile, iter_tiles, tile_grid  # noqa: E402


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    return build_scene(tmp_path_factory.mktemp("odm"))


@pytest.fixture
def om(scene):
    with OdmMap(OdmPaths.from_project(scene.root)) as m:
        yield m


# ----------------------------------------------------------------- rasters

def test_project_layout_and_crs(scene, om):
    p = OdmPaths.from_project(scene.root)
    assert p.dsm is not None and p.dtm is not None
    assert om.geo.crs.to_epsg() == 32755
    assert om.ortho_res_m == pytest.approx((ORTHO_RES, ORTHO_RES))


def test_geographic_crs_refused():
    with pytest.raises(MapError):
        MapGeo("EPSG:4326")


def test_mismatched_dem_crs_refused(scene, tmp_path):
    from rasterio.transform import from_origin
    bad = tmp_path / "dtm_mga.tif"
    with rasterio.open(bad, "w", driver="GTiff", width=10, height=10, count=1, dtype="float32",
                       crs="EPSG:7855", transform=from_origin(scene.x0, scene.y0 + 1, 0.1, 0.1)) as ds:
        ds.write(np.zeros((10, 10), np.float32), 1)
    with pytest.raises(MapError, match="differs"):
        OdmMap(OdmPaths(ortho=scene.paths["ortho"], dtm=bad))


def test_missing_file_refused(scene, tmp_path):
    with pytest.raises(MapError, match="not found"):
        OdmMap(OdmPaths(ortho=scene.paths["ortho"], dtm=tmp_path / "nope.tif"))


def test_latlon_round_trip_within_a_centimetre(scene, om):
    x, y = scene.utm(17.3, 23.9)
    lat, lon = om.geo.to_latlon(x, y)
    assert -35.37 < lat < -35.35 and 149.15 < lon < 149.18
    x2, y2 = om.geo.from_latlon(lat, lon)
    assert math.hypot(x2 - x, y2 - y) < 0.01


def test_dtm_bilinear_matches_plane(scene, om):
    for e, n in ((5.0, 5.0), (22.37, 31.11), (44.9, 2.2)):
        z = om.dtm.elev(*scene.utm(e, n))
        assert z == pytest.approx(scene.ground(e, n), abs=0.01)


def test_dem_nodata_and_off_map_are_none(scene, om):
    assert om.dtm.elev(*scene.utm(1.25, 45.25)) is None       # hole in the DEM
    assert om.dtm.elev(*scene.utm(-5.0, 10.0)) is None        # off the map


def test_dsm_box_max_sees_the_tree(scene, om):
    te0, tn0, te1, tn1, th = scene.tree
    top = om.dsm.max_in_box(*scene.utm(30.0, 26.0), 1.5)
    assert top == pytest.approx(scene.ground(te1, 24) + th, abs=0.05)


def test_canopy_height_aligned_to_ortho_grid(scene, om):
    # shrub: 1.5 m tall disc at (24, 40); ortho px = (480, 200)
    from rasterio.windows import Window
    chm = om.chm_window(Window(430, 150, 100, 100))
    assert chm.shape == (100, 100)
    assert float(np.nanmax(chm)) == pytest.approx(1.5, abs=0.05)
    assert float(chm[50, 50]) == pytest.approx(1.5, abs=0.05)   # shrub centre
    assert float(chm[2, 2]) == pytest.approx(0.0, abs=0.02)     # bare pasture
    assert np.nanmin(chm) >= 0.0


# ------------------------------------------------------------------- tiles

def test_tile_cores_partition_the_image_exactly():
    tiles = tile_grid(1000, 700, size=256, overlap=64)
    cover = np.zeros((700, 1000), np.int32)
    for t in tiles:
        assert t.window.col_off + t.window.width <= 1000
        assert t.window.row_off + t.window.height <= 700
        c0, r0, c1, r1 = (int(round(v)) for v in t.core)
        cover[r0:r1, c0:c1] += 1
    assert (cover == 1).all()


def test_small_raster_gives_one_tile():
    tiles = tile_grid(100, 80, size=640, overlap=128)
    assert len(tiles) == 1 and tiles[0].core == (0.0, 0.0, 100.0, 80.0)


def test_overlap_must_be_smaller_than_tile():
    with pytest.raises(ValueError):
        tile_grid(1000, 1000, size=64, overlap=64)


def test_iter_tiles_skips_empty_and_fills_channels(om):
    tiles = list(iter_tiles(om, size=256, overlap=64))
    n_grid = len(tile_grid(om.ortho.width, om.ortho.height, 256, 64))
    assert 0 < len(tiles) <= n_grid
    t = tiles[0]
    assert t.rgb.shape == (256, 256, 3) and t.rgb.dtype == np.uint8
    assert t.valid.shape == (256, 256) and t.chm.shape == (256, 256)


def test_alpha_zero_strip_is_invalid(om, scene):
    tiles = [t for t in iter_tiles(om, 256, 64) if t.window.col_off + t.window.width == om.ortho.width]
    strip_col = int(scene.nodata_strip_e / ORTHO_RES) - tiles[0].col_off
    assert not tiles[0].valid[:, strip_col + 2:].any()
    assert tiles[0].valid[:, : strip_col - 2].all()


# --------------------------------------------------------------- detectors

def test_polygon_area_centroid_square():
    a, cx, cy = polygon_area_centroid([(0, 0), (4, 0), (4, 2), (0, 2), (0, 0)])
    assert (a, cx, cy) == pytest.approx((8.0, 2.0, 1.0))


def test_colour_blob_finds_weeds_and_height_filter_drops_shrub(om, scene):
    det = ColourBlobDetector(min_area_px=8, max_canopy_h_m=0.6)
    found = []
    for t in iter_tiles(om, 256, 64):
        for d in det.detect(t):
            if t.owns(t.col_off + d.cx, t.row_off + d.cy):
                found.append(((t.col_off + d.cx) * ORTHO_RES, SIZE_M - (t.row_off + d.cy) * ORTHO_RES))
    assert len(found) == len(scene.weeds)              # every weed once, no shrub
    for we, wn, _ in scene.weeds.values():
        best = min(math.hypot(e - we, n - wn) for e, n in found)
        assert best < 0.05                             # within one ortho pixel


def test_without_height_filter_shrub_is_a_false_positive(om, scene):
    det = ColourBlobDetector(min_area_px=8)
    n = sum(1 for t in iter_tiles(om, 256, 64) for d in det.detect(t)
            if t.owns(t.col_off + d.cx, t.row_off + d.cy))
    assert n == len(scene.weeds) + 1


def test_scripted_detector():
    d = ScriptedDetector(lambda tile: [PixelDetection(1, 2, 0.9)])
    assert d.detect(None)[0].cy == 2


# ------------------------------------------------- Ultralytics adapter (no ultralytics needed)

class _FakeBoxes:
    def __init__(self, xyxy, conf, cls):
        self.xyxy, self.conf, self.cls = np.array(xyxy, float), np.array(conf), np.array(cls)


class _FakeMasks:
    def __init__(self, xy):
        self.xy = [np.array(p, float) for p in xy]


class _FakeResult:
    def __init__(self, boxes, masks=None, names=None):
        self.boxes, self.masks, self.names = boxes, masks, names or {0: "capeweed"}


class _FakeModel:
    def __init__(self, path):
        self.path, self.seen = path, []

    def predict(self, img, **kw):
        self.seen.append((img.copy(), kw))
        return [_FakeResult(_FakeBoxes([[10, 10, 20, 20]], [0.8], [0]),
                            _FakeMasks([[(10, 10), (20, 10), (20, 20), (10, 20)]]))]


@pytest.fixture
def weights(tmp_path):
    p = tmp_path / "weeds-seg.pt"
    p.write_bytes(b"not really a model")
    return p


def _tile(chm=True):
    rgb = np.zeros((32, 32, 3), np.uint8)
    rgb[..., 0], rgb[..., 1], rgb[..., 2] = 10, 20, 30
    t = Tile(index=(0, 0), window=None, core=(0, 0, 32, 32), rgb=rgb,
             valid=np.ones((32, 32), bool))
    if chm:
        t.chm = np.full((32, 32), 1.0, np.float32)
        t.chm[0, 0] = np.nan
    return t


def test_adapter_refuses_bare_model_name_never_downloads(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    called = []
    with pytest.raises(FileNotFoundError, match="never"):
        UltralyticsSegAdapter("yolo26n-seg.pt", yolo_factory=lambda p: called.append(p))
    with pytest.raises(FileNotFoundError):
        UltralyticsSegAdapter(None, yolo_factory=lambda p: called.append(p))
    assert called == []                    # model loader never reached


def test_adapter_rejects_bad_suffix_and_channel_order(tmp_path, weights):
    bad = tmp_path / "weights.zip"
    bad.write_bytes(b"x")
    with pytest.raises(ValueError):
        UltralyticsSegAdapter(bad, yolo_factory=_FakeModel)
    with pytest.raises(ValueError):
        UltralyticsSegAdapter(weights, channels=4, input_order="BGR", yolo_factory=_FakeModel)
    with pytest.raises(ValueError):
        UltralyticsSegAdapter(weights, channels=5, yolo_factory=_FakeModel)


def test_adapter_3ch_sends_bgr_and_parses_masks(weights):
    a = UltralyticsSegAdapter(weights, conf=0.3, yolo_factory=_FakeModel)
    dets = a.detect(_tile())
    img, kw = a.model.seen[0]
    assert img.shape == (32, 32, 3) and tuple(img[0, 0]) == (30, 20, 10)   # BGR
    assert kw["conf"] == 0.3 and kw["verbose"] is False
    assert len(dets) == 1
    d = dets[0]
    assert d.label == "capeweed" and d.confidence == pytest.approx(0.8)
    assert (d.cx, d.cy, d.area_px) == pytest.approx((15.0, 15.0, 100.0))
    assert d.extra["area_from"] == "mask"


def test_adapter_4ch_scales_canopy_height(weights):
    a = UltralyticsSegAdapter(weights, channels=4, chm_full_scale_m=2.0, yolo_factory=_FakeModel)
    img = a.build_input(_tile())
    assert img.shape == (32, 32, 4)
    assert img[5, 5, 3] == 127                         # 1.0 m of 2.0 m full scale
    assert img[0, 0, 3] == 0                           # NaN height -> 0
    with pytest.raises(ValueError, match="canopy"):
        a.build_input(_tile(chm=False))


def test_adapter_box_only_model_falls_back_to_box_area(weights):
    a = UltralyticsSegAdapter(weights, yolo_factory=_FakeModel)
    r = _FakeResult(_FakeBoxes([[0, 0, 4, 5], [1, 1, 2, 2]], [0.9, 0.6], [0, 0]), masks=None)
    dets = a.parse(r)
    assert [d.area_px for d in dets] == [20.0, 1.0]
    assert all(d.extra["area_from"] == "bbox" for d in dets)
    assert a.parse(_FakeResult(_FakeBoxes(np.zeros((0, 4)), [], []))) == []

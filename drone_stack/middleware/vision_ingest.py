"""Video ingestion for the OpenIPC -> YOLO bridge: frames in, newest frame out.

Contributed by Maya (AI agent, Sesame), an AI assistant made by Sesame,
drafted at G's request in October 2026. EXPERIMENTAL: the OpenCV / GStreamer readers
below have NOT been run against an OpenIPC camera. The replay reader and the
mailbox are covered by tests that need no camera, GPU or model download.

Interface (what the rest of the bridge relies on)
--------------------------------------------------
    Frame            one decoded picture plus everything known about WHEN it
                     was taken (see vision_clock.py for the clock domains).
    LatestFrameSlot  bounded mailbox between the reader thread and the
                     detector. Capacity 1 by default: if the detector is
                     slower than the camera, older frames are DROPPED (and
                     counted), never queued. A queue would add latency that
                     grows without bound, which is the opposite of what a
                     controller acting on "the newest picture" needs.
    FrameSource      anything with start(), stop(), status() that puts Frames
                     into a LatestFrameSlot: OpenCVStreamSource for RTSP /
                     RTP-UDP / a file, ReplayFrameSource for tests and benches.

Transports (from the OpenIPC docs summarised in docs/OPENIPC.md)
-----------------------------------------------------------------
    RTSP pull   rtsp://USER:PASS@CAMERA:554/stream=0 (main) or stream=1 (sub).
                Needs an IP route to the camera (bench, APFPV, bridged GS).
    RTP/UDP     Majestic outgoing push or WFB-NG ground station forwarding to
                UDP 5600, H.265 or H.264. OpenCV's pip wheels have no
                GStreamer; use an OpenCV build with GStreamer and the
                pipeline from gst_rtp_pipeline(), or have the GS restream RTSP.
    file        any video file OpenCV can open (bench replay of a recording).

Timing facts this module records, and what they are NOT
-------------------------------------------------------
    arrival_mono   GS_MONO taken right after the decoder returned the frame.
                   T2: this is AFTER network transfer, jitter buffering and
                   decode. It is not the capture time.
    stream_pts_s   decoder presentation timestamp (CAP_PROP_POS_MSEC / 1000).
                   OpenCV documents it as the position of the frame in the
                   stream; for a live RTSP / RTP source whether it is real,
                   monotonic and 1:1 with camera time is UNVERIFIED for
                   OpenIPC. Values <= 0 are recorded as None.
    epoch          increments on every (re)connect. pts are only comparable
                   within one epoch (T10).
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol
from urllib.parse import urlsplit, urlunsplit


# =============================================================== frame
@dataclass
class Frame:
    """One decoded frame and its timing facts.

    image        HxWx3 BGR numpy array (OpenCV convention) or None for
                 metadata-only replays.
    camera_wall_s  CAMERA_WALL capture time if some source provides it
                 (replay files today); None from the live readers.
    """
    seq: int
    image: Any
    width: int
    height: int
    arrival_mono: float
    stream_pts_s: Optional[float] = None
    camera_wall_s: Optional[float] = None
    epoch: int = 0
    source_id: str = "cam0"

    @property
    def frame_id(self) -> str:
        return f"{self.source_id}-e{self.epoch}-{self.seq}"


# =============================================================== mailbox
class LatestFrameSlot:
    """Bounded, thread-safe, newest-wins mailbox.

    put() never blocks: when full, the OLDEST frame is discarded and counted
    in `dropped`. get() returns the NEWEST frame and empties the slot, so a
    slow consumer always works on the most recent picture. Capacity > 1 is
    allowed (e.g. to look at two consecutive frames) but get() still returns
    only the newest and discards the rest.

    Timing issue T13: the effective published rate is the detector's rate,
    not the camera's. Frames overwritten here are counted in `dropped`; a
    frame that still waited longer than pipeline.max_frame_age_s is dropped
    by the bridge as waited_too_long.
    """

    def __init__(self, capacity: int = 1):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._items: List[Frame] = []
        self._cv = threading.Condition()
        self.put_count = 0
        self.dropped = 0          # overwritten before anyone read them
        self.discarded_on_get = 0
        self._closed = False

    def put(self, frame: Frame) -> None:
        with self._cv:
            self._items.append(frame)
            self.put_count += 1
            while len(self._items) > self.capacity:
                self._items.pop(0)
                self.dropped += 1
            self._cv.notify_all()

    def get(self, timeout: Optional[float] = None) -> Optional[Frame]:
        with self._cv:
            if not self._items and not self._closed:
                self._cv.wait(timeout)
            if not self._items:
                return None
            newest = self._items[-1]
            self.discarded_on_get += len(self._items) - 1
            self._items.clear()
            return newest

    def __len__(self) -> int:
        with self._cv:
            return len(self._items)

    def close(self):
        with self._cv:
            self._closed = True
            self._cv.notify_all()


class FrameSource(Protocol):
    def start(self) -> "FrameSource": ...
    def stop(self) -> None: ...
    def status(self) -> Dict[str, Any]: ...


# =============================================================== helpers
def redact_url(url: str) -> str:
    """Hide the password in rtsp://user:pass@host/... for logs and status."""
    try:
        p = urlsplit(url)
    except ValueError:
        return "<unparseable url>"
    if p.password is None:
        return url
    host = p.hostname or ""
    if p.port:
        host += f":{p.port}"
    return urlunsplit((p.scheme, f"{p.username}:***@{host}", p.path, p.query, p.fragment))


def expand_url_env(url: str) -> str:
    """Allow `rtsp://root:${OPENIPC_PASSWORD}@...` so the password lives in the
    environment, not in the config file."""
    return os.path.expandvars(url)


def gst_rtp_pipeline(port: int = 5600, codec: str = "h265", latency_ms: int = 0) -> str:
    """GStreamer pipeline string for OpenCV (cv2.CAP_GSTREAMER) receiving RTP on UDP.

    Based on the APFPV example in docs/OPENIPC.md (udpsrc port=5600 !
    application/x-rtp ! rtph265depay ! avdec_h265), ending in an appsink that
    keeps only the newest buffer (drop=true max-buffers=1 sync=false) so the
    pipeline itself does not queue frames. UNTESTED here: the pip OpenCV wheels
    are built without GStreamer.
    latency_ms > 0 inserts an rtpjitterbuffer (reorders late packets at the
    cost of that much added latency).
    """
    codec = codec.lower()
    if codec not in ("h264", "h265"):
        raise ValueError("codec must be h264 or h265")
    enc = "H265" if codec == "h265" else "H264"
    jb = f"rtpjitterbuffer latency={latency_ms} ! " if latency_ms > 0 else ""
    return (f"udpsrc port={port} caps=\"application/x-rtp,media=video,clock-rate=90000,"
            f"encoding-name={enc}\" ! {jb}rtp{codec}depay ! {codec}parse ! avdec_{codec} ! "
            "videoconvert ! video/x-raw,format=BGR ! appsink drop=true max-buffers=1 sync=false")


# =============================================================== live reader
class OpenCVStreamSource:
    """Reads RTSP / RTP / file video with OpenCV in its own thread.

    * Reads as fast as the stream delivers and puts every decoded frame into
      the slot; the slot keeps only the newest. Reading continuously also
      stops OpenCV's internal buffer from filling with old frames.
    * Disconnection: a failed read closes the capture, marks the source
      disconnected, waits `reconnect_backoff_s` (doubling up to
      `max_backoff_s`) and reopens with a NEW epoch.
    * A stalled stream (no frame for `stall_s`) is reported in status(); the
      bridge then publishes nothing, and the controller stops after its own
      frame timeout (3 s by default).
    * FFmpeg options for RTSP (transport, timeouts, low-delay flags) go in
      through the OPENCV_FFMPEG_CAPTURE_OPTIONS environment variable, which
      OpenCV reads when it opens a capture.

    `capture_factory(url_or_pipeline, api_preference)` is injectable so tests
    can run the reconnect logic with a fake capture.
    """

    def __init__(self, url: str, slot: LatestFrameSlot, *, backend: str = "ffmpeg",
                 source_id: str = "cam0", rtsp_transport: str = "udp",
                 open_timeout_s: float = 5.0, read_timeout_s: float = 2.0,
                 reconnect_backoff_s: float = 0.5, max_backoff_s: float = 5.0,
                 stall_s: float = 1.0, capture_factory: Optional[Callable] = None,
                 mono: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        if backend not in ("ffmpeg", "gstreamer", "any"):
            raise ValueError("backend must be ffmpeg, gstreamer or any")
        self.url = expand_url_env(url)
        self.slot, self.backend, self.source_id = slot, backend, source_id
        self.rtsp_transport = rtsp_transport
        self.open_timeout_s, self.read_timeout_s = open_timeout_s, read_timeout_s
        self.reconnect_backoff_s, self.max_backoff_s = reconnect_backoff_s, max_backoff_s
        self.stall_s = stall_s
        self._factory = capture_factory
        self._mono, self._sleep = mono, sleep
        self.epoch = -1
        self.seq = 0
        self.connected = False
        self.reconnects = 0
        self.read_failures = 0
        self.last_error = ""
        self.last_frame_mono: Optional[float] = None
        self.pts_missing = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---------------------------------------------------------- opening
    def _open(self):
        if self._factory is not None:
            return self._factory(self.url, self.backend)
        import cv2  # lazy: tests and replay do not need OpenCV
        if self.backend == "ffmpeg":
            if self.url.startswith("rtsp"):
                # Low-delay demux, chosen RTSP transport, socket timeout (us).
                # REVIEW: older FFmpeg calls the RTSP socket timeout
                # "stimeout", newer releases "timeout"; which one the GS build
                # honours is unverified. Set OPENCV_FFMPEG_CAPTURE_OPTIONS
                # yourself to override this default completely.
                os.environ.setdefault(
                    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
                    f"rtsp_transport;{self.rtsp_transport}|fflags;nobuffer|flags;low_delay|"
                    f"stimeout;{int(self.read_timeout_s * 1e6)}")
            api = cv2.CAP_FFMPEG
        elif self.backend == "gstreamer":
            api = cv2.CAP_GSTREAMER
        else:
            api = cv2.CAP_ANY
        params = []
        for name, val in (("CAP_PROP_OPEN_TIMEOUT_MSEC", self.open_timeout_s),
                          ("CAP_PROP_READ_TIMEOUT_MSEC", self.read_timeout_s)):
            if hasattr(cv2, name):           # present in recent OpenCV releases
                params += [getattr(cv2, name), int(val * 1000)]
        return cv2.VideoCapture(self.url, api, params) if params else cv2.VideoCapture(self.url, api)

    @staticmethod
    def _pts_s(cap) -> Optional[float]:
        try:
            import cv2
            prop = cv2.CAP_PROP_POS_MSEC
        except Exception:
            prop = 0                          # CAP_PROP_POS_MSEC == 0 in OpenCV
        try:
            v = float(cap.get(prop))
        except Exception:
            return None
        return v / 1000.0 if v > 0 else None

    # ---------------------------------------------------------- loop
    def start(self) -> "OpenCVStreamSource":
        self._thread = threading.Thread(target=self._run, name=f"video-{self.source_id}",
                                        daemon=True)
        self._thread.start()
        return self

    def run_once(self, max_frames: Optional[int] = None) -> int:
        """One connect-read-until-failure cycle (used by the thread and by tests).
        Returns the number of frames read."""
        n = 0
        cap = None
        try:
            cap = self._open()
            if cap is None or not cap.isOpened():
                self.last_error = "could not open video source " + redact_url(self.url)
                return 0
            self.epoch += 1
            self.connected = True
            while not self._stop.is_set():
                ok, img = cap.read()
                arrival = self._mono()        # T2: after decode, not capture
                if not ok or img is None:
                    self.read_failures += 1
                    self.last_error = "read failed (stream ended or link lost)"
                    break
                self.seq += 1
                pts = self._pts_s(cap)
                if pts is None:
                    self.pts_missing += 1
                h, w = img.shape[:2]
                self.slot.put(Frame(self.seq, img, int(w), int(h), arrival, pts,
                                    None, self.epoch, self.source_id))
                self.last_frame_mono = arrival
                n += 1
                if max_frames is not None and n >= max_frames:
                    break
        except Exception as e:                # never let the reader thread die silently
            self.last_error = f"{type(e).__name__}: {e}"
        finally:
            self.connected = False
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
        return n

    def _run(self):
        backoff = self.reconnect_backoff_s
        while not self._stop.is_set():
            got = self.run_once()
            if self._stop.is_set():
                break
            self.reconnects += 1
            backoff = self.reconnect_backoff_s if got else min(backoff * 2, self.max_backoff_s)
            self._sleep(backoff)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.read_timeout_s + 1.0)

    def status(self) -> Dict[str, Any]:
        now = self._mono()
        age = None if self.last_frame_mono is None else now - self.last_frame_mono
        return {"source": redact_url(self.url), "backend": self.backend,
                "connected": self.connected, "epoch": self.epoch, "frames": self.seq,
                "reconnects": self.reconnects, "read_failures": self.read_failures,
                "last_error": self.last_error, "pts_missing": self.pts_missing,
                "last_frame_age_s": None if age is None else round(age, 3),
                "stalled": age is None or age > self.stall_s,
                "slot_dropped": self.slot.dropped}


# =============================================================== replay
@dataclass
class ReplayEvent:
    """One scripted step for ReplayFrameSource.

    kind "frame": deliver a frame at arrival offset `at_s` (seconds after
         start, GS_MONO) with optional pts / camera time / image.
    kind "disconnect": the link drops; the next frame starts a new epoch.
    """
    at_s: float
    kind: str = "frame"
    pts_s: Optional[float] = None
    camera_wall_s: Optional[float] = None
    image: Any = None
    width: int = 640
    height: int = 480


class ReplayFrameSource:
    """Deterministic frame source for tests, benches and recorded sessions.

    step() delivers the next scripted event immediately (no sleeping), so tests
    control time exactly. run_realtime() plays the script on the real clock.
    load_jsonl() reads a timing script saved from a real session: one JSON
    object per line with at_s, kind, pts_s, camera_wall_s, width, height and
    optionally `image` (a path, loaded with OpenCV if available).
    `image_fn(event, seq)` can render an image on demand (tests draw a target).
    """

    def __init__(self, events: Iterable[ReplayEvent], slot: LatestFrameSlot, *,
                 source_id: str = "replay", t0_mono: float = 0.0,
                 image_fn: Optional[Callable[[ReplayEvent, int], Any]] = None):
        self.events = list(events)
        self.slot, self.source_id, self.t0 = slot, source_id, t0_mono
        self.image_fn = image_fn
        self.i = 0
        self.seq = 0
        self.epoch = 0
        self.connected = True
        self.disconnects = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def step(self) -> Optional[Frame]:
        while self.i < len(self.events):
            ev = self.events[self.i]
            self.i += 1
            if ev.kind == "disconnect":
                self.connected = False
                self.disconnects += 1
                continue
            if not self.connected:
                self.epoch += 1
                self.connected = True
            self.seq += 1
            img = self.image_fn(ev, self.seq) if self.image_fn else ev.image
            if isinstance(img, str):
                img = _load_image(img)
            w, h = ev.width, ev.height
            if img is not None and hasattr(img, "shape"):
                h, w = img.shape[:2]
            f = Frame(self.seq, img, int(w), int(h), self.t0 + ev.at_s, ev.pts_s,
                      ev.camera_wall_s, self.epoch, self.source_id)
            self.slot.put(f)
            return f
        return None

    def start(self) -> "ReplayFrameSource":
        self._thread = threading.Thread(target=self.run_realtime, daemon=True,
                                        name=f"replay-{self.source_id}")
        self._thread.start()
        return self

    def run_realtime(self, mono: Callable[[], float] = time.monotonic):
        start = mono()
        self.t0 = start
        while not self._stop.is_set() and self.i < len(self.events):
            wait = self.events[self.i].at_s - (mono() - start)
            if wait > 0:
                time.sleep(min(wait, 0.05))
                continue
            # Live arrival time, not the scripted one, when playing in real time.
            ev = self.events[self.i]
            ev_at = ev.at_s
            ev.at_s = mono() - start
            self.step()
            ev.at_s = ev_at
        self.connected = False

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def status(self) -> Dict[str, Any]:
        return {"source": self.source_id, "connected": self.connected, "epoch": self.epoch,
                "frames": self.seq, "remaining": len(self.events) - self.i,
                "disconnects": self.disconnects, "slot_dropped": self.slot.dropped}

    @classmethod
    def load_jsonl(cls, path: str, slot: LatestFrameSlot, **kw) -> "ReplayFrameSource":
        base = os.path.dirname(os.path.abspath(path))
        evs = []
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                d = json.loads(line)
                img = d.get("image")
                if isinstance(img, str) and not os.path.isabs(img):
                    img = os.path.join(base, img)
                evs.append(ReplayEvent(at_s=float(d["at_s"]), kind=d.get("kind", "frame"),
                                       pts_s=d.get("pts_s"), camera_wall_s=d.get("camera_wall_s"),
                                       image=img, width=int(d.get("width", 640)),
                                       height=int(d.get("height", 480))))
        return cls(evs, slot, **kw)


def _load_image(path: str):
    try:
        import cv2
    except Exception as e:  # pragma: no cover - depends on the machine
        raise RuntimeError("replaying image files needs OpenCV (pip install "
                           "opencv-python-headless)") from e
    img = cv2.imread(path)
    if img is None:
        raise FileNotFoundError(path)
    return img

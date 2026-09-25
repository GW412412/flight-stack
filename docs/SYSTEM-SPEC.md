# System spec: camera + radio for precision spot-spraying

Concrete end-to-end video and radio choices for this project. Decisions are locked
from notes in [OPENIPC-CAMERAS.md](OPENIPC-CAMERAS.md), [OPENIPC.md](OPENIPC.md),
[WEED-ID.md](WEED-ID.md), and standard OpenIPC / WFB-NG practice. Not a flight
manual; tune legally for the operating country.

---

## Hard constraints (design targets)

| # | Constraint | Target used in this spec |
|---|------------|--------------------------|
| 1 | **Maximum practical wireless range** (low-altitude hill-country spray, ~5–15 m AGL) | **~600–800 m** RF video on **5 GHz** with clear LOS / mild foliage; treat **>1 km** as unreliable on Wi‑Fi video at spray height |
| 2 | **End-to-end latency** for real-time weed ID + spray trigger | **Video path ≤ ~80–120 ms** air→GS decode; **spray command on MAVLink/ELRS telem**, not on the video RF path (command RTT typically tens of ms) |
| 3 | **Bitrate vs camera resolution** | Enough for **720p** ID frames from the chosen **5 MP IMX335** (downscaled in Majestic), without saturating a 20 MHz WFB-NG link |

Rationale: at spray height, 5 GHz Fresnel and terrain block long digital-FPV links; OpenIPC docs cite APFPV ~35–70 ms and WFB-NG as the lower-latency airborne path. Live spray decisions must not wait on a round-trip through a high-bitrate 1080p90 encode.

---

## Chosen hardware

### Camera / air unit — **OpenIPC Thinker Air Unit (Base) + IMX335**

| Item | Choice |
|------|--------|
| Air unit | **OpenIPC Thinker (Base)** |
| Sensor | **IMX335** (5 MP; OpenIPC’s preferred IQ on Mario/Thinker kits) |
| Mass | ~**8.8–13.4 g** with heatsink (from camera notes) |
| Power | 2–6S via integrated 3 A BEC |

**Why this over Mario / WiFiLink 2:** lightest official shortlist kit for a compact sprayer; IMX335 over IMX415 for IQ; Base + external Wi‑Fi card lets us pick **RTL8812AU** and **5 GHz** power independently of a soldered Tiny radio (~50 mW). Mario is heavier; WiFiLink 2 is IMX415 and ~25–30 g.

### Radio / ground — **WFB-NG + RTL8812AU (air and GS), 5 GHz**

| Role | Choice |
|------|--------|
| Air Wi‑Fi | **RTL8812AU** USB on Thinker Base (prefer AU over EU so **40 MHz** WFB-NG remains available if needed; this spec defaults to **20 MHz**) |
| Band | **5 GHz** only for video (example centre **5700 MHz** / clean DFS-legal channel for the region) |
| Link mode | **WFB-NG** (monitor-mode wifibroadcast) as primary |
| Ground station | SBC or laptop **GS with matching RTL8812AU** (PixelPilot or OpenIPC SBC GS); restream locally to FFmpeg / weed-ID |
| IP tooling bridge | On GS: decode WFB-NG → local **RTP/UDP :5600** or **RTSP restream** for `scripts/extract_frames.py` |
| RC | **ELRS on 2.4 GHz** (unchanged; separate antennas) |
| RTK | Separate sub‑GHz radio or **NTRIP** — not on the video Wi‑Fi |

**Why WFB-NG over APFPV alone:** APFPV is simpler for bare RTSP (`rtsp://…`) but docs put latency in the **35–70 ms** class and call it not racing-grade; WFB-NG is the OpenIPC FPV path aimed at lower latency airborne video. For FFmpeg we **restream on the GS** rather than expecting a raw RTSP URL over WFB-NG RF.

**Why not 2.4 GHz video:** would sit on ELRS; OpenIPC notes warn against both on 2.4 on a compact airframe.

---

## Locked video settings (Majestic / WFB-NG)

| Setting | Value | Rationale |
|---------|-------|-----------|
| **Codec** | **H.264** (main); H.265 optional only if GS has reliable hardware decode | Real-time weed-ID on the project’s older **Tesla K80 / CPU** GS: H.264 is safer; Frigate/OpenIPC notes HEVC software stutter |
| **Resolution** | **1280×720** (main stream) | Enough GSD for rosette cues at low AGL once tiled; far less bitrate than 1080p90; IMX335 can supply this easily |
| **Framerate** | **30 fps** | Smooth enough for tracking / ID; 90 fps burns bitrate and decode without helping spray timing |
| **Target bitrate** | **6 Mbps** average (encoder cap **8 Mbps**) | Fits 720p30 H.264 with headroom under WFB-NG ~36 Mbps video-class links; leaves margin for hills / MCS drops |
| **GOP / keyframe** | **1 s** (e.g. GOP = fps) | Limits decode catch-up delay after brief fades |
| **Substream (optional HUD)** | **640×360 @ 15 fps, ~1.5 Mbps** H.264 | Operator / low-rate monitoring without touching the ID main stream |

### Wireless PHY

| Setting | Value | Rationale |
|---------|-------|-----------|
| **Channel width** | **20 MHz** | Better range/robustness than 40 MHz at low altitude; EU RTL8812 cards often lack WFB-NG 40 MHz anyway |
| **Band / channel** | **5 GHz**, fixed clean channel (set via OpenIPC env, e.g. `wlanfreq` style config; avoid crowded DFS if unsupported) | Coexists with ELRS 2.4; APFPV docs: prefer 5 GHz |
| **Transmit power** | **Region-legal maximum** on the 8812 PA; never exceed local rules. Bench/pit start **low (~25 mW class)** then raise only with antennas fitted | OpenIPC warns stock pit power and legal limits; WiFiLink-class PAs cite **28 dBm FCC / 20 dBm CE** as order-of-magnitude ceiling for similar cards — **verify the exact 8812 module** |
| **Antennas** | 2× air (MIMO/diversity) + 1–2× GS; **linear polarisation matched**; ≥ few cm from ELRS antennas | Required for MIMO 8812; never power PA without antennas |

### Fallback behaviour (automatic / operator)

Ordered degradation when SNR or packet loss rises (implement in Majestic bitrate + GS logic):

1. **Hold** 720p30 but drop encoder to **3 Mbps**.  
2. Drop to **960×540 @ 20 fps, 2 Mbps**.  
3. Drop to **640×360 @ 10 fps, 1 Mbps** (ID quality degraded — spray only on high-confidence or pause auto-spray).  
4. **Freeze live ID**; switch to **periodic `/image.jpg` stills** or **SD recording** for post-flight ODM / weed map; spray only from **precomputed waypoints**.  
5. If video RF is lost: **sprayer safe** (pump off / Lua RTL-off scripts as configured); continue ELRS + RTK control.

Do **not** fail open on spray when video dies.

---

## End-to-end data path (real-time ID + spray)

```
IMX335 → Thinker SSC338Q + Majestic (H.264 720p30 @ ~6 Mbps)
      → RTL8812AU @ 5 GHz, WFB-NG 20 MHz
      → GS RTL8812AU → WFB-NG decode
      → local restream (RTP :5600 or RTSP)
      → weed-ID process (tile/ROI; see WEED-ID.md)
      → spray decision
      → MAVLink / telem to Pixhawk (AC_Sprayer / servo / Lua)
      → ELRS carries RC; video RF never carries RC
```

**Latency budget (design intent):**

| Segment | Budget |
|---------|--------|
| Capture + encode | ~20–40 ms |
| WFB-NG air→GS | ~20–40 ms (link quality dependent) |
| Decode + ID (ROI, not full ortho) | ~20–40 ms on a healthy GS |
| MAVLink spray command | ~10–30 ms |
| **Total aim** | **≲ 100–150 ms** decision loop |

Full-orthomosaic ODM is **offline** (not in this loop). Live ID runs on **stream tiles / ROIs**, not on a completed ODM GeoTIFF.

---

## Mapping / ODM (non-real-time)

For OpenDroneMap and offline weed maps: prefer **onboard SD stills** or `extract_frames.py` at **0.5–1 fps** from a GS restream / APFPV bench link — not the full 6 Mbps 30 fps path. Continuous high-rate video is unnecessary for orthomosaics ([OPENIPC.md](OPENIPC.md) §5).

---

## Explicit non-choices

| Not chosen | Why |
|------------|-----|
| Mario AIO as primary | Heavier; Thinker Base meets mass better |
| RunCam WiFiLink 2 | IMX415 + mass; keep as retail spare only |
| APFPV as primary RF | Higher / less FPV-oriented latency; use only as **bench / IP tooling** fallback |
| 1080p90 @ ~20 Mbps | Breaks range margin and ID latency on the K80-class GS |
| Video + ELRS both on 2.4 GHz | Desense risk on compact airframe |
| Spray commands over Wi‑Fi video | Wrong reliability domain; use FC telem |

---

## Acceptance checks (before field spray with auto trigger)

1. Antennas on; 5 GHz channel clear; ELRS RSSI healthy with video TX at flight power.  
2. Measure GS glass-to-glass or encode-to-decode latency; confirm **≲ 120 ms** video before enabling auto-spray.  
3. Confirm fallback steps pull bitrate down under attenuation (tree line / behind hill).  
4. Confirm pump **off** on video-loss / failsafe.  
5. `ffprobe` / `extract_frames.py` against **GS-local** RTSP/UDP restream, not against a fantasy RF-RTSP URL.

---

## Summary one-liner

**Thinker Base + IMX335 → H.264 720p30 @ ~6 Mbps → RTL8812AU WFB-NG 20 MHz on 5 GHz → GS restream → ROI weed-ID → MAVLink spray; ELRS 2.4 + separate RTK; degrade bitrate then stills/waypoints on link stress.**

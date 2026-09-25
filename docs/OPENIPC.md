# OpenIPC for precision spot-spraying (research notes)

Local research notes for using **OpenIPC** as a camera / video-link option on a
fixed-wing or compact spot-sprayer airframe. Content is compiled from official
OpenIPC docs and wiki pages (URLs below). Not a vendor recommendation.

> **Scope:** live camera over Wi‑Fi, ground-station ingest for FFmpeg / weed-ID /
> ODM, and RF coexistence with ELRS + RTK.

---

## 1. How OpenIPC works for live camera over Wi‑Fi

OpenIPC is a **Buildroot-based firmware ecosystem** for IP-camera SoCs, plus a
lower-latency **FPV / URLLC** stack for drones. Video is produced by the
**Majestic** streamer (binary; Lite / Ultimate / FPV flavours) running on the
camera SoC.

### Two deployment shapes (do not confuse them)

| Mode | What it is | Typical transport | Best for |
|------|------------|-------------------|----------|
| **IP-camera / LAN** | Flashed IP camera on Ethernet or consumer Wi‑Fi AP | RTSP, WebRTC, HLS, MJPEG, JPEG stills, RTP push | Bench, short-range laptop pull, NVR / Frigate |
| **FPV air unit** | Camera SoC + Realtek Wi‑Fi card as VTX | **WFB-NG** (wifibroadcast / monitor-mode) or **APFPV** (VTX as Wi‑Fi AP) | Airborne video to PixelPilot / SBC GS / phone |

Sources: [Ecosystem](https://openipc.org/ecosystem),
[Majestic streamer wiki](https://github.com/OpenIPC/wiki/blob/master/en/majestic-streamer.md),
[WFB-NG quick start](https://docs.openipc.org/use-cases/fpv/wfb-ng/-quick-start/),
[APFPV guide](https://docs.openipc.org/use-cases/fpv/apfpv/apfpv/).

### Protocols Majestic exposes

Documented in the Majestic wiki and Frigate howto:

- **RTSP** — default port **554**; paths `stream=0` (main) and `stream=1` (sub).
- **RTP over UDP / unix** — `outgoing.servers` entries like `udp://RECEIVER_IP:5600`.
- **WebRTC** — browser Preview (Lite/Ultimate; **not** in FPV flavour).
- **HLS** — `/master.m3u8` when enabled.
- **MJPEG** — `/mjpeg`; **JPEG stills** — `/image.jpg` (query params for size/quality/crop on supported builds).
- **RTMP(S)** push — Lite/Ultimate only.

Exact copy-paste URLs for a given camera live under **Majestic → Endpoints** in
the camera WebUI: [Majestic Endpoints](https://openipc.org/majestic-endpoints).

### Typical bitrates (from published configs / docs — not guarantees)

| Context | Figures found in docs / examples |
|---------|----------------------------------|
| Surveillance-style Majestic | Example `video0.bitrate: 4096` (kbps) at ~20 fps — [majestic-config](https://github.com/OpenIPC/wiki/blob/master/en/majestic-config.md) |
| FPV-oriented Majestic sample | Example `1080p`, `fps: 90`, `bitrate: 20480` (kbps), H.265 — [configurator majestic.yaml](https://github.com/OpenIPC/configurator/blob/master/majestic.yaml) |
| WFB-NG on RTL8812AU | Community measurement cited in docs: **~52 Mbps total / ~36 Mbps video** (2025-04-21) — [RTL8812AU](https://docs.openipc.org/use-cases/fpv/net-cards/rtl8812au/) |
| APFPV adaptive link (APALink) | Example `bitrate_max` **10–22 Mbps**, fallback **2 Mbps** — [APFPV guide](https://docs.openipc.org/use-cases/fpv/apfpv/apfpv/) |
| APFPV latency | Docs state roughly **35–70 ms** (not racing-grade) |

Architecture (airborne FPV):

```
Sensor (e.g. IMX335/415) → SoC + Majestic encode (H.264/H.265)
        → Wi‑Fi card (RTL8812AU/EU …) → RF
        → Ground RX (PixelPilot / SBC GS / laptop Wi‑Fi)
        → display and/or RTP/UDP / browser
```

---

## 2. Hardware needed

### Supported camera SoCs (IP-camera firmware)

OpenIPC’s **recommended** list includes (among others):

- **Goke:** GK7205V200 / V210 / V300, GK7605V100  
- **HiSilicon:** HI3516EV200 / EV300  
- **SigmaStar:** SSC30KD / SSC30KQ, SSC333 / 335 / 337 / 338Q, SSC377 / 378 series  

Full / featured lists: [Featured SoCs](https://openipc.org/supported-hardware/featured),
[Full list](https://openipc.org/supported-hardware/full-list).  
Identify SoC + sensor + flash before flashing: [Installation wiki](https://github.com/OpenIPC/wiki/blob/master/en/installation.md),
[ipctool](https://github.com/OpenIPC/ipctool).

### FPV air-unit hardware (OpenIPC docs)

From the [WFB-NG quick start](https://docs.openipc.org/use-cases/fpv/wfb-ng/-quick-start/):

- **Sensors commonly called out:** IMX335 (higher IQ / up to 1080p@120), IMX415 (e.g. 1080p@90).  
- **SoCs commonly called out:** **SSC338Q** (preferred), **SSC30KQ** (cut-down).  
- **Video radios:** **RTL8812EU** or **RTL8812AU** (most sold air units EU-based; EU lacks WFB-NG 40 MHz mode).  
- Always attach antennas before powering Wi‑Fi modules; cool air units on the bench.

APFPV Wi‑Fi chips on VTX: RTL8812AU, RTL8812EU, RTL8733BU —
[APFPV](https://docs.openipc.org/use-cases/fpv/apfpv/apfpv/).

### Companion board vs camera-onboard

- **Camera-onboard (typical FPV kit):** sensor + SoC + Wi‑Fi card as one **air unit / VTX**, HD-plug to flight controller — no separate companion required for video.  
- **Companion board:** optional for MAVLink / spray logic / onboard ML; video can still be OpenIPC air-unit **or** an IP camera + Ethernet to the companion.  
- Do **not** assume a random USB webcam on a Pi replaces an OpenIPC SoC path for low-latency RF video.

### Ground station receive side

| GS option | Notes (from OpenIPC FPV docs) |
|-----------|-------------------------------|
| **PixelPilot (PP)** | Android (Snapdragon); RTL8812AU USB NIC; Meta Quest tested |
| **SBC GS** | e.g. Radxa Zero 3W/3E; RTL8812AU / **8812EU**; premade: RunCam WiFiLink-RX, Emax Wyvern Link VRX |
| **APFPV laptop / phone** | Join VTX SSID (default examples: SSID `OpenIPC`, pass `12345678`); browser at `http://192.168.0.1`; RTP UDP port **5600** |
| **aviateur / PixelPilot_rk** | Cross-platform / Rockchip RX projects in the [ecosystem](https://openipc.org/ecosystem) |

---

## 3. Stream to ground station for FFmpeg frame extraction?

### Yes — when you have an IP path (RTSP or RTP/UDP)

OpenIPC’s own Frigate guide documents FFmpeg-compatible RTSP:

```text
rtsp://<user>:<pass>@<camera-ip>:554/stream=0    # main
rtsp://<user>:<pass>@<camera-ip>:554/stream=1    # sub
```

Verify:

```sh
ffprobe -v error -rtsp_transport tcp -i \
  "rtsp://root:YOUR_PASSWORD@192.168.1.10:554/stream=0"
```

Source: [howto-frigate-integration](https://github.com/OpenIPC/wiki/blob/master/en/howto-frigate-integration.md).

**APFPV** documents RTP/H.265 reception (GStreamer example; same UDP port pattern works with tools that understand RTP):

```sh
gst-launch-1.0 udpsrc port=5600 ! application/x-rtp ! rtph265depay ! \
  avdec_h265 ! fpsdisplaysink sync=false
```

Majestic can also **push** RTP: `outgoing.servers: [ udp://GS_IP:5600 ]` —
[majestic-config](https://github.com/OpenIPC/wiki/blob/master/en/majestic-config.md).

This repo’s `scripts/extract_frames.py` is built for RTSP/UDP URLs consumed by
ffmpeg — use the RTSP forms above when the GS has an IP route to the camera
(APFPV station mode, Ethernet bench, or companion bridge).

### Caveats

- **WFB-NG** is a monitor-mode broadcast link decoded by PixelPilot / SBC GS
  software — not a plain “open RTSP URL over the radio.” For FFmpeg, prefer
  **APFPV**, a bridged IP interface on the GS, or pull stills/RTSP after the GS
  restreams locally.
- Prefer **H.264** if the GS lacks hardware H.265 decode; Frigate guide notes
  software HEVC stutter.
- Use TCP for RTSP across lossy Wi‑Fi when pulling (`-rtsp_transport tcp`).

**Stills without a continuous decode pipeline:**

```text
http://CAMERA_IP/image.jpg
http://CAMERA_IP/image.jpg?width=1920&height=1080&qfactor=80
```

Documented in [majestic-streamer](https://github.com/OpenIPC/wiki/blob/master/en/majestic-streamer.md).

---

## 4. RF / link count (ELRS + RTK + video)

OpenIPC does **not** replace RC or RTK. A typical precision-spray stack is
**three separate RF roles**:

| Role | Typical radio | Common bands |
|------|---------------|--------------|
| Flight control (ELRS) | ELRS TX ↔ RX on FC | **2.4 GHz** (also 900 MHz variants exist — pick one) |
| RTK / GPS corrections | RTK radio, or telemetry carrying RTCM, or NTRIP via LTE | Often **433 / 868 / 915 MHz**, or cellular — **not** the video Wi‑Fi |
| Video (OpenIPC) | RTL8812AU/EU air + GS NIC | Prefer **5 GHz** Wi‑Fi channels; cards are dual-band |

**Antenna / radio count (typical):**

1. ELRS aircraft antenna (+ ground TX antenna)  
2. GNSS / RTK antenna(s) on airframe (+ base or NTRIP — no air video radio)  
3. Video Wi‑Fi: often **2×** antennas on air (MIMO / diversity on 8812) and **1–2×** on GS  

So expect **≥3 RF systems** and **several antennas**, not “one radio does all.”
Do not piggyback RC on the OpenIPC Wi‑Fi link for a spray aircraft.

---

## 5. Bandwidth: live video vs stills / periodic frames (ODM / weed-ID)

| Need | Fit | Bandwidth character |
|------|-----|---------------------|
| Pilot FPV / aim boom / confirm spray | Live H.264/H.265 | Continuous multi‑Mbps (see §1); competes with range |
| ODM orthomosaic / weed-ID map | **High-res stills** or onboard SD recording | Burst JPEGs or offline files — far less sustained RF |
| Live weed-ID on GS | Substream or 1 fps extract via FFmpeg | Modest if capped (e.g. substream 640×360 @ 5–15 fps) |

**Stills-over-Wi‑Fi are enough** for mapping / ODM / offline weed-ID if:

- you poll `/image.jpg` (or record MP4/JPEG to the camera SD and retrieve later), and  
- GPS/RTK tags attach on the companion or in post.

Continuous full-rate 1080p90 @ 10–20 Mbps is **unnecessary** for orthomosaic
pipelines and burns link margin. Prefer: **onboard record + periodic stills**,
and keep live video at a **low bitrate substream** for the operator.

---

## 6. Coexistence with ELRS on 2.4 GHz

OpenIPC FPV Wi‑Fi cards (**RTL8812AU/EU**) are **dual-band**. Official APFPV
guidance: **“Use 5GHz WiFi when possible (less crowded than 2.4GHz)”** and set
frequency via env (example `fw_setenv wlanfreq 5700`) —
[APFPV](https://docs.openipc.org/use-cases/fpv/apfpv/apfpv/).

**Do:**

- Put **video on 5 GHz**, keep **ELRS on 2.4 GHz** (or move ELRS to 900 MHz if that is your regional setup).  
- Physically separate video and RC antennas; never power Wi‑Fi PA without antennas.  
- Pick a clean 5 GHz channel; city Wi‑Fi congestion is called out in the WFB-NG guide.  
- Respect legal TX power (docs warn stock ~25 mW pit; higher power needs local compliance).

**Don’t:**

- Run OpenIPC video and ELRS both on **2.4 GHz** on a compact airframe without testing — desense and packet loss are expected when antennas are close.  
- Assume “Wi‑Fi video” is as robust as a dedicated digital FPV system without failovers.  
- Share one antenna across RC and video.

Practical layout for this project: **ELRS 2.4 + RTK on sub‑GHz or NTRIP + OpenIPC video on 5.8 GHz Wi‑Fi.**

---

## Recommendation (spot-sprayer / fixed-wing)

Use OpenIPC as a **dedicated video / mapping camera path**, not as RC or RTK.
For FFmpeg frame grabs and weed-ID experiments, prefer **APFPV or Ethernet/Wi‑Fi
IP mode** so you get real **RTSP** (`…:554/stream=0`) or **UDP RTP :5600** URLs;
pair with `scripts/extract_frames.py`. For field mapping / ODM, **do not** rely
on continuous high-bitrate live video — use **`/image.jpg` stills** or SD
recordings. Prefer an **SSC338Q-class air unit + RTL8812** with **5 GHz** video
while **ELRS stays on 2.4**. Keep RTK on its own link. Avoid putting video and
ELRS on the same 2.4 GHz band on a compact airframe.

---

## Best official / docs / repo links

| Resource | URL |
|----------|-----|
| OpenIPC site | https://openipc.org/ |
| Ecosystem map | https://openipc.org/ecosystem |
| Featured SoCs | https://openipc.org/supported-hardware/featured |
| Full SoC list | https://openipc.org/supported-hardware/full-list |
| Majestic Endpoints (meta) | https://openipc.org/majestic-endpoints |
| Majestic streamer wiki | https://github.com/OpenIPC/wiki/blob/master/en/majestic-streamer.md |
| Majestic config wiki | https://github.com/OpenIPC/wiki/blob/master/en/majestic-config.md |
| Frigate / FFmpeg RTSP howto | https://github.com/OpenIPC/wiki/blob/master/en/howto-frigate-integration.md |
| Installation wiki | https://github.com/OpenIPC/wiki/blob/master/en/installation.md |
| FPV WFB-NG quick start | https://docs.openipc.org/use-cases/fpv/wfb-ng/-quick-start/ |
| APFPV beginner guide | https://docs.openipc.org/use-cases/fpv/apfpv/apfpv/ |
| RTL8812AU net cards | https://docs.openipc.org/use-cases/fpv/net-cards/rtl8812au/ |
| Firmware repo | https://github.com/OpenIPC/firmware |
| devourer (userspace Wi‑Fi) | https://github.com/OpenIPC/devourer |
| ipctool | https://github.com/OpenIPC/ipctool |

---

## Sources used for this note

Only URLs fetched or returned by web search during research (2026-09-26 local):

- https://openipc.org/ecosystem  
- https://openipc.org/majestic-endpoints  
- https://openipc.org/supported-hardware/featured  
- https://openipc.org/supported-hardware/full-list  
- https://github.com/OpenIPC/wiki/blob/master/en/majestic-streamer.md  
- https://github.com/OpenIPC/wiki/blob/master/en/majestic-config.md  
- https://github.com/OpenIPC/wiki/blob/master/en/howto-frigate-integration.md  
- https://github.com/OpenIPC/wiki/blob/master/en/installation.md  
- https://docs.openipc.org/use-cases/fpv/wfb-ng/-quick-start/  
- https://docs.openipc.org/use-cases/fpv/apfpv/apfpv/  
- https://docs.openipc.org/use-cases/fpv/net-cards/rtl8812au/  
- https://github.com/OpenIPC/configurator/blob/master/majestic.yaml  
- https://github.com/OpenIPC/devourer  
- https://github.com/OpenIPC/firmware (via ecosystem page)

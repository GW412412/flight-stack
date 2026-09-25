# OpenIPC camera modules for compact spot-spraying (research notes)

Local research notes on **lightweight, low-power OpenIPC-compatible camera / air-unit
hardware** for low-altitude hill-country spot spraying and **rosette weed detection**.
Companion to [OPENIPC.md](OPENIPC.md) (link architecture, RF coexistence, FFmpeg ingest).

> **Not a vendor recommendation.** Specs and prices below are from official OpenIPC
> docs, vendor product pages, or manufacturer datasheets (URLs in Sources). Where a
> live retail price was not found, that is stated explicitly.

---

## 1. What matters for this airframe

| Need | Why |
|------|-----|
| **Light + low power** | Compact sprayer AUW is tight; video TX current competes with pump and ELRS |
| **Low-light / dawn–dusk** | Rosettes on hill pasture are often sprayed early/late; STARVIS-class sensors help |
| **OpenIPC streaming** | Majestic RTSP / RTP-UDP → GS → `scripts/extract_frames.py` / FFmpeg |
| **Separate from FC** | Video SoC is **not** the flight controller; FC keeps MAVLink / sprayer; camera is a peer on UART (OSD) + RF (video) |

OpenIPC’s own FPV hardware guidance (2025–2026 docs) centres on:

- **SoC:** **SSC338Q** preferred; **SSC30KQ** is a cut-down option  
- **Sensors commonly sold on FPV AIOs:** **IMX335** (better IQ / up to 1080p@120) and **IMX415** (e.g. 1080p@90)  
- **Wi‑Fi TX:** **RTL8812AU** or **RTL8812EU** (EU lacks WFB-NG 40 MHz mode)

Source: [WFB-NG quick start — hardware choice](https://docs.openipc.org/use-cases/fpv/wfb-ng/-quick-start/).

---

## 2. Sensor cheat-sheet (low-light & OpenIPC relevance)

| Sensor | Class / notes | OpenIPC FPV retail reality | Low-light note |
|--------|---------------|----------------------------|----------------|
| **IMX335** | Sony 5 MP; default on Mario / Thinker kits | **Primary** on official AIOs | Good all-round; OpenIPC docs call IQ superior to IMX415 for FPV modes |
| **IMX415** | Sony ~8 MP; RunCam WiFiLink default | **Very common** on retail AIOs | Fine daylight; generally weaker IQ than IMX335 per OpenIPC docs |
| **IMX462** | Sony **STARVIS**, 1/2.8″, 2.9 µm, 1080p@120 | **Not** on current Mario/Thinker/RunCam AIO SKUs surveyed | Strong low-light/NIR: Sony flyer sensitivity **10741 digit** (F5.6, 1/30 s) — [flyer PDF](https://www.sony-semicon.com/files/62/flyer_security/IMX462LQR_LQR1_Flyer.pdf) |
| **IMX307** | Sony **STARVIS**, same optical format as 462 | More common on flashed **IP cameras** than FPV AIOs | Sensitivity **7747 digit** (same flyer conditions) — [flyer PDF](https://www.sony-semicon.com/files/62/flyer_security/IMX307LQD_LQR_Flyer.pdf) |
| **IMX219** | Pi-camera class | Not a typical OpenIPC SSC338Q AIO sensor | Skip for this stack unless you deliberately run a Pi companion path |

**Practical implication for rosettes:** Prefer an **IMX335** OpenIPC AIO today for supported firmware + IQ. Treat **IMX462 / IMX307 STARVIS** as a **custom MIPI / flash-your-own IP-camera** path if dawn/dusk detection is the bottleneck — confirm SoC sensor driver + Majestic profile before buying loose modules. OpenIPC [sensor-profiles](https://github.com/OpenIPC/sensor-profiles) currently documents **IMX335 / IMX415** FPV profiles, not IMX462.

STARVIS definition (Sony): back-illuminated security sensors with ≥2000 mV per 1 µm² sensitivity class — see flyers above.

---

## 3. Specific boards / kits (weight, power, price)

### 3.1 OpenIPC Thinker Air Unit (top pick for compact AUW)

| Item | Value | Source |
|------|-------|--------|
| SoC | SigmaStar **SSC338Q** | [Thinker docs](https://docs.openipc.org/hardware/openipc/vtx/fpv-thinker-aio/) |
| Sensors | Default **IMX335** module (14×14 mm, ~140° FOV, f/2.8); optional **IMX415** module | same |
| Board size | **25×25 mm** (PCB); heatsink mounts **25.5×25.5 mm** | same |
| Weight | **~8.8 g** without heatsink; **~13.4 g** with heatsink (main unit + camera) | same |
| Power | **2–6S**, integrated **3 A** BEC | same |
| Wi‑Fi | **Base:** external USB Wi‑Fi (RTL8812AU/EU etc.); **Tiny:** integrated **RTL8731BU** (~50 mW @ 5 GHz, ~125 mW @ 2.4 GHz per footnote) | same |
| I/O | 3× UART, MIPI camera, USB for Wi‑Fi (Base), Ethernet, power | same |
| Price | **Unknown** from OpenIPC docs / store pages fetched for this note (store links exist; no public USD figure captured) | — |

**Fit:** Lightest documented official OpenIPC AIO — best match for a compact spot-sprayer. Prefer **Base + RTL8812 on 5 GHz** over Tiny’s low-power 8731 if hill range matters (see [OPENIPC.md](OPENIPC.md) RF notes).

### 3.2 OpenIPC AIO “Mario” v1.0 (balanced AIO + SD record)

| Item | Value | Source |
|------|-------|--------|
| SoC / Wi‑Fi / flash | **SSC338Q** + **RTL8812AU** + NOR 16M | [Mario docs](https://docs.openipc.org/hardware/openipc/vtx/fpv-mario-aio/), [fpvwiki summary](https://fpvwiki.co.uk/openipc-mario-aio), [builder list](https://github.com/OpenIPC/builder) |
| Sensor | **IMX335**, 14×14 mm, ~150° FOV M8 | same |
| Board | **30×32 mm**, M2 holes 25.5×25.5 mm | same |
| Weight | Main board **9.86 g**; radiator **7.36 g**; fan **5.37 g** (~**22.6 g** if all fitted) | fpvwiki / OpenIPC specs |
| Power | **2S–6S**; RF BEC up to **3 A @ 5 V**; MISC BEC up to **2 A @ 5 V**; fan rail up to 500 mA | Mario docs |
| Extras | Onboard SD, USB‑C debug / CDC Ethernet, FC telemetry GH1.25, dual-band PA (docs discuss RF settings carefully) | same |
| Price | **Unknown** (store URL listed; no USD price on pages fetched) | https://store.openipc.org/OpenIPC-AIO-Mario-v1-0-p633320808 |

**Fit:** Heavier than Thinker but integrated Wi‑Fi + SD recording for mapping / post-flight stills without continuous high-bitrate RF.

### 3.3 RunCam WiFiLink 2 (easiest retail buy; known prices)

| Item | Value | Source |
|------|-------|--------|
| SoC / sensor | **SSC338Q** + **IMX415**, FOV **160°** | [RunCam WiFiLink 2 docs](https://docs.openipc.org/hardware/runcam/vtx/runcam-wifilink-v2/) |
| Wi‑Fi | Custom **RTL8812EU** (5 / 10 / 20 MHz BW); PA **28 dBm (FCC) / 20 dBm (CE)** | same |
| Size / weight | PCB **30.6×33 mm**; holes **25.5×25.5**; **25 g** without fan / **30 g** with fan | same |
| Power | **DC 9–22 V** (BEC recommended; not direct LiPo in docs) | same |
| Firmware | `ssc338q_fpv_openipc_urllc_aio_nor` | same |
| FC note | Docs list **ArduPilot** among supported FCs | same |
| Idle/diagnostic current | U‑Boot-accessible example: **13.5 V × 0.182 A ≈ 2.5 W** (failed-flash diagnostic — **not** full TX load) | same page |
| Price (sourced) | **US $81.99** (VTX only, Lumenier, page fetched 2026-09-26); **US $116.99** for **2‑G** kit with ground NIC | [Lumenier WiFiLink 2](https://www.lumenier.com/products/runcam-wifilink-2-w-openipc), [2‑G](https://www.lumenier.com/products/runcam-wifilink-2-g-w-openipc) |

**WiFiLink v1 (older):** IMX415, ~**30 g**, 9–30 V, SSC338Q — [GetFPV product page](https://www.getfpv.com/runcam-wifilink-w-openipc.html) (price not shown on page fetch). Builder marks **RunCam WiFiLink** as done (SSC338Q + IMX415).

**Fit:** Heavier than Thinker; IMX415 is not the best low-light pick in OpenIPC’s own comparison, but the unit is widely stocked and has **published USD prices**.

### 3.4 Other OpenIPC-ecosystem boards (brief)

| Board | Sensor / notes | Weight / power | Price |
|-------|----------------|----------------|-------|
| **OpenIPC AIO UltraSight** | SSC338Q + **IMX415** (builder); needs reliable **5 V / ≥3 A** and active cooling | Weight **not found** on wiki page fetch | **Unknown** — [wiki](https://github.com/OpenIPC/wiki/blob/master/en/fpv-openipc-aio-ultrasight.md) |
| **Emax Wyvern Link** | Listed in builder / OpenFPV as light TinyWhoop-class | “Very light” (OpenFPV table; no grams) | **Unknown** |
| Flashed **IP cameras** (Goke / HiSilicon / SigmaStar + GC*/SC*/IMX*) | See [builder device list](https://github.com/OpenIPC/builder) | Often heavier plastic housings — poor for AUW | Varies; often cheaper used gear |

OpenFPV comparison table: https://openfpv.com.ua/en/hardware/vtx/

---

## 4. Feeding FFmpeg / frame extraction over Wi‑Fi

Same paths as [OPENIPC.md](OPENIPC.md) §3 — short checklist:

1. Prefer an **IP-routable** mode for tooling: **APFPV**, Ethernet bench, or GS that restreams locally.  
2. **RTSP** (Majestic): `rtsp://root:PASS@CAM_IP:554/stream=0` (main) / `stream=1` (sub) — [Frigate howto](https://github.com/OpenIPC/wiki/blob/master/en/howto-frigate-integration.md).  
3. **RTP/UDP**: Majestic `outgoing.servers: [ udp://GS_IP:5600 ]` or APFPV UDP **5600**.  
4. Point this repo’s `scripts/extract_frames.py` at those URLs.  
5. **WFB-NG** alone is **not** a plain RTSP URL over RF — decode on PixelPilot / SBC GS, then restream or pull stills (`/image.jpg`) for weed-ID.

For hill spraying: run a **low-bitrate substream** or **1 fps stills** for detection; keep FPV bitrate modest. Continuous 1080p90 @ tens of Mbps burns link margin and battery.

---

## 5. ArduPilot / OpenIPC integration (separate video path)

```
                    ┌─────────────────────────────┐
  ELRS / RC  ──────►│  Flight controller (ArduCopter)│◄──── RTK / telem
                    │  AC_Sprayer · OA · Lua         │
                    └──────────┬──────────────────────┘
                               │ UART only (MSP DisplayPort OSD)
                               │ SERIALn_PROTOCOL = 42
                               │ OSD_TYPE = 5
                    ┌──────────▼──────────────────────┐
                    │  OpenIPC air unit (SSC338Q+sensor) │
                    │  Majestic encode → Wi‑Fi VTX       │
                    └──────────┬──────────────────────┘
                               │ RF video (5 GHz preferred)
                    ┌──────────▼──────────────────────┐
                    │  GS: PixelPilot / SBC / laptop     │
                    │  → FFmpeg / extract_frames.py      │
                    └────────────────────────────────────┘
```

**Do:**

- Treat the camera as a **dedicated video peer**, not as the FC or companion ML computer (unless you deliberately add one).  
- Wire **TX↔RX + GND** UART for OSD; use **[msposd](https://github.com/OpenIPC/msposd)** on the air unit to draw ArduPilot MSP DisplayPort over the video.  
- ArduPilot params (from [DisplayPort OSD docs](https://ardupilot.org/copter/docs/common-displayport.html) + msposd README):  
  - `OSD_TYPE = 5` (MSP_DISPLAYPORT)  
  - `SERIALn_PROTOCOL = 42` (DisplayPort), baud **115200**  
  - `MSP_OPTIONS`: bit0 **cleared** for DisplayPort (see ArduPilot note); msposd documents ArduPilot font install separately.  
- Keep **ELRS on 2.4 GHz** (or 900 MHz) and **OpenIPC video on 5 GHz** — [OPENIPC.md](OPENIPC.md) §6.  
- Power the AIO from a **clean BEC**; Mario / Thinker / RunCam all warn about wiring and cooling.

**Don’t:**

- Expect the OpenIPC SoC to replace ArduPilot guidance / sprayer PWM.  
- Share one UART for both MAVLink telemetry *and* MSP DisplayPort.  
- Assume USB webcam → Pi is equivalent to an OpenIPC SSC338Q + Majestic path for RF FPV.

RunCam docs explicitly list ArduPilot as a supported FC for WiFiLink 2 wiring.

---

## 6. Recommendation for this project

| Rank | Pick | Why |
|------|------|-----|
| **1** | **OpenIPC Thinker (Base) + IMX335 + RTL8812AU/EU @ 5 GHz** | Lightest sourced official kit (~9–13 g); IMX335 preferred IQ; modular Wi‑Fi power | 
| **2** | **OpenIPC Mario AIO (IMX335)** | Integrated AU + SD + known board weights; good if you want onboard recording for ODM |
| **3** | **RunCam WiFiLink 2 (IMX415)** | Easiest purchase with **sourced** prices (**$81.99** / **$116.99** 2‑G); heavier; sensor less ideal for dusk rosettes |

If **dawn/dusk STARVIS** proves necessary after flight tests, evaluate a **custom IMX462 (or IMX307) MIPI module** on a supported SoC or a flashed STARVIS IP-camera board — only after confirming OpenIPC sensor support for that exact combo. Do not assume IMX462 works on Mario/Thinker without driver evidence.

Cross-read: [OPENIPC.md](OPENIPC.md) for RTSP/WFB/APFPV and ELRS coexistence; [WEED-ID.md](WEED-ID.md) for offline detection; [OBSTACLE-AVOIDANCE.md](OBSTACLE-AVOIDANCE.md) for low-altitude FC sensors (separate from this camera).

---

## Best links

| Resource | URL |
|----------|-----|
| Thinker Air Unit docs | https://docs.openipc.org/hardware/openipc/vtx/fpv-thinker-aio/ |
| Mario AIO docs | https://docs.openipc.org/hardware/openipc/vtx/fpv-mario-aio/ |
| RunCam WiFiLink 2 docs | https://docs.openipc.org/hardware/runcam/vtx/runcam-wifilink-v2/ |
| WFB-NG hardware choice | https://docs.openipc.org/use-cases/fpv/wfb-ng/-quick-start/ |
| OpenIPC builder device list | https://github.com/OpenIPC/builder |
| Featured SoCs | https://openipc.org/supported-hardware/featured |
| Sensor profiles (IMX335/415) | https://github.com/OpenIPC/sensor-profiles |
| msposd (ArduPilot OSD) | https://github.com/OpenIPC/msposd |
| ArduPilot DisplayPort | https://ardupilot.org/copter/docs/common-displayport.html |
| Majestic / Frigate RTSP | https://github.com/OpenIPC/wiki/blob/master/en/howto-frigate-integration.md |
| Sony IMX462 flyer | https://www.sony-semicon.com/files/62/flyer_security/IMX462LQR_LQR1_Flyer.pdf |
| Sony IMX307 flyer | https://www.sony-semicon.com/files/62/flyer_security/IMX307LQD_LQR_Flyer.pdf |
| Lumenier WiFiLink 2 price | https://www.lumenier.com/products/runcam-wifilink-2-w-openipc |
| OpenFPV VTX comparison | https://openfpv.com.ua/en/hardware/vtx/ |

---

## Sources used for this note

Fetched or search-returned during research (local date 2026-09-26):

- https://docs.openipc.org/hardware/openipc/vtx/fpv-thinker-aio/  
- https://docs.openipc.org/hardware/openipc/vtx/fpv-mario-aio/  
- https://docs.openipc.org/hardware/runcam/vtx/runcam-wifilink-v2/  
- https://docs.openipc.org/use-cases/fpv/wfb-ng/-quick-start/  
- https://fpvwiki.co.uk/openipc-mario-aio  
- https://github.com/OpenIPC/builder  
- https://github.com/OpenIPC/sensor-profiles  
- https://github.com/OpenIPC/msposd  
- https://github.com/OpenIPC/wiki/blob/master/en/howto-frigate-integration.md  
- https://github.com/OpenIPC/wiki/blob/master/en/fpv-openipc-aio-ultrasight.md  
- https://openipc.org/  
- https://openipc.org/supported-hardware/featured  
- https://openfpv.com.ua/en/hardware/vtx/  
- https://www.lumenier.com/products/runcam-wifilink-2-w-openipc  
- https://www.lumenier.com/products/runcam-wifilink-2-g-w-openipc  
- https://www.getfpv.com/runcam-wifilink-w-openipc.html  
- https://ardupilot.org/copter/docs/common-displayport.html  
- https://www.sony-semicon.com/files/62/flyer_security/IMX462LQR_LQR1_Flyer.pdf  
- https://www.sony-semicon.com/files/62/flyer_security/IMX307LQD_LQR_Flyer.pdf  

# Radio link consolidation analysis

Can one RF system carry **ELRS-equivalent RC**, **WFB-NG-equivalent video**,
**MAVLink telemetry** (spray, voice modes, RTCM), and **OpenRTLS UWB** for this
precision spot-sprayer? Compiled **2026-09-26**. Research notes only — not a
regulatory certificate or flight manual.

**Status of this doc:** analysis + **future-path recommendations**. It does
**not** unlock INTEGRATION / SYSTEM-SPEC locks for v1 unless explicitly adopted
later.

---

## Purpose

1. Inventory the radios this project already locked.  
2. Check physics and band coexistence (2.4 GHz, 5 GHz Wi‑Fi / WFB, UWB).  
3. Survey real open and proprietary stacks that multiplex RC + video + telem.  
4. Recommend the **simplest achievable** consolidation for *this* spray aircraft
   — honestly, including why a pure single-radio design is a poor fit for
   chemical application.

---

## Current link inventory (from docs)

Respect these as **v1 locks** (INTEGRATION, SYSTEM-SPEC, OPENIPC, AIRFRAME,
LIVE-PIPELINE). Recommendations below are marked **[recommendation]** vs
**[current lock]**.

| Role | Medium (locked) | Payload | Forbidden on that RF |
|------|-----------------|---------|----------------------|
| **RC** | **ELRS 2.4 GHz** → FC CRSF/SBUS | Sticks, modes, arm, kill, sprayer inhibit | Video; weed ID |
| **Video** | **WFB-NG** on **RTL8812AU**, **5 GHz** | H.264 pixels (Thinker + IMX335) | RC; MAVLink spray; RTCM |
| **Telemetry** | **SiK** and/or **ELRS telem** (UART / Pi bridge) | MAVLink (modes, `DO_SPRAYER`, mission, attitude, GPS) + **RTCM inject** from GCS | High-rate video |
| **RTK** | Holybro F9P base → GCS inject over **telem** (or NTRIP) | RTCM | Riding WFB video RF |
| **OpenRTLS (optional)** | **DWM1000-class UWB** (~3.5–6.5 GHz) + Ethernet/UART | Local pose → `VISION_POSITION_ESTIMATE` | Replacing ELRS; riding WFB |

**Authority ([INTEGRATION.md](INTEGRATION.md)):** Pilot+ELRS > FC > GS MAVLink >
UI. Video RF must not carry RC. Video loss in live touch-up → **pump off**;
continue on ELRS.

---

## Physics / band constraints (2.4 vs 5 vs UWB)

### 2.4 GHz

- Crowded ISM (Wi‑Fi, Bluetooth, microwave leakage, many RC systems).  
- **ELRS** (LoRa / FLRC-class timings on SX128x / LR1121) is engineered for
  **small, frequent control packets** with known failsafe behaviour.  
- Putting **WFB / OpenIPC video on 2.4 next to ELRS** on a compact F450 is a
  known desense risk ([OPENIPC.md](OPENIPC.md) §6). **[current lock]** keeps
  video off 2.4.

### 5 GHz (Wi‑Fi / WFB-NG / OpenHD / Ruby)

- Wider channels (20/40 MHz), higher throughput — suitable for **~6 Mbps H.264
  720p30** as locked in SYSTEM-SPEC.  
- Monitor-mode **wifibroadcast** (WFB-NG, OpenHD, RubyFPV) is **half-duplex**:
  uplink (RC / MAVLink / retransmit requests) and downlink (video) **share air
  time**. Video bursts delay small control frames; MCS drops under foliage /
  low AGL hurt *everything* on that card.  
- A **single RTL8812AU** can carry aggregated UDP streams (video + MAVLink + IP
  tunnel) — WFB-NG and OpenHD document this — but that is **packet multiplexing
  on one half-duplex PHY**, not independent failsafe domains.  
- ESP32 / ESP-NOW / DroneBridge-class links can carry **RC + MAVLink** at
  kilobit rates; they **cannot** replace a 5 GHz digital video downlink.

### UWB (OpenRTLS / DWM1000)

- Qorvo/Decawave **DWM1000**: IEEE 802.15.4a UWB, channels **1–7**, roughly
  **3.5–6.5 GHz**, ~500 MHz occupied bandwidth, low EIRP under UWB masks.  
  Common certified configs use **Channel 5 ≈ 6.5 GHz**.  
- Classic **5 GHz Wi‑Fi / WFB (802.11a/n/ac, ~5.15–5.85 GHz)** does **not**
  overlap DW1000 Ch5; coexistence with WFB on 5 GHz is mainly **antenna/desense
  proximity**, not identical centre frequency.  
- **Wi‑Fi 6E / 6 GHz** *does* overlap UWB Ch5–7 and can severely degrade ranging
  (published IPSN / Brunner et al. work on Wi‑Fi 6E vs UWB). Keep WFB on
  **5 GHz**, not 6E, if UWB is fitted.  
- Regulatory: UWB is **not** “just another Wi‑Fi channel.” Outdoor fixed
  infrastructure and some bands are restricted (e.g. FCC Part 15 Subpart F /
  15.250 indoor constraints in the US; Australia requires matching the ACMA
  LIPD / apparatus rules for the finished product). **Treat OpenRTLS as its own
  legal and RF domain.**  
- UWB data rates (~110 kbps–6.8 Mbps burst class) and short range (~tens of
  metres per hop for DIY DWM1000) make it useless as a video or long-range RC
  pipe. **UWB always stays a separate radio** for local pose.

### Time-multiplex fantasy (one ESP32, one 8812, or SDR “does all”)

| Approach | What it can do | Why it fails as *the only* spray link |
|----------|----------------|----------------------------------------|
| **One ESP32 / ESP-NOW** | RC + MAVLink (DroneBridge, hx_espnow_rc, community ESP-NOW bridges) | No HD video budget; ESP-NOW and STA Wi‑Fi fight over channel; not ELRS-grade LQ/failsafe |
| **One RTL8812AU WFB/OpenHD/Ruby** | Video + bidirectional MAVLink; optional joystick `RC_CHANNELS_OVERRIDE` | Half-duplex contention; OpenHD docs warn Wi‑Fi is poor for many small RC packets; **common-mode** video fade = RC fade |
| **ELRS alone** | Excellent RC + **MAVLink mode** telem (kb/s class) | **Cannot** carry 720p video |
| **Multi-protocol SDR** | Lab demos / research | No mature, flight-proven open stack that is ELRS-equivalent *and* WFB-equivalent on one PA for chemical ops |
| **DJI OcuSync / Lightbridge-class** | Combined OFDM with **prioritized** control vs video on proprietary ASIC | Closed ecosystem; not an ArduPilot / OpenIPC drop-in |

**Bottom line:** multiplexing **video + MAVLink** on 5 GHz is real and shipped.
Multiplexing **hard RC authority** onto that same half-duplex Wi‑Fi PHY is
technically possible and **explicitly discouraged** by OpenHD for good reason.
Pure single-radio RC+video+telem+UWB is **not** an achievable safe design here.

---

## Option analysis

| Option | Radios | RC | Video | MAVLink / RTCM | UWB | Latency / failsafe notes | Fit for *this* sprayer |
|--------|--------|----|-------|----------------|-----|--------------------------|------------------------|
| **A. Status quo (v1 locks)** | ELRS 2.4 + WFB 5 GHz + SiK/ELRS telem (+ opt. UWB) | ELRS hard | WFB only | Telem / SiK | Separate | Best independence: video death → pump off, ELRS flies | **Current lock** — correct for spray |
| **B. ELRS + WFB; MAVLink on WFB tunnel** | ELRS + WFB (+ opt. UWB); drop SiK | ELRS hard | WFB | WFB mavlink peer + optional ELRS backup | Separate | WFB-NG supports bidirectional MAVLink aggregation; spray/voice share video airtime — risk under MCS drop | Achievable; **weakens** spray-command independence from video |
| **C. ELRS MAVLink mode + WFB video-only** | **ELRS (RC+telem)** + **WFB** (+ opt. UWB) | ELRS | WFB pixels only | ELRS MAVLink (stubborn retries); RTCM via same | Separate | ELRS docs: one UART for RC+MAVLink; kb/s not Mb/s — enough for spray/modes/RTCM, **not** video | **Best consolidation** of SiK away without touching RC authority |
| **D. OpenHD / Ruby single 5 GHz (video+telem±RC)** | One (or diversity) 8812 link (+ opt. UWB); ELRS optional | Optional JOY_RC / Ruby RC stream | Same PHY | Same PHY UART bridge | Separate | OpenHD: prefer **2.4 ELRS + 5.8 WFB**; JOY_RC is beta / Wi‑Fi-hostile for small packets. Ruby routes RC+telem+video over one or more radios | Demo / hobby OK; **do not** make WFB/OpenHD the sole RC for spray |
| **E. Proprietary DJI-class** | One OcuSync/Lightbridge-style OFDM | Prioritized in ASIC | Same modem | Same modem | N/A / vendor | Works because of closed QoS + dual-band hop — not portable to Pixhawk+OpenIPC | Out of scope |
| **F. SDR “one radio to rule them all”** | Custom | Fantasy | Fantasy | Fantasy | Fantasy | No production open stack matching ELRS failsafe + WFB video | Reject for v1–v2 |
| **G. HDZero / Walksnail + ELRS** | Digital VTX + ELRS | ELRS still required | Digital FPV | MSP/OSD ≠ full MAVLink spray path | Separate | Fine FPV; not a WFB/OpenIPC GS ID pipeline substitute | Not this project’s camera path |
| **H. ESP-NOW / DroneBridge only** | ESP32 pair | Possible | No | Yes (low rate) | Separate | Cheap telem/RC experiment | Cannot replace WFB or ELRS here |
| **I. UWB shared with 5 GHz Wi‑Fi hardware** | One dual-use radio | — | — | — | — | Different PHY, masks, and drivers; DW1000 ≠ RTL8812 | **Impossible / unsafe** — keep UWB separate |

---

## Recommendation

**[recommendation — future consolidation path]** Keep **hard RC on ELRS 2.4 GHz**
and **pixels-only video on WFB-NG 5 GHz RTL8812AU**; consolidate **SiK away** by
running **ExpressLRS MAVLink mode** (or ELRS telem) so **RC + MAVLink + RTCM
inject** share the ELRS link; keep **OpenRTLS / DWM1000 UWB** as an **optional
third RF domain** for canopy pose only. That is the simplest *achievable*
reduction (three → two mandatory RF systems, UWB still separate when used). Do
**not** put RC or spray authority on the 5 GHz video PHY for chemical ops: a
single half-duplex Wi‑Fi fade would take sticks, telem, and video together,
fighting the project’s fail-closed rule (video loss → pump off while ELRS
continues). Pure one-radio RC+video+telem is technically demoable via
OpenHD/Ruby/WFB joystick tunnels but is **unsafe as sole authority** for this
sprayer; proprietary OcuSync-class links are closed and irrelevant to the
ArduPilot/OpenIPC stack.

### Bullet locks for a future consolidation path

- **[current lock remains]** ELRS 2.4 = hard flight / arm / kill / sprayer
  inhibit authority.  
- **[current lock remains]** WFB-NG 5 GHz = **pixels only** until a future
  change explicitly rewrites INTEGRATION.  
- **[recommendation]** Prefer **ELRS MAVLink mode** (ESP-based TX/RX, ≥ 3.5.x)
  to carry GCS MAVLink + RTCM and retire a dedicated SiK pair when UART and
  throughput allow (use higher packet rates for param/mission download; lower
  rates for range in flight — per ExpressLRS MAVLink docs).  
- **[recommendation]** If MAVLink is ever tunnelled on WFB (OpenHD / WFB-NG
  `drone_mavlink`), treat it as **non-authoritative convenience** only; RC and
  failsafe stay on ELRS; spray arbiter still fails closed on ELRS loss.  
- **[recommendation]** OpenHD / RubyFPV may be evaluated as an alternate **video
  + optional telem** stack, but adopt their own guidance: **ELRS on 2.4 +
  wifibroadcast on 5.8**, not JOY_RC alone.  
- **[recommendation / lock]** **UWB always separate** — own antennas, own
  driver, Ch5 (~6.5 GHz) preferred vs 5 GHz WFB; never a substitute for ELRS;
  verify ACMA/local UWB outdoor rules before paddock deployment.  
- **[recommendation]** Reject SDR- and single-8812-as-sole-RC designs for spray
  sorties.

---

## What stays separate and why (failsafe)

| Keep separate | Why |
|---------------|-----|
| **ELRS RC vs WFB video** | Independent PHY and failsafe. Video fade must not freeze sticks. INTEGRATION: video RF lost → pump off, **continue on ELRS**. |
| **Spray / voice MAVLink vs video RF** | Live ID and voice are GS conveniences; commands go through `mavlink_hub` on telem. Shared video airtime would couple ID quality to command delivery. |
| **RTCM vs WFB** | Corrections are small but safety-adjacent for cm spray georeg; inject over telem (ELRS MAVLink or SiK), not the video card. |
| **OpenRTLS UWB vs everything else** | Different band plan, power mask, and purpose (local pose). Aid only; if UWB drops → RTK/GPS + slow/Loiter near canopy. Antenna spacing from 5 GHz PA and 2.4 ELRS still required to limit desense. |
| **Motor kill** | Hardware / RC only — never a Wi‑Fi or UWB path. |

**Why pure single-radio is unsafe for spray ops:** one PA, one antenna farm, one
half-duplex schedule, one driver crash, or one DFS/interference event becomes a
**common-mode failure** of pilot authority, telem, and video. Proprietary
combined links mitigate that with dedicated control prioritization silicon and
fleet QA; open wifibroadcast stacks do not claim that guarantee — OpenHD
literally recommends a separate ELRS link.

---

## Related docs

| Doc | Relevance |
|-----|-----------|
| [INTEGRATION.md](INTEGRATION.md) | Authority order; bus table; OpenRTLS subsection; fail-closed rules |
| [SYSTEM-SPEC.md](SYSTEM-SPEC.md) | Locked Thinker + WFB 5 GHz + ELRS 2.4; latency budget |
| [OPENIPC.md](OPENIPC.md) | WFB-NG / RTL8812; ELRS coexistence; “do not piggyback RC on video Wi‑Fi” |
| [AIRFRAME.md](AIRFRAME.md) | F450 wiring; F9P RTK; OpenRTLS §6.1 |
| [LIVE-PIPELINE.md](LIVE-PIPELINE.md) | Spray on telem only; video-loss → pump off |
| [VOICE-CONTROL.md](VOICE-CONTROL.md) | Voice → MAVLink modes; ELRS hard authority |
| [STACK.md](STACK.md) | GCS / RTK inject patterns |

### External references (projects named in research)

- ExpressLRS MAVLink mode — https://www.expresslrs.org/software/mavlink/  
- WFB-NG (svpcom) — bidirectional MAVLink + video + IP tunnel on raw Wi‑Fi  
- OpenHD joystick RC notes — prefer ELRS 2.4 + 5.8 GHz wifibroadcast  
- RubyFPV — multi-process router for video / telem / RC over one or more radios  
- DroneBridge for ESP32 / ESP-NOW MAVLink bridges — telem (±RC), not HD video  
- HDZero / Walksnail — digital FPV; MSP/OSD ≠ full ArduPilot spray telem replacement  
- DJI Lightbridge / OcuSync — proprietary combined OFDM (control prioritized)  
- Qorvo DWM1000 — UWB 3.5–6.5 GHz; Ch5 ~6.5 GHz; Wi‑Fi 6E coexistence risk  

---

*End of analysis. v1 hardware locks unchanged until INTEGRATION / SYSTEM-SPEC
are deliberately revised.*

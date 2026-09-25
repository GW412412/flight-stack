# Ground-station & air diversity — WFB-NG video + ELRS control

Research notes for **diversity receive / dual-antenna** setups on every RF link
this sprayer uses: **WFB-NG 5 GHz video**, **ELRS 2.4 GHz RC**, and **telemetry**.
Compiled **2026-09-26**. Aligns with [SYSTEM-SPEC.md](SYSTEM-SPEC.md),
[OPENIPC.md](OPENIPC.md), [OPENIPC-CAMERAS.md](OPENIPC-CAMERAS.md),
[LINK-CONSOLIDATION.md](LINK-CONSOLIDATION.md), and [AIRFRAME.md](AIRFRAME.md).

> **Not a vendor recommendation or regulatory certificate.** Prices are
> **approximate class** figures (USD order-of-magnitude, 2025–2026 retail /
> AliExpress). Exact SKUs and live prices change; verify chipset, firmware
> target, and local TX-power / outdoor rules before buying.

**Airframe note:** the locked airframe today is a **multirotor F450**
([AIRFRAME.md](AIRFRAME.md)). A caller referred to “from the fixed wing” —
the **same ground-station diversity RX** applies to a **future fixed-wing TX**
on the same WFB-NG and ELRS links. Air-side antenna mounting differs (see §6).

---

## 0. Full-stack diversity map (recommendation)

Goal: every mandatory RF role has a **dual-antenna or multi-adapter** option on
the side that fails first under paddock multipath / body shadowing.

| Link | Band | Locked role | Diversity recommendation (v1+) |
|------|------|-------------|-------------------------------|
| **ELRS RC** | 2.4 GHz | Hard stick / arm / kill / sprayer inhibit | **Air:** true-diversity ELRS RX (dual RF chain). **GS/handset:** Gemini dual-TX module *or* single TX + good antenna placement; optional dual-antenna Gemini TX when budget allows. |
| **WFB-NG video** | 5 GHz | H.264 pixels only | **GS:** **2× RTL8812AU** USB adapters, **same channel**, spatially separated antennas (primary). Air: keep dual-antenna 8812 MIMO/STBC as already implied by SYSTEM-SPEC. |
| **Telemetry** | SiK sub‑GHz **or** ELRS MAVLink | MAVLink + RTCM inject | Prefer **migrate telem to ELRS MAVLink** ([LINK-CONSOLIDATION.md](LINK-CONSOLIDATION.md) option C) so diversity follows the ELRS RX/TX above. If SiK is kept: use a **dual-antenna SiK** pair (or two SiK radios in soft failover) — classic single-SMA SiK has **no** diversity. |
| **RTK** | GNSS L1/L2 (+ base radio or NTRIP) | cm position | Not “diversity RX” in the RF sense; use a proper survey antenna on the base and clear mast on the rover. |
| **OpenRTLS UWB** (optional) | ~3.5–6.5 GHz | Local pose aid | Separate antennas; not a substitute for ELRS ([LINK-CONSOLIDATION.md](LINK-CONSOLIDATION.md)). |

**Authority unchanged:** Pilot + ELRS > FC > GS MAVLink. Video RF never carries
RC. Video loss → pump off; continue on ELRS ([INTEGRATION.md](INTEGRATION.md)).

---

## 1. What a WFB-NG / OpenIPC diversity RX looks like

### 1.1 Two USB adapters (recommended GS path)

```
                    ┌─ RTL8812AU #1 ── omni A (e.g. left of GS mast)
Air Thinker 8812 ══╡  SAME 5 GHz channel / freq
                    └─ RTL8812AU #2 ── omni B (right / elevated)  ─ or panel aimed at far edge
                              │
                              ▼
                     wfb_rx / wfb-ng GS
                     (multi-NIC list)
                              │
                              ▼
                     local RTP/RTSP restream → weed-ID
```

- Two (or more) **RTL8812AU** (or OpenIPC-tested **8812EU** on SBC GS) USB NICs.
- Each NIC gets its **own antenna(s)** and preferably a **different spatial
  position** (metres apart on a mast / backpack / table edge) so one path can
  survive body/terrain shadowing while the other fades.
- All GS adapters listen on the **same centre frequency / channel** as the air
  TX. This is **spatial diversity**, not dual-channel frequency diversity.

### 1.2 Single-adapter “antenna diversity” (weaker GS option)

An RTL8812AU board with **two RP-SMA / IPEX ports** (e.g. Alfa AWUS036ACH-class)
can use **both RF chains on one card**:

| Mechanism | What it does | Role here |
|-----------|--------------|-----------|
| **MIMO / STBC on TX** | WFB-NG can enable STBC so a dual-antenna 8812AU transmits on both ports | Useful on **air** (and GS uplink); see Setup HOWTO note on `stbc` |
| **Two antennas on one NIC** | Improves that card’s own RX/TX, but antennas sit **centimetres** apart | Better than one whip; **does not** replace two cards metres apart |
| **True multi-NIC diversity** | Separate USB adapters → separate positions → `wfb_rx` combines | **Preferred GS diversity** for a ~5-acre paddock |

**Do not confuse** “two antennas on one USB stick” with “two USB sticks on a
mast.” For walk-around GS work, buy the **second adapter**.

### 1.3 Channels: same channel, not different channels

| Approach | Correct for WFB-NG spatial diversity? |
|----------|----------------------------------------|
| Two GS NICs on **same** `wifi_channel` / `wlanfreq` as air | **Yes** — both hear the same packets; software keeps the first valid copy |
| Two GS NICs on **different** 5 GHz channels | **No** for diversity combining — air TX is on one channel; the off-channel NIC hears nothing useful |
| Frequency hopping / dual-channel video | Not the WFB-NG multi-NIC model; out of scope for this note |

OpenIPC / SYSTEM-SPEC stay on **5 GHz, 20 MHz**, region-legal power, matched
linear polarisation.

---

## 2. Does WFB-NG support multiple RX interfaces? (current truth)

**Yes.** Upstream **svpcom/wfb-ng** documents multi-adapter RX and TX diversity
out of the box.

| Fact | Source / detail |
|------|-----------------|
| `wfb_rx` accepts **multiple interfaces** | `wfb_rx … interface1 [interface2] …` — [wfb_tx/wfb_rx reference](https://github.com/svpcom/wfb-ng/wiki/wfb_tx-and-wfb_rx-reference) |
| List multiple NICs in config | `/etc/default/wifibroadcast` (legacy) / GS NIC list; Setup HOWTO: *“specify multiple nics … directed and/or omnidirected antennas”* — [Setup HOWTO](https://github.com/svpcom/wfb-ng/wiki/Setup-HOWTO) |
| Combining behaviour | Packet-level: **first intact copy wins**; later duplicates discarded before FEC/decrypt path (minimal latency; does not wait for “strongest”) — same idea in OpenIPC-core diversity notes |
| GS **TX** diversity (uplink) | With multiple GS cards, **TX adapter selected by RX RSSI** so you can mix omni + directional without a tracker — PX4 WFB-NG guide + Setup HOWTO |
| Single dual-antenna card STBC | `stbc = 1` (etc.) in stream config for dual-port 8812AU |
| Remote forwarder / aggregator | Cards on **different hosts** can forward to a central `wfb_rx -a` aggregator — advanced / multi-laptop GS |
| 4-port cards | Docs historically: cards like Alfa AWUS1900 **not** supported as 4× diversity; prefer **2× 2-port** adapters |

PX4’s WFB-NG page summarises the product intent: *“Automatic TX diversity —
use multiple cards on the ground to avoid antenna tracker”* and *“TX/RX
diversity for multiple adapters supported out of box.”*
([PX4 WFB-NG guide](https://docs.px4.io/main/en/companion_computer/video_streaming_wfb_ng_wifi)).

**OpenIPC GS stacks** (PixelPilot, SBC GS, aviateur) sit on the same WFB-NG /
wifibroadcast family. Practical checklist for this project’s laptop/SBC GS:

1. Two OpenIPC-tested **RTL8812AU** (or SBC-preferred **BL-M8812EU2 / 8812EU**) NICs.  
2. Patched injection drivers / Devourer as required; **never** stock unmanaged.  
3. Stable udev names (`wfb0`, `wfb1`).  
4. Both unmanaged by NetworkManager; both on the **same** channel as air.  
5. Confirm with `wfb-cli gs` that both radios show RX traffic.  
6. Restream locally to RTP `:5600` / RTSP for `scripts/extract_frames.py`
   ([SYSTEM-SPEC.md](SYSTEM-SPEC.md)).

---

## 3. Cheap second video NIC (AliExpress-class)

Order-of-magnitude **USD ~10–25** for a second USB stick suitable as a
**diversity peer** — not a guaranteed long-range PA.

| Class | Approx. price | Notes |
|-------|---------------|-------|
| Generic **RTL8812AU** dual-antenna USB (AliExpress / no-name AC1200) | **~$10–20** | Buy only listings that state **RTL8812AU** (VID/PID often `0bda:8812`). Verify monitor/injection with the WFB/OpenIPC driver before field use. |
| RunCam / OpenIPC kit “G-set” style ground NIC | Bundle-dependent | Low-power; fine for PixelPilot / short USB runs ([OPENIPC RTL8812AU docs](https://docs.openipc.org/use-cases/fpv/net-cards/rtl8812au/)) |
| Alfa **AWUS036ACH**-class | Higher (~$40–70 retail class) | Known dual RP-SMA 8812AU; good for one solid primary + cheap stick as second |
| “AC180 2W high power” AliExpress PA sticks | Tempting, **avoid as primary** | svpcom Setup HOWTO warns of bad RF design / SNR and burn risk; prefer **BL-M8812EU2** or proven AU modules with cooling |

**Practical buy rule for this repo:** one **known-good** 8812AU/EU as primary GS
card; one **cheap second 8812AU** for spatial diversity. Match antenna
connectors (RP-SMA vs SMA vs IPEX pigtails). Active cooling + solid 5 V for any
high-power module.

---

## 4. GS video antennas for a ~5-acre paddock

**~5 acres ≈ 2 ha.** A square of that area is roughly **~140 m on a side**;
corner-to-corner ~**200 m**. SYSTEM-SPEC targets **~600–800 m** practical 5 GHz
video at **5–15 m AGL** with clear LOS — so the paddock itself is **inside**
the design range if antennas and power are sane. The failure mode is
**multipath, body shadow, tree lines, and hills**, not raw free-space loss.

| Antenna style | Pros | Cons | Use |
|---------------|------|------|-----|
| **Omni dipole / cloverleaf** (linear matched to air) | Walk-around; no aiming; cheap | Less gain at long edge | **Default diversity pair** |
| **Panel / patch** | Gain toward one sector | Must aim; null behind | Optional third NIC or swap onto card #2 at long edge |
| **Yagi** | Highest gain | Narrow beam; tracker or careful aim | Only if fixed GS at one corner aiming across the block |

### Recommended default

1. **2× omni** on two diversity NICs, spaced **≥0.5–1 m** (better: opposite
   sides of a mast / backpack poles), both vertical linear, matched to air.  
2. **Optional:** keep a **panel** in the kit; when working a long straight edge
   from one corner, put the panel on NIC #2 aimed down the fence line; leave
   NIC #1 on omni for fade fill.  
3. Keep GS video antennas **clear of ELRS TX** and SiK (if any); never power an
   8812 PA without antennas.  
4. Start pit power low; raise only within **region-legal** limits
   ([SYSTEM-SPEC.md](SYSTEM-SPEC.md)).

---

## 5. ELRS control diversity (2.4 GHz)

ELRS is the **hard RC authority**. Diversity here protects sticks / arm / kill /
sprayer inhibit when the airframe banks or a wing blanks one antenna.

### 5.1 Air RX options (prefer ELRS SKUs)

| Class | Example SKUs (illustrative) | How diversity works | Approx. price class |
|-------|-----------------------------|---------------------|---------------------|
| **Antenna diversity** | RadioMaster **RP3** | **One** RF chain, **two** antennas; firmware switches to the better antenna | Often **~$15–20** |
| **True diversity** (dual RF chain) | BetaFPV **SuperD**, HappyModel **EP1 Dual**, RadioMaster **RP4TD** | **Two** SX128x (or similar) paths receive **simultaneously**; pick valid/stronger packet | SuperD retail **~$20–26**; RP4TD **~$25–35**; AliExpress often **~$15–25** |
| Single antenna | RP1 / EP1 / Lite | No diversity | Cheapest; OK for bench, weak for spray AUW |

**Recommendation for this F450 sprayer:** lock a **true-diversity** 2.4 GHz ELRS
RX (SuperD / EP1 Dual / RP4TD class). Place the two antennas at ~**90°**
(orthogonal polarisation) and as far apart as the frame allows (arms / tail),
clear of carbon and the 5 GHz Thinker antennas.

Crossfire Nano dual-antenna is the same *idea* but **out of preferred stack** —
stay on **ELRS** to match AIRFRAME / INTEGRATION.

### 5.2 True diversity vs antenna diversity vs Gemini (be accurate)

| Mode | Hardware | Behaviour | Increases range to failsafe? |
|------|----------|-----------|------------------------------|
| **Antenna diversity** | 1× RF + RF switch + 2 antennas | Switches between antennas (RSSI/LQ driven) | Modest multipath help |
| **True diversity** (“receiver diversity”) | 2× RF chains + 2 antennas | Both listen at once; best packet wins | Better LQ under blanking; **Gemini-capable** |
| **Gemini** (single-band) | Gemini **TX** (2× TX chains) + true-diversity **RX** | TX sends the **same** packet on **two frequencies** (~40 MHz apart on 2.4 GHz); RX hears both | **Higher / stabler LQ** to the failsafe cliff — official docs: **does not** magically extend distance to the moon |
| **Gemini-X / GemX** | Dual-band LR1121 TX + matching RX | Simultaneous **900 MHz + 2.4 GHz** | Cross-band interference avoidance; needs GemX hardware both ends |
| **Switch / Antenna mode on TX** | Dual-antenna TX without Gemini | TX may switch or listen on both for telem | Helps telem RX even with single-antenna air RX |

Official reference: [ExpressLRS Gemini](https://www.expresslrs.org/software/gemini/).
Oscar Liang’s diversity RX write-up remains a clear RP3 vs SuperD distinction:
[ELRS diversity receivers](https://oscarliang.com/expresslrs-diversity-receivers/).

### 5.3 Cheap dual-antenna ELRS RX (AliExpress-class)

| Class | Approx. USD | Comment |
|-------|-------------|---------|
| True diversity SuperD / EP1 Dual clones or OEM | **~$15–25** | Prefer branded SuperD / HappyModel / RadioMaster for known configurator targets |
| RP3 antenna-diversity | Often **under ~$20** | Fine budget step; upgrade to true diversity for Gemini later |
| Avoid | No-name “ELRS dual antenna” without clear **true vs antenna** labelling | If Gemini is a goal, you need **true diversity** |

Flash with ExpressLRS Configurator; match binding phrase / UID; CRSF into Pixhawk
UART per AIRFRAME.

### 5.4 Ground / handset TX diversity

| Option | What it is | Practical for handset? |
|--------|------------|------------------------|
| **Single TX + T-antenna or Moxon** | Ranger Nano / internal ELRS; aim Moxon when needed | **Default** for many; keep antenna vertical, clear of body |
| **Gemini dual-TX Nano** | e.g. BetaFPV **SuperG Nano** — dual RP-SMA, dual ~1 W paths, Gemini mode | **Best practical handset upgrade** for Nano bay (Zorro etc.) + adapter for JR bay |
| **Gemini-X module** | e.g. RadioMaster **Nomad** dual 1 W Gemini Xrossband (~$45–55 class) | When you want 2.4+900; needs matching RX; heavier/costlier |
| Two separate TX modules | Exotic | Not practical on one handset |

**Recommendation:** v1 can fly with a solid single ELRS TX + **true-diversity air
RX**. When adding “diversity for everything,” pair **SuperG / Nomad-class Gemini
TX** with the true-diversity air RX and enable Gemini in Lua on both ends.
Respect CE/FCC power (often 100 mW CE). Gemini helps **LQ under interference**,
not raw distance.

---

## 6. Telemetry diversity (SiK vs ELRS MAVLink)

| Path | Diversity reality | Recommendation |
|------|-------------------|----------------|
| **Classic SiK** (single SMA each end) | **No** diversity | Accept as weak link, or buy **dual-antenna SiK** hardware if available for your radio, or run **two SiK pairs** only if you have a soft failover story (unusual) |
| **ELRS MAVLink mode** | Shares the ELRS RF path — **inherits** true-diversity RX + Gemini TX | **Preferred consolidation** per [LINK-CONSOLIDATION.md](LINK-CONSOLIDATION.md): drop dedicated SiK when UART/rate allow; RTCM inject over ELRS telem |
| **MAVLink on WFB tunnel** | Gets WFB multi-NIC diversity but **shares video airtime** | Convenience only — **not** authoritative for spray ([LINK-CONSOLIDATION.md](LINK-CONSOLIDATION.md)) |

For “diversity for everything,” the clean answer is: **true-diversity ELRS for
RC+telem**, **2× 8812AU WFB for video**, retire single-SMA SiK.

---

## 7. Fixed-wing TX vs F450 (same GS)

| Side | Multirotor F450 (current) | Future fixed-wing |
|------|---------------------------|-------------------|
| **GS WFB diversity** | 2× 8812AU + 2× omni (± panel) | **Identical** |
| **GS ELRS** | Handset / Gemini TX | **Identical** |
| **Air WFB antennas** | Two linear antennas clear of props; attitude mostly level | Mount for **bank/pitch**: prefer one antenna on each wing or wingtip + fuse with polarisation diversity; avoid single belly antenna that nulls in turns |
| **Air ELRS antennas** | Orthogonal on arms | Orthogonal on wing / vertical stab — same true-diversity RX PCB |
| **Video bitrate / channel** | SYSTEM-SPEC 720p30 @ ~6 Mbps, 20 MHz 5 GHz | Same link budget class unless range increases |

The GS diversity investment is **airframe-agnostic**. Revisit air antenna
placement when a fixed-wing airframe is locked.

---

## 8. Practical shopping / build checklist

**Video GS**

- [ ] Primary RTL8812AU/EU (known-good)  
- [ ] Second cheap RTL8812AU (~$10–20 class)  
- [ ] 2× 5 GHz omni (matched polarisation to air)  
- [ ] Optional 5 GHz panel  
- [ ] USB power adequacy / powered hub; cooling  
- [ ] wfb-ng multi-NIC config; same channel; `wfb-cli gs` shows both  

**ELRS**

- [ ] True-diversity 2.4 GHz RX (SuperD / EP1 Dual / RP4TD class)  
- [ ] Orthogonal antenna placement on F450  
- [ ] Handset: good T or Moxon; optional SuperG/Nomad Gemini TX  
- [ ] If Gemini: Lua Antenna Mode = Gemini on TX **and** RX  

**Telem**

- [ ] Prefer ELRS MAVLink; else dual-antenna SiK or accept single-SMA risk  

**Acceptance**

- [ ] Walk a paddock edge with airframe powered on ground: video FEC/errors and
      ELRS LQ stay healthy when one GS omni or one air ELRS antenna is shaded.  
- [ ] Confirm video-loss → pump off still works with dual NICs.  
- [ ] Confirm ELRS failsafe independent of WFB.

---

## 9. Related docs

| Doc | Relevance |
|-----|-----------|
| [SYSTEM-SPEC.md](SYSTEM-SPEC.md) | Locked Thinker + WFB 5 GHz + ELRS 2.4; antennas “1–2× GS” |
| [OPENIPC.md](OPENIPC.md) | WFB-NG vs APFPV; GS options; RF coexistence |
| [OPENIPC-CAMERAS.md](OPENIPC-CAMERAS.md) | Air unit / 8812 choices |
| [LINK-CONSOLIDATION.md](LINK-CONSOLIDATION.md) | Keep RC off video PHY; ELRS MAVLink vs SiK |
| [AIRFRAME.md](AIRFRAME.md) | F450; ELRS RX mass; antenna clearance |
| [INTEGRATION.md](INTEGRATION.md) | Authority order; video-loss fail-closed |

### External references

- svpcom WFB-NG Setup HOWTO — multi-NIC GS — https://github.com/svpcom/wfb-ng/wiki/Setup-HOWTO  
- wfb_rx multiple interfaces — https://github.com/svpcom/wfb-ng/wiki/wfb_tx-and-wfb_rx-reference  
- PX4 WFB-NG video guide (TX/RX diversity summary) — https://docs.px4.io/main/en/companion_computer/video_streaming_wfb_ng_wifi  
- OpenIPC RTL8812AU net cards — https://docs.openipc.org/use-cases/fpv/net-cards/rtl8812au/  
- OpenIPC WFB-NG quick start — https://docs.openipc.org/use-cases/fpv/wfb-ng/-quick-start/  
- ExpressLRS Gemini — https://www.expresslrs.org/software/gemini/  
- ExpressLRS FAQ (diversity vs true diversity) — https://www.expresslrs.org/faq/  
- Oscar Liang — ELRS diversity RX (RP3 vs SuperD) — https://oscarliang.com/expresslrs-diversity-receivers/  
- Oscar Liang — Gemini overview — https://oscarliang.com/expresslrs-gemini/  

---

## 10. One-line summary

**GS: 2× RTL8812AU on the same 5 GHz channel with 2× omni (optional panel) for
WFB-NG; air+handset: true-diversity ELRS RX and optional Gemini TX for control;
migrate SiK telem onto ELRS MAVLink so diversity covers RC and telem together —
same GS setup later serves a fixed-wing TX.**

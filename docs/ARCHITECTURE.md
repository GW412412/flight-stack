# Architecture

One-glance view of the stack. Source of truth: [DECISIONS.md](DECISIONS.md). Updated 23 Oct 2026.

```mermaid
flowchart LR
  G["G's radio<br/>override / failsafe (highest priority)"] -->|RC| FC
  FC["Flight controller (Cube, ArduPilot)<br/>telemetry source · master authority until overridden"]
  CAM[Camera] -->|video| VID

  subgraph CPU["CPU-only computer"]
    MR(("mavlink-router<br/>fan-out hub"))
    VID["Video pipeline<br/>OpenCV + YOLO nano/small<br/>weed detection + classification"]
    VM["Mission Planner VM"]
    MP["mavproxy (terminal)"]
    QGC["QGroundControl"]
  end

  subgraph GPU["GPU computer"]
    W["Watchers<br/>ID + min/max each"]
    D1["d1-omni<br/>decision model"]
  end

  CM["Conversation model (Spray drone)"]

  FC <-->|MAVLink| MR
  MR --> VM
  MR --> QGC
  MR --> MP
  MR --> W
  MR --> D1
  VID -->|detections| CM
  W -.->|alert, advisory only| CM
  D1 -.->|yes/no + confidence, advisory only| CM
  CM -->|commands| MR
  CM -.->|low-confidence flags| G
```

**Rules**
- Decision models and watchers advise only. They never command the drone. The conversation model acts on high-confidence answers and flags low-confidence ones to G.
- G's radio overrides everything. The failsafe fires on whichever trigger comes first.
- mavlink-router logs who said what.

**Mission shape**
- A four-corner paddock waypoint file, with cameras looking along the fence line and inwards. A crosshatch pattern is optional.
- **Auto** mode for the survey, then **Guided** mode when a potential target is spotted. Either G or the conversation model can take the sticks.

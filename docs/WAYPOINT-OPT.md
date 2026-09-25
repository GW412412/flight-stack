# Economic waypoint optimization for compact spot-spraying

Research notes for ordering weed targets into a mission that minimises **flight time** and **spray volume**, with **wind**, **3D hill terrain**, and a **45° departure spray angle** per target. Compiled 2026-09-26. Links are to real solvers, papers, and libraries only.

---

## 1. Problem shape

**Inputs:** weed target positions (from weed ID), optional canopy area / dose per target, home/depot, wind vector, DEM (ODM DSM/DTM), vehicle turn radius / cruise speed, spray geometry (45° departure).

**Outputs:** ordered waypoint list (and headings) for Mission Planner / QGC / ArduCopter, plus estimated time and spray volume.

This is **not** full-field coverage (boustrophedon). It is **visit selected points** (spot spraying) — a routing problem with kinematics and physics in the edge costs.

---

## 2. Algorithm options

| Class | Role | When to use |
|-------|------|-------------|
| **TSP / ATSP** | Visit all targets once, return (or open path) | Must spray every detection; costs may be asymmetric |
| **Prize-collecting / orienteering** | Optional visits; skip low-value weeds if time/tank limited | Tank or battery scarce; drop marginal targets |
| **Dubins / trochoid paths** | Heading-constrained, min-time paths between poses | Multirotor with yaw/heading for spray boom; fixed-wing-like turn limits |
| **Weighted / multi-objective** | Minimise α·time + β·spray (+ γ·risk) | Explicit economic trade-off |

**ATSP (asymmetric TSP)** is the natural discrete model once edge costs depend on **direction** (wind, climb, spray departure heading).

**Prize-collecting:** OR-Tools models “drop node” via **penalties / disjunctions** — same idea as prize-collecting TSP / orienteering ([Penalties and dropping visits](https://developers.google.com/optimization/routing/penalties)).

---

## 3. Encoding wind, 3D terrain, and 45° spray departure

Build a **directed cost matrix** \(C_{ij}\) = cost to fly from target \(i\) (after spraying) to target \(j\) (approach + spray setup).

### Wind

- Prefer **ground-speed / time** along the path, not Euclidean distance.
- Uniform wind: trochoidal / wind-aware Dubins between poses — see CMU **trochoids** package (wind + Dubins): https://github.com/castacks/trochoids
- No wind: classical Dubins with `pip install dubins` — https://pypi.org/project/dubins/ · https://github.com/AndrewWalker/pydubins
- Agricultural strip planning with wind-corrected paths (Matlab reference): https://github.com/arthurrichards77/drone_scans (paper ECC 2018: https://ieeexplore.ieee.org/document/8550307)

Approximate fallback:  
`time ≈ path_length / max(ε, V_air · cos ψ + V_wind · …)` using local heading samples along the Dubins path.

### 3D hill terrain (climb cost)

- Sample altitudes from ODM **DSM/DTM** along the horizontal path (https://docs.opendronemap.org/).
- Add energy/time term: \(c_{\mathrm{climb}} \cdot \sum \max(0, \Delta h)\) and optional descent credit; or inflate speed when climb rate limited.
- Keep AGL safe: follow terrain + clearance, not a flat plane.

### 45° spray departure angle

Interpret as a **pose constraint** after each spray:

- At target \(i\), approach heading \(\theta_i^{\mathrm{in}}\) may be free or wind-aligned.
- **Departure** heading fixed relative to spray geometry: e.g. \(\theta_i^{\mathrm{out}} = \theta_i^{\mathrm{spray}} + 45°\) (or boom axis + 45°).
- Edge \(i \to j\) then connects pose \((x_i,y_i,\theta_i^{\mathrm{out}})\) to \((x_j,y_j,\theta_j^{\mathrm{in}})\) with Dubins/trochoid cost → **asymmetric** \(C_{ij} \neq C_{ji}\).

If spray must face into wind for drift control, set \(\theta_i^{\mathrm{spray}}\) from wind and fold the 45° offset into \(\theta_i^{\mathrm{out}}\).

### Spray volume in the objective

- Volume ≈ \(\sum_{i \in \mathrm{route}} dose(area_i)\) (from weed masks) + optional purge.
- **Must-spray-all:** volume is almost route-independent → optimise **time** only (TSP/ATSP).
- **Optional targets:** prize = herbicide saved / weed severity; OR-Tools `AddDisjunction` penalty = forgone benefit; or pymoo NSGA-II with objectives `[time, volume]`.

Weighted scalarisation:  
`cost = w_t * flight_time + w_v * spray_volume`  
(tune \(w\) from chemical price and battery/flight cost).

---

## 4. Open-source tools (verified links)

| Tool | Use | Link |
|------|-----|------|
| **Google OR-Tools** Routing | TSP / VRP / ATSP via distance matrix; drop nodes (penalties) | TSP guide: https://developers.google.com/optimization/routing/tsp · Penalties: https://developers.google.com/optimization/routing/penalties · Routing hub: https://developers.google.com/optimization/routing |
| **Concorde** | Exact symmetric TSP (academic) | https://www.math.uwaterloo.ca/tsp/concorde.html |
| **LKH / LKH-3** | Strong heuristic TSP / constrained variants (widely used with ATSP transforms) | Author page often at https://akira.ruc.dk/~keld/research/LKH/ (verify locally); common mirror used in planning repos: https://github.com/cerebis/LKH3 |
| **pymoo** | Multi-objective (e.g. time vs volume) with permutation encoding | https://pymoo.org/ · https://github.com/anyoptimization/pymoo |
| **dubins (PyPI)** | Shortest Dubins path between 2D poses | https://pypi.org/project/dubins/ |
| **castacks/trochoids** | Time-optimal UAV paths **in wind** + Dubins | https://github.com/castacks/trochoids |
| **OpenCoverage** | Polygon **coverage** (survey), wind-aligned sweeps, QGC WPL out — *not* spot TSP, but useful for scouting flights | https://pypi.org/project/opencoverage/ · https://github.com/irvingvasquez/opencoverage |
| **drone_scans** | Field strips + TSP sequencing + wind paths (Matlab) | https://github.com/arthurrichards77/drone_scans |

Export missions as QGroundControl / Mission Planner waypoints compatible with the ArduPilot stack in `docs/STACK.md`.

---

## 5. Suggested pipeline

```
weed GeoJSON (from WEED-ID)
        │
        ▼
assign pose per target (spray heading from wind; departure = +45°)
        │
        ▼
for all ordered pairs (i,j): C_ij = Dubins/trochoid time
                            + climb cost from DEM
                            + optional spray setup time
        │
        ▼
OR-Tools ATSP (matrix)  ──or──  LKH on ATSP→TSP transform
        │         optional: AddDisjunction penalties if tank/time limited
        ▼
smooth path / sample waypoints → .waypoints / QGC WPL
```

For Pareto exploration of time vs chemical use: **pymoo NSGA-II** on permutations, or repeated OR-Tools solves with different penalty weights.

---

## 6. Concise recommendation

1. Model as **ATSP** with edge costs = **wind-aware Dubins/trochoid time** + **DEM climb**.
2. Encode **45° departure** as fixed outbound heading in each node pose.
3. Solve with **OR-Tools** (matrix callback + guided local search); use **LKH** if instances get large.
4. If battery/tank binding: **drop visits** via OR-Tools penalties (prize-collecting), prizes ∝ weed severity / canopy area.
5. Use **pymoo** only if you need an explicit multi-objective front; use **OpenCoverage** for scout orthomosaic flights, not for spot-spray ordering.

---

## Key links

- OR-Tools TSP: https://developers.google.com/optimization/routing/tsp
- OR-Tools penalties / drop visits: https://developers.google.com/optimization/routing/penalties
- Concorde: https://www.math.uwaterloo.ca/tsp/concorde.html
- pymoo: https://pymoo.org/ · https://github.com/anyoptimization/pymoo
- dubins: https://pypi.org/project/dubins/ · https://github.com/AndrewWalker/pydubins
- trochoids (wind): https://github.com/castacks/trochoids
- OpenCoverage: https://pypi.org/project/opencoverage/
- drone_scans: https://github.com/arthurrichards77/drone_scans
- ODM docs (DEM/ortho): https://docs.opendronemap.org/

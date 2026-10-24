"""Cut one long visiting order into sorties (one battery and one tank each).

Contributed by Maya (AI agent, Sesame). Experimental: synthetic tests only.
Split out of map_route.py after Grok's review of PR #5.

"Route first, cluster second": the planner first orders ALL targets as one
tour (map_route_order), then this module walks that order and starts a new
sortie whenever the next target would break one of three limits:

  - battery: flying time so far + the hop to this target + the trip home
    must stay within the usable battery time (after the reserve)
  - tank:    the doses so far + this target's dose must fit in the tank
  - items:   the waypoints the sortie would upload (targets plus any
    obstacle climb points) must stay within the gate's max_mission_items

Each sortie is then re-ordered on its own with 2-opt, and that new order is
kept only if it still fits the battery and item limits.
"""
from __future__ import annotations

from typing import Callable, List, Sequence

from map_route_order import tour_cost, two_opt


def split_into_sorties(order: Sequence[int], C, dose_ml: Callable[[int], float],
                       items_for: Callable[[List[int]], int], budget_s: float,
                       tank_ml: float, max_items: int, big: float) -> List[List[int]]:
    """Greedy split of `order` (node numbers, 0 = home) into sorties.

    dose_ml(k):       dose for target node k
    items_for(nodes): waypoint count a sortie visiting `nodes` would upload
    big:              the cost that marks an infeasible leg
    """
    groups: List[List[int]] = []
    cur: List[int] = []          # the sortie being filled
    t_acc = 0.0                  # its flying time so far (home -> ... -> last target)
    dose_acc = 0.0               # its spray volume so far
    for k in order:
        # Step 1: cost of reaching k: from the sortie's last target, or from
        # home if the sortie is empty.
        step = C[cur[-1]][k] if cur else C[0][k]
        # Step 2: would adding k keep the sortie within every limit? The trip
        # home from k (C[k][0]) is counted, so the battery check always
        # includes getting back.
        fits = bool(cur and step < big
                    and t_acc + step + C[k][0] <= budget_s
                    and dose_acc + dose_ml(k) <= tank_ml
                    and items_for(cur + [k]) <= max_items)
        # Step 3: if not, close this sortie and start a fresh one from home.
        if cur and not fits:
            groups.append(cur)
            cur, t_acc, dose_acc = [], 0.0, 0.0
            step = C[0][k]
        # Step 4: add k to the (possibly new) sortie.
        cur.append(k)
        t_acc += step
        dose_acc += dose_ml(k)
    if cur:
        groups.append(cur)
    return groups


def tidy_sortie(C, group: List[int], items_for: Callable[[List[int]], int],
                budget_s: float, max_items: int, max_passes: int = 50) -> List[int]:
    """Re-order one sortie with 2-opt. Keep the new order only if it still
    fits the battery budget and the item limit; otherwise keep the original,
    which the split above already checked."""
    g2 = two_opt(C, group, max_passes)
    if tour_cost(C, g2) > budget_s or items_for(g2) > max_items:
        return list(group)
    return g2

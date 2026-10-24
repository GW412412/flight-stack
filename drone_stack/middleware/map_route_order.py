"""Visiting order for map targets: nearest neighbour, then 2-opt.

Contributed by Maya (AI agent, Sesame). Experimental: synthetic tests only.
Split out of map_route.py after Grok's review of PR #5.

Everything here works on a cost matrix C, where C[i][j] is the time in
seconds to fly from node i to node j (and centre on j). Node 0 is home;
nodes 1..n are targets. The matrix is ASYMMETRIC: on a hillside, climbing
from i to j takes longer than descending from j to i. That is why the usual
symmetric travelling-salesman shortcuts are not used.

These functions only reorder numbers. They know nothing about maps, drones
or safety limits; infeasible legs simply carry a huge cost (BIG in
map_route_obstacles), so they sort to the end and are never chosen when a
feasible alternative exists.
"""
from __future__ import annotations

from typing import List, Sequence


def tour_cost(C, order: Sequence[int]) -> float:
    """Time for home -> order[0] -> ... -> order[-1] -> home."""
    if not order:
        return 0.0
    # Out from home to the first target, and back home from the last one.
    cost = C[0][order[0]] + C[order[-1]][0]
    # Plus every target-to-target leg in between, in the order given.
    for a, b in zip(order, order[1:]):
        cost += C[a][b]
    return cost


def nearest_neighbour(C, nodes: Sequence[int]) -> List[int]:
    """A quick first order: from home, always go to the cheapest unvisited
    target next. Not optimal, but a good start for 2-opt."""
    left, order, cur = list(nodes), [], 0      # cur = 0: start at home
    while left:
        # Cheapest next hop from where we are. Ties go to the lowest node
        # number, so the same input always gives the same route.
        nxt = min(left, key=lambda j: (C[cur][j], j))
        order.append(nxt)
        left.remove(nxt)
        cur = nxt
    return order


def two_opt(C, order: Sequence[int], max_passes: int = 50) -> List[int]:
    """Improve an order by reversing stretches of it while that saves time.

    Classic 2-opt, except that the WHOLE tour is re-scored for each
    candidate: with asymmetric costs, reversing a stretch turns its uphill
    legs into downhill ones, so a local before/after shortcut would be wrong.
    O(n^3) per pass, which is fine for the ~25 weeds per paddock in
    docs/LIVE-PIPELINE.md."""
    best = list(order)
    best_cost = tour_cost(C, best)
    for _ in range(max_passes):
        improved = False
        # Try reversing every stretch best[i..j].
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                cand = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                cc = tour_cost(C, cand)
                # Keep it only if it is really faster (the tolerance stops
                # floating-point noise from looping forever).
                if cc < best_cost - 1e-9:
                    best, best_cost, improved = cand, cc, True
        # A full pass with no improvement: this is a local optimum.
        if not improved:
            break
    return best

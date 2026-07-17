"""Greedy heuristic implementation of the OptimizationEngine port.

No solver: anchor the leader, then walk back through the fleet placing each
follower one mean headway behind the (possibly already held) vehicle ahead
— holding it by the shortfall, clamped to the budget. Followers already far
enough back are left alone (holds cannot move a vehicle forward).

It exists to prove the port swappable (CARMA_OPTIMIZER=heuristic) and as
the production twin of the fake engine the use-case tests use.
"""

from dataclasses import dataclass

from carma.application.ports import OptimizationRequest
from carma.domain.headway import HeadwayPlan, build_plan, gaps, order_leader_first


@dataclass(frozen=True, slots=True)
class HeuristicOptimizationEngine:
    name: str = "heuristic"

    def solve(self, request: OptimizationRequest) -> HeadwayPlan:
        vehicles = order_leader_first(request.vehicles)
        if len(vehicles) < 2:
            return build_plan(vehicles, [0] * len(vehicles))
        positions = [vehicle.position_seconds for vehicle in vehicles]
        target = sum(gaps(positions)) / (len(positions) - 1)
        holds = [0]
        ahead = positions[0]
        for position in positions[1:]:
            hold = min(max(round(position - (ahead - target)), 0), request.max_hold_seconds)
            holds.append(hold)
            ahead = position - hold
        return build_plan(vehicles, holds)

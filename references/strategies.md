# Strategy Guide

> **Part of**: Multi-user Trip Planner (`../SKILL.md`)
> **Category**: integration
> **Reading Level**: Intermediate

## Purpose

Detailed strategy semantics, scoring formula, constraint rules, and real-world examples.

## Core Strategies

| Strategy | Description | Best When |
|---|---|---|
| same-train | All travelers share the same train (board at different stations) | A direct train passes through all origins |
| partial-same-train | Subset shares an overlapping main segment | Some travelers can share part of the route |
| transfer-merge | Different first legs, merge at hub, share final leg | No single train covers all origins |
| synchronized-arrival | Fully independent trains, arrival gap within threshold | No shared leg possible, but arrival times align |
| best-compromise | Mixed strategy optimized by weighted score | Complex trade-offs between strategies |

## Strategy Generation Pipeline

1. **Detect same-train**: Find trains that appear in multiple travelers' direct routes. A train passing through both origins means travelers can board at different stations and ride together.

2. **Generate transfer-merge**: For each traveler, find hub cities between origin and destination. Query origin->hub and hub->destination legs. Combine into transfer itineraries.

3. **Find synchronized-arrival**: For travelers with no shared trains, find independent routes where arrival times are within the threshold.

4. **Build partial-same-train**: When some but not all travelers share a train segment, classify as partial-same-train with `shared_train_ratio`.

5. **Strategy downgrade**: If a combination is marked "same-train" but travelers don't share any train number, automatically downgrade: same-train -> partial-same-train (if pairwise overlap) -> synchronized-arrival (if no overlap).

## Scoring Formula

```
score = 50 + (bonus - penalty) * 100

penalty = w_travel_time      * normalized_total_travel_time
        + w_price            * normalized_total_price
        + w_transfer_penalty * normalized_transfer_count
        + w_arrival_sync     * normalized_arrival_time_diff
        + w_comfort_penalty  * normalized_comfort_penalty

bonus   = w_priority_satisfaction * normalized_priority_score
        + w_same_train_bonus      * normalized_shared_ratio
```

- Score clamped to [0, 100]
- All normalized features in [0, 1]
- Weights normalized to sum to 1.0
- `same_train_bonus` rewards shared train segments
- `arrival_sync` penalizes arrival time spread

## Weight Profiles

| Profile | travel_time | price | transfer | arrival_sync | comfort | priority | same_train |
|---|---|---|---|---|---|---|---|
| balanced | 0.25 | 0.15 | 0.20 | 0.15 | 0.10 | 0.05 | 0.10 |
| speed-first | 0.40 | 0.10 | 0.15 | 0.15 | 0.05 | 0.05 | 0.10 |
| comfort-first | 0.15 | 0.10 | 0.30 | 0.10 | 0.20 | 0.05 | 0.10 |
| budget-first | 0.15 | 0.35 | 0.15 | 0.10 | 0.10 | 0.05 | 0.10 |
| sync-first | 0.15 | 0.10 | 0.15 | 0.35 | 0.05 | 0.05 | 0.15 |

## Example: Guangzhou + Wuhan -> Beijing

**Input**: Two travelers, balanced weights, max_transfers=2, arrival_sync=45min

**Plan 1 (same-train, score=47.9)**:
- traveler_1: G302 广州南 08:37 -> 北京西 16:27
- traveler_2: G302 武汉 12:15 -> 北京西 16:27
- Spread: 0min (perfect sync), 0 transfers

**Plan 2 (synchronized-arrival, score=23.8)**:
- traveler_1: G304 广州南 12:13 -> 北京西 19:30
- traveler_2: G1580 武汉 12:55 -> 北京西 19:13
- Spread: 17min, 0 transfers

**Plan 3 (mixed, score=14.1)**:
- traveler_1: G1026 广州南 07:22 -> 北京西 17:58 (same-train)
- traveler_2: G348 武汉 13:40 -> 北京西 17:28 (synchronized-arrival)
- Spread: 30min

## Example: Chengdu + Shanghai -> Shenzhen

**Input**: Cross-region route, no shared direct trains

**Plan 1 (transfer-merge, score=30.3)**:
- traveler_1: G3705 成都东 07:39 -> 广州南 14:33, G419 广州南 14:49 -> 深圳北 15:27
- traveler_2: G245 上海虹桥 08:00 -> 南昌西 11:04, G3345 南昌西 11:20 -> 深圳 14:52
- Spread: 35min, 1 transfer each

## Relaxation Suggestions

When `total_feasible == 0`, the system automatically suggests constraint relaxations:

| Dimension | Typical Relaxation | Effect |
|---|---|---|
| max_transfers | 1 -> 2 | Enables transfer-merge strategies |
| arrival_sync_minutes | 45 -> 75 | Allows wider arrival spread |
| departure_time_range | 07:00-15:00 -> 06:00-20:00 | More train options |
| accept_standing | false -> true | Includes standing tickets |

## Hub Selection Algorithm

Hub cities are selected based on geographic detour ratio:

1. Calculate direct distance (origin -> destination) using Haversine
2. For each candidate hub, calculate detour = (origin->hub + hub->dest) / direct
3. Filter out hubs with detour > 2.0x
4. Sort by detour ratio, select top 2 hubs
5. Hub candidates: 30+ major Chinese rail hubs (Wuhan, Zhengzhou, Changsha, etc.)

## Summary

- Five strategy types cover all multi-user coordination scenarios
- Scoring formula balances time, cost, comfort, and synchronization
- Strategy auto-downgrade ensures valid combinations are never rejected
- Relaxation suggestions help users find plans even with strict initial constraints
- Hub selection is geographic, not hard-coded — works for any city pair

## Related References

- Architecture (`architecture.md`) - Data acquisition and pipeline
- API Reference (`api-reference.md`) - Tool parameters and schemas

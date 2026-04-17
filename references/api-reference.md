# API Reference

> **Part of**: Multi-user Trip Planner (`../SKILL.md`)
> **Category**: integration
> **Reading Level**: Intermediate

## Purpose

Complete input/output schemas, tool parameters, and provider contract details.

## MCP Tools

### trip_planner_quick_plan (Recommended Entry Point)

Minimal input, auto-resolves from real-time data, supports Chinese city names.

**Parameters:**
- `origins` (string, required): Comma-separated cities, e.g., "Guangzhou,Wuhan" or "广州,武汉"
- `destination` (string, required): City name, e.g., "Beijing" or "北京"
- `date` (string, required): Travel date YYYY-MM-DD
- `departure_time_range` (string, default "07:00-15:00"): Departure window
- `max_transfers` (int, default 2): Maximum transfers per traveler
- `arrival_sync_minutes` (int, default 45): Maximum arrival time difference
- `weight_profile` (string, default "balanced"): Scoring profile
- `data_source` (string, default "auto"): Provider mode
- `topk` (int, default 3): Number of top plans to return

### trip_planner_parse_request

Extract structured params from natural language. Returns inferred fields and missing_fields list.

**Parameters:**
- `request_text` (string, required): Free-text trip request

### trip_planner_plan

Full control over constraints and weights. Requires complete payload with candidate_options.

**Parameters:**
- `payload` (JSON, required): Complete planning payload (see schema below)
- `topk` (int, default 3): Number of top plans
- `data_source` (string, default "auto"): Provider mode

### trip_planner_plan_with_provider

Auto-fetches candidate options from provider, then plans.

**Parameters:**
- `payload` (JSON, required): Planning payload (candidate_options auto-resolved)
- `provider_name` (string, default "auto"): Provider mode
- `topk` (int, default 3): Number of top plans

### trip_planner_plan_from_file

Load payload from JSON file and plan.

**Parameters:**
- `file_path` (string, required): Path to JSON payload file
- `topk` (int, default 3): Number of top plans

### trip_planner_explain

Generate human-friendly plan explanation.

**Parameters:**
- `plan_json` (JSON, required): Plan result to explain
- `audience` (string, default "general"): "general" | "technical" | "elderly"

### trip_planner_validate_stations

Validate station names against verified HSR database.

**Parameters:**
- `station_names` (list[string], required): Station names to validate

### trip_planner_list_cities

List all 157 supported cities and their HSR stations.

**Parameters:** None

## Input Schema (Full Payload)

```json
{
  "travelers": [
    {
      "name": "string",
      "origin": "string",
      "earliest_departure": "YYYY-MM-DD HH:MM",
      "latest_departure": "YYYY-MM-DD HH:MM",
      "priority_weight": 1.0,
      "preferences": { "max_transfers": 2, "avoid_transfer": false }
    }
  ],
  "destination": "string",
  "date": "YYYY-MM-DD",
  "constraints": {
    "max_transfers": 2,
    "max_arrival_time_diff_minutes": 45,
    "latest_arrival": "YYYY-MM-DD HH:MM",
    "must_train_types": ["HSR"],
    "accept_standing": false,
    "allowed_transfer_hubs": ["Wuhan", "Zhengzhou East"]
  },
  "weights": {
    "travel_time": 0.25,
    "price": 0.15,
    "transfer_penalty": 0.2,
    "arrival_sync": 0.15,
    "comfort_penalty": 0.1,
    "priority_satisfaction": 0.05,
    "same_train_bonus": 0.1
  }
}
```

## Output Schema

```json
{
  "total_feasible": 8,
  "top_plans": [
    {
      "rank": 1,
      "score": 47.9,
      "strategy_mix": ["same-train"],
      "total_travel_minutes": 722,
      "total_price": 830.0,
      "arrival_spread_minutes": 0,
      "traveler_plans": { "traveler_1": { ... }, "traveler_2": { ... } },
      "reasoning": "同车全程; 到达时间完美同步; 无中转..."
    }
  ],
  "relaxation_suggestions": [],
  "meta": {
    "data_source": "12306-direct",
    "total_combos": 144,
    "timed_out": false
  }
}
```

## Constraint Validation Rules

Discard any itinerary violating:
- `latest_arrival` cutoff
- `max_transfers` per traveler and total
- `max_arrival_time_diff_minutes` across all travelers
- Individual traveler departure windows
- `must_train_types` filter
- `accept_standing` rejection
- `allowed_transfer_hubs` whitelist
- Per-traveler preference constraints
- Transfer time: 15-180 minutes between legs
- Same-train consistency: auto-downgrade strategy if shared train not found

## Error Handling

| Error | Cause | Action |
|---|---|---|
| `ProviderError` | External API failure | Auto-fallback to next tier |
| `ValueError` | Invalid input | Ask user for corrected info |
| Timeout | API slow | Check `timed_out` in meta, suggest narrowing constraints |
| Network error | 12306 unreachable | Auto-fallback to estimation |

## Summary

- `trip_planner_quick_plan` is the default entry point for most requests
- Full payload gives complete control over constraints and weights
- Output always includes `data_source`, `relaxation_suggestions`, and `meta`
- Constraint validation is strict but auto-downgrades strategies instead of rejecting

## Related References

- Architecture (`architecture.md`) - Data acquisition and pipeline
- Strategy Guide (`strategies.md`) - Scoring formula and strategy details

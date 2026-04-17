# Architecture & Data Flow

> **Part of**: Multi-user Trip Planner (`../SKILL.md`)
> **Category**: integration
> **Reading Level**: Intermediate

## Purpose

Detailed architecture of the three-tier data acquisition system, LLM-Skill collaboration boundary, and complete data pipeline.

## Three-Tier Data Acquisition

```
+------------------+     +------------------+     +----------------------+
|  12306-direct    | --> |  12306-mcp       | --> | distance-estimation  |
|  (PRIMARY)       |     |  (SECONDARY)     |     | (TERTIARY/FALLBACK)  |
+------------------+     +------------------+     +----------------------+
| Free, real-time  |     | External MCP     |     | Haversine formula    |
| No API key       |     | npx 12306-mcp    |     | For uncovered routes |
| Direct public API|     | Set env var      |     | Always available     |
+------------------+     +------------------+     +----------------------+
```

### Tier 1: 12306-direct

- Queries official 12306 `leftTicket/query` endpoint directly
- Station code mapping via `station_name.js` with TTL cache
- Rate-limited to 0.5 req/s, thread-safe
- Multiple endpoint fallback (query/queryZ/queryA/queryG)
- Session reuse across requests (shared session with cookie management)
- No API key or registration required

### Tier 2: 12306-mcp

- Connects to external 12306-MCP server (npm package `12306-mcp`)
- Requires `TRIP_PLANNER_12306_MCP_URL` environment variable
- Supports both direct and interline (transfer) route queries
- Fallback when direct API is blocked or returns errors

### Tier 3: distance-estimation

- Haversine formula based on verified station coordinates
- Estimated speed: 250 km/h (G-trains), 200 km/h (D-trains)
- Price estimation: 0.46 CNY/km (second class)
- Always available as universal fallback
- Data source marked as `distance-estimation` in output

**Default mode: `data_source=auto`** — automatically tries all three tiers in order.

## LLM-Skill Collaboration Boundary

```
+--------------------------------------------------+
|                    LLM Agent                      |
|  - Natural language understanding & intent parse  |
|  - Missing information follow-up questions        |
|  - Result presentation & explanation to user      |
|  - User interaction & plan selection              |
|  - Context management across conversation turns   |
+--------------------------------------------------+
                        |
                        | Structured API calls
                        v
+--------------------------------------------------+
|              Skill (This Tool)                     |
|  - Real-time data fetching (12306 API queries)    |
|  - Constraint filtering (time/transfers/arrival)  |
|  - Strategy generation (same-train/transfer/sync) |
|  - Scoring & ranking (weighted optimization)      |
|  - Route analysis & validation                    |
|  - Station name resolution & normalization        |
+--------------------------------------------------+
```

**CRITICAL**: LLM must NOT participate in data filtering or strategy generation. Never fabricate train schedules, prices, or travel times. Never filter candidates based on LLM's own knowledge.

## Complete Data Flow

```
User Input (Natural Language)
    |
    v
[LLM] Parse intent, extract cities/date/preferences
    |
    v
[Skill] trip_planner_parse_request / trip_planner_quick_plan
    |
    v
[Skill] Resolve city names -> station codes (station_repository)
    |
    v
[Skill] Fetch real-time train data:
    |   12306-direct.query_trains(from_code, to_code, date)
    |   -> If fails: 12306-mcp.query_trains(...)
    |   -> If fails: distance-estimation (Haversine)
    |
    v
[Skill] Pre-fetch hub->destination data (shared cache)
    |
    v
[Skill] Apply strategy generation:
    |   - Detect same-train opportunities
    |   - Generate transfer-merge via hub cities
    |   - Find synchronized-arrival combinations
    |   - Build partial-same-train options
    |
    v
[Skill] Constraint validation & scoring:
    |   - Filter by time/transfers/arrival diff
    |   - Apply weighted scoring formula
    |   - Rank and ensure diversity
    |   - Generate relaxation suggestions if 0 feasible
    |
    v
[Skill] Return structured results (JSON/Markdown)
    |
    v
[LLM] Present results, explain trade-offs, ask for user choice
```

## Performance Optimizations

- **Parallel API requests**: ThreadPoolExecutor for multi-traveler queries
- **Shared session**: 12306 HTTP session reused across all adapter instances
- **Adapter caching**: Provider instances cached to avoid re-initialization
- **Hub->dest cache**: Pre-fetched hub->destination data shared across travelers
- **Smart hub skip**: Skip hub queries when same-train detected with sufficient options
- **Strategy-priority trimming**: When trimming options, keep same-train > partial-same-train > transfer-merge > synchronized-arrival
- **File-based TTL cache**: 600s TTL for repeated queries

## Provider Modes

| Mode | Description | Requires |
|---|---|---|
| `auto` | Three-tier fallback (recommended) | Nothing |
| `12306-direct` | Direct 12306 public API | Nothing |
| `12306-mcp` | External 12306-MCP server | `TRIP_PLANNER_12306_MCP_URL` |
| `12306` | 12306 proxy API | endpoint + key |
| `ctrip` | Ctrip proxy API | endpoint + key |
| `mock` | Distance estimation only | Nothing |

## Summary

- Three-tier data acquisition ensures reliability: real-time API -> MCP -> estimation
- Clear LLM-Skill boundary: LLM handles language, Skill handles data and optimization
- Performance optimized with parallel requests, shared caches, and smart query skipping
- Default `auto` mode provides best experience with zero configuration

## Related References

- API Reference (`api-reference.md`) - Tool parameters and schemas
- Strategy Guide (`strategies.md`) - Strategy semantics and scoring

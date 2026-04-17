---
name: multi-user-trip-planner
description: Plan coordinated multi-user high-speed rail trips; when users need multi-person trip planning from different origins to one destination
dependency:
  python:
    - pydantic>=2.0
    - fastapi
    - uvicorn
    - httpx
    - requests
    - mcp
---

# Multi-user Trip Planner

Plan and rank coordinated rail itineraries where N travelers depart from different origins and converge on one destination. Uses real-time 12306 data with three-tier fallback (12306-direct -> 12306-mcp -> distance-estimation).

## When to Use This Skill

- User mentions multi-person trip planning ("我在广州，朋友在武汉，我们要去北京")
- Same-train routing across multiple boarding stations
- Transfer-merge meetup at intermediate hub
- Synchronized arrival from different departure cities
- Group rail trip comparison and ranking
- Chinese city names (广州、武汉、北京、同步到达、同车、中转)

## Core Principles

1. **Real-time data first**: Always query 12306 API before estimation. Never fabricate train schedules or prices.
2. **LLM does NOT filter or generate strategies**: The skill handles data fetching, strategy generation, constraint validation, and scoring. LLM only parses intent, asks follow-ups, and presents results.
3. **Strategy diversity**: Results include different strategy types (same-train, partial-same-train, transfer-merge, synchronized-arrival), not just variations of one strategy.
4. **Graceful degradation**: When no feasible plans exist, provide relaxation suggestions. When API fails, fall back to estimation.

## Quick Start

1. **Quick plan**: Call `trip_planner_quick_plan(origins="Guangzhou,Wuhan", destination="Beijing", date="2026-04-20")`
2. **Review results**: Check `top_plans` for ranked options with scores, strategies, and trade-offs
3. **Present to user**: Explain top 2-3 options with key differences (time, price, arrival spread)
4. **Adjust if needed**: If `total_feasible == 0`, check `relaxation_suggestions` and re-run with relaxed constraints

## Tool Selection

| Scenario | Tool | Why |
|---|---|---|
| Natural language / vague request | `trip_planner_parse_request` | Extract structured params from free text |
| Quick planning, only know cities + date | `trip_planner_quick_plan` | Minimal input, auto-resolves from real-time data, Chinese support |
| Full structured data ready | `trip_planner_plan` | Full control over constraints and weights |
| Data in JSON file | `trip_planner_plan_from_file` | Batch processing |
| Need auto-fetched candidates | `trip_planner_plan_with_provider` | Auto provider with real-time data |
| Explain a plan result | `trip_planner_explain` | Human-friendly summary |
| Verify station names | `trip_planner_validate_stations` | Check against verified database |
| List supported cities | `trip_planner_list_cities` | Show all 157 supported cities |

## Weight Profiles

| Profile | Focus | When to Use |
|---|---|---|
| `balanced` | Even trade-off | Default |
| `speed-first` | Minimize travel time | Urgent trips |
| `comfort-first` | Minimize transfers + standing | Elderly, families |
| `budget-first` | Minimize price | Cost-sensitive |
| `sync-first` | Minimize arrival gap | Must arrive together |

## LLM Collaboration Rules

**LLM MUST NOT**: Fabricate train data, filter candidates based on own knowledge, decide which strategies to apply, override validation results.

**LLM SHOULD**: Extract structured params from natural language, ask follow-ups for missing info, present results in user-friendly language, help users choose between options, re-invoke skill with adjusted params when needed.

**Data source transparency**: Output always indicates data source. If `distance-estimation` was used, inform user that times and prices are approximate.

## Navigation

For detailed information:
- **Architecture & Data Flow**: `references/architecture.md` - Three-tier data acquisition, LLM-Skill boundary, complete pipeline
- **API Reference**: `references/api-reference.md` - Full input/output schemas, tool parameters, provider modes
- **Strategy Guide**: `references/strategies.md` - Strategy semantics, scoring formula, constraint rules, examples

## Key Reminders

- ALWAYS use `trip_planner_quick_plan` as the default entry point for most user requests
- When `total_feasible == 0`, check `relaxation_suggestions` before telling user "no plans found"
- Data source `12306-direct` = real-time; `distance-estimation` = approximate — always disclose this
- Default `data_source=auto` (three-tier fallback) — no need to specify unless user requests a specific provider
- Chinese city names are supported in `trip_planner_quick_plan` and `trip_planner_parse_request`

## Execution

### MCP Server

```bash
pip install mcp
python ${CLAUDE_SKILL_DIR}/scripts/mcp_server.py
```

### CLI

```bash
python ${CLAUDE_SKILL_DIR}/scripts/plan_trips.py --input input.json --topk 3
```

### FastAPI

```bash
pip install -r ${CLAUDE_SKILL_DIR}/requirements.txt
python ${CLAUDE_SKILL_DIR}/scripts/service_api.py
```

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `TRIP_PLANNER_12306_MCP_URL` | 12306-MCP server endpoint | (empty) |
| `TRIP_PLANNER_CACHE_DIR` | Cache directory | `.cache/trip_planner` |
| `TRIP_PLANNER_LOG_LEVEL` | Logging level | `WARNING` |

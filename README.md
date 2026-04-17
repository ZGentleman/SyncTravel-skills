# SyncTravel 🚄

**Multi-origin synchronized trip planner for China's high-speed rail**

[English](#) · [中文](README.zh-CN.md)

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io)

---

## The Problem

You're in Guangzhou, your partner's in Wuhan, and you're both heading to Beijing this weekend. You open 12306 and see dozens of trains — which ones get you there at roughly the same time? You each search separately, screenshot results, compare times in a chat, and 30 minutes later you still haven't decided. Oh, and what about transfer routes that might be cheaper? You didn't even consider those.

**SyncTravel solves this.** Give it multiple origin cities, one destination, and a date — it queries live 12306 data, generates strategy-optimized route combinations, and ranks them by score.

## Quick Example

```python
from scripts.utils import build_quick_payload
from scripts.provider_layer import fetch_and_generate
from scripts.plan_trips import plan_trip_data

payload = build_quick_payload(origins="广州,武汉", destination="北京", date="2026-04-20")
candidates = fetch_and_generate(payload, provider_name="auto")
payload["candidate_options"] = candidates
result = plan_trip_data(payload, topk=3)
```

**Output (live 12306 data):**

| # | Strategy | Arrival Gap | Total Cost | Details |
|---|----------|-------------|------------|---------|
| 1 | Same Train | 0 min | ¥830 | Both on G302 — board at Guangzhou/Wuhan, arrive Beijing West |
| 2 | Partial Same Train | 0 min | ¥786 | Guangzhou direct on G304; Wuhan transfers at Zhengzhou East to G304 |
| 3 | Sync Arrival | 17 min | ¥937 | Separate trains, arrive within 17 minutes of each other |

## Quick Start

```bash
git clone https://github.com/your-username/SyncTravel.git
cd SyncTravel
pip install -r requirements.txt
```

Three ways to use it:

**Python SDK** — 3 lines of code, see [Quick Example](#quick-example) above.

**AI Agent (MCP)** — Add to your MCP client config:
```json
{
  "mcpServers": {
    "sync-travel": {
      "command": "python",
      "args": [".cursor/skills/multi-user-trip-planner/scripts/mcp_server.py"]
    }
  }
}
```
Then just say: *"I'm in Guangzhou, my friend's in Wuhan, we're going to Beijing on April 20th"*

**REST API** — `python scripts/service_api.py` then `POST /plan/quick`

---

## Features

- **Live 12306 data** — Direct public API, free, no registration, no API key
- **5 planning strategies** — Same Train, Partial Same Train, Transfer Merge, Sync Arrival, Best Compromise
- **Composite scoring** — Time, cost, transfers, arrival sync, comfort — weighted and ranked
- **Auto constraint relaxation** — If no plan meets your constraints, it suggests which ones to relax
- **157+ cities** — Verified HSR station data with Chinese city name support
- **MCP-native** — Works as an Agent Skill in any MCP-compatible AI client
- **3-tier data fallback** — 12306 direct → 12306-MCP → distance estimation (never returns empty)

## Real-World Scenarios

| Scenario | Origins → Destination | What SyncTravel Does |
|----------|----------------------|---------------------|
| Long-distance couple | Beijing + Shanghai → Nanjing | Finds trains that arrive close together |
| Group trip | Chengdu + Wuhan + Hangzhou → Changsha | Plans for 3+ people with different strategies |
| Business meetup | Shenzhen + Xi'an → Zhengzhou | Minimizes waiting time at destination |
| Family reunion | Harbin + Shanghai → Hometown | Balances cost and convenience |

---

## Architecture & Design

SyncTravel follows a layered architecture with clear separation of concerns:

```
┌─────────────────────────────────────────────┐
│            User / AI Agent                   │
│     (Natural language → structured input)    │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│            MCP Server (8 Tools)              │
│  quick_plan · parse_request · plan · explain │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│         Data Acquisition Layer               │
│  ┌───────────┐ ┌──────────┐ ┌────────────┐ │
│  │12306 Direct│→│12306-MCP │→│Dist Estimate│ │
│  └───────────┘ └──────────┘ └────────────┘ │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│         Strategy Generator                   │
│  same_train · partial_same · transfer_merge │
│  sync_arrival · best_compromise              │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│           Planning Engine                    │
│  validate → score → rank → diversify        │
│  → relax constraints if needed               │
└─────────────────────────────────────────────┘
```

### Key Design Decisions

**Data-fetching ≠ Strategy logic.** The provider layer fetches raw candidate trains. The strategy generator applies planning logic independently. This means you can swap data sources without touching strategy code, and vice versa.

**LLM-Skill boundary.** The LLM handles natural language understanding, missing-info follow-up, and result explanation. The Skill handles data acquisition, constraint validation, and optimization scoring. The LLM never fabricates train data; the Skill never makes decisions for the user.

**3-tier fallback.** Real-time 12306 data is preferred, but if it's unavailable (rate limits, network issues), the system falls back to 12306-MCP, then to distance-based estimation. Every query returns a result.

**Strategy downgrade.** When a combination is tagged "same_train" but the trains don't actually match, the system downgrades the strategy to "partial_same_train" or "sync_arrival" instead of discarding the combination. This prevents valid routes from being lost.

### Strategies Explained

| Strategy | What It Means | Example |
|----------|--------------|---------|
| **Same Train** | Both riders on the same train | G80 departs Guangzhou, stops at Wuhan — both board, same train to Beijing |
| **Partial Same Train** | Share a train after a transfer | A goes direct; B transfers at Zhengzhou East, then joins A on the same train |
| **Transfer Merge** | Meet at a hub, continue together | Both arrive at Zhengzhou, meet up, take the same train to Beijing |
| **Sync Arrival** | Separate trains, similar arrival time | A takes G80, B takes G1580 — arrive 17 minutes apart |
| **Best Compromise** | Mixed strategy, best overall score | A uses same-train, B uses transfer-merge |

---

## Agent Skill Integration

SyncTravel is a standard MCP Agent Skill. It exposes 8 tools:

| Tool | Purpose |
|------|---------|
| `trip_planner_quick_plan` | Main entry — cities + date → ranked plans |
| `trip_planner_parse_request` | Extract structured params from natural language |
| `trip_planner_plan` | Full-param planning |
| `trip_planner_plan_with_provider` | Planning with explicit data source |
| `trip_planner_plan_from_file` | Planning from JSON input file |
| `trip_planner_explain` | Human-readable plan explanation |
| `trip_planner_validate_stations` | Verify station names |
| `trip_planner_list_cities` | List supported cities |

### How AI + Skill Work Together

```
User: "我和朋友分别从广州和武汉出发，4月20号去北京"
  │
  ▼
AI: Parses intent → calls trip_planner_quick_plan
  │
  ▼
Skill: Queries 12306 → generates strategies → scores & ranks
  │
  ▼
AI: Presents results in natural language, asks follow-up questions
```

---

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `TRIP_PLANNER_12306_MCP_URL` | External 12306-MCP server URL | (empty) |
| `TRIP_PLANNER_CACHE_DIR` | Cache directory | `.cache/trip_planner` |
| `TRIP_PLANNER_CACHE_TTL_SECONDS` | Cache TTL in seconds | `600` |
| `TRIP_PLANNER_LOG_LEVEL` | Logging level | `WARNING` |

## Project Structure

```
SyncTravel/
├── .cursor/skills/multi-user-trip-planner/
│   ├── SKILL.md                    # Agent Skill entry point
│   ├── scripts/
│   │   ├── mcp_server.py           # MCP server (8 tools)
│   │   ├── plan_trips.py           # Core planning engine
│   │   ├── provider_layer.py       # Data acquisition + strategy orchestration
│   │   ├── strategy_generator.py   # 5 strategy algorithms
│   │   ├── railway_api.py          # 12306 / Ctrip adapters
│   │   ├── models.py               # Pydantic data models
│   │   ├── utils.py                # Payload builders, NL parser, formatters
│   │   ├── station_repository.py   # City-station mapping
│   │   ├── route_analyzer.py       # Route validation & analysis
│   │   └── service_api.py          # REST API (FastAPI)
│   ├── assets/
│   │   └── station_data.json       # 157+ city HSR station database
│   └── references/
│       ├── architecture.md         # Architecture deep-dive
│       ├── api-reference.md        # API reference
│       └── strategies.md           # Strategy semantics & scoring
├── examples/
│   ├── quick_start.py              # Getting started example
│   └── quick_start.json            # Sample input
└── requirements.txt
```

## Performance

| Route | Travelers | Feasible Plans | Time |
|-------|-----------|----------------|------|
| Guangzhou + Wuhan → Beijing | 2 | 26 | ~5s (incl. 12306 query) |
| Chengdu + Shanghai → Shenzhen | 2 | 24 | ~12s |
| Hangzhou + Nanjing → Shanghai | 2 | 62 | ~8s |
| Estimation-only mode | any | 50+ | <0.1s |

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you'd like to change.

## License

[MIT](LICENSE)

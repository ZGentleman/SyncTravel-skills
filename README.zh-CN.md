# SyncTravel 🚄

**多人异地出发，同一目的地汇合 —— 高铁协同出行规划引擎**

[中文](#) · [English](README.md)

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io)

---

## 你是不是也遇到过这种情况？

你在广州，对象在武汉，周末约好一起去北京。打开12306一看，车次几十趟，哪趟能差不多时间到？两个人各自查，查完对着截图比时间，比完发现还有中转方案没考虑……来回折腾半小时还没定下来。

**SyncTravel 就是干这个的。** 输入多个出发城市、一个目的地、一个日期 —— 自动查12306实时数据，生成策略优化的路线组合，按评分排序输出。

## 快速看个例子

```python
from scripts.utils import build_quick_payload
from scripts.provider_layer import fetch_and_generate
from scripts.plan_trips import plan_trip_data

payload = build_quick_payload(origins="广州,武汉", destination="北京", date="2026-04-20")
candidates = fetch_and_generate(payload, provider_name="auto")
payload["candidate_options"] = candidates
result = plan_trip_data(payload, topk=3)
```

**输出（真实12306数据）：**

| # | 策略 | 到达时间差 | 总价 | 说明 |
|---|------|-----------|------|------|
| 1 | 同车 | 0分钟 | ¥830 | 两人都坐G302，广州/武汉上车，北京西下车 |
| 2 | 部分同车 | 0分钟 | ¥786 | 广州直达G304，武汉先到郑州东再转G304 |
| 3 | 同步到达 | 17分钟 | ¥937 | 各坐各的，差不多时间到 |

## 快速开始

```bash
git clone https://github.com/your-username/SyncTravel.git
cd SyncTravel
pip install -r requirements.txt
```

三种使用方式：

**Python SDK** —— 3行代码，见上面的[例子](#快速看个例子)。

**AI Agent（MCP协议）** —— 在MCP客户端配置：
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
然后直接说：*"我在广州，朋友在武汉，4月20号去北京，帮我规划一下"*

**REST API** —— `python scripts/service_api.py`，然后 `POST /plan/quick`

---

## 功能特性

- **实时12306数据** —— 直接调公共接口，免费，不用注册，不用API Key
- **5种规划策略** —— 同车、部分同车、中转汇合、同步到达、最优折中
- **综合评分排序** —— 时间、价格、换乘、到达同步度、舒适度，加权打分
- **自动约束放宽** —— 约束太严没有可行方案时，自动建议放宽哪些条件
- **全国157+城市** —— 已验证高铁站数据，支持中文城市名
- **MCP原生支持** —— 作为Agent Skill在任何MCP兼容的AI客户端中使用
- **三层降级兜底** —— 12306直连 → 12306-MCP → 距离估算，永远有结果

## 真实使用场景

| 场景 | 出发地 → 目的地 | SyncTravel做什么 |
|------|----------------|-----------------|
| 异地恋见面 | 北京 + 上海 → 南京 | 找到达时间最接近的车次 |
| 朋友出游 | 成都 + 武汉 + 杭州 → 长沙 | 3人以上多策略规划 |
| 出差汇合 | 深圳 + 西安 → 郑州 | 最小化到达等待时间 |
| 家庭聚会 | 哈尔滨 + 上海 → 老家 | 平衡价格和便利性 |

---

## 架构与设计

SyncTravel采用分层架构，数据获取与策略逻辑完全解耦：

```
┌─────────────────────────────────────────────┐
│            用户 / AI Agent                    │
│     （自然语言 → 结构化输入）                  │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│            MCP Server（8个工具）               │
│  quick_plan · parse_request · plan · explain │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│            数据获取层                          │
│  ┌───────────┐ ┌──────────┐ ┌────────────┐ │
│  │12306 直连 │→│12306-MCP │→│ 距离估算    │ │
│  └───────────┘ └──────────┘ └────────────┘ │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│            策略生成器                          │
│  同车 · 部分同车 · 中转汇合                   │
│  同步到达 · 最优折中                           │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│            规划引擎                            │
│  验证 → 打分 → 排序 → 多样性 → 放宽建议       │
└─────────────────────────────────────────────┘
```

### 核心设计决策

**数据获取 ≠ 策略逻辑。** Provider层只负责拉取原始候选车次，Strategy Generator独立应用规划逻辑。换数据源不用动策略代码，改策略不用动数据层。

**LLM-Skill边界。** LLM负责自然语言理解、缺失信息追问、结果解释；Skill负责数据获取、约束验证、优化评分。LLM不编造车次数据，Skill不替用户做决定。

**三层降级。** 优先实时12306数据，不可用时（限流、网络问题）降级到12306-MCP，再降级到距离估算。每次查询都有结果返回。

**策略降级。** 当组合被标记为"同车"但实际车次不匹配时，系统将策略降级为"部分同车"或"同步到达"，而不是丢弃整个组合。避免有效路线被误杀。

### 策略解释

| 策略 | 含义 | 举例 |
|------|------|------|
| **同车** | 两人坐同一趟车 | G80从广州出发，经停武汉，两人分别上车，同一趟车到北京 |
| **部分同车** | 中转后坐同一趟 | A直达北京，B先到郑州东，换乘后和A坐同一趟 |
| **中转汇合** | 在中间站碰头，再一起走 | 两人都到郑州，碰面后一起坐车去北京 |
| **同步到达** | 各坐各的，差不多时间到 | A坐G80，B坐G1580，到北京差17分钟 |
| **最优折中** | 混合策略，综合评分最优 | A用同车策略，B用中转汇合策略 |

---

## Agent Skill 集成

SyncTravel是标准MCP Agent Skill，暴露8个工具：

| 工具 | 用途 |
|------|------|
| `trip_planner_quick_plan` | 最常用 —— 城市+日期 → 排序方案 |
| `trip_planner_parse_request` | 从自然语言提取结构化参数 |
| `trip_planner_plan` | 完整参数规划 |
| `trip_planner_plan_with_provider` | 指定数据源规划 |
| `trip_planner_plan_from_file` | 从JSON文件规划 |
| `trip_planner_explain` | 人话解释方案 |
| `trip_planner_validate_stations` | 验证站名是否正确 |
| `trip_planner_list_cities` | 列出支持的城市 |

### AI + Skill 协作流程

```
用户: "我和朋友分别从广州和武汉出发，4月20号去北京"
  │
  ▼
AI: 解析意图 → 调用 trip_planner_quick_plan
  │
  ▼
Skill: 查询12306 → 生成策略 → 评分排序
  │
  ▼
AI: 用自然语言呈现结果，追问偏好
```

---

## 配置

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `TRIP_PLANNER_12306_MCP_URL` | 外部12306-MCP服务地址 | （空） |
| `TRIP_PLANNER_CACHE_DIR` | 缓存目录 | `.cache/trip_planner` |
| `TRIP_PLANNER_CACHE_TTL_SECONDS` | 缓存有效期（秒） | `600` |
| `TRIP_PLANNER_LOG_LEVEL` | 日志级别 | `WARNING` |

## 项目结构

```
SyncTravel/
├── .cursor/skills/multi-user-trip-planner/
│   ├── SKILL.md                    # Agent Skill 入口
│   ├── scripts/
│   │   ├── mcp_server.py           # MCP服务器（8个工具）
│   │   ├── plan_trips.py           # 核心规划引擎
│   │   ├── provider_layer.py       # 数据获取 + 策略编排
│   │   ├── strategy_generator.py   # 5种策略算法
│   │   ├── railway_api.py          # 12306 / 携程适配器
│   │   ├── models.py               # Pydantic 数据模型
│   │   ├── utils.py                # 载荷构建、自然语言解析、格式化
│   │   ├── station_repository.py   # 城市-站点映射
│   │   ├── route_analyzer.py       # 路线验证与分析
│   │   └── service_api.py          # REST API（FastAPI）
│   ├── assets/
│   │   └── station_data.json       # 157+城市高铁站数据库
│   └── references/
│       ├── architecture.md         # 架构详解
│       ├── api-reference.md        # API参考
│       └── strategies.md           # 策略语义与评分
├── examples/
│   ├── quick_start.py              # 快速上手示例
│   └── quick_start.json            # 示例输入
└── requirements.txt
```

## 性能

| 路线 | 人数 | 可行方案 | 耗时 |
|------|------|---------|------|
| 广州 + 武汉 → 北京 | 2 | 26 | ~5s（含12306查询） |
| 成都 + 上海 → 深圳 | 2 | 24 | ~12s |
| 杭州 + 南京 → 上海 | 2 | 62 | ~8s |
| 纯估算模式 | 任意 | 50+ | <0.1s |

## 参与贡献

欢迎提交 Pull Request。重大改动请先开 Issue 讨论。

## 开源协议

[MIT](LICENSE)

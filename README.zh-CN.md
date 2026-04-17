# SyncTravel skills

**多人异地出发，同一目的地汇合 —— 高铁协同出行规划引擎**

[中文](#) · [English](README.md)

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io)

---

## 📋 目录

- [问题场景](#-问题场景)
- [快速开始](#-快速开始)
- [功能特性](#-功能特性)
- [使用示例](#-使用示例)
- [架构设计](#-架构设计)
- [Agent Skill 集成](#-agent-skill-集成)
- [配置说明](#-配置说明)
- [项目结构](#-项目结构)
- [性能表现](#-性能表现)
- [参与贡献](#-参与贡献)

---

## 🎯 问题场景

你是不是也遇到过这种情况？

> 你在广州，对象在武汉，周末约好一起去北京。打开12306一看，车次几十趟，哪趟能差不多时间到？两个人各自查，查完对着截图比时间，比完发现还有中转方案没考虑……来回折腾半小时还没定下来。

**SyncTravel 就是解决这个问题的。** 输入多个出发城市、一个目的地、一个日期 —— 自动查询12306实时数据，生成策略优化的路线组合，按评分排序输出。

---

## 🚀 快速开始

### 安装

```bash
git clone https://github.com/ZGentleman/SyncTravel-skills.git
cd SyncTravel-skills
pip install -r requirements.txt
```

### 三种使用方式

#### 1. Python SDK（3行代码）

```python
from scripts.utils import build_quick_payload
from scripts.provider_layer import fetch_and_generate
from scripts.plan_trips import plan_trip_data

payload = build_quick_payload(origins="广州,武汉", destination="北京", date="2026-04-20")
candidates = fetch_and_generate(payload, provider_name="auto")
payload["candidate_options"] = candidates
result = plan_trip_data(payload, topk=3)
```

#### 2. AI Agent（MCP协议）

在 MCP 客户端配置：

```json
{
  "mcpServers": {
    "sync-travel": {
      "command": "python",
      "args": ["scripts/mcp_server.py"]
    }
  }
}
```

然后直接说：*"我在广州，朋友在武汉，4月20号去北京，帮我规划一下"*

#### 3. REST API

```bash
python scripts/service_api.py
```

然后调用：
```bash
curl -X POST http://localhost:8000/plan/quick \
  -H "Content-Type: application/json" \
  -d '{"origins": "广州,武汉", "destination": "北京", "date": "2026-04-20"}'
```

---

## ✨ 功能特性

| 特性 | 说明 |
|------|------|
| 🚄 **实时12306数据** | 直接调用公共接口，免费，无需注册，无需API Key |
| 🧠 **5种规划策略** | 同车、部分同车、中转汇合、同步到达、最优折中 |
| 📊 **综合评分排序** | 时间、价格、换乘、到达同步度、舒适度，加权打分 |
| 🔄 **自动约束放宽** | 约束太严没有可行方案时，自动建议放宽哪些条件 |
| 🗺️ **全国157+城市** | 已验证高铁站数据，支持中文城市名 |
| 🤖 **MCP原生支持** | 作为Agent Skill在任何MCP兼容的AI客户端中使用 |
| 🛡️ **三层降级兜底** | 12306直连 → 12306-MCP → 距离估算，永远有结果 |

---

## 💡 使用示例

### 演示效果

<p align="center">
  <img src="https://github.com/ZGentleman/blog-images/blob/main/test1.png?raw=true" width="80%" alt="SyncTravel Demo 1">
</p>
<p align="center">
  <img src="https://github.com/ZGentleman/blog-images/blob/main/test2.png?raw=true" width="80%" alt="SyncTravel Demo 2">
</p>
<p align="center">
  <img src="https://github.com/ZGentleman/blog-images/blob/main/test3.png?raw=true" width="80%" alt="SyncTravel Demo 3">
</p>

### 示例输出（真实12306数据）

| 排名 | 策略 | 到达时间差 | 总价 | 说明 |
|------|------|-----------|------|------|
| 1 | 同车 | 0分钟 | ¥830 | 两人都坐G302，广州/武汉上车，北京西下车 |
| 2 | 部分同车 | 0分钟 | ¥786 | 广州直达G304，武汉先到郑州东再转G304 |
| 3 | 同步到达 | 17分钟 | ¥937 | 各坐各的，差不多时间到 |

### 真实使用场景

| 场景 | 出发地 → 目的地 | SyncTravel 做什么 |
|------|----------------|------------------|
| 💑 异地恋见面 | 北京 + 上海 → 南京 | 找到达时间最接近的车次 |
| 👥 朋友出游 | 成都 + 武汉 + 杭州 → 长沙 | 3人以上多策略规划 |
| 💼 出差汇合 | 深圳 + 西安 → 郑州 | 最小化到达等待时间 |
| 👨‍👩‍👧‍👦 家庭聚会 | 哈尔滨 + 上海 → 老家 | 平衡价格和便利性 |

---

## 🏗️ 架构设计

SyncTravel 采用分层架构，数据获取与策略逻辑完全解耦：

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
│  ┌───────────┐ ┌──────────┐ ┌────────────┐  │
│  │12306 直连 │→│12306-MCP │→│ 距离估算    │  │
│  └───────────┘ └──────────┘ └────────────┘  │
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

1. **数据获取 ≠ 策略逻辑**  
   Provider 层只负责拉取原始候选车次，Strategy Generator 独立应用规划逻辑。换数据源不用动策略代码，改策略不用动数据层。

2. **LLM-Skill 边界**  
   - LLM 负责：自然语言理解、缺失信息追问、结果解释
   - Skill 负责：数据获取、约束验证、优化评分
   - LLM 不编造车次数据，Skill 不替用户做决定

3. **三层降级**  
   优先实时12306数据，不可用时（限流、网络问题）降级到 12306-MCP，再降级到距离估算。每次查询都有结果返回。

4. **策略降级**  
   当组合被标记为"同车"但实际车次不匹配时，系统将策略降级为"部分同车"或"同步到达"，而不是丢弃整个组合。避免有效路线被误杀。

### 策略详解

| 策略 | 含义 | 举例 |
|------|------|------|
| **同车** | 两人坐同一趟车 | G80从广州出发，经停武汉，两人分别上车，同一趟车到北京 |
| **部分同车** | 中转后坐同一趟 | A直达北京，B先到郑州东，换乘后和A坐同一趟 |
| **中转汇合** | 在中间站碰头，再一起走 | 两人都到郑州，碰面后一起坐车去北京 |
| **同步到达** | 各坐各的，差不多时间到 | A坐G80，B坐G1580，到北京差17分钟 |
| **最优折中** | 混合策略，综合评分最优 | A用同车策略，B用中转汇合策略 |

---

## 🤖 Agent Skill 集成

SyncTravel 是标准 MCP Agent Skill，暴露 8 个工具：

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

## ⚙️ 配置说明

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `TRIP_PLANNER_12306_MCP_URL` | 外部12306-MCP服务地址 | （空） |
| `TRIP_PLANNER_CACHE_DIR` | 缓存目录 | `.cache/trip_planner` |
| `TRIP_PLANNER_CACHE_TTL_SECONDS` | 缓存有效期（秒） | `600` |
| `TRIP_PLANNER_LOG_LEVEL` | 日志级别 | `WARNING` |

---

## 📁 项目结构

```
SyncTravel/
├── scripts/
│   ├── mcp_server.py           # MCP服务器（8个工具）
│   ├── plan_trips.py           # 核心规划引擎
│   ├── provider_layer.py       # 数据获取 + 策略编排
│   ├── strategy_generator.py   # 5种策略算法
│   ├── railway_api.py          # 12306 / 携程适配器
│   ├── models.py               # Pydantic 数据模型
│   ├── utils.py                # 载荷构建、自然语言解析、格式化
│   ├── station_repository.py   # 城市-站点映射
│   ├── route_analyzer.py       # 路线验证与分析
│   └── service_api.py          # REST API（FastAPI）
├── assets/
│   ├── station_data.json       # 157+城市高铁站数据库
│   └── input.sample.json       # 示例输入
├── references/
│   ├── architecture.md         # 架构详解
│   ├── api-reference.md        # API参考
│   └── strategies.md           # 策略语义与评分
├── SKILL.md                    # Agent Skill 入口
├── README.md                   # 英文文档
├── README.zh-CN.md             # 中文文档
└── requirements.txt            # 依赖列表
```

---

## ⚡ 性能表现

| 路线 | 人数 | 可行方案 | 耗时 |
|------|------|---------|------|
| 广州 + 武汉 → 北京 | 2 | 26 | ~5s（含12306查询） |
| 成都 + 上海 → 深圳 | 2 | 24 | ~12s |
| 杭州 + 南京 → 上海 | 2 | 62 | ~8s |
| 纯估算模式 | 任意 | 50+ | <0.1s |

---

## 🤝 参与贡献

欢迎提交 Pull Request！

1. Fork 本仓库
2. 创建你的特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交你的改动 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 打开一个 Pull Request

重大改动请先开 Issue 讨论。

---

## 📄 开源协议

本项目基于 [MIT](LICENSE) 协议开源。

---

<div align="center">

**⭐ 如果这个项目对你有帮助，请给它一个 Star！**

</div>

# EvoAlpha OS - 文档索引

> **版本**: v2.1.0
> **最后更新**: 2026-01-19
> **项目状态**: 📊 数据采集系统已完成 | 🚧 其他模块开发中

---

## 📚 文档导航

### 🚀 快速开始

| 文档 | 路径 | 说明 | 适用对象 |
|------|------|------|---------|
| **项目总览** | [README.md](./README.md) | ⭐ **首先阅读** | 所有人 |
| **数据采集快速开始** | [backend/data_job/docs/QUICKSTART.md](./backend/data_job/docs/QUICKSTART.md) | 3分钟上手 | 数据使用者 |

### 🏗️ 架构与设计

| 文档 | 路径 | 说明 | 状态 |
|------|------|------|------|
| **技术蓝图** | [BLUEPRINT.md](./BLUEPRINT.md) | 完整系统设计 | ✅ 已定稿 |
| **数据采集架构** | [backend/data_job/docs/ARCHITECTURE.md](./backend/data_job/docs/ARCHITECTURE.md) | v2.1.0架构设计 | ✅ 最新 |
| **项目结构** | [PROJECT_STRUCTURE.md](./PROJECT_STRUCTURE.md) | 目录结构说明 | ⚠️ 需更新 |

### 💻 开发指南

| 文档 | 路径 | 说明 | 状态 |
|------|------|------|------|
| **数据采集开发规范** | [backend/data_job/docs/DEVELOPMENT_GUIDE.md](./backend/data_job/docs/DEVELOPMENT_GUIDE.md) | 新增采集器指南 | ✅ 最新 |
| **开发日志** | [CLAUDE.md](./CLAUDE.md) | 开发经验记录 | ✅ 维护中 |

### 📊 模块文档

#### 数据采集系统 (v2.1.0 ✅ 已完成)

| 文档 | 路径 | 说明 |
|------|------|------|
| **主文档** | [backend/data_job/README.md](./backend/data_job/README.md) | 完整使用指南 |
| **快速开始** | [backend/data_job/docs/QUICKSTART.md](./backend/data_job/docs/QUICKSTART.md) | 3分钟上手 |
| **系统架构** | [backend/data_job/docs/ARCHITECTURE.md](./backend/data_job/docs/ARCHITECTURE.md) | 架构设计 |
| **开发指南** | [backend/data_job/docs/DEVELOPMENT_GUIDE.md](./backend/data_job/docs/DEVELOPMENT_GUIDE.md) | 开发规范 |
| **优化报告** | [backend/data_job/INCREMENTAL_UPDATE_REPORT.md](./backend/data_job/INCREMENTAL_UPDATE_REPORT.md) | 性能优化 |
| **采集规划** | [backend/data_job/COLLECTION_SCHEDULE.md](./backend/data_job/COLLECTION_SCHEDULE.md) | 频率规划 |

#### 其他模块 (开发中)

| 模块 | 文档 | 状态 |
|------|------|------|
| 量化引擎 | 待创建 | 🚧 开发中 |
| AI Agent | 待创建 | ⏳ 未开始 |
| API接口 | 待创建 | ⏳ 未开始 |
| 前端 | 待创建 | ⏳ 未开始 |

### 📋 历史文档（归档）

| 文档 | 路径 | 说明 | 归档原因 |
|------|------|------|---------|
| **代码移植计划** | [MIGRATION_PLAN.md](./MIGRATION_PLAN.md) | 从EvoQuant OS移植 | ✅ 移植已完成 |
| **数据分析报告** | [DATA_ANALYSIS.md](./DATA_ANALYSIS.md) | 数据采集模块分析 | ✅ 数据采集系统已完成 |
| **项目初始化计划** | [PROJECT_PLAN.md](./PROJECT_PLAN.md) | 项目初始化 | ✅ 初始化已完成 |

---

## 🗂️ 推荐阅读路径

### 对于新加入的开发者

```
1. README.md                    (了解项目概况)
   ↓
2. BLUEPRINT.md                 (理解技术架构)
   ↓
3. PROJECT_STRUCTURE.md         (熟悉目录结构)
   ↓
4. backend/data_job/docs/QUICKSTART.md  (了解数据采集)
   ↓
5. 选择感兴趣的模块深入阅读
```

### 对于数据使用者

```
1. README.md                    (了解项目概况)
   ↓
2. backend/data_job/docs/QUICKSTART.md  (快速开始使用)
   ↓
3. backend/data_job/README.md    (完整数据采集说明)
```

### 对于贡献者

```
1. README.md                    (了解项目概况)
   ↓
2. BLUEPRINT.md                 (理解技术架构)
   ↓
3. CLAUDE.md                    (学习开发经验)
   ↓
4. backend/data_job/docs/DEVELOPMENT_GUIDE.md  (学习开发规范)
   ↓
5. 选择模块贡献代码
```

---

## 📝 文档维护规范

### 更新频率

| 文档类型 | 更新频率 | 负责人 |
|---------|---------|--------|
| README.md | 每次重大更新 | 项目维护者 |
| 技术蓝图 | 重大架构变更时 | 架构师 |
| 模块文档 | 功能更新时 | 模块负责人 |
| 开发日志 | 每次开发活动时 | 开发者 |

### 版本标注

所有文档应在头部标注：
- 版本号
- 最后更新时间
- 文档状态（✅最新 / ⚠️需更新 / ❌过时）

### 归档流程

当文档内容过时时：
1. 移动到 `docs/archive/` 目录
2. 在本索引中记录归档信息
3. 更新相关链接指向新文档

---

## 🔍 文档搜索

### 按主题查找

**数据相关**：
- 数据采集 → [backend/data_job/README.md](./backend/data_job/README.md)
- 数据库结构 → [BLUEPRINT.md#三数据层设计](./BLUEPRINT.md#三数据层设计)

**架构相关**：
- 系统架构 → [BLUEPRINT.md#二技术架构](./BLUEPRINT.md#二技术架构)
- 模块设计 → [BLUEPRINT.md#四功能模块](./BLUEPRINT.md#四功能模块)

**开发相关**：
- 开发规范 → [backend/data_job/docs/DEVELOPMENT_GUIDE.md](./backend/data_job/docs/DEVELOPMENT_GUIDE.md)
- 开发经验 → [CLAUDE.md](./CLAUDE.md)

---

## 📞 获取帮助

如果文档有疑问或建议：
1. 查看对应模块的README
2. 查看开发日志 [CLAUDE.md](./CLAUDE.md)
3. 提交Issue或PR

---

**文档版本**: v2.1.0
**最后更新**: 2026-01-19
**维护者**: EvoAlpha OS Team

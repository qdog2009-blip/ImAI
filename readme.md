# 私有 IM 项目规则与架构规范

## 1. 项目概述

目标：构建一个高可用、生产就绪且部署灵活的私有即时通讯 (IM) 系统。

* **性能**：单服务器节点在生产环境 (Postgres+Redis) 下必须支持 1000+ 并发用户。
* **便携性**：支持极简模式 (SQLite+文件缓存)，实现零外部容器依赖的快速部署。
* **隐私**：私有化部署，客户端连接需要严格的“服务器密钥” (Server Secret Key) 校验。
* **扩展性**：AI 机器人、离线推送、数据存储与缓存均需通过设计模式实现无缝切换。
* **配置管理**：强制使用 `.env` 文件进行全局环境变量管理，实现代码与配置的完全分离。
* **平台**：Flutter (Android/iOS) 客户端 + Python 后端。

---

## 2. 技术栈 (严格限制)

**后端 (Server)**

* **语言**：Python 3.13。
* **核心框架**：FastAPI (HTTP) + `websockets` 库 (处理原始 Socket)。
* **并发与异步**：强制使用 `asyncio`。严禁任何阻塞式 I/O。
* **协议**：`protobuf` (将 `.proto` 编译为 Dart 强类型类)，所有通讯均使用protobuf，禁止使用json；除了文件、图片、视>频等使用http协议。
* **配置加载**：使用 `pydantic-settings` 或 `python-dotenv` 强制从 `.env` 文件加载全局配置。
* **数据库引擎 (策略+工厂模式)**：SQLAlchemy (异步 ORM) + Alembic。生产级使用 PostgreSQL (`asyncpg`)，轻量级使用 SQLite (`aiosqlite`)。
* **缓存与状态 (策略+工厂模式)**：统一 `BaseCacheProvider` 接口。高并发使用 Redis (`redis.asyncio`)，轻量级使用本地文件缓存 (`aiofiles`)。
* **存储策略**：支持 `LocalFileSystem` 或 `S3` (MinIO/AWS)。
* **大模型 (LLM)**：适配 OpenClaw，预留 DeepSeek 等模型接口。
* **推送分发 (Push)**：适配极光 JPush、MobPush 等。
* **网关与 DevOps**：Nginx (WSS 代理) + Docker (容器编排) + Gitea Webhook (自动化部署)。

**前端 (Client)**

* **框架**：Flutter (Stable 渠道)。
* **状态管理**：Riverpod 或 Provider。
* **本地数据库**：Isar (NoSQL) 或 SQLite (通过 `drift`) 用于消息持久化缓存。
* **网络**：`dio` (REST API), `web_socket_channel` (WS)。
* **协议**：`protobuf` (将 `.proto` 编译为 Dart 强类型类)，所有通讯均使用protobuf，禁止使用json；除了文件、图片、视频等使用http协议。

---

## 3. 架构蓝图 (Monorepo)

项目目录必须遵循以下结构。代码助手在创建文件时需严格按照此扁平化的路径规范进行对应：

* **`project_root/.env`**：本地开发环境变量配置文件（包含数据库、缓存类型、各类 API Key，**禁止提交至 Git**）
* **`project_root/.env.example`**：环境变量配置模板（用于示例和部署参考，**需提交至 Git**）
* **`project_root/devops/`**：部署与基础设施（包含 docker-compose.yml, nginx.conf）
* **`project_root/protos/`**：双端共享协议定义（包含 chat.proto, build_protos.sh）
* **`project_root/server/alembic/`**：通用数据库迁移配置
* **`project_root/server/app/api/`**：REST 接口（认证、同步、上传、机器人）
* **`project_root/server/app/core/`**：核心基建（DB 引擎工厂、安全配置、WS 连接管理器、基于 `.env` 的全局 Settings）
* **`project_root/server/app/models/`**：SQLAlchemy 声明式 ORM 模型
* **`project_root/server/app/services/`**：核心策略与工厂服务（包含 cache, storage, llm, push 模块）
* **`project_root/server/app/tasks/`**：后台异步任务（Webhook 分发、推送执行）
* **`project_root/server/main.py`**：后端应用入口文件
* **`project_root/server/requirements.txt`**：后端依赖清单
* **`project_root/client/lib/generated/`**：Protobuf 编译生成的 Dart 文件存放目录
* **`project_root/client/lib/features/`**：Flutter 客户端功能模块（认证、聊天、设置）
* **`project_root/client/lib/core/`**：Flutter 客户端核心基建（网络、本地数据库、工具类）
* **`project_root/client/pubspec.yaml`**：Flutter 依赖配置文件
* **`project_root/readme.md`**：本需求规则文档

---

## 4. 功能清单 (完成定义)

**✅ A 部分：客户端功能 (Flutter)**

* **初始设置与安全**：登录前验证服务器地址与“服务器密钥”连通性。
* **身份验证**：注册 (Pending 状态待审批) 与登录 (获取 JWT)。
* **聊天核心与 UI**：支持全媒体渲染，发送状态机反馈，结合本地库的无限滚动加载。
* **消息同步与重连**：基于指数退避的断线重连，发送 `Message_ID` 水位线主动拉取 (Pull) 离线消息。
* **本地资源管理**：媒体文件本地缓存映射与上限清理。

**✅ B 部分：后端功能 (Python)**

* **多环境数据层 (DB & Cache)**：核心服务在启动时读取 `.env` 中的 `DB_TYPE` 和 `CACHE_TYPE`。开发/NAS 环境下零配置启动 SQLite 与文件缓存；生产环境下启用 Postgres 与 Redis。
* **WebSocket 健壮性保障**：实现定时 Ping/Pong 心跳机制、依据 `Client_Msg_ID` 的防重防抖，以及依靠 Cache 限制多设备挤占。
* **离线消息与同步机制**：消息 Write-First 异步落库，依据 Cache 在线状态进行 WS 实时推送或累加未读数，提供全量水位线同步 API。
* **机器人生态与 LLM**：高度解耦的 AI 注入工厂，支持非流式/流式响应及外部 Webhook 异步分发，所需 API 密钥均通过 `.env` 注入。
* **消息推送判定逻辑**：基于离线状态、路由规则 (@提及) 以及免打扰设置触发抽象推送分发。

---

## 5. 编码规则 (不可违反清单)

* **全异步红线 (后端)**：严禁阻塞式 I/O。数据库必须用 `asyncpg` 或 `aiosqlite`；文件读写必须用 `aiofiles`；外部 API 必须用 `httpx` 或线程池包装。
* **配置隔离红线**：严禁在代码中硬编码任何密码、Secret Key、S3 配置或 API Token。必须提供完整的 `.env.example` 模板，所有敏感信息与环境依赖项必须从 `.env` 中读取。
* **多态与抽象红线**：禁止在 API Route 或 WS Manager 中直接调用特定数据库或 Redis 的专有 API。所有读写必须通过 `BaseCacheProvider` 和 ORM 抽象层。
* **Protobuf 事实来源**：修改通讯结构必先改 `protobuf` 并重新编译双端代码。严禁在 Socket 中混用 JSON。
* **数据库迁移**：不论是 SQLite 还是 Postgres，严禁手动改表，统一使用 Alembic 异步迁移。


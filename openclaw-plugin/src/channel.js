/**
 * ImAI ChannelPlugin 实现。
 *
 * 将 ImAI IM 服务器接入 OpenClaw 的 channel 系统:
 *  - config    : 账号配置读写 (从 OpenClaw 全局配置中存取)
 *  - setup     : 通过 CLI 向导写入服务器地址、密钥、用户名、密码
 *  - gateway   : 长驻 WebSocket 连接，接收用户消息并路由给 OpenClaw AI
 *  - outbound  : 将 OpenClaw AI 回复通过 ImAI WebSocket 发回给用户
 */

import fs from "node:fs";
import path from "node:path";
import { ImAIClient } from "./imai-client.js";

const CHANNEL_ID = "imai";

/** 每个账号对应一个活跃客户端 */
const _clients = new Map();

// ------------------------------------------------------------------ //
//  配置路径辅助
// ------------------------------------------------------------------ //
//  OpenClaw 全局配置 (cfg) 是一个 plain object。
//  遵循 OpenClaw 内置 channel 惯例，存储在 cfg.channels?.[CHANNEL_ID] 下:
//
//  ~/.openclaw/openclaw.json:
//  {
//    "channels": {
//      "imai": {
//        "accounts": {
//          "default": {
//            "serverUrl": "http://your-server:8000",
//            "serverSecretKey": "...",
//            "username": "openclaw-bot",
//            "password": "..."
//          }
//        }
//      }
//    }
//  }

function getAccounts(cfg) {
  return cfg?.channels?.[CHANNEL_ID]?.accounts ?? {};
}

function setAccount(cfg, accountId, data) {
  return {
    ...cfg,
    channels: {
      ...cfg?.channels,
      [CHANNEL_ID]: {
        ...cfg?.channels?.[CHANNEL_ID],
        accounts: {
          ...getAccounts(cfg),
          [accountId]: data,
        },
      },
    },
  };
}

function removeAccount(cfg, accountId) {
  const accounts = { ...getAccounts(cfg) };
  delete accounts[accountId];
  return {
    ...cfg,
    channels: {
      ...cfg?.channels,
      [CHANNEL_ID]: {
        ...cfg?.channels?.[CHANNEL_ID],
        accounts,
      },
    },
  };
}

// ------------------------------------------------------------------ //
//  ChannelPlugin 工厂
// ------------------------------------------------------------------ //

export function createImAIChannel() {
  return {
    // ────────── 基础元信息 ──────────
    id: CHANNEL_ID,

    meta: {
      id:       CHANNEL_ID,
      label:    "ImAI",
      docsPath: "channels/imai",
    },

    capabilities: {
      text: true,
    },

    // ────────── 账号配置读写 ──────────
    config: {
      /** 返回所有已配置的账号 ID 列表 */
      listAccountIds(cfg) {
        return Object.keys(getAccounts(cfg));
      },

      /** 根据 accountId 解析账号配置对象 */
      resolveAccount(cfg, accountId) {
        const id = accountId ?? "default";
        const stored = getAccounts(cfg)[id] ?? {};
        return { id, ...stored, enabled: stored.enabled ?? true };
      },

      /** 默认账号 ID */
      defaultAccountId(cfg) {
        const ids = Object.keys(getAccounts(cfg));
        return ids[0] ?? "default";
      },

      /** 是否已配置 */
      isConfigured(account) {
        return !!(account.serverUrl && account.username && account.password);
      },

      unconfiguredReason(account) {
        if (!account.serverUrl)  return "缺少 serverUrl";
        if (!account.username)   return "缺少 username";
        if (!account.password)   return "缺少 password";
        return "";
      },

      /** 删除账号 */
      deleteAccount({ cfg, accountId }) {
        return removeAccount(cfg, accountId);
      },

      /** 描述账号状态 */
      describeAccount(account) {
        const configured = !!(account.serverUrl && account.username);
        return {
          id:      account.id,
          enabled: account.enabled ?? true,
          state:   configured ? "configured" : "not configured",
          label:   configured
            ? `${account.username}@${account.serverUrl}`
            : "(未配置)",
        };
      },
    },

    // ────────── CLI 向导写入配置 ──────────
    setup: {
      /**
       * CLI 向导完成后将输入写入全局配置。
       *
       * input 字段 (来自 configSchema.properties 的 key):
       *   serverUrl, serverSecretKey, username, password, deviceId
       */
      applyAccountConfig({ cfg, accountId, input }) {
        const id = accountId || "default";
        return setAccount(cfg, id, {
          serverUrl:       input.serverUrl,
          serverSecretKey: input.serverSecretKey,
          username:        input.username,
          password:        input.password,
          deviceId:        input.deviceId || "openclaw-plugin",
        });
      },

      /** 校验输入：返回错误字符串，或 null 表示合法 */
      validateInput({ input }) {
        if (!input.serverUrl)       return "serverUrl 不能为空";
        if (!input.serverSecretKey) return "serverSecretKey 不能为空";
        if (!input.username)        return "username 不能为空";
        if (!input.password)        return "password 不能为空";
        try {
          new URL(input.serverUrl);
        } catch {
          return "serverUrl 格式不正确，应为完整 URL，如 http://192.168.1.100:8000";
        }
        return null;
      },
    },

    // ────────── Gateway：长驻连接 ──────────
    gateway: {
      /**
       * 启动 ImAI WebSocket 连接，处理消息并路由给 OpenClaw。
       *
       * OpenClaw 在每个账号启用时调用本方法，并传入:
       *   ctx.account        — resolveAccount 返回的账号对象
       *   ctx.cfg            — OpenClaw 全局配置
       *   ctx.abortSignal    — 停止时触发
       *   ctx.channelRuntime — Plugin SDK 提供的消息路由能力
       *   ctx.setStatus      — 更新账号在线状态
       */
      async startAccount(ctx) {
        const { account, cfg, abortSignal, channelRuntime, setStatus, log } = ctx;

        const logger = {
          info:  (msg) => log?.info(msg)  ?? console.info(msg),
          warn:  (msg) => log?.warn(msg)  ?? console.warn(msg),
          error: (msg) => log?.error(msg) ?? console.error(msg),
          debug: (msg) => log?.debug?.(msg),
        };

        setStatus?.({ id: account.id, state: "configured", label: "连接中…" });

        // 推导水位线文件路径（与 sessions.json 同目录）
        const { session } = channelRuntime;
        const sessionsPath = session.resolveStorePath(undefined, {});
        const watermarkPath = path.join(
          path.dirname(sessionsPath),
          `imai-${account.id}-watermark.json`
        );
        logger.info(`[imai] sessionsPath=${sessionsPath}`);
        logger.info(`[imai] watermarkPath=${watermarkPath}`);

        // 加载持久化水位线
        let initialLastMsgId = 0;
        try {
          const raw = fs.readFileSync(watermarkPath, "utf8");
          logger.info(`[imai] 水位线文件内容: ${raw}`);
          const data = JSON.parse(raw);
          // protobufjs Long.toJSON() 返回字符串，兼容 number 和 string 两种形式
          const parsed = Number(data.lastServerMsgId);
          if (Number.isFinite(parsed) && parsed > 0) {
            initialLastMsgId = parsed;
          }
        } catch (err) {
          logger.info(`[imai] 水位线文件不存在或读取失败: ${err.message}`);
        }
        logger.info(`[imai] 加载水位线 lastServerMsgId=${initialLastMsgId}`);

        const client = new ImAIClient({
          serverUrl:       account.serverUrl,
          serverSecretKey: account.serverSecretKey,
          username:        account.username,
          password:        account.password,
          deviceId:        account.deviceId ?? "openclaw-plugin",
          log:             logger,
          initialLastMsgId,

          /** 水位线更新时持久化到磁盘 */
          onLastMsgIdUpdate: (id) => {
            try {
              fs.mkdirSync(path.dirname(watermarkPath), { recursive: true });
              const content = JSON.stringify({ lastServerMsgId: id });
              fs.writeFileSync(watermarkPath, content, "utf8");
              logger.info(`[imai] 水位线已写入 ${watermarkPath} → ${content}`);
            } catch (err) {
              logger.error(`[imai] 水位线写入失败: ${err.message}`);
            }
          },

          /** WebSocket 握手完成后通知 OpenClaw 状态变为 linked */
          onConnected: () => {
            setStatus?.({ id: account.id, state: "linked", label: `${account.username} 已连接` });
          },

          /** 连接断开进入重连等待时通知 OpenClaw，避免 health-monitor 误判 stale-socket */
          onReconnecting: (attempt) => {
            setStatus?.({ id: account.id, state: "configured", label: `重新连接中… (第 ${attempt} 次)` });
          },

          /** 收到 ImAI 用户消息时的回调 */
          onMessage: async (incoming) => {
            await _routeIncoming({ incoming, account, cfg, channelRuntime, logger });
          },
        });

        _clients.set(account.id, client);

        try {
          await client.run(abortSignal);
        } finally {
          _clients.delete(account.id);
          setStatus?.({ id: account.id, state: "not linked", label: "已断开" });
        }
      },

      async stopAccount(ctx) {
        _clients.get(ctx.account.id)?.stop();
      },
    },

    // ────────── Outbound：发送回复 ──────────
    outbound: {
      /**
       * "gateway" 模式：OpenClaw 通过本适配器将 AI 回复发回给 ImAI 用户。
       * ctx.to   — 目标用户 ID (即原始发送方 senderId)
       * ctx.text — 要发送的文本
       */
      deliveryMode: "gateway",

      async sendText(ctx) {
        const { account, to, text } = ctx;
        const client = _clients.get(account.id);
        if (!client) {
          throw new Error(`[imai] 账号 ${account.id} 无活跃连接`);
        }
        await client.sendText(to, text);
        return { ok: true };
      },
    },
  };
}

// ------------------------------------------------------------------ //
//  消息路由：ImAI → OpenClaw
// ------------------------------------------------------------------ //

async function _routeIncoming({ incoming, account, cfg, channelRuntime, logger }) {
  if (!channelRuntime) {
    // 无 channelRuntime 时 (单元测试等场景) 仅打日志
    logger.warn(`[imai] 无 channelRuntime，消息未路由: ${incoming.text}`);
    return;
  }

  try {
    const reply = channelRuntime.reply;
    // 1. 构建最终化消息上下文 (FinalizedMsgContext)
    const rawCtx = {
      Body:              incoming.text,
      RawBody:           incoming.text,
      CommandBody:       incoming.text,
      From:              incoming.senderId,
      To:                account.username,   // bot 用户名即接收方
      SessionKey:        `${CHANNEL_ID}:${account.id}:${incoming.senderId}`,
      Provider:          account.id,
      Surface:           CHANNEL_ID,
      MessageSid:        incoming.serverMsgId || incoming.clientMsgId,
      ChatType:          "direct",
      ConversationLabel: incoming.senderId,
      CommandAuthorized: false,
      UntrustedContext:  [],
    };

    const ctx = reply.finalizeInboundContext(rawCtx);

    // 2. 持久化 session 元数据 (WebUI 依赖此记录展示会话)
    const { session } = channelRuntime;
    const storePath = session.resolveStorePath(undefined, {});
    await session.recordInboundSession({
      storePath,
      sessionKey: rawCtx.SessionKey,
      ctx,
      onRecordError: (err) => {
        logger.error(`[imai] session 记录失败: ${err.message}`);
      },
    });

    // 3. 通过 dispatchReplyWithBufferedBlockDispatcher 路由到 OpenClaw AI 并回复
    const client = _clients.get(account.id);
    await reply.dispatchReplyWithBufferedBlockDispatcher({
      ctx,
      cfg,
      dispatcherOptions: {
        deliver: async (payload) => {
          const text = payload.text ?? payload.body ?? "";
          if (text && client) {
            await client.sendText(incoming.senderId, text);
          }
        },
        onError: (err) => {
          logger.error(`[imai] 回复发送失败: ${err.message}`);
        },
      },
    });
  } catch (err) {
    logger.error(`[imai] 路由消息失败: ${err.message}`);
  }
}

/**
 * ImAI WebSocket 客户端。
 *
 * 职责:
 *  1. REST 认证: validate server key → login → get user_id + JWT
 *  2. WebSocket 连接 (含自动重连)
 *  3. 心跳 Ping → Pong
 *  4. 离线消息同步 (SyncRequest)
 *  5. 收到 CHAT_MESSAGE 时调用 onMessage 回调
 *  6. sendText(receiverId, text) — 发送文本消息
 */

import WebSocket from "ws";
import { randomUUID } from "node:crypto";
import {
  EnvelopeType,
  ContentType,
  buildPong,
  buildAuthRequest,
  buildSyncRequest,
  buildChatMessage,
  parseEnvelope,
} from "./proto.js";

export class ImAIClient {
  /**
   * @param {object} opts
   * @param {string} opts.serverUrl          - e.g. "http://localhost:8000"
   * @param {string} opts.serverSecretKey
   * @param {string} opts.username
   * @param {string} opts.password
   * @param {string} [opts.deviceId]
   * @param {function} opts.onMessage        - async (msg: IncomingMessage) => void
   * @param {function} [opts.onConnected]    - () => void，WebSocket 握手完成后调用
   * @param {function} [opts.onReconnecting] - (attempt: number) => void，重连等待开始时调用
   * @param {object}  [opts.log]             - { info, warn, error, debug }
   */
  constructor(opts) {
    this.serverUrl = opts.serverUrl.replace(/\/$/, "");
    this.serverSecretKey = opts.serverSecretKey;
    this.username = opts.username;
    this.password = opts.password;
    this.deviceId = opts.deviceId ?? "openclaw-plugin";
    this.onMessage = opts.onMessage;
    this.onConnected    = opts.onConnected    ?? null;
    this.onReconnecting = opts.onReconnecting ?? null;
    this.log = opts.log ?? console;
    /** 初始水位线（从持久化存储中加载） */
    this._lastServerMsgId = opts.initialLastMsgId ?? 0;
    /** 水位线更新时的回调，用于持久化 */
    this._onLastMsgIdUpdate = opts.onLastMsgIdUpdate ?? null;

    this._userId = "";
    this._token = "";
    this._ws = null;
    this._stopped = false;
  }

  // ---------------------------------------------------------------- //
  //  公开接口
  // ---------------------------------------------------------------- //

  /**
   * 启动主循环 (含自动重连)。
   * @param {AbortSignal} [signal]
   */
  async run(signal) {
    this._stopped = false;
    let attempt = 0;

    while (!this._stopped && !(signal?.aborted)) {
      try {
        await this._authenticate();
        await this._connectAndLoop(signal);
        attempt = 0;
      } catch (err) {
        if (this._stopped || signal?.aborted) break;
        attempt++;
        const delay = Math.min(5 * attempt, 60) * 1000;
        this.log.warn(
          `[imai] 连接异常: ${err.message}，${delay / 1000}s 后第 ${attempt} 次重连…`
        );
        this.onReconnecting?.(attempt);
        await sleep(delay, signal);
      }
    }
  }

  /** 停止客户端。 */
  stop() {
    this._stopped = true;
    this._ws?.close();
  }

  /**
   * 向指定用户发送文本消息。
   * @param {string} receiverId
   * @param {string} text
   */
  async sendText(receiverId, text) {
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN) {
      this.log.warn("[imai] sendText: WebSocket 未连接，消息丢弃");
      return;
    }
    const buf = await buildChatMessage(
      randomUUID().replace(/-/g, ""),
      this._userId,
      receiverId,
      text
    );
    this._ws.send(buf);
    this.log.debug?.(`[imai] 已发送消息 → ${receiverId}`);
  }

  // ---------------------------------------------------------------- //
  //  内部 — 认证
  // ---------------------------------------------------------------- //

  async _authenticate() {
    const base = this.serverUrl;

    // 1. 校验服务器密钥
    const validateRes = await fetchJSON(`${base}/api/validate`, {
      method: "POST",
      body: JSON.stringify({ server_secret_key: this.serverSecretKey }),
    });
    if (!validateRes.valid) {
      throw new Error(`[imai] 服务器密钥校验失败: ${JSON.stringify(validateRes)}`);
    }
    this.log.info(`[imai] 服务器验证通过 — ${validateRes.server_name}`);

    // 2. 登录，获取 JWT + user_id
    const loginRes = await fetchJSON(`${base}/api/login`, {
      method: "POST",
      body: JSON.stringify({
        username: this.username,
        password: this.password,
      }),
    });
    this._userId = loginRes.user_id;
    this._token = loginRes.access_token;
    this.log.info(`[imai] 登录成功 — user_id=${this._userId}`);
  }

  // ---------------------------------------------------------------- //
  //  内部 — WebSocket 连接与消息循环
  // ---------------------------------------------------------------- //

  async _connectAndLoop(signal) {
    const wsBase = this.serverUrl
      .replace(/^https:\/\//, "wss://")
      .replace(/^http:\/\//, "ws://");
    const wsUrl = `${wsBase}/ws?user_id=${this._userId}&device_id=${this.deviceId}`;

    this.log.info(`[imai] 正在连接 ${wsUrl}`);

    await new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl);
      this._ws = ws;

      // 取消信号：直接关闭
      signal?.addEventListener("abort", () => ws.close());

      ws.once("open", async () => {
        this.log.info("[imai] WebSocket 已连接");
        try {
          // 发送 AuthRequest（服务端目前通过 query param 鉴权，此帧为扩展备用）
          ws.send(await buildAuthRequest(this.serverSecretKey, this._token, this.deviceId));
          // 拉取离线消息
          ws.send(await buildSyncRequest(this._lastServerMsgId));
          // 握手完成，通知外部状态已连接
          this.onConnected?.();
        } catch (err) {
          this.log.error(`[imai] 握手异常: ${err.message}`);
        }
      });

      ws.on("message", async (data) => {
        try {
          await this._dispatch(Buffer.from(data));
        } catch (err) {
          this.log.error(`[imai] 消息处理异常: ${err.message}`);
        }
      });

      ws.once("close", (code, reason) => {
        this._ws = null;
        if (this._stopped || signal?.aborted) {
          resolve();
        } else {
          reject(new Error(`[imai] 连接关闭 code=${code} reason=${reason}`));
        }
      });

      ws.once("error", (err) => {
        this._ws = null;
        reject(err);
      });
    });
  }

  async _dispatch(buf) {
    const envelope = await parseEnvelope(buf);

    switch (Number(envelope.type)) {
      case EnvelopeType.PING:
        this._ws?.send(await buildPong());
        this.log.debug?.("[imai] Ping → Pong");
        break;

      case EnvelopeType.CHAT_MESSAGE: {
        const msg = envelope.chatMessage;
        // protobufjs int64 → Long 对象，需转成 JS number 后才能正确 JSON 序列化
        const smid = Number(msg.serverMsgId);
        if (smid > this._lastServerMsgId) {
          this._lastServerMsgId = smid;
          this._onLastMsgIdUpdate?.(smid);
        }
        await this._handleChatMessage(msg);
        break;
      }

      case EnvelopeType.SYNC_RESPONSE: {
        const sync = envelope.syncResponse;
        this.log.info(
          `[imai] 离线消息同步 count=${sync.messages?.length ?? 0} has_more=${sync.hasMore}`
        );
        for (const msg of sync.messages ?? []) {
          const smid = Number(msg.serverMsgId);
          if (smid > this._lastServerMsgId) {
            this._lastServerMsgId = smid;
          }
          await this._handleChatMessage(msg);
          // 每条消息处理完后立即持久化，避免进程崩溃导致水位线丢失
          this._onLastMsgIdUpdate?.(this._lastServerMsgId);
        }
        // 若服务端还有更多历史消息，继续拉取，直到 has_more=false
        if (sync.hasMore && this._ws?.readyState === WebSocket.OPEN) {
          this.log.info(`[imai] has_more=true，继续拉取 from=${this._lastServerMsgId}`);
          this._ws.send(await buildSyncRequest(this._lastServerMsgId));
        }
        break;
      }

      case EnvelopeType.AUTH_RESPONSE: {
        const r = envelope.authResponse;
        if (r?.success) {
          this.log.info(`[imai] AuthResponse 成功 user_id=${r.userId}`);
        } else {
          this.log.warn(`[imai] AuthResponse 失败: ${r?.message}`);
        }
        break;
      }

      case EnvelopeType.MESSAGE_ACK:
        this.log.debug?.(
          `[imai] MessageAck cid=${envelope.messageAck?.clientMsgId} smid=${envelope.messageAck?.serverMsgId}`
        );
        break;

      case EnvelopeType.KICK_NOTICE:
        this.log.warn(
          `[imai] 被踢下线 reason=${envelope.kickNotice?.reason} device=${envelope.kickNotice?.deviceId}`
        );
        break;

      case EnvelopeType.ERROR_NOTICE:
        this.log.warn(
          `[imai] 服务器错误 code=${envelope.errorNotice?.code} msg=${envelope.errorNotice?.message}`
        );
        break;

      case EnvelopeType.PRESENCE_NOTIFY:
        this.log.debug?.(
          `[imai] 在线状态 user=${envelope.presenceNotify?.userId} status=${envelope.presenceNotify?.status}`
        );
        break;

      default:
        this.log.debug?.(`[imai] 未处理的消息类型 type=${envelope.type}`);
    }
  }

  async _handleChatMessage(msg) {
    // 只处理文本消息，且不处理自身发出的消息
    if (String(msg.senderId) === this._userId) return;
    if (Number(msg.contentType) !== ContentType.TEXT) {
      this.log.debug?.(`[imai] 忽略非文本消息 content_type=${msg.contentType}`);
      return;
    }

    const text = msg.text?.text ?? "";
    if (!text.trim()) return;

    this.log.info(`[imai] 收到消息 from=${msg.senderId} text=${text.slice(0, 60)}`);

    await this.onMessage({
      clientMsgId:  String(msg.clientMsgId ?? ""),
      serverMsgId:  String(msg.serverMsgId ?? ""),
      senderId:     String(msg.senderId ?? ""),
      receiverId:   String(msg.receiverId ?? ""),
      text,
    });
  }
}

// ------------------------------------------------------------------ //
//  工具函数
// ------------------------------------------------------------------ //

async function fetchJSON(url, init = {}) {
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
    },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${url}: ${body}`);
  }
  return res.json();
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new Error("aborted"));
    });
  });
}

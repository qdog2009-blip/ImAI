/**
 * Protobuf 辅助模块 — 基于 protobufjs 动态加载 chat.proto。
 *
 * 提供:
 *  - load()              首次调用时加载 .proto 文件，后续复用缓存
 *  - buildEnvelope(type, payload)  构造 Envelope 二进制
 *  - parseEnvelope(buf)            解析收到的 Envelope
 *  - EnvelopeType / ContentType    枚举常量
 */

import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { randomUUID } from "node:crypto";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ------------------------------------------------------------------ //
//  加载 proto (惰性单例)
// ------------------------------------------------------------------ //

let _root = null;

export async function loadProto() {
  if (_root) return _root;

  // protobufjs 支持 ESM，使用 createRequire 兼容 dynamic import
  const { default: protobuf } = await import("protobufjs");
  const protoPath = path.join(__dirname, "chat.proto");
  _root = await protobuf.load(protoPath);
  return _root;
}

// ------------------------------------------------------------------ //
//  枚举常量 (与 chat.proto 保持一致)
// ------------------------------------------------------------------ //

export const EnvelopeType = Object.freeze({
  ENVELOPE_TYPE_UNSPECIFIED: 0,
  AUTH_REQUEST:   1,
  AUTH_RESPONSE:  2,
  PING:          10,
  PONG:          11,
  CHAT_MESSAGE:  20,
  MESSAGE_ACK:   21,
  READ_RECEIPT:  30,
  SYNC_REQUEST:  40,
  SYNC_RESPONSE: 41,
  PRESENCE_NOTIFY: 50,
  BOT_REQUEST:   60,
  BOT_RESPONSE:  61,
  BOT_STREAM_CHUNK: 62,
  KICK_NOTICE:   70,
  ERROR_NOTICE:  80,
});

export const ChannelType = Object.freeze({
  CHANNEL_TYPE_UNSPECIFIED: 0,
  PRIVATE: 1,
  GROUP:   2,
});

export const ContentType = Object.freeze({
  CONTENT_TYPE_UNSPECIFIED: 0,
  TEXT:     1,
  IMAGE:    2,
  AUDIO:    3,
  VIDEO:    4,
  FILE:     5,
  LOCATION: 6,
});

export const DeviceType = Object.freeze({
  DEVICE_TYPE_UNSPECIFIED: 0,
  ANDROID: 1,
  IOS:     2,
  WEB:     3,
  DESKTOP: 4,
});

// ------------------------------------------------------------------ //
//  Envelope 构建 / 解析
// ------------------------------------------------------------------ //

/**
 * 将指定类型与 payload 封装为 Envelope 并序列化为 Buffer。
 *
 * @param {number} type         EnvelopeType 枚举值
 * @param {string} payloadKey   Envelope oneof 字段名，如 "chatMessage"
 * @param {object} payloadValue payload 对象
 */
export async function buildEnvelope(type, payloadKey, payloadValue) {
  const root = await loadProto();
  const Envelope = root.lookupType("imai.Envelope");

  const msg = Envelope.create({
    type,
    timestampMs: Date.now(),
    requestId: randomUUID().replace(/-/g, ""),
    [payloadKey]: payloadValue,
  });

  const err = Envelope.verify(msg);
  if (err) throw new Error(`Envelope verify: ${err}`);

  return Buffer.from(Envelope.encode(msg).finish());
}

/**
 * 从 Buffer 解析出 Envelope 对象。
 */
export async function parseEnvelope(buf) {
  const root = await loadProto();
  const Envelope = root.lookupType("imai.Envelope");
  return Envelope.decode(buf);
}

// ------------------------------------------------------------------ //
//  便捷构建函数
// ------------------------------------------------------------------ //

export async function buildPong() {
  return buildEnvelope(EnvelopeType.PONG, "pong", {});
}

export async function buildAuthRequest(serverSecretKey, token, deviceId) {
  return buildEnvelope(EnvelopeType.AUTH_REQUEST, "authRequest", {
    serverSecretKey,
    token,
    deviceId,
    deviceType: DeviceType.DESKTOP,
  });
}

export async function buildSyncRequest(lastServerMsgId = 0, limit = 50) {
  return buildEnvelope(EnvelopeType.SYNC_REQUEST, "syncRequest", {
    lastServerMsgId: Number(lastServerMsgId),
    limit,
  });
}

export async function buildChatMessage(clientMsgId, senderId, receiverId, text) {
  return buildEnvelope(EnvelopeType.CHAT_MESSAGE, "chatMessage", {
    clientMsgId,
    senderId,
    receiverId,
    channel: ChannelType.PRIVATE,
    contentType: ContentType.TEXT,
    text: { text },
  });
}

/**
 * OpenClaw channel plugin — ImAI IM 服务器
 *
 * 安装:
 *   openclaw plugins install @openclaw-plugin/imai
 *
 * 配置 (openclaw channel setup imai):
 *   serverUrl        ImAI 服务器地址, 如 http://192.168.1.100:8000
 *   serverSecretKey  服务端配置的 SERVER_SECRET_KEY
 *   username         OpenClaw bot 在 ImAI 上的账号用户名
 *   password         对应密码
 *   deviceId         (可选) 设备标识符, 默认 openclaw-plugin
 *
 * 使用:
 *   其他 ImAI 用户直接发私信给 bot 账号即可与 OpenClaw AI 对话。
 */

import { createImAIChannel } from "./channel.js";

/**
 * OpenClaw 插件入口。
 * OpenClaw 加载插件时调用此函数，传入 Plugin API 对象。
 *
 * @param {import("openclaw").OpenClawPluginApi} api
 */
export function register(api) {
  api.registerChannel(createImAIChannel());
}

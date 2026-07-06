/**
 * 后端 Python 服务地址。
 *
 * 所有 renderer → backend 的 HTTP 调用（chat / sandbox / skills / workspace /
 * approve / health / mcp / memory）都走这个 base。Tauri command 走 `invoke()`，
 * 不经过这里。
 */
export const API_BASE = "http://127.0.0.1:8123";

/**
 * vitest 全局 setup：在所有测试文件导入前替换 jsdom 的 localStorage 为内存版，
 * 并安装默认的 Tauri internals no-op 桩。
 *
 * 背景：
 * - jsdom 的 localStorage 在 `--localstorage-file` 路径无效时 setItem 会抛错，
 *   而 zustand persist 在 store 模块导入时即捕获 storage。各测试文件用
 *   `vi.hoisted()` 单独 mock 会在 forks 池模块缓存场景下互相干扰。集中在此
 *   替换一次，保证所有测试文件拿到的 localStorage 都是可写的内存版。
 * - Tauri 2.x 的 `invoke()` / `listen()` 依赖 `window.__TAURI_INTERNALS__`。
 *   未调用 `installApiMock()` 的测试（如纯 store 测试）也需要此对象存在，
 *   否则模块导入即抛 TypeError。此处安装 no-op 默认值；各测试文件可通过
 *   `installApiMock(mockApi)` 覆盖为有实际路由的 mock。
 */
import { installDefaultTauriInternals } from "./api-mock";

const store = new Map<string, string>();

const mockStorage: Storage = {
  getItem: (key: string) => store.get(key) ?? null,
  setItem: (key: string, value: string) => {
    store.set(key, String(value));
  },
  removeItem: (key: string) => {
    store.delete(key);
  },
  clear: () => store.clear(),
  key: (index: number) => Array.from(store.keys())[index] ?? null,
  get length() {
    return store.size;
  },
};

Object.defineProperty(globalThis, "localStorage", {
  value: mockStorage,
  configurable: true,
  writable: true,
});

// 安装默认 no-op Tauri internals（invoke → undefined, transformCallback → ID）
installDefaultTauriInternals();
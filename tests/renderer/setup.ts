/**
 * vitest 全局 setup：在所有测试文件导入前替换 jsdom 的 localStorage 为内存版。
 *
 * 背景：jsdom 的 localStorage 在 `--localstorage-file` 路径无效时 setItem 会抛错，
 * 而 zustand persist 在 store 模块导入时即捕获 storage。
 * 各测试文件用 `vi.hoisted()` 单独 mock 会在 forks 池模块缓存场景下互相干扰
 * （一个文件的 mock 可能被另一个文件的已缓存 store 捕获）。
 * 集中在 setupFiles 中替换一次，保证所有测试文件拿到的 localStorage 都是可写的内存版。
 */
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
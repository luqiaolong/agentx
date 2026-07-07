/**
 * 统一 key/name 校验正则与函数，消除 6 个文件中的重复定义。
 *
 * 与后端 skills_store._NAME_RE 一致：^[a-zA-Z0-9_-]{1,64}$
 */
export const KEY_RE = /^[a-zA-Z0-9_-]{1,64}$/;
export const NAME_RE = KEY_RE;

/**
 * 校验 key 格式，返回错误信息或 null（表示通过）。
 */
export function validateKey(key: string): string | null {
  if (!key) return "名称不能为空";
  if (!KEY_RE.test(key)) return "仅允许字母、数字、下划线、连字符，长度 1-64";
  return null;
}

export function validateName(name: string): string | null {
  return validateKey(name);
}

import Store from "electron-store";
import { safeStorage } from "electron";

const store = new Store();

/**
 * electron-store + safeStorage 封装。
 * 加密值以 `enc:<base64>` 形式存储，明文回退以 `plain:<value>` 形式存储。
 * safeStorage 不可用时回退到明文并 console.warn。
 */
export function getDecrypted(key: string): string | null {
  const stored = store.get(key);
  if (typeof stored !== "string") return null;
  if (stored.startsWith("enc:")) {
    if (!safeStorage.isEncryptionAvailable()) return null;
    try {
      const buf = Buffer.from(stored.slice(4), "base64");
      return safeStorage.decryptString(buf);
    } catch {
      return null;
    }
  }
  if (stored.startsWith("plain:")) {
    return stored.slice(6);
  }
  return null;
}

export function setEncrypted(key: string, value: string): void {
  if (safeStorage.isEncryptionAvailable()) {
    const encrypted = safeStorage.encryptString(value);
    store.set(key, `enc:${encrypted.toString("base64")}`);
  } else {
    console.warn(`safeStorage 不可用，${key} 将以明文存储`);
    store.set(key, `plain:${value}`);
  }
}

export function getMilvusCredentials(): { user: string | null; password: string | null } {
  return {
    user: getDecrypted("milvus.user"),
    password: getDecrypted("milvus.password"),
  };
}

export function setMilvusCredentials(user: string, password: string): void {
  setEncrypted("milvus.user", user);
  setEncrypted("milvus.password", password);
}

export function getApiKey(provider: string): string | null {
  return getDecrypted(`apikey.${provider}`);
}

export function setApiKey(provider: string, key: string): void {
  setEncrypted(`apikey.${provider}`, key);
}

/**
 * 统一日志封装，预留 Langfuse 上报接口。
 *
 * 当前仅 console.warn/error，M2 可接入 Langfuse。
 */
export const logger = {
  warn(msg: string, e?: unknown): void {
    if (e !== undefined) {
      // eslint-disable-next-line no-console
      console.warn(`[agentx] ${msg}`, e);
    } else {
      // eslint-disable-next-line no-console
      console.warn(`[agentx] ${msg}`);
    }
  },
  error(msg: string, e?: unknown): void {
    if (e !== undefined) {
      // eslint-disable-next-line no-console
      console.error(`[agentx] ${msg}`, e);
    } else {
      // eslint-disable-next-line no-console
      console.error(`[agentx] ${msg}`);
    }
  },
};

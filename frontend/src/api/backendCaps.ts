/** 后端能力门禁：新前端可能配旧后端进程（dist 已更新但服务未重启）。
 *  用未登录也可区分的路由探测：import-jobs 新后端 401/200、旧后端 404。
 *  旧后端时游客入口隐藏、统一导入降级到 import-flex/import-run，避免 405/404 卡死入口。 */
let cached: Promise<boolean> | null = null;

export function probeLegacyBackend(): Promise<boolean> {
  if (!cached) {
    cached = fetch('/api/competition/import-jobs', { cache: 'no-store' })
      .then((r) => r.status === 404)
      .catch(() => false);
  }
  return cached;
}
import { getBundle, getDatasetId } from '../dataSource';

export const LOCAL_DATA_NOTICE = '当前数据仅在本地分析与核对。导入合成或自定义评测数据后可使用外部 AI。';

export function canUseExternalAi(): boolean {
  return /^eval-[0-9a-f]{8}$/.test(getDatasetId()) && getBundle()?.externalAiAllowed === true;
}

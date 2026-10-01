import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, Button, Card, Descriptions, Input, List, Popconfirm, Space, Tag, Tooltip,
  Typography, message,
} from 'antd';
import { IdcardOutlined, KeyOutlined, TeamOutlined } from '@ant-design/icons';
import apiClient from '../../api/axios';
import { isLoggedIn } from '../../api/auth';
import { AUTH_CHANGED_EVENT, notifyAuthChanged } from '../UserAuth';

const { Text, Paragraph } = Typography;

interface RoleInfo {
  role: 'student' | 'teacher' | 'admin';
  source: string;
  can_create_course: boolean;
  policy: string;
}

/** 邀请码签发权限（由后端 invite_policy 给出，前端只用于展示） */
interface InvitePolicy {
  is_admin: boolean;
  can_issue: boolean;
  max_ttl_days: number;
  max_uses: number;
  quota: number | null;
  reason: string;
}

interface InviteRow {
  code: string;
  max_uses: number;
  used_count: number;
  expires_at?: string | null;
  revoked_at?: string | null;
  created_by?: string | null;
}

const SOURCE_LABEL: Record<string, string> = {
  env: '部署时的教师白名单',
  invite: '教师邀请码开通',
  admin: '管理员开通',
  course: '历史数据（早期建课时授予）',
  none: '尚未开通',
  unknown: '—',
};

/**
 * 教师身份开通（受控流程）
 *
 * 修复说明（2026-09-27）：此前"创建课程"会自动把创建者变成教师，
 * 任何注册用户都能自助获得教师权限。
 *
 * 现在：用户凭**教师邀请码**开通，或在部署时用 TEACHER_USERS 指定。
 * 邀请码签发进一步收紧（同日）：**默认仅管理员可签发**；教师签发需部署方
 * 显式开启 TEACHER_CAN_INVITE=1，且受有效期/次数/配额约束。
 * 界面只是入口，权限判定全在后端（services.auth_service.invite_policy）。
 */
const TeacherActivation: React.FC = () => {
  const [role, setRole] = useState<RoleInfo | null>(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invite, setInvite] = useState<{ code: string; expires_at: string; max_uses: number } | null>(null);
  const [policy, setPolicy] = useState<InvitePolicy | null>(null);
  const [invites, setInvites] = useState<InviteRow[]>([]);

  const load = useCallback(async () => {
    if (!isLoggedIn()) {
      setRole(null);
      return;
    }
    try {
      const res: any = await apiClient.get('/teacher/role');
      setRole(res?.data || null);
    } catch (e: any) {
      setRole(null);
      setError(e?.response?.data?.message || '无法获取角色信息');
    }
  }, []);

  /** 教师/管理员：拉取自己的签发权限与邀请码列表（学生无权限，静默跳过） */
  const loadInvites = useCallback(async () => {
    try {
      const res: any = await apiClient.get('/teacher/invites');
      setPolicy(res?.data?.policy || null);
      setInvites(res?.data?.invites || []);
    } catch {
      setPolicy(null);
      setInvites([]);
    }
  }, []);

  useEffect(() => {
    load();
    const onAuth = () => { load(); };
    window.addEventListener(AUTH_CHANGED_EVENT, onAuth);
    return () => window.removeEventListener(AUTH_CHANGED_EVENT, onAuth);
  }, [load]);

  // 只有教师/管理员才去拉签发权限与邀请码台账（学生没有该权限，避免无意义的 403 请求）
  useEffect(() => {
    if (role && (role.role === 'teacher' || role.role === 'admin')) loadInvites();
    else { setPolicy(null); setInvites([]); }
  }, [role, loadInvites]);

  const revoke = async (target: string) => {
    setBusy(true);
    try {
      await apiClient.post(`/teacher/invites/${target}/revoke`);
      message.success('邀请码已撤销，立即失效');
      await loadInvites();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '撤销失败');
    } finally {
      setBusy(false);
    }
  };

  const redeem = async () => {
    const value = code.trim().toUpperCase();
    if (!value) {
      message.warning('请填写教师邀请码');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await apiClient.post('/teacher/apply-invite', { code: value });
      message.success('教师身份已开通，可以创建课程了');
      setCode('');
      await load();
      notifyAuthChanged();       // 让侧边栏菜单立刻显示「教师工作台」
    } catch (e: any) {
      const msg = e?.response?.data?.message || '开通失败，请检查邀请码';
      setError(msg);
      message.error(msg);
    } finally {
      setBusy(false);
    }
  };

  const createInvite = async () => {
    setBusy(true);
    try {
      const res: any = await apiClient.post('/teacher/invites', { max_uses: 1 });
      setInvite(res?.data || null);
      message.success('邀请码已生成（一次性，请妥善转交）');
      await loadInvites();
    } catch (e: any) {
      message.error(e?.response?.data?.message || '生成失败');
    } finally {
      setBusy(false);
    }
  };

  /** 尚未用完且未撤销、未过期的邀请码 */
  const activeInvites = invites.filter((i) => {
    if (i.revoked_at) return false;
    if ((i.used_count || 0) >= (i.max_uses || 1)) return false;
    if (i.expires_at && new Date(i.expires_at) < new Date()) return false;
    return true;
  });

  if (!isLoggedIn()) {
    return (
      <Card size="small" title={<Space><IdcardOutlined />教师身份</Space>}>
        <Text type="secondary">登录后可查看/开通教师身份。</Text>
      </Card>
    );
  }

  return (
    <Card size="small" title={<Space><IdcardOutlined />教师身份</Space>}>
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Descriptions size="small" column={1}>
          <Descriptions.Item label="当前角色">
            {role?.role === 'teacher' || role?.role === 'admin'
              ? <Tag color="blue">{role.role === 'admin' ? '管理员' : '教师'}</Tag>
              : <Tag>学生</Tag>}
          </Descriptions.Item>
          <Descriptions.Item label="身份来源">
            <Text type="secondary">{SOURCE_LABEL[role?.source || 'none'] || role?.source}</Text>
          </Descriptions.Item>
        </Descriptions>

        {role?.can_create_course ? (
          <Alert
            type="success"
            showIcon
            message="你已具备教师身份"
            description="左侧菜单中的「教师工作台」可以创建课程与班级、上传课程资料、编写教案、发布练习并查看班级学情。"
          />
        ) : (
          <>
            <Alert
              type="info"
              showIcon
              message="创建课程需要教师身份"
              description={role?.policy || '教师身份需由管理员或凭教师邀请码开通；创建课程不再自动授予。'}
            />
            <Space wrap>
              <Input
                prefix={<KeyOutlined />}
                placeholder="填写教师邀请码（形如 T-XXXXXXXX）"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                onPressEnter={redeem}
                style={{ width: 300 }}
                maxLength={16}
              />
              <Button type="primary" loading={busy} onClick={redeem}>开通教师身份</Button>
            </Space>
            {error && <Alert type="warning" showIcon message={error} />}
            <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 0 }}>
              没有教师邀请码？请联系管理员或课程负责人；部署时也可用环境变量 <Text code>TEACHER_USERS</Text> 指定初始教师。
              <br />
              <Text type="secondary" style={{ fontSize: 12 }}>
                注意：教师邀请码（T- 开头）用于<Text strong>开通教师身份</Text>，
                与学生在「我的学习」页入班用的 6 位<Text strong>班级邀请码</Text>不是一回事。
              </Text>
            </Paragraph>
          </>
        )}

        {(role?.role === 'teacher' || role?.role === 'admin') && (
          <div>
            <Space wrap>
              <Tooltip title={policy && !policy.can_issue ? policy.reason : ''}>
                <Button icon={<TeamOutlined />} loading={busy} disabled={!policy?.can_issue}
                  onClick={createInvite}>
                  生成一个教师邀请码
                </Button>
              </Tooltip>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {policy?.can_issue
                  ? `用于邀请同事开通教师身份（一次性；有效期上限 ${policy.max_ttl_days} 天${
                    policy.quota != null ? `；你名下未用尽的邀请码最多 ${policy.quota} 个` : ''}）`
                  : (policy?.reason || '用于邀请同事开通教师身份')}
              </Text>
            </Space>

            {/* 权限收紧后的说明：默认仅管理员可签发，教师需部署方显式开启 */}
            {policy && !policy.can_issue && (
              <Alert
                style={{ marginTop: 8 }}
                type="info"
                showIcon
                message="教师邀请码默认由管理员签发"
                description={
                  <span>
                    为避免教师身份无控制扩散，签发权限默认只给管理员。
                    如需允许教师签发，请由管理员在部署环境设置
                    <Text code>TEACHER_CAN_INVITE=1</Text>
                    （并可用 <Text code>TEACHER_ISSUE_QUOTA</Text>、
                    <Text code>TEACHER_ISSUE_MAX_TTL_DAYS</Text> 调整配额与有效期上限）。
                  </span>
                }
              />
            )}

            {invite && (
              <Alert
                style={{ marginTop: 8 }}
                type="success"
                showIcon
                message={<span>教师邀请码：<Text code copyable>{invite.code}</Text></span>}
                description={`有效期至 ${String(invite.expires_at).slice(0, 10)}，可用 ${invite.max_uses} 次。请通过可靠渠道转交，用后即失效。`}
              />
            )}

            {/* 邀请码台账：管理员看全部，教师只看自己签发的 */}
            {activeInvites.length > 0 && (
              <List
                size="small"
                style={{ marginTop: 8 }}
                header={
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    未使用的教师邀请码 {activeInvites.length} 个
                    {policy?.is_admin ? '（管理员可见全部）' : '（仅显示你签发的）'}
                  </Text>
                }
                dataSource={activeInvites}
                renderItem={(it) => (
                  <List.Item
                    actions={[
                      <Popconfirm key="r" title="撤销这个教师邀请码？"
                        description="撤销后立即失效，已开通的账号不受影响。"
                        okText="撤销" cancelText="取消" onConfirm={() => revoke(it.code)}>
                        <Button type="link" size="small" danger>撤销</Button>
                      </Popconfirm>,
                    ]}
                  >
                    <Space size={8} wrap>
                      <Text code>{it.code}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {String(it.expires_at || '').slice(0, 10) || '长期'} 到期 ·
                        已用 {it.used_count || 0}/{it.max_uses || 1}
                      </Text>
                    </Space>
                  </List.Item>
                )}
              />
            )}
          </div>
        )}
      </Space>
    </Card>
  );
};

export default TeacherActivation;

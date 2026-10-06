/** 认证相关类型（放在独立文件，避免 api 模块之间循环引用） */
export interface AuthUser {
  id: string;
  username: string;
  display_name?: string | null;
  is_legacy?: boolean;
  status?: string;
  created_at?: string;
  /**
   * 角色（由**后端**判定，前端仅用于决定显示哪些入口）
   *
   * 注意：隐藏菜单≠权限。所有越权请求都会在后端被拒绝（403），
   * 前端不承担任何安全职责。
   */
  role?: 'student' | 'teacher' | 'admin';
}

# 认证重复提示修复（2026-10-07）

## 现象

未登录（或令牌失效）访问“任务与复习”等受保护页面时，连续弹出十几条
“登录状态已失效，请重新登录”，同时页面显示“部分内容加载失败”，下面又是“暂无待办”。

## 真实根因（沿代码与请求定位，不是靠隐藏提示解决）

### 根因 1：拦截器对**每一个** 401 都派发一次失效事件
`frontend/src/api/axios.ts` 旧实现：

```ts
if (status === 401) {
  localStorage.removeItem(TOKEN_KEY);                       // 每个 401 都清
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT)); // 每个 401 都广播
}
```

而 `TasksPage` 一次加载就用 `Promise.all` 发 6 个请求
（`/student/home`、`/student/tasks`、`/quiz/wrong`、`/quiz/reviews`、`/student/assignments`、
`/plan/adjustments`）。未登录时它们**全部 401** → 一次加载产生 6 次失效事件。

### 根因 2：页面上同时挂载了多个 UserAuth 实例，每个都监听并 toast
`App.tsx` 里有三处 `<AccountMenu />`（侧栏、顶栏、抽屉），每个内部都渲染 `<UserAuth />`；
宽屏下顶栏由 CSS `.app-topbar { display:none }` 隐藏，但 **React 组件依然挂载**，监听器依然存在。

UserAuth 旧实现：
```ts
const onExpired = () => { setUser(null); message.warning('登录状态已失效，请重新登录'); ... };
window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
```

6 次事件 × 2 个挂载实例 = **12 条重复提示**，与截图完全一致。

### 根因 3：未登录时页面照常发起个人接口请求
`TasksPage` 的 `useEffect` 直接 `load()`，不看登录态；`CourseContext` 虽判断了
`isLoggedIn()`，但页面自身仍在无令牌情况下请求。

### 根因 4：403 与 401 未在状态层区分；注销/切号存在竞态
旧实现只有“清令牌”这一种反应，且旧账号在途请求的返回仍可能写回新账号页面。

## 修复（从请求源头与状态层）

### 1. 新增唯一认证状态源 `frontend/src/api/authState.ts`
`anonymous → initializing → authenticated`，失效单独记为 `expired`；
维护 `generation`（登录代次）与 `expiredToken`（已处理过失效的令牌）。

关键函数 `markExpired()`：**同一个令牌的并发 401 只返回一次 true**，
调用方据此只清一次、只广播一次、只提示一次。

### 2. 拦截器（`api/axios.ts`）
- 请求时记录 `__authGen` 与 `__token`；
- 401：仅当"该请求确实带着当前令牌"且 `markExpired()` 为真时才派发一次失效事件；
  没带令牌的 401（本来就没登录）不当作"登录失效"；
- 403：只记一条 warn，**不清账号、不改变登录态**；
- 响应若属于旧代次（注销/切号前发出）→ 抛 `StaleAuthError`，不写回当前页面；
- 网络错误 / 500 / 解析错误照旧 reject，交给调用方如实处理。

### 3. 全站唯一提示入口 `frontend/src/context/AuthContext.tsx`
`AuthProvider` 只挂载一次，负责：启动时校验一次 `/auth/me`、消费唯一一次失效广播并提示一次、
提供 `login/register/logout`。`UserAuth` 与 `AccountMenu` 不再各自请求身份、不再各自 toast。

### 4. 受保护路由不挂载页面 `App.tsx` 的 `<Protected>`
未登录 / 已过期 / 校验中时**只渲染一个登录卡片**（`components/LoginRequired`），
页面组件根本不挂载 → 课程、任务、作业、掌握度等个人接口一个都不会发出，
也就不会再出现“登录提示 + 加载失败 + 暂无数据”同时出现。

登录成功后认证状态变为 `authenticated`，同一地址上的页面自动渲染 ——
这就是“登录后回到原本要进入的页面”，无需额外跳转。

### 5. 首次使用空状态
`TutorWorkspace` 在没有课程也没有资料时，给出一个突出主操作
「创建学科并上传资料」（跳 `/student/materials?create=1`，自动打开创建弹窗）
与次级入口「加入课程」（跳 `?join=1`，用邀请码加入），不再只显示一句“还没有课程或资料”。

## 验证（浏览器真实运行，非仅断言）

`tests/_browser/verify_auth.js`，12/12 通过：

| 断言 | 结果 |
|---|---|
| 未登录进入 /student/tasks 只显示统一登录入口 | PASS |
| 未登录不显示“加载失败/暂无任务” | PASS |
| 未登录不弹任何 toast | PASS（0 条） |
| 未登录**不发起**任何个人接口请求 | PASS（抓到的 /api 个人请求为空） |
| 过期令牌回到登录入口 | PASS |
| 并发 401 只提示一次 | PASS（峰值 1 条、累计 1 条） |
| 过期后不残留“部分内容加载失败” | PASS |
| 登录后正常渲染任务页且不再提示失效 | PASS |
| 学生访问教师端（403）仍保持登录、不提示失效 | PASS |
| 注销后受保护页面回到登录入口 | PASS |

另：`tests/_browser` 会在浏览器里真实统计 `.ant-message-notice` 峰值与总数，
并只统计以 `/api/` 开头的后端请求（`/src/api/*.ts` 是 Vite 的模块源码，不计入）。

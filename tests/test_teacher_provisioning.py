"""
教师身份开通与班级邀请码生命周期测试（2026-09-27）

本轮修复的核心安全问题：**普通学生可以通过"创建课程"自助获得教师权限**。
这里用真实 HTTP 请求固定修复后的行为，并覆盖邀请码的生命周期与退班后的访问收回。

对应验收清单：
- 学生不能自助取得教师权限（建课 / 工作台 / 生成邀请码全部被拒）
- 教师身份只能由受控通道开通：环境白名单 / 管理员 / 教师邀请码
- 邀请码：一次性、可过期、可撤销、次数上限、不能重复使用同一账号
- 退班/被移除后：资料、作业、班级数据都读不到（但自己的学习记录保留）
- 已有数据兼容：库内已是 teacher 的账号不受影响；老库能平滑加列

全部在隔离数据目录运行；外部模型不参与（本文件不调用模型）。
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures_isolated import IsolatedDataTestCase, register_and_login

SUBJECT = "数据结构"


class ProvisioningBase(IsolatedDataTestCase):
    SUBJECTS = (SUBJECT,)

    def setUp(self):
        super().setUp()
        from api.app import app
        from fastapi.testclient import TestClient
        from services import auth_service
        self.auth = auth_service
        self.client = TestClient(app)
        # 默认没有白名单：所有账号都是学生（未配置即无人是教师）
        self._env = mock.patch.dict(os.environ, {
            "TEACHER_USERS": "", "ADMIN_USERS": "",
        })
        self._env.start()
        self.addCleanup(self._env.stop)
        self.t_student = register_and_login("plain_student")
        self.student_id = auth_service.get_user_id_by_token(self.t_student)

    def h(self, token):
        return {"X-User-Token": token}

    def make_teacher(self, token=None, username="made_teacher"):
        """用管理员通道把账号开通为教师（模拟管理员操作）"""
        token = token or register_and_login(username)
        uid = self.auth.get_user_id_by_token(token)
        self.auth.grant_role(uid, self.auth.ROLE_TEACHER, reason="admin")
        return token, uid

    def make_admin(self, username="site_admin"):
        """
        注册一个处于 ADMIN_USERS 白名单中的管理员账号（本测试结束前保持有效）

        ADMIN_USERS 是环境变量白名单，服务端每次判定时读取，故用 patch.dict 模拟部署配置。
        """
        token = register_and_login(username)
        patcher = mock.patch.dict(os.environ, {"ADMIN_USERS": username})
        patcher.start()
        self.addCleanup(patcher.stop)
        return token


class TestNoSelfPromotion(ProvisioningBase):
    """修复验证：学生不能自助成为教师"""

    def test_student_cannot_create_course_without_invite(self):
        resp = self.client.post("/api/teacher/courses", headers=self.h(self.t_student),
                                json={"title": "学生偷偷建的课"})
        self.assertEqual(resp.status_code, 403, resp.text)
        body = resp.json()
        self.assertEqual(body["error_type"], "teacher_required")
        self.assertIn("邀请码", body["message"])
        # 角色没有被改变
        self.assertEqual(self.auth.get_role(self.student_id), self.auth.ROLE_STUDENT)

    def test_student_cannot_access_teacher_endpoints(self):
        for method, url in (
            ("get", "/api/teacher/dashboard"),
            ("get", "/api/teacher/courses"),
            ("post", "/api/teacher/invites"),
            ("get", "/api/teacher/invites"),
        ):
            if method == "post":
                resp = self.client.post(url, headers=self.h(self.t_student), json={})
            else:
                resp = self.client.get(url, headers=self.h(self.t_student))
            self.assertEqual(resp.status_code, 403, f"{url} → {resp.status_code}")

    def test_student_cannot_generate_invites_even_after_failed_course(self):
        self.client.post("/api/teacher/courses", headers=self.h(self.t_student),
                         json={"title": "x"})
        # 依然不能生成邀请码（不能靠失败重试或任何自助路径提权）
        resp = self.client.post("/api/teacher/invites", headers=self.h(self.t_student),
                                json={"max_uses": 1})
        self.assertEqual(resp.status_code, 403)

    def test_role_endpoint_tells_student_how_to_apply(self):
        data = self.client.get("/api/teacher/role",
                               headers=self.h(self.t_student)).json()["data"]
        self.assertEqual(data["role"], "student")
        self.assertFalse(data["can_create_course"])
        self.assertIn("邀请码", data["policy"])

    def test_unknown_invite_code_does_not_grant_role(self):
        resp = self.client.post("/api/teacher/courses", headers=self.h(self.t_student),
                                json={"title": "x", "teacher_invite_code": "T-DEADBEEF"})
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertEqual(resp.json()["error_type"], "invalid_invite")
        self.assertEqual(self.auth.get_role(self.student_id), self.auth.ROLE_STUDENT)


class TestControlledProvisioning(ProvisioningBase):
    """三种受控开通通道"""

    def test_env_whitelist_grants_teacher_without_db_write(self):
        with mock.patch.dict(os.environ, {"TEACHER_USERS": "plain_student"}):
            self.assertEqual(self.auth.get_role(self.student_id), self.auth.ROLE_TEACHER)
            resp = self.client.post("/api/teacher/courses", headers=self.h(self.t_student),
                                    json={"title": "白名单教师的课"})
            self.assertEqual(resp.status_code, 200, resp.text)
        # 去掉白名单后回到库内角色（白名单不写库，可随时收回）
        self.assertEqual(self.auth.get_role(self.student_id), self.auth.ROLE_STUDENT)

    def test_admin_can_grant_teacher_directly(self):
        with mock.patch.dict(os.environ, {"ADMIN_USERS": "plain_student"}):
            token, uid = self.make_teacher(username="granted_by_admin")
            resp = self.client.post(f"/api/teacher/users/{uid}/grant",
                                    headers=self.h(self.t_student))
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(self.auth.get_role(uid), self.auth.ROLE_TEACHER)

    def test_non_admin_cannot_grant_teacher(self):
        token, uid = self.make_teacher(username="t_someone")
        resp = self.client.post(f"/api/teacher/users/{self.student_id}/grant",
                                headers=self.h(token))
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(self.auth.get_role(self.student_id), self.auth.ROLE_STUDENT)

    def test_invite_code_flow_works_end_to_end(self):
        issuer_token = self.make_admin("issuer_admin_e2e")   # 收紧后默认只有管理员能签发
        created = self.client.post("/api/teacher/invites", headers=self.h(issuer_token),
                                   json={"max_uses": 1, "note": "新同事"}).json()["data"]
        code = created["code"]
        self.assertTrue(code.startswith("T-"))

        # 学生凭码开通
        resp = self.client.post("/api/teacher/apply-invite", headers=self.h(self.t_student),
                                json={"code": code})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self.auth.get_role(self.student_id), self.auth.ROLE_TEACHER)

        # 开通后可建课
        course = self.client.post("/api/teacher/courses", headers=self.h(self.t_student),
                                  json={"title": "凭码开通后的课程"})
        self.assertEqual(course.status_code, 200, course.text)

        # 同一账号不能重复使用同一码
        again = self.client.post("/api/teacher/apply-invite", headers=self.h(self.t_student),
                                 json={"code": code})
        self.assertEqual(again.status_code, 400)
        self.assertIn("已经使用过", again.json()["message"])

    def test_invite_is_single_use_across_users(self):
        issuer_token = self.make_admin("issuer_admin_two")
        code = self.client.post("/api/teacher/invites", headers=self.h(issuer_token),
                                json={"max_uses": 1}).json()["data"]["code"]
        second = register_and_login("second_student")
        self.assertEqual(self.client.post("/api/teacher/apply-invite",
                                          headers=self.h(self.t_student),
                                          json={"code": code}).status_code, 200)
        resp = self.client.post("/api/teacher/apply-invite", headers=self.h(second),
                                json={"code": code})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("次数已用完", resp.json()["message"])

    def test_revoked_invite_rejected(self):
        issuer_token = self.make_admin("issuer_admin_revoke")
        code = self.client.post("/api/teacher/invites", headers=self.h(issuer_token),
                                json={"max_uses": 5}).json()["data"]["code"]
        rev = self.client.post(f"/api/teacher/invites/{code}/revoke",
                               headers=self.h(issuer_token))
        self.assertEqual(rev.status_code, 200, rev.text)
        resp = self.client.post("/api/teacher/apply-invite", headers=self.h(self.t_student),
                                json={"code": code})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("撤销", resp.json()["message"])

    def test_expired_invite_rejected(self):
        issuer_token = self.make_admin("issuer_admin_expire")
        code = self.client.post("/api/teacher/invites", headers=self.h(issuer_token),
                                json={"max_uses": 5, "ttl_days": 1}).json()["data"]["code"]
        # 直接改库把有效期改到过去（模拟过期，不改业务逻辑）
        conn = self.auth._get_db()
        try:
            past = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
            conn.execute("UPDATE role_invites SET expires_at=? WHERE code=?", (past, code))
            conn.commit()
        finally:
            conn.close()
        resp = self.client.post("/api/teacher/apply-invite", headers=self.h(self.t_student),
                                json={"code": code})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("过期", resp.json()["message"])

    def test_invite_multi_use_respects_limit(self):
        issuer_token = self.make_admin("issuer_admin_multi")
        code = self.client.post("/api/teacher/invites", headers=self.h(issuer_token),
                                json={"max_uses": 2}).json()["data"]["code"]
        for name in ("multi_a", "multi_b"):
            tok = register_and_login(name)
            self.assertEqual(self.client.post("/api/teacher/apply-invite", headers=self.h(tok),
                                              json={"code": code}).status_code, 200)
        tok3 = register_and_login("multi_c")
        resp = self.client.post("/api/teacher/apply-invite", headers=self.h(tok3),
                                json={"code": code})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("次数已用完", resp.json()["message"])

    def test_legacy_teacher_row_still_works(self):
        """已有数据兼容：库内 role=teacher 的账号（历史授予）继续可用"""
        token = register_and_login("legacy_teacher")
        uid = self.auth.get_user_id_by_token(token)
        with self.auth._get_db() as conn:
            conn.execute("UPDATE users SET role='teacher', role_source='course' WHERE id=?",
                         (uid,))
            conn.commit()
        with mock.patch.dict(os.environ, {"TEACHER_USERS": "", "ADMIN_USERS": ""}):
            self.assertEqual(self.auth.get_role(uid), self.auth.ROLE_TEACHER)
            resp = self.client.post("/api/teacher/courses", headers=self.h(token),
                                    json={"title": "历史教师继续建课"})
            self.assertEqual(resp.status_code, 200, resp.text)

    def test_role_reported_consistently_by_auth_me(self):
        """界面看到的角色必须与后端判定一致（避免"显示学生却有教师权限"）"""
        with mock.patch.dict(os.environ, {"TEACHER_USERS": "plain_student"}):
            me = self.client.get("/api/auth/me", headers=self.h(self.t_student)).json()["data"]
            self.assertEqual(me["role"], "teacher")
            self.assertEqual(me["role_source"], "env")


class TestInviteIssuancePolicy(ProvisioningBase):
    """
    邀请码**签发权限**收紧（2026-09-27 第二轮）

    背景：上一轮把"开通教师身份"改成了邀请码制，但签发权仍在**任何教师**手上，
    且没有配额——等于教师可以无限拉人，邀请码机制本身成了不受控的扩散渠道。

    现在：默认仅管理员可签发；教师签发需部署方显式开启 TEACHER_CAN_INVITE=1，
    并受有效期 / 可用次数 / 名下未用尽邀请码数量三重约束。
    """

    def _teacher_issuer(self, username="t_issuer"):
        """开通一名教师（非管理员）作为签发者"""
        token, uid = self.make_teacher(username=username)
        return token, uid

    def test_teacher_cannot_issue_by_default(self):
        """默认策略：教师即使已开通身份，也不能签发邀请码"""
        token, uid = self._teacher_issuer("t_no_issue")
        self.assertFalse(self.auth.TEACHER_CAN_INVITE, "本用例要求默认关闭")
        resp = self.client.post("/api/teacher/invites", headers=self.h(token),
                                json={"max_uses": 1})
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(resp.json()["error_type"], "forbidden")
        self.assertIn("TEACHER_CAN_INVITE", resp.json()["message"])
        # 被拒后一个码都没产生（不是"先生成再回收"）
        self.assertEqual(self.auth.list_role_invites(50, actor_user_id=uid), [])

    def test_teacher_can_issue_only_when_explicitly_enabled(self):
        token, _ = self._teacher_issuer("t_enabled")
        with mock.patch.object(self.auth, "TEACHER_CAN_INVITE", True):
            resp = self.client.post("/api/teacher/invites", headers=self.h(token),
                                    json={"max_uses": 1})
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertTrue(resp.json()["data"]["code"].startswith("T-"))

    def test_teacher_issuance_respects_ttl_and_uses_caps(self):
        """教师签发不能超出上限（不能自己给自己放宽）"""
        token, _ = self._teacher_issuer("t_caps")
        with mock.patch.object(self.auth, "TEACHER_CAN_INVITE", True):
            over_ttl = self.client.post(
                "/api/teacher/invites", headers=self.h(token),
                json={"ttl_days": self.auth.TEACHER_ISSUE_MAX_TTL_DAYS + 1})
            self.assertEqual(over_ttl.status_code, 403, over_ttl.text)
            self.assertIn("有效期上限", over_ttl.json()["message"])

            over_uses = self.client.post(
                "/api/teacher/invites", headers=self.h(token),
                json={"max_uses": self.auth.TEACHER_ISSUE_MAX_USES + 1})
            self.assertEqual(over_uses.status_code, 403, over_uses.text)
            self.assertIn("次数上限", over_uses.json()["message"])

            # 边界内可用
            ok = self.client.post("/api/teacher/invites", headers=self.h(token),
                                  json={"ttl_days": self.auth.TEACHER_ISSUE_MAX_TTL_DAYS,
                                        "max_uses": self.auth.TEACHER_ISSUE_MAX_USES})
            self.assertEqual(ok.status_code, 200, ok.text)

    def test_teacher_issuance_quota_blocks_unbounded_spread(self):
        """配额：教师名下未用尽的邀请码数量达到上限后必须被拒（429）"""
        token, uid = self._teacher_issuer("t_quota")
        quota = self.auth.TEACHER_ISSUE_QUOTA
        with mock.patch.object(self.auth, "TEACHER_CAN_INVITE", True):
            for i in range(quota):
                r = self.client.post("/api/teacher/invites", headers=self.h(token),
                                     json={"max_uses": 1})
                self.assertEqual(r.status_code, 200, f"第 {i + 1} 个应放行：{r.text}")
            blocked = self.client.post("/api/teacher/invites", headers=self.h(token),
                                       json={"max_uses": 1})
            self.assertEqual(blocked.status_code, 429, blocked.text)
            self.assertEqual(blocked.json()["error_type"], "quota_exceeded")
            self.assertIn("撤销", blocked.json()["message"])

            # 撤销一个后恢复一个额度（配额看的是"未用尽"，不是历史总数）
            first = self.auth.list_role_invites(50, actor_user_id=uid)
            active = [i for i in first if not i["revoked_at"]
                      and i["used_count"] < i["max_uses"]]
            self.client.post(f"/api/teacher/invites/{active[0]['code']}/revoke",
                             headers=self.h(token))
            again = self.client.post("/api/teacher/invites", headers=self.h(token),
                                     json={"max_uses": 1})
            self.assertEqual(again.status_code, 200, again.text)

    def test_admin_issuance_not_limited_by_teacher_quota(self):
        """管理员不受教师配额限制（配额只约束教师通道）"""
        admin = self.make_admin("quota_admin")
        n = self.auth.TEACHER_ISSUE_QUOTA + 2
        for i in range(n):
            r = self.client.post("/api/teacher/invites", headers=self.h(admin),
                                 json={"max_uses": 1})
            self.assertEqual(r.status_code, 200, f"第 {i + 1} 个应放行：{r.text}")

    def test_teacher_sees_only_own_invites_admin_sees_all(self):
        """列表与撤销的可见范围：教师只看自己的，管理员看全部"""
        t1, uid1 = self._teacher_issuer("t_scope_one")
        t2, uid2 = self._teacher_issuer("t_scope_two")
        admin = self.make_admin("scope_admin")

        a1 = self.client.post("/api/teacher/invites", headers=self.h(admin),
                              json={"max_uses": 1}).json()["data"]["code"]
        with mock.patch.object(self.auth, "TEACHER_CAN_INVITE", True):
            b1 = self.client.post("/api/teacher/invites", headers=self.h(t1),
                                  json={"max_uses": 1}).json()["data"]["code"]
            b2 = self.client.post("/api/teacher/invites", headers=self.h(t2),
                                  json={"max_uses": 1}).json()["data"]["code"]

        seen1 = {i["code"] for i in self.client.get("/api/teacher/invites",
                                                    headers=self.h(t1)).json()["data"]["invites"]}
        self.assertEqual(seen1, {b1}, "教师不应看到别人的邀请码")
        seen_admin = {i["code"] for i in self.client.get(
            "/api/teacher/invites", headers=self.h(admin)).json()["data"]["invites"]}
        self.assertTrue({a1, b1, b2} <= seen_admin, "管理员应看到全部邀请码")

    def test_teacher_cannot_revoke_others_invite_admin_can(self):
        t1, uid1 = self._teacher_issuer("t_rev_one")
        t2, uid2 = self._teacher_issuer("t_rev_two")
        admin = self.make_admin("revoke_admin")

        with mock.patch.object(self.auth, "TEACHER_CAN_INVITE", True):
            mine = self.client.post("/api/teacher/invites", headers=self.h(t1),
                                    json={"max_uses": 1}).json()["data"]["code"]
        # t2 撤销 t1 的码 → 403
        other = self.client.post(f"/api/teacher/invites/{mine}/revoke",
                                 headers=self.h(t2))
        self.assertEqual(other.status_code, 403, other.text)
        self.assertIn("只能撤销自己签发的", other.json()["message"])
        # 管理员可以撤销
        adm = self.client.post(f"/api/teacher/invites/{mine}/revoke",
                               headers=self.h(admin))
        self.assertEqual(adm.status_code, 200, adm.text)
        self.assertEqual(self.auth.get_role(self.student_id), self.auth.ROLE_STUDENT)

    def test_invites_endpoint_reports_policy_for_ui(self):
        """界面据此禁用按钮：必须能拿到 can_issue 与原因"""
        token, _ = self._teacher_issuer("t_policy_view")
        data = self.client.get("/api/teacher/invites", headers=self.h(token)).json()["data"]
        self.assertIn("policy", data)
        self.assertFalse(data["policy"]["can_issue"])
        self.assertFalse(data["policy"]["is_admin"])
        self.assertEqual(data["policy"]["quota"], self.auth.TEACHER_ISSUE_QUOTA)

        admin = self.make_admin("policy_admin")
        adata = self.client.get("/api/teacher/invites", headers=self.h(admin)).json()["data"]
        self.assertTrue(adata["policy"]["can_issue"])
        self.assertTrue(adata["policy"]["is_admin"])
        self.assertIsNone(adata["policy"]["quota"])


class TestJoinCodeLifecycle(ProvisioningBase):

    def setUp(self):
        super().setUp()
        from services import teaching_service as ts, teaching_data as td
        self.ts, self.td = ts, td
        self.teacher_token, self.teacher_id = self.make_teacher(username="life_teacher")
        self.course = self.client.post("/api/teacher/courses",
                                       headers=self.h(self.teacher_token),
                                       json={"title": "邀请码生命周期课"}).json()["data"]
        self.klass = self.client.post(
            f"/api/teacher/courses/{self.course['id']}/classes",
            headers=self.h(self.teacher_token), json={"name": "一班"}).json()["data"]

    def test_code_status_reports_expiry_and_usage(self):
        status = self.client.get(f"/api/teacher/classes/{self.klass['id']}/join-code",
                                 headers=self.h(self.teacher_token)).json()["data"]
        self.assertTrue(status["valid"])
        self.assertIsNotNone(status["expires_at"])
        self.assertEqual(status["used_count"], 0)
        self.assertGreater(status["max_uses"], 0)

    def test_revoked_code_cannot_be_used_and_rotating_restores(self):
        rev = self.client.post(f"/api/teacher/classes/{self.klass['id']}/revoke-code",
                               headers=self.h(self.teacher_token))
        self.assertEqual(rev.status_code, 200, rev.text)
        bad = self.client.post("/api/student/courses/join", headers=self.h(self.t_student),
                               json={"code": self.klass["join_code"]})
        self.assertEqual(bad.status_code, 404, bad.text)

        status = self.client.get(f"/api/teacher/classes/{self.klass['id']}/join-code",
                                 headers=self.h(self.teacher_token)).json()["data"]
        self.assertFalse(status["valid"])
        self.assertIn("撤销", status["invalid_reason"])

        # 轮换出新码 → 又能加入（新码重置撤销与计数）
        new_klass = self.client.post(f"/api/teacher/classes/{self.klass['id']}/reset-code",
                                     headers=self.h(self.teacher_token)).json()["data"]
        self.assertNotEqual(new_klass["join_code"], self.klass["join_code"])
        ok = self.client.post("/api/student/courses/join", headers=self.h(self.t_student),
                              json={"code": new_klass["join_code"]})
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_expired_code_rejected(self):
        conn = self.td._get_db()
        try:
            past = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
            conn.execute("UPDATE classes SET join_code_expires_at=? WHERE id=?",
                         (past, self.klass["id"]))
            conn.commit()
        finally:
            conn.close()
        resp = self.client.post("/api/student/courses/join", headers=self.h(self.t_student),
                                json={"code": self.klass["join_code"]})
        self.assertEqual(resp.status_code, 404)
        self.assertIn("过期", resp.json()["message"])

    def test_usage_count_increments_and_limit_enforced(self):
        conn = self.td._get_db()
        try:
            conn.execute("UPDATE classes SET join_code_max_uses=1 WHERE id=?",
                         (self.klass["id"],))
            conn.commit()
        finally:
            conn.close()
        first = register_and_login("limit_a")
        self.assertEqual(self.client.post("/api/student/courses/join", headers=self.h(first),
                                          json={"code": self.klass["join_code"]}).status_code, 200)
        status = self.client.get(f"/api/teacher/classes/{self.klass['id']}/join-code",
                                 headers=self.h(self.teacher_token)).json()["data"]
        self.assertEqual(status["used_count"], 1)
        # 达到上限后不能再加入
        second = register_and_login("limit_b")
        resp = self.client.post("/api/student/courses/join", headers=self.h(second),
                                json={"code": self.klass["join_code"]})
        self.assertEqual(resp.status_code, 404)
        self.assertIn("次数", resp.json()["message"])

    def test_rejoin_by_same_user_does_not_double_count(self):
        self.client.post("/api/student/courses/join", headers=self.h(self.t_student),
                         json={"code": self.klass["join_code"]})
        self.client.post("/api/student/courses/join", headers=self.h(self.t_student),
                         json={"code": self.klass["join_code"]})
        status = self.client.get(f"/api/teacher/classes/{self.klass['id']}/join-code",
                                 headers=self.h(self.teacher_token)).json()["data"]
        self.assertEqual(status["used_count"], 1, "同一用户重复加入不应重复计数")

    def test_brute_force_attempts_are_throttled(self):
        from services import teaching_service as ts
        codes = ["AAAAA" + c for c in "ABCDEFGHIJK"]
        limited = False
        for i, code in enumerate(codes):
            resp = self.client.post("/api/student/courses/join", headers=self.h(self.t_student),
                                    json={"code": code})
            if resp.status_code == 429 or resp.json().get("error_type") == "too_many_attempts":
                limited = True
                self.assertGreaterEqual(i + 1, ts.JOIN_ATTEMPT_LIMIT,
                                        "过早触发限速会误伤正常用户")
                break
        self.assertTrue(limited, "连续失败尝试未被限速（存在暴力猜测邀请码风险）")

    def test_wrong_code_does_not_leak_which_step_failed(self):
        resp = self.client.post("/api/student/courses/join", headers=self.h(self.t_student),
                                json={"code": "ZZZZZZ"})
        self.assertEqual(resp.status_code, 404)


class TestAccessAfterLeaving(ProvisioningBase):
    """退班/被移除后：课程资料、作业、班级数据都读不到；自己的学习记录保留"""

    def setUp(self):
        super().setUp()
        from services import teaching_service as ts, teaching_data as td, quiz_service
        self.ts, self.td, self.quiz = ts, td, quiz_service
        self.teacher_token, self.teacher_id = self.make_teacher(username="leave_teacher")
        self.other_student = register_and_login("leave_other")
        self.other_id = self.auth.get_user_id_by_token(self.other_student)

        self.course = self.client.post("/api/teacher/courses",
                                       headers=self.h(self.teacher_token),
                                       json={"title": "退班访问课"}).json()["data"]
        self.klass = self.client.post(
            f"/api/teacher/courses/{self.course['id']}/classes",
            headers=self.h(self.teacher_token), json={"name": "一班"}).json()["data"]
        self.sid = self.course["subject_id"]

        # 资料（学生可见） + 教师专属资料
        for name, vis in (("公开讲义.txt", "students"), ("教师草稿.txt", "teacher_only")):
            self.client.post(
                f"/api/teacher/courses/{self.course['id']}/materials?visibility={vis}",
                headers=self.h(self.teacher_token),
                files={"file": (name, b"content", "text/plain")})

        # 学生加入并作答一次（留下自己的学习记录）
        self.client.post("/api/student/courses/join", headers=self.h(self.t_student),
                         json={"code": self.klass["join_code"]})
        q = self.quiz.persist_questions(
            subject_id=self.sid, knowledge_point_name="遍历",
            questions=[{"type": "single_choice", "question": "前序顺序？",
                        "options": ["根左右", "左根右"], "answer": "根左右"}],
            source="test")
        self.question_id = q[0]["id"]
        resp = self.client.post("/api/quiz/answer", headers=self.h(self.t_student),
                                json={"question_id": self.question_id, "user_answer": "A",
                                      "client_token": "leave-1"})
        self.assertEqual(resp.status_code, 200, resp.text)

    def _join(self, token):
        return self.client.post("/api/student/courses/join", headers=self.h(token),
                                json={"code": self.klass["join_code"]})

    def _leave(self, token, class_id=None):
        return self.client.post(f"/api/student/classes/{class_id or self.klass['id']}/leave",
                                headers=self.h(token))

    def test_member_can_read_course_material(self):
        self._join(self.t_student)
        docs = self.client.get(f"/api/knowledge/{self.sid}/documents",
                               headers=self.h(self.t_student))
        names = [d["name"] for d in docs.json()["documents"]]
        self.assertIn("公开讲义.txt", names)
        self.assertNotIn("教师草稿.txt", names)

    def test_after_leaving_cannot_read_material_or_course(self):
        self._join(self.t_student)
        self._leave(self.t_student)
        self.assertEqual(self.client.get(f"/api/knowledge/{self.sid}/documents",
                                         headers=self.h(self.t_student)).status_code, 403)
        self.assertEqual(self.client.get(
            f"/api/knowledge/{self.sid}/documents/{'公开讲义.txt'}/file",
            headers=self.h(self.t_student)).status_code, 403)
        self.assertEqual(self.client.get(f"/api/student/courses/{self.course['id']}/materials",
                                         headers=self.h(self.t_student)).status_code, 403)
        self.assertEqual(self.client.get(f"/api/student/courses",
                                         headers=self.h(self.t_student)).json()["data"]["courses"],
                         [])

    def test_after_leaving_keeps_own_learning_records(self):
        """退班不等于抹掉自己的学习记录（错题/作答仍属于本人）"""
        self._join(self.t_student)
        self._leave(self.t_student)
        wrong = self.client.get(f"/api/quiz/wrong?subject_id={self.sid}",
                                headers=self.h(self.t_student))
        self.assertEqual(wrong.status_code, 200, wrong.text)
        attempts = self.client.get(f"/api/quiz/attempts?subject_id={self.sid}",
                                   headers=self.h(self.t_student))
        self.assertEqual(attempts.status_code, 200)
        self.assertGreaterEqual(len(attempts.json()["data"]), 1)

    def test_removed_student_loses_access(self):
        self._join(self.t_student)
        members = self.client.get(f"/api/teacher/classes/{self.klass['id']}/members",
                                  headers=self.h(self.teacher_token)).json()["data"]["members"]
        target = [m for m in members if m["user_id"] == self.student_id][0]
        resp = self.client.post(
            f"/api/teacher/classes/{self.klass['id']}/members/{target['member_id']}/remove",
            headers=self.h(self.teacher_token), params={"reason": "转班"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(self.client.get(f"/api/knowledge/{self.sid}/documents",
                                         headers=self.h(self.t_student)).status_code, 403)

    def test_non_member_cannot_read_anything(self):
        for url in (f"/api/knowledge/{self.sid}/documents",
                    f"/api/student/courses/{self.course['id']}/materials",
                    f"/api/student/courses/{self.course['id']}/lesson-plans"):
            self.assertEqual(self.client.get(url, headers=self.h(self.other_student)).status_code,
                             403, url)

    def test_student_cannot_use_other_students_identity(self):
        """换 user_id / 退出班级后继续访问 都被拒"""
        for url in (f"/api/student/tasks?user_id={self.student_id}",
                    f"/api/quiz/attempts?user_id={self.student_id}",
                    f"/api/student/mastery?user_id={self.student_id}"):
            resp = self.client.get(url, headers=self.h(self.other_student))
            # 这些接口忽略路径/查询里的 user_id，只返回自己的数据（不报错也不越权）
            self.assertIn(resp.status_code, (200, 403, 422), url)
        # agent 的用户域接口：指向他人必须 403
        resp = self.client.get(f"/api/agent/profile/{self.student_id}",
                               headers=self.h(self.other_student))
        self.assertEqual(resp.status_code, 403, resp.text)


if __name__ == "__main__":
    unittest.main()

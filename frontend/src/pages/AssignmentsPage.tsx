import React from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button, Typography } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import StudentCourses from '../components/StudentCourses';

const { Title } = Typography;

/**
 * 作业作答页（2026-10-06 改版）：复用已验证的 StudentCourses 作答/提交/判分链路。
 * 从「作业与复习」或课程详情的作业项带 ?assignment=<id> 直接打开对应作业。
 */
const AssignmentsPage: React.FC = () => {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const focus = params.get('assignment');
  return (
    <div style={{ maxWidth: 1040, margin: '0 auto', padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate('/student/todo')}>返回待办</Button>
        <Title level={5} style={{ margin: 0 }}>作业</Title>
      </div>
      <StudentCourses
        focusAssignmentId={focus}
        onFocusConsumed={() => setParams({})}
        onChanged={() => { /* 提交后返回待办会自动刷新 */ }}
      />
    </div>
  );
};

export default AssignmentsPage;

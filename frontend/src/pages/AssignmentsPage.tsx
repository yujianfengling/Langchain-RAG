import React from 'react';
import { Button, Typography } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useNavigate, useSearchParams } from 'react-router-dom';
import StudentCourses from '../components/StudentCourses';

const { Title, Text } = Typography;

/** 课程与作业直达页：弹窗打开期间保留 assignment 参数供刷新恢复，关闭时消费深链。 */
const AssignmentsPage: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const assignmentId = searchParams.get('assignment');

  return (
    <div className="app-page app-page-narrow">
      <header className="page-header">
        <div className="page-heading">
          <div className="page-eyebrow">课程学习</div>
          <Title level={1} className="page-title">课程与作业</Title>
          <Text className="page-subtitle">查看已加入的课程、老师发布的教案与作业。</Text>
        </div>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/student/tasks')}>
          返回任务与复习
        </Button>
      </header>
      <StudentCourses
        focusAssignmentId={assignmentId}
        onFocusConsumed={() => navigate('/student/assignments', { replace: true })}
      />
    </div>
  );
};

export default AssignmentsPage;

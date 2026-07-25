import React, { useState, useEffect } from 'react';
import { Select, Spin, message } from 'antd';
import { BookOutlined } from '@ant-design/icons';
import { Subject } from '../../types';
import { getSubjects } from '../../api/subjects';

const { Option } = Select;

interface SubjectSelectorProps {
  value?: string;
  onChange?: (value: string) => void;
  style?: React.CSSProperties;
}

const SubjectSelector: React.FC<SubjectSelectorProps> = ({ value, onChange, style }) => {
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [loading, setLoading] = useState(false);

  // 加载学科列表
  useEffect(() => {
    const loadSubjects = async () => {
      setLoading(true);
      try {
        const data = await getSubjects();
        setSubjects(data);
      } catch (error) {
        console.error('加载学科列表失败:', error);
        message.error('加载学科列表失败');
      } finally {
        setLoading(false);
      }
    };

    loadSubjects();
  }, []);

  return (
    <Select
      value={value}
      onChange={onChange}
      placeholder="选择学科"
      loading={loading}
      style={{ width: '100%', ...style }}
      suffixIcon={<BookOutlined />}
      notFoundContent={loading ? <Spin size="small" /> : '暂无学科数据'}
    >
      {subjects.map(subject => (
        <Option key={subject.subject_id} value={subject.subject_id}>
          {subject.name}
        </Option>
      ))}
    </Select>
  );
};

export default SubjectSelector;
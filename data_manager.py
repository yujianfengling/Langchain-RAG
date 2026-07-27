#!/usr/bin/env python3
"""
数据管理模块
负责学科信息管理、文件列表管理、文件读写
"""

import json
import os
from typing import List, Dict, Any, Optional

# 使用项目配置系统
try:
    from config import settings
    DATA_DIR = str(settings.path.data_dir)
    SUBJECTS_FILE = str(settings.path.data_dir / "subjects.json")
    FILES_FILE = str(settings.path.data_dir / "files.json")
except ImportError:
    # 兼容独立运行模式
    DATA_DIR = "data"
    SUBJECTS_FILE = "data/subjects.json"
    FILES_FILE = "data/files.json"

# 确保数据目录存在
os.makedirs(DATA_DIR, exist_ok=True)


def load_subs() -> List[Dict[str, Any]]:
    """
    加载学科信息
    
    Returns:
        学科列表，每个学科包含id、name、description等字段
    """
    if not os.path.exists(SUBJECTS_FILE):
        return []
    try:
        with open(SUBJECTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def save_subs(subjects: List[Dict[str, Any]]) -> bool:
    """
    保存学科信息
    
    Args:
        subjects: 学科列表
        
    Returns:
        保存是否成功
    """
    try:
        with open(SUBJECTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(subjects, f, ensure_ascii=False, indent=2)
        return True
    except IOError:
        return False


def get_files(subject_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    获取文件列表
    
    Args:
        subject_id: 可选，学科ID，如果提供则只返回该学科的文件
        
    Returns:
        文件列表，每个文件包含id、name、subject_id、path等字段
    """
    if not os.path.exists(FILES_FILE):
        return []
    try:
        with open(FILES_FILE, 'r', encoding='utf-8') as f:
            files = json.load(f)
        if subject_id:
            files = [f for f in files if f.get('subject_id') == subject_id]
        return files
    except (json.JSONDecodeError, IOError):
        return []


def save_files(files: List[Dict[str, Any]]) -> bool:
    """
    保存文件列表
    
    Args:
        files: 文件列表
        
    Returns:
        保存是否成功
    """
    try:
        with open(FILES_FILE, 'w', encoding='utf-8') as f:
            json.dump(files, f, ensure_ascii=False, indent=2)
        return True
    except IOError:
        return False


def create_subj(name: str, description: str = "") -> Dict[str, Any]:
    """
    创建学科
    
    Args:
        name: 学科名称
        description: 学科描述
        
    Returns:
        创建的学科信息
    """
    subjects = load_subs()
    subject_id = f"subj_{len(subjects) + 1}"
    new_subject = {
        "id": subject_id,
        "name": name,
        "description": description,
        "created_at": "2026-07-24"
    }
    subjects.append(new_subject)
    save_subs(subjects)
    return new_subject


def delete_subj(subject_id: str) -> bool:
    """
    删除学科
    
    Args:
        subject_id: 学科ID
        
    Returns:
        删除是否成功
    """
    subjects = load_subs()
    subjects = [s for s in subjects if s.get('id') != subject_id]
    save_subs(subjects)
    
    # 同时删除该学科下的所有文件
    files = get_files()
    files = [f for f in files if f.get('subject_id') != subject_id]
    save_files(files)
    
    return True


def add_file(subject_id: str, file_name: str, file_path: str) -> Dict[str, Any]:
    """
    添加文件
    
    Args:
        subject_id: 学科ID
        file_name: 文件名
        file_path: 文件路径
        
    Returns:
        添加的文件信息
    """
    files = get_files()
    file_id = f"file_{len(files) + 1}"
    new_file = {
        "id": file_id,
        "name": file_name,
        "subject_id": subject_id,
        "path": file_path,
        "created_at": "2026-07-24"
    }
    files.append(new_file)
    save_files(files)
    return new_file


def delete_file(file_id: str) -> bool:
    """
    删除文件
    
    Args:
        file_id: 文件ID
        
    Returns:
        删除是否成功
    """
    files = get_files()
    files = [f for f in files if f.get('id') != file_id]
    save_files(files)
    return True


def update_subj_desc(subject_id: str, description: str) -> bool:
    """
    更新学科描述
    
    Args:
        subject_id: 学科ID
        description: 新的描述
        
    Returns:
        更新是否成功
    """
    subjects = load_subs()
    for subject in subjects:
        if subject.get('id') == subject_id:
            subject['description'] = description
            break
    save_subs(subjects)
    return True


def rename_subj(subject_id: str, new_name: str) -> bool:
    """
    重命名学科
    
    Args:
        subject_id: 学科ID
        new_name: 新的名称
        
    Returns:
        重命名是否成功
    """
    subjects = load_subs()
    for subject in subjects:
        if subject.get('id') == subject_id:
            subject['name'] = new_name
            break
    save_subs(subjects)
    return True


def update_file_info(file_id: str, **kwargs) -> bool:
    """
    更新文件信息
    
    Args:
        file_id: 文件ID
        **kwargs: 要更新的字段
        
    Returns:
        更新是否成功
    """
    files = get_files()
    for file in files:
        if file.get('id') == file_id:
            for key, value in kwargs.items():
                if key in file:
                    file[key] = value
            break
    save_files(files)
    return True


def read_file_content(file_path: str) -> str:
    """
    读取文件内容
    
    Args:
        file_path: 文件路径
        
    Returns:
        文件内容字符串
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except (IOError, UnicodeDecodeError):
        return ""


def get_all_content(subject_id: Optional[str] = None) -> str:
    """
    获取所有文件内容（合并后）
    
    Args:
        subject_id: 可选，学科ID，如果提供则只包含该学科的文件
        
    Returns:
        合并后的文件内容
    """
    files = get_files(subject_id)
    all_content = []
    for file in files:
        content = read_file_content(file.get('path', ''))
        if content:
            all_content.append(f"=== {file.get('name', '未知文件')} ===\n{content}")
    return "\n\n".join(all_content)

# ============ 别名（推荐使用完整名称） ============
load_subjects = load_subs
save_subjects = save_subs
create_subject = create_subj
delete_subject = delete_subj

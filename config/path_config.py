"""
路径配置模块
管理项目相关的路径配置
"""

from pathlib import Path
from typing import Dict, Any, List
from .base import BaseConfig, Field, PYDANTIC_V2, get_project_root


class PathConfig(BaseConfig):
    """路径配置"""
    
    # 项目根目录
    project_root: Path = Field(default_factory=get_project_root)
    
    # 数据目录
    data_dir: Path = Field(default_factory=lambda: get_project_root() / "data")
    
    # 向量存储目录
    vector_store_path: Path = Field(default_factory=lambda: get_project_root() / "data" / "vector_store")
    
    @property
    def vector_store_dir(self) -> Path:
        """向量存储目录（兼容别名）"""
        return self.vector_store_path
    
    # 学科资料目录
    subjects_data_path: Path = Field(default_factory=lambda: get_project_root() / "data" / "subjects")
    
    # 日志目录
    log_dir: Path = Field(default_factory=lambda: get_project_root() / "logs")
    
    if PYDANTIC_V2:
        model_config = {
            "env_prefix": "",
            "case_sensitive": False
        }
    else:
        class Config:
            env_prefix = ""
            case_sensitive = False
    
    def ensure_directories(self) -> None:
        """确保所有必要的目录存在"""
        directories = [
            self.data_dir,
            self.vector_store_path,
            self.subjects_data_path,
            self.log_dir
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
    
    def get_subject_vector_store_path(self, subject_id: str) -> Path:
        """
        获取指定学科的向量存储路径
        
        Args:
            subject_id: 学科ID
            
        Returns:
            学科向量存储路径
        """
        return self.vector_store_path / subject_id
    
    def ensure_subject_directory(self, subject_id: str) -> Path:
        """
        确保学科目录存在
        
        Args:
            subject_id: 学科ID
            
        Returns:
            学科向量存储路径
        """
        subject_path = self.get_subject_vector_store_path(subject_id)
        subject_path.mkdir(parents=True, exist_ok=True)
        return subject_path
    
    def list_subjects(self) -> List[str]:
        """
        列出所有已创建的学科目录
        
        Returns:
            学科ID列表
        """
        if not self.vector_store_path.exists():
            return []
        
        return [
            d.name for d in self.vector_store_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
    
    def validate(self) -> list:
        """
        验证路径配置
        
        Returns:
            错误列表
        """
        errors = []
        
        # 检查项目根目录
        if not self.project_root.exists():
            errors.append(f"项目根目录不存在: {self.project_root}")
        
        return errors
    
    def get_masked_config(self) -> Dict[str, str]:
        """获取配置字典"""
        return {
            "project_root": str(self.project_root),
            "data_dir": str(self.data_dir),
            "vector_store_path": str(self.vector_store_path),
            "subjects_data_path": str(self.subjects_data_path),
            "log_dir": str(self.log_dir)
        }


# 公开API
__all__ = [
    "PathConfig"
]

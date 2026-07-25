# api/exceptions.py
# 自定义API异常类


class APIException(Exception):
    """API基础异常类"""

    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class ValidationError(APIException):
    """参数验证错误"""

    def __init__(self, message: str):
        super().__init__(message, status_code=400)


class KnowledgeBaseNotFoundError(APIException):
    """知识库不存在错误"""

    def __init__(self, subject_id: str):
        message = f"知识库不存在: {subject_id}"
        super().__init__(message, status_code=404)


class LLMServiceError(APIException):
    """LLM服务错误"""

    def __init__(self, message: str):
        super().__init__(message, status_code=500)


class RAGServiceError(APIException):
    """RAG服务错误"""

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, message: str):
        super().__init__(message, status_code=500)


class ModelServiceError(APIException):
    """模型服务异常（LLM调用超时、不可用等）"""
    def __init__(self, message: str = "模型服务异常"):
        super().__init__(message=message, status_code=500)


class SubjectServiceError(APIException):
    """学科服务异常"""
    def __init__(self, message: str = "学科服务异常"):
        super().__init__(message=message, status_code=400)


class FileServiceError(APIException):
    """文件服务异常"""
    def __init__(self, message: str = "文件服务异常"):
        super().__init__(message=message, status_code=400)


class KnowledgePointParseError(RAGServiceError):
    """知识点解析失败"""
    pass


class SummaryLengthError(RAGServiceError):
    """总结长度不符合要求"""
    pass
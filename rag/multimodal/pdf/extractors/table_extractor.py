"""
表格提取器

从 PDF 中提取表格并结构化。
"""

import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class TableExtractor:
    """
    表格提取器
    
    从 PDF 中提取表格并转换为结构化数据。
    """
    
    def __init__(self):
        """初始化表格提取器"""
        logger.info("TableExtractor 初始化完成")
    
    def extract(
        self,
        table_elements: List[PDFElement]
    ) -> List[Dict[str, Any]]:
        """
        提取表格
        
        Args:
            table_elements: 表格元素列表
            
        Returns:
            结构化表格数据列表
        """
        extracted_tables = []
        
        for table_element in table_elements:
            if table_element.type != ElementType.TABLE:
                continue
            
            try:
                # 获取表格数据
                table_data = table_element.content.get('data', [])
                
                if not table_data:
                    logger.warning("空表格，跳过")
                    continue
                
                # 结构化处理
                structured_table = self._structure_table(table_data, table_element)
                extracted_tables.append(structured_table)
                
            except Exception as e:
                logger.warning(f"提取表格失败: {str(e)}")
                continue
        
        logger.info(f"提取了 {len(extracted_tables)} 个表格")
        return extracted_tables
    
    def _structure_table(
        self,
        table_data: List[List[str]],
        table_element: PDFElement
    ) -> Dict[str, Any]:
        """
        结构化表格数据
        
        Args:
            table_data: 原始表格数据
            table_element: 表格元素
            
        Returns:
            结构化的表格数据
        """
        # 检查是否有表头
        has_header = table_element.metadata.get('has_header', False)
        
        # 处理表头
        headers = []
        data_rows = []
        
        if has_header and len(table_data) > 0:
            headers = table_data[0]
            data_rows = table_data[1:]
        else:
            # 如果没有表头，使用列索引作为表头
            if table_data:
                headers = [f"Column_{i}" for i in range(len(table_data[0]))]
                data_rows = table_data
        
        # 转换为字典列表
        rows = []
        for row in data_rows:
            row_dict = {}
            for i, cell in enumerate(row):
                if i < len(headers):
                    header = headers[i]
                    # 清理单元格内容
                    cell_value = self._clean_cell_value(cell)
                    row_dict[header] = cell_value
            rows.append(row_dict)
        
        # 构建结构化结果
        structured_table = {
            'headers': headers,
            'rows': rows,
            'row_count': len(rows),
            'col_count': len(headers),
            'has_header': has_header,
            'page': table_element.page,
            'bbox': table_element.bbox,
            'confidence': table_element.confidence,
            'metadata': table_element.metadata,
        }
        
        return structured_table
    
    def _clean_cell_value(self, cell: Any) -> Any:
        """
        清理单元格值
        
        Args:
            cell: 单元格值
            
        Returns:
            清理后的值
        """
        if cell is None:
            return ""
        
        # 转换为字符串
        cell_str = str(cell).strip()
        
        # 尝试转换为数字
        try:
            # 尝试整数
            if '.' not in cell_str:
                return int(cell_str)
            # 尝试浮点数
            return float(cell_str)
        except ValueError:
            pass
        
        # 尝试转换为布尔值
        if cell_str.lower() in ['true', 'yes', '是', '对']:
            return True
        elif cell_str.lower() in ['false', 'no', '否', '错']:
            return False
        
        return cell_str
    
    def to_csv(
        self,
        structured_table: Dict[str, Any],
        output_path: Optional[Path] = None
    ) -> Optional[str]:
        """
        将表格导出为 CSV
        
        Args:
            structured_table: 结构化表格数据
            output_path: 输出文件路径
            
        Returns:
            CSV 内容或文件路径
        """
        try:
            import csv
            import io
            
            headers = structured_table['headers']
            rows = structured_table['rows']
            
            # 创建 CSV 内容
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
            
            csv_content = output.getvalue()
            
            # 如果指定了输出路径，保存到文件
            if output_path:
                with open(output_path, 'w', encoding='utf-8', newline='') as f:
                    f.write(csv_content)
                logger.info(f"CSV 导出成功: {output_path}")
                return str(output_path)
            
            return csv_content
            
        except Exception as e:
            logger.error(f"CSV 导出失败: {str(e)}")
            return None
    
    def to_markdown(self, structured_table: Dict[str, Any]) -> str:
        """
        将表格转换为 Markdown 格式
        
        Args:
            structured_table: 结构化表格数据
            
        Returns:
            Markdown 格式的表格
        """
        headers = structured_table['headers']
        rows = structured_table['rows']
        
        # 构建 Markdown 表格
        lines = []
        
        # 表头
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        
        # 数据行
        for row in rows:
            row_values = [str(row.get(header, "")) for header in headers]
            lines.append("| " + " | ".join(row_values) + " |")
        
        return "\n".join(lines)

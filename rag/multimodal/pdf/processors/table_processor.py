"""
表格处理器

处理和优化表格内容。
"""

import logging
from typing import Dict, Any, List, Optional

from ..models import PDFElement, ElementType

logger = logging.getLogger(__name__)


class TableProcessor:
    """
    表格处理器
    
    处理和优化表格内容，包括数据清洗、结构优化等。
    """
    
    def __init__(self):
        """初始化表格处理器"""
        logger.info("TableProcessor 初始化完成")
    
    def process(
        self,
        table_element: PDFElement
    ) -> Dict[str, Any]:
        """
        处理表格元素
        
        Args:
            table_element: 表格元素
            
        Returns:
            处理结果
        """
        if table_element.type != ElementType.TABLE:
            return {'error': '不是表格元素'}
        
        table_data = table_element.content.get('data', [])
        
        try:
            # 清洗数据
            cleaned_data = self._clean_data(table_data)
            
            # 检测表头
            has_header = self._detect_header(cleaned_data)
            
            # 分离表头和数据
            headers, rows = self._separate_header_and_data(cleaned_data, has_header)
            
            # 优化结构
            optimized_rows = self._optimize_rows(rows, headers)
            
            result = {
                'headers': headers,
                'rows': optimized_rows,
                'row_count': len(optimized_rows),
                'col_count': len(headers),
                'has_header': has_header,
                'page': table_element.page,
                'bbox': table_element.bbox,
                'confidence': table_element.confidence,
                'metadata': table_element.metadata,
            }
            
            logger.info(f"表格处理完成: {len(optimized_rows)} 行 x {len(headers)} 列")
            return result
            
        except Exception as e:
            logger.error(f"表格处理失败: {str(e)}")
            return {
                'data': table_data,
                'error': str(e),
            }
    
    def process_batch(
        self,
        table_elements: List[PDFElement]
    ) -> List[Dict[str, Any]]:
        """
        批量处理表格
        
        Args:
            table_elements: 表格元素列表
            
        Returns:
            处理结果列表
        """
        results = []
        
        for element in table_elements:
            result = self.process(element)
            results.append(result)
        
        logger.info(f"批量处理完成: {len(results)} 个表格")
        return results
    
    def _clean_data(self, data: List[List[Any]]) -> List[List[Any]]:
        """
        清洗表格数据
        
        Args:
            data: 原始数据
            
        Returns:
            清洗后的数据
        """
        cleaned = []
        
        for row in data:
            cleaned_row = []
            for cell in row:
                # 清洗单元格
                cleaned_cell = self._clean_cell(cell)
                cleaned_row.append(cleaned_cell)
            cleaned.append(cleaned_row)
        
        # 删除全空行
        cleaned = [row for row in cleaned if any(cell != '' for cell in row)]
        
        return cleaned
    
    def _clean_cell(self, cell: Any) -> Any:
        """
        清洗单元格
        
        Args:
            cell: 原始单元格
            
        Returns:
            清洗后的单元格
        """
        if cell is None:
            return ''
        
        # 转换为字符串
        cell_str = str(cell).strip()
        
        # 删除多余空格
        cell_str = ' '.join(cell_str.split())
        
        return cell_str
    
    def _detect_header(self, data: List[List[Any]]) -> bool:
        """
        检测是否有表头
        
        Args:
            data: 表格数据
            
        Returns:
            是否有表头
        """
        if len(data) < 2:
            return False
        
        first_row = data[0]
        second_row = data[1]
        
        # 统计第一行中看起来像表头的列数
        header_like_count = 0
        total_cols = min(len(first_row), len(second_row))
        
        for i in range(total_cols):
            first_cell = first_row[i]
            second_cell = second_row[i]
            
            # 转换为字符串进行比较
            first_str = str(first_cell).strip()
            second_str = str(second_cell).strip()
            
            # 检查是否是数字
            first_is_number = self._is_number(first_str)
            second_is_number = self._is_number(second_str)
            
            # 如果第一行是文本，第二行是数字，很可能是表头
            if not first_is_number and second_is_number:
                header_like_count += 1
            
            # 如果第一行是短文本，第二行是长文本，可能是表头
            elif not first_is_number and not second_is_number:
                if len(first_str) < len(second_str) * 0.7:
                    header_like_count += 1
            
            # 如果第一行包含常见的表头关键词
            if any(keyword in first_str.lower() for keyword in ['name', 'age', 'city', 'id', 'type', 'value', 'description', '日期', '名称', '类型', '数量']):
                header_like_count += 1
        
        # 如果超过一半的列看起来像表头，则认为有表头
        return header_like_count > total_cols / 2
    
    def _is_number(self, text: str) -> bool:
        """检查文本是否是数字"""
        try:
            float(text)
            return True
        except ValueError:
            return False
    
    def _separate_header_and_data(
        self,
        data: List[List[Any]],
        has_header: bool
    ) -> tuple:
        """
        分离表头和数据
        
        Args:
            data: 表格数据
            has_header: 是否有表头
            
        Returns:
            (headers, rows)
        """
        if has_header and len(data) > 0:
            headers = data[0]
            rows = data[1:]
        else:
            # 使用列索引作为表头
            if data:
                headers = [f"Column_{i}" for i in range(len(data[0]))]
            else:
                headers = []
            rows = data
        
        return headers, rows
    
    def _optimize_rows(
        self,
        rows: List[List[Any]],
        headers: List[Any]
    ) -> List[Dict[str, Any]]:
        """
        优化行数据结构
        
        Args:
            rows: 原始行数据
            headers: 表头
            
        Returns:
            优化后的行数据
        """
        optimized = []
        
        for row in rows:
            row_dict = {}
            for i, cell in enumerate(row):
                if i < len(headers):
                    header = headers[i]
                    # 转换数据类型
                    converted_cell = self._convert_cell_type(cell)
                    row_dict[header] = converted_cell
            optimized.append(row_dict)
        
        return optimized
    
    def _convert_cell_type(self, cell: Any) -> Any:
        """
        转换单元格数据类型
        
        Args:
            cell: 原始单元格
            
        Returns:
            转换后的单元格
        """
        if cell is None or cell == '':
            return None
        
        cell_str = str(cell).strip()
        
        # 尝试转换为整数
        try:
            return int(cell_str)
        except ValueError:
            pass
        
        # 尝试转换为浮点数
        try:
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
        processed_table: Dict[str, Any],
        output_path: Optional[str] = None
    ) -> Optional[str]:
        """
        导出为 CSV
        
        Args:
            processed_table: 处理后的表格数据
            output_path: 输出路径
            
        Returns:
            CSV 内容或文件路径
        """
        try:
            import csv
            import io
            
            headers = processed_table['headers']
            rows = processed_table['rows']
            
            # 创建 CSV 内容
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
            
            csv_content = output.getvalue()
            
            if output_path:
                with open(output_path, 'w', encoding='utf-8', newline='') as f:
                    f.write(csv_content)
                return output_path
            
            return csv_content
            
        except Exception as e:
            logger.error(f"CSV 导出失败: {str(e)}")
            return None
    
    def to_markdown(self, processed_table: Dict[str, Any]) -> str:
        """
        导出为 Markdown
        
        Args:
            processed_table: 处理后的表格数据
            
        Returns:
            Markdown 格式
        """
        headers = processed_table['headers']
        rows = processed_table['rows']
        
        lines = []
        
        # 表头
        lines.append("| " + " | ".join(str(h) for h in headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        
        # 数据行
        for row in rows:
            row_values = [str(row.get(h, "")) for h in headers]
            lines.append("| " + " | ".join(row_values) + " |")
        
        return "\n".join(lines)

#!/usr/bin/env python3
"""
智慧教育系统主模块
包含Gradio界面和数据管理函数
"""

from data_manager import (
    load_subs,
    save_subs,
    get_files,
    save_files,
    create_subj,
    delete_subj,
    add_file,
    delete_file,
    update_subj_desc,
    rename_subj,
    update_file_info,
    read_file_content,
    get_all_content,
    SUBJECTS_FILE,
    FILES_FILE,
    DATA_DIR
)

from image_utils import (
    encode_image_to_base64,
    encode_image_to_base64_from_pil
)

from mindmap_service import generate_mindmap


def main():
    """
    主函数，Gradio界面入口
    """
    print("智慧教育系统启动中...")
    print("当前学科数量:", len(load_subs()))
    print("当前文件数量:", len(get_files()))
    
    # 这里可以启动Gradio界面
    # import gradio as gr
    # demo = gr.Interface(...)
    # demo.launch()


if __name__ == "__main__":
    main()
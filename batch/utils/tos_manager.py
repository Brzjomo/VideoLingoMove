#!/usr/bin/env python3
"""
批量处理TOS管理器
用于在批量处理模式下管理TOS上传和清理
"""

import os
import sys
from typing import List, Dict, Optional
from rich.console import Console

# 添加项目根目录到系统路径
current_dir = os.path.dirname(os.path.abspath(__file__))  # utils目录
batch_dir = os.path.dirname(current_dir)  # batch目录
root_dir = os.path.dirname(batch_dir)  # 项目根目录
sys.path.append(root_dir)

from core.all_whisper_methods.tos_service import TOSService

console = Console()

class BatchTOSManager:
    """批量处理TOS管理器"""

    def __init__(self):
        """初始化TOS管理器"""
        self.tos_service = None
        self.enabled = False
        self.auto_cleanup = False

        # 初始化TOS服务
        self._init_tos_service()

        # 批量处理中上传的文件跟踪
        self.batch_uploaded_files = []

    def _init_tos_service(self):
        """初始化TOS服务"""
        try:
            self.tos_service = TOSService()
            self.enabled = self.tos_service.is_enabled()
            self.auto_cleanup = self.tos_service.auto_cleanup

            if self.enabled:
                console.print("[green]✅ TOS服务已启用[/green]")
                if self.auto_cleanup:
                    console.print("[cyan]ℹ️ TOS自动清理已启用，文件将在ASR处理完成后删除[/cyan]")
            else:
                console.print("[yellow]⚠️ TOS服务未启用，将使用本地文件处理[/yellow]")

        except Exception as e:
            console.print(f"[red]❌ TOS服务初始化失败: {str(e)}[/red]")
            self.enabled = False
            self.tos_service = None

    def is_enabled(self) -> bool:
        """检查TOS是否启用"""
        return self.enabled and self.tos_service is not None

    def upload_file_for_batch(self, local_file_path: str, video_name: str) -> Optional[str]:
        """
        为批量处理上传文件到TOS

        Args:
            local_file_path: 本地文件路径
            video_name: 视频名称（用于生成对象键）

        Returns:
            str: 公共URL，如果上传失败返回None
        """
        if not self.is_enabled():
            console.print(f"[yellow]⚠️ TOS未启用，使用本地文件: {local_file_path}[/yellow]")
            return f"file://{os.path.abspath(local_file_path)}"

        try:
            # 生成有意义的对象键，包含视频名称
            import time
            import uuid
            timestamp = int(time.time())
            file_ext = os.path.splitext(local_file_path)[1]

            # 清理视频名称，移除特殊字符
            safe_video_name = "".join(c for c in video_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_video_name = safe_video_name.replace(' ', '_')[:50]  # 限制长度

            object_key = f"batch/{safe_video_name}_{timestamp}_{uuid.uuid4().hex[:8]}{file_ext}"

            # 上传文件
            success, object_key, public_url = self.tos_service.upload_file(local_file_path, object_key)

            if success:
                # 记录批量处理中的上传文件
                self.batch_uploaded_files.append({
                    'object_key': object_key,
                    'local_path': local_file_path,
                    'video_name': video_name,
                    'public_url': public_url
                })
                console.print(f"[green]✅ 批量处理文件上传成功: {video_name}[/green]")
                console.print(f"[cyan]  对象键: {object_key}[/cyan]")
                return public_url
            else:
                console.print(f"[yellow]⚠️ 批量处理文件上传失败，使用本地文件: {video_name}[/yellow]")
                return f"file://{os.path.abspath(local_file_path)}"

        except Exception as e:
            console.print(f"[red]❌ 批量处理文件上传异常: {video_name} - {str(e)}[/red]")
            console.print(f"[yellow]⚠️ 回退到本地文件处理[/yellow]")
            return f"file://{os.path.abspath(local_file_path)}"

    def cleanup_after_video_processed(self, video_name: str) -> bool:
        """
        视频处理完成后清理TOS文件

        Args:
            video_name: 视频名称

        Returns:
            bool: 是否清理成功
        """
        if not self.is_enabled() or not self.auto_cleanup:
            return False

        # 查找该视频对应的TOS文件
        files_to_cleanup = []
        for file_info in self.batch_uploaded_files[:]:  # 使用副本遍历
            if file_info['video_name'] == video_name:
                files_to_cleanup.append(file_info)

        if not files_to_cleanup:
            console.print(f"[yellow]⚠️ 未找到视频对应的TOS文件: {video_name}[/yellow]")
            return False

        success_count = 0
        for file_info in files_to_cleanup:
            try:
                success = self.tos_service.cleanup_uploaded_file(file_info['object_key'])
                if success:
                    # 从批量跟踪列表中移除
                    if file_info in self.batch_uploaded_files:
                        self.batch_uploaded_files.remove(file_info)
                    success_count += 1
                    console.print(f"[green]✅ 批量处理完成，已删除TOS文件: {video_name} - {file_info['object_key']}[/green]")
                else:
                    console.print(f"[yellow]⚠️ 批量处理完成，但删除TOS文件失败: {video_name} - {file_info['object_key']}[/yellow]")
            except Exception as e:
                console.print(f"[yellow]⚠️ 清理TOS文件时出错: {video_name} - {str(e)}[/yellow]")

        return success_count > 0

    def cleanup_all_batch_files(self):
        """清理所有批量处理中的TOS文件"""
        if not self.is_enabled():
            return

        console.print("[cyan]🧹 清理批量处理中的所有TOS文件...[/cyan]")

        files_to_delete = self.batch_uploaded_files.copy()
        deleted_count = 0

        for file_info in files_to_delete:
            try:
                success = self.tos_service.cleanup_uploaded_file(file_info['object_key'])
                if success:
                    if file_info in self.batch_uploaded_files:
                        self.batch_uploaded_files.remove(file_info)
                    deleted_count += 1
            except Exception as e:
                console.print(f"[yellow]⚠️ 清理TOS文件时出错: {file_info['video_name']} - {str(e)}[/yellow]")

        console.print(f"[green]✅ 已清理 {deleted_count} 个批量处理TOS文件[/green]")

    def get_batch_upload_info(self) -> Dict:
        """获取批量处理上传信息"""
        return {
            'enabled': self.enabled,
            'auto_cleanup': self.auto_cleanup,
            'uploaded_count': len(self.batch_uploaded_files),
            'uploaded_files': self.batch_uploaded_files.copy()
        }


# 单例实例
_batch_tos_manager = None

def get_batch_tos_manager() -> BatchTOSManager:
    """获取批量处理TOS管理器单例"""
    global _batch_tos_manager
    if _batch_tos_manager is None:
        _batch_tos_manager = BatchTOSManager()
    return _batch_tos_manager


if __name__ == "__main__":
    # 测试代码
    manager = BatchTOSManager()
    print(f"TOS启用: {manager.is_enabled()}")
    print(f"自动清理: {manager.auto_cleanup}")

    if manager.is_enabled():
        info = manager.get_batch_upload_info()
        print(f"批量上传信息: {info}")
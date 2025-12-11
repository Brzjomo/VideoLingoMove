"""
TOS (火山引擎对象存储) 服务类
用于将音频文件上传到TOS并获取可公开访问的URL
"""

import os
import sys
import uuid
import time
from typing import Optional, Tuple
from rich import print as rprint

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core.config_utils import load_key


class TOSService:
    """TOS服务类"""

    def __init__(self):
        """初始化TOS配置"""
        # 首先尝试从config.yaml获取，如果为空则从环境变量获取
        self.access_key = load_key("tos.access_key")
        self.secret_key = load_key("tos.secret_key")

        # 如果config.yaml中为空，尝试从环境变量获取
        if not self.access_key:
            self.access_key = os.getenv('TOS_ACCESS_KEY')
        if not self.secret_key:
            self.secret_key = os.getenv('TOS_SECRET_KEY')

        self.endpoint = load_key("tos.endpoint")
        self.region = load_key("tos.region")
        self.bucket_name = load_key("tos.bucket_name")
        self.enabled = load_key("tos.enabled")
        self.public_url_prefix = load_key("tos.public_url_prefix")
        self.auto_cleanup = load_key("tos.auto_cleanup")

        # 初始化TOS客户端
        self.client = None
        self._init_tos_client()

        # 跟踪已上传的文件
        self.uploaded_files = []

    def _init_tos_client(self):
        """初始化TOS客户端"""
        if not self.enabled:
            rprint("[yellow]⚠️ TOS上传功能已禁用[/yellow]")
            return

        # 检查AK/SK来源
        ak_source = "环境变量" if os.getenv('TOS_ACCESS_KEY') and not load_key("tos.access_key") else "配置文件"
        sk_source = "环境变量" if os.getenv('TOS_SECRET_KEY') and not load_key("tos.secret_key") else "配置文件"

        if not self.access_key or not self.secret_key:
            rprint("[yellow]⚠️ TOS Access Key或Secret Key未配置[/yellow]")
            rprint("[yellow]检查来源: config.yaml或环境变量TOS_ACCESS_KEY/TOS_SECRET_KEY[/yellow]")
            rprint("[yellow]将使用file:// URL（可能不被火山引擎ASR接受）[/yellow]")
            self.enabled = False
            return

        try:
            import tos
            self.client = tos.TosClientV2(
                ak=self.access_key,
                sk=self.secret_key,
                endpoint=self.endpoint,
                region=self.region,
                connection_time=30,
                socket_timeout=60,
                max_retry_count=3
            )
            rprint(f"[green]✅ TOS客户端初始化成功[/green]")
            rprint(f"[cyan]Bucket: {self.bucket_name}[/cyan]")
            rprint(f"[cyan]Endpoint: {self.endpoint}[/cyan]")
            rprint(f"[cyan]AK来源: {ak_source}, SK来源: {sk_source}[/cyan]")

            # 设置公共URL前缀
            if not self.public_url_prefix:
                self.public_url_prefix = f"https://{self.bucket_name}.{self.endpoint}"

        except ImportError:
            rprint("[red]❌ 未安装tos库，请运行: pip install tos[/red]")
            self.enabled = False
        except Exception as e:
            rprint(f"[red]❌ TOS客户端初始化失败: {str(e)}[/red]")
            self.enabled = False

    def upload_file(self, local_file_path: str, object_key: Optional[str] = None) -> Tuple[bool, str, str]:
        """
        上传文件到TOS

        Args:
            local_file_path: 本地文件路径
            object_key: TOS中的对象键（可选，自动生成）

        Returns:
            tuple: (成功与否, 对象键, 公共URL)
        """
        if not self.enabled or not self.client:
            rprint("[yellow]⚠️ TOS未启用，返回本地文件路径[/yellow]")
            file_url = f"file://{os.path.abspath(local_file_path)}"
            return False, local_file_path, file_url

        # 检查文件是否存在
        if not os.path.exists(local_file_path):
            raise FileNotFoundError(f"文件不存在: {local_file_path}")

        # 生成对象键
        if not object_key:
            # 使用UUID和时间戳生成唯一键
            timestamp = int(time.time())
            file_ext = os.path.splitext(local_file_path)[1]
            object_key = f"asr-audio/{timestamp}_{uuid.uuid4().hex}{file_ext}"

        try:
            rprint(f"[cyan]📤 上传文件到TOS: {local_file_path} -> {object_key}[/cyan]")

            # 上传文件
            with open(local_file_path, 'rb') as f:
                self.client.put_object(
                    bucket=self.bucket_name,
                    key=object_key,
                    content=f
                )

            # 生成公共URL
            public_url = f"{self.public_url_prefix}/{object_key}"

            rprint(f"[green]✅ 文件上传成功[/green]")
            rprint(f"[cyan]对象键: {object_key}[/cyan]")
            rprint(f"[cyan]公共URL: {public_url}[/cyan]")

            # 记录已上传的文件
            self.uploaded_files.append({
                'object_key': object_key,
                'local_path': local_file_path,
                'upload_time': time.time(),
                'public_url': public_url
            })

            return True, object_key, public_url

        except Exception as e:
            rprint(f"[red]❌ 文件上传失败: {str(e)}[/red]")
            # 回退到本地文件路径
            file_url = f"file://{os.path.abspath(local_file_path)}"
            return False, local_file_path, file_url

    def delete_file(self, object_key: str) -> bool:
        """
        从TOS删除文件

        Args:
            object_key: TOS中的对象键

        Returns:
            bool: 是否删除成功
        """
        if not self.enabled or not self.client:
            rprint("[yellow]⚠️ TOS未启用，跳过删除[/yellow]")
            return False

        try:
            self.client.delete_object(
                bucket=self.bucket_name,
                key=object_key
            )
            rprint(f"[green]✅ 文件删除成功: {object_key}[/green]")
            return True
        except Exception as e:
            rprint(f"[red]❌ 文件删除失败: {object_key} - {str(e)}[/red]")
            return False

    def cleanup_old_files(self):
        """清理旧文件（现在只检查自动清理是否启用）"""
        if not self.enabled or not self.client or not self.auto_cleanup:
            return

        # 现在不再根据保留时间清理，所以这个方法只检查配置
        rprint("[cyan]ℹ️ 自动清理已启用，文件将在ASR任务返回成功后删除[/cyan]")

    def cleanup_all_files(self):
        """清理所有已上传的文件"""
        if not self.enabled or not self.client:
            return

        rprint("[cyan]🧹 清理所有TOS文件...[/cyan]")
        files_to_delete = self.uploaded_files.copy()
        deleted_count = 0

        for file_info in files_to_delete:
            if self.delete_file(file_info['object_key']):
                if file_info in self.uploaded_files:
                    self.uploaded_files.remove(file_info)
                deleted_count += 1

        rprint(f"[green]✅ 已清理 {deleted_count} 个文件[/green]")

    def cleanup_uploaded_file(self, object_key: str) -> bool:
        """
        清理指定已上传的文件（ASR任务提交成功后调用）

        Args:
            object_key: TOS中的对象键

        Returns:
            bool: 是否删除成功
        """
        if not self.enabled or not self.client:
            return False

        # 查找对应的文件信息
        file_info_to_delete = None
        for file_info in self.uploaded_files:
            if file_info['object_key'] == object_key:
                file_info_to_delete = file_info
                break

        if not file_info_to_delete:
            rprint(f"[yellow]⚠️ 未找到要删除的文件: {object_key}[/yellow]")
            return False

        # 删除文件
        success = self.delete_file(object_key)
        if success:
            # 从已上传文件列表中移除
            self.uploaded_files.remove(file_info_to_delete)
            rprint(f"[green]✅ ASR任务提交成功，已删除TOS文件: {object_key}[/green]")
        else:
            rprint(f"[yellow]⚠️ ASR任务提交成功，但删除TOS文件失败: {object_key}[/yellow]")

        return success

    def get_public_url(self, object_key: str) -> str:
        """
        获取文件的公共URL

        Args:
            object_key: TOS中的对象键

        Returns:
            str: 公共URL
        """
        return f"{self.public_url_prefix}/{object_key}"

    def is_enabled(self) -> bool:
        """检查TOS是否启用"""
        return self.enabled and self.client is not None


def test_tos_service():
    """测试TOS服务"""
    try:
        # 加载配置
        from core.config_utils import load_key
        access_key = load_key("tos.access_key")
        secret_key = load_key("tos.secret_key")

        if not access_key or not secret_key:
            rprint("[yellow]⚠️ TOS配置不完整，跳过测试[/yellow]")
            rprint("[yellow]请在config.yaml中配置tos.access_key和tos.secret_key[/yellow]")
            return

        # 创建测试文件
        import tempfile
        test_content = b"Test content for TOS upload"
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.txt', delete=False) as f:
            f.write(test_content)
            test_file = f.name

        rprint("[cyan]开始测试TOS服务...[/cyan]")

        # 测试TOS服务
        tos_service = TOSService()

        if not tos_service.is_enabled():
            rprint("[red]❌ TOS服务未启用[/red]")
            return

        # 上传文件
        success, object_key, public_url = tos_service.upload_file(test_file)
        if success:
            rprint(f"[green]✅ 上传测试成功[/green]")
            rprint(f"[cyan]对象键: {object_key}[/cyan]")
            rprint(f"[cyan]公共URL: {public_url}[/cyan]")

            # 测试删除
            tos_service.delete_file(object_key)
            rprint("[green]✅ 删除测试成功[/green]")
        else:
            rprint("[red]❌ 上传测试失败[/red]")

        # 清理测试文件
        if os.path.exists(test_file):
            os.remove(test_file)

        rprint("[green]✅ TOS服务测试完成[/green]")

    except Exception as e:
        rprint(f"[red]❌ 测试失败: {str(e)}[/red]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_tos_service()
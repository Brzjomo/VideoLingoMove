"""
TOS (火山引擎对象存储) 服务类
用于将音频文件上传到TOS并获取可公开访问的URL

本模块是**唯一的 TOS 实现**。历史上 batch/utils/tos_manager.py 里另有一个
`BatchTOSManager`，与这里功能重叠但从未接线；重构 Round 2 已把它合并进来并删除该文件：
  - 单例入口：get_tos_service()
  - 批量场景的对象键命名：upload_file(..., name_hint=<视频名>)
  - 按视频清理：cleanup_by_name(video_name)
"""

import os
import sys
import re
import uuid
import time
from typing import Optional, Tuple, List, Dict, Any
from rich import print as rprint

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core.config_utils import load_key

# 对象键前缀。批量模式下会追加 batch/ 子前缀，便于在控制台按来源筛选。
DEFAULT_PREFIX = 'asr-audio'


class TOSService:
    """TOS服务类"""

    def __init__(self):
        """初始化TOS配置"""
        # 首先尝试从config.yaml获取（环境变量 VIDEOLINGO_TOS_* 会由 load_key 优先覆盖）
        self.access_key = load_key("tos.access_key")
        self.secret_key = load_key("tos.secret_key")

        # 向后兼容：如果仍为空，尝试旧的环境变量名
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

        # 跟踪已上传的文件。每项：
        # {object_key, local_path, public_url, upload_time, name_hint}
        self.uploaded_files: List[Dict[str, Any]] = []

        # 文件缓存：避免重复上传同一个文件
        # key: 本地文件路径的绝对路径
        # value: (object_key, public_url, upload_time)
        self.file_cache: Dict[str, Tuple[str, str, float]] = {}

    def _init_tos_client(self):
        """初始化TOS客户端"""
        if not self.enabled:
            rprint("[yellow]⚠️ TOS上传功能已禁用[/yellow]")
            return

        # 检查AK/SK来源（用于日志提示）
        ak_from_env = bool(os.getenv('VIDEOLINGO_TOS_ACCESS_KEY') or os.getenv('TOS_ACCESS_KEY'))
        sk_from_env = bool(os.getenv('VIDEOLINGO_TOS_SECRET_KEY') or os.getenv('TOS_SECRET_KEY'))
        ak_source = "环境变量" if ak_from_env else "配置文件"
        sk_source = "环境变量" if sk_from_env else "配置文件"

        if not self.access_key or not self.secret_key:
            rprint("[yellow]⚠️ TOS Access Key或Secret Key未配置[/yellow]")
            rprint("[yellow]检查来源: config.yaml 的 tos.access_key / tos.secret_key，"
                   "或环境变量 VIDEOLINGO_TOS_ACCESS_KEY / VIDEOLINGO_TOS_SECRET_KEY[/yellow]")
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
            rprint("[green]✅ TOS客户端初始化成功[/green]")
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

    @staticmethod
    def _safe_name(name: str, max_length: int = 50) -> str:
        """把视频名清洗成可安全放进对象键的形式（合并自原 BatchTOSManager）。"""
        safe = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff._-]+', '_', str(name or '')).strip('_')
        return safe[:max_length] or 'file'

    def _build_object_key(self, local_file_path: str, prefix: Optional[str] = None,
                          name_hint: Optional[str] = None) -> str:
        """构造对象键。

        Args:
            local_file_path: 用于取扩展名
            prefix: 对象键前缀；默认 DEFAULT_PREFIX（asr-audio）
            name_hint: 语义化名称（通常是视频名）。给了它就生成
                `<prefix>/<safe_name>_<timestamp>_<uuid8><ext>`，便于人工在控制台识别；
                否则生成 `<prefix>/<timestamp>_<uuid><ext>`
        """
        base_prefix = (prefix if prefix is not None else DEFAULT_PREFIX).strip('/')
        timestamp = int(time.time())
        file_ext = os.path.splitext(local_file_path)[1]
        if name_hint:
            safe = self._safe_name(name_hint)
            return f"{base_prefix}/{safe}_{timestamp}_{uuid.uuid4().hex[:8]}{file_ext}"
        return f"{base_prefix}/{timestamp}_{uuid.uuid4().hex}{file_ext}"

    def upload_file(self, local_file_path: str, object_key: Optional[str] = None,
                    prefix: Optional[str] = None,
                    name_hint: Optional[str] = None) -> Tuple[bool, str, str]:
        """
        上传文件到TOS

        Args:
            local_file_path: 本地文件路径
            object_key: TOS中的对象键（可选，自动生成）
            prefix: 对象键前缀（仅在自动生成 object_key 时生效）
            name_hint: 语义化名称，便于在 TOS 控制台识别（仅自动生成时生效）

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

        # 获取文件的绝对路径
        abs_path = os.path.abspath(local_file_path)

        # 检查文件是否已经在缓存中（避免重复上传）
        if abs_path in self.file_cache:
            cached_key, cached_url, cached_time = self.file_cache[abs_path]
            rprint("[cyan]📁 文件已在缓存中，使用已上传的TOS文件[/cyan]")
            rprint(f"[cyan]本地文件: {abs_path}[/cyan]")
            rprint(f"[cyan]TOS对象键: {cached_key}[/cyan]")
            rprint(f"[cyan]公共URL: {cached_url}[/cyan]")
            rprint(f"[cyan]上传时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(cached_time))}[/cyan]")
            return True, cached_key, cached_url

        # 生成对象键
        if not object_key:
            object_key = self._build_object_key(local_file_path, prefix=prefix, name_hint=name_hint)

        try:
            rprint(f"[cyan]📤 上传文件到TOS: {local_file_path} -> {object_key}[/cyan]")

            with open(local_file_path, 'rb') as f:
                self.client.put_object(
                    bucket=self.bucket_name,
                    key=object_key,
                    content=f
                )

            public_url = f"{self.public_url_prefix}/{object_key}"

            rprint("[green]✅ 文件上传成功[/green]")
            rprint(f"[cyan]对象键: {object_key}[/cyan]")
            rprint(f"[cyan]公共URL: {public_url}[/cyan]")

            upload_time = time.time()
            self.uploaded_files.append({
                'object_key': object_key,
                'local_path': local_file_path,
                'public_url': public_url,
                'upload_time': upload_time,
                'name_hint': name_hint,
            })
            self.file_cache[abs_path] = (object_key, public_url, upload_time)

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

        # 清空文件缓存
        self.file_cache.clear()
        rprint("[cyan]🗑️ 已清空文件缓存[/cyan]")

        rprint(f"[green]✅ 已清理 {deleted_count} 个文件[/green]")

    def cleanup_uploaded_file(self, object_key: str) -> bool:
        """
        清理指定已上传的文件（ASR返回结果后调用）

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

        success = self.delete_file(object_key)
        if success:
            self.uploaded_files.remove(file_info_to_delete)

            # 从文件缓存中移除
            local_path = file_info_to_delete.get('local_path')
            if local_path:
                abs_path = os.path.abspath(local_path)
                if abs_path in self.file_cache:
                    del self.file_cache[abs_path]
                    rprint(f"[cyan]🗑️ 已从文件缓存中移除: {abs_path}[/cyan]")

            rprint(f"[green]✅ ASR处理完成，已删除TOS文件: {object_key}[/green]")
        else:
            rprint(f"[yellow]⚠️ ASR处理完成，但删除TOS文件失败: {object_key}[/yellow]")

        return success

    def cleanup_by_name(self, name_hint: str) -> int:
        """
        清理某个 name_hint（通常是视频名）对应的所有已上传文件。
        合并自原 BatchTOSManager.cleanup_after_video_processed()。

        Args:
            name_hint: 上传时传入的 name_hint

        Returns:
            int: 成功删除的文件数
        """
        if not self.is_enabled():
            return 0

        targets = [f for f in self.uploaded_files if f.get('name_hint') == name_hint]
        if not targets:
            rprint(f"[yellow]⚠️ 未找到 {name_hint} 对应的TOS文件[/yellow]")
            return 0

        deleted = 0
        for info in targets:
            if self.cleanup_uploaded_file(info['object_key']):
                deleted += 1

        rprint(f"[green]✅ {name_hint}: 已清理 {deleted}/{len(targets)} 个TOS文件[/green]")
        return deleted

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

    def get_upload_info(self) -> Dict:
        """获取上传信息汇总（合并自原 BatchTOSManager.get_batch_upload_info）。"""
        return {
            'enabled': self.is_enabled(),
            'auto_cleanup': self.auto_cleanup,
            'uploaded_count': len(self.uploaded_files),
            'uploaded_files': self.uploaded_files.copy(),
        }

    def clear_file_cache(self, local_file_path: str = None):
        """
        清理文件缓存

        Args:
            local_file_path: 可选，指定要清理的本地文件路径。如果为None，清理整个缓存。
        """
        if local_file_path:
            abs_path = os.path.abspath(local_file_path)
            if abs_path in self.file_cache:
                del self.file_cache[abs_path]
                rprint(f"[cyan]🗑️ 已从缓存中移除文件: {abs_path}[/cyan]")
            else:
                rprint(f"[yellow]⚠️ 文件不在缓存中: {abs_path}[/yellow]")
        else:
            self.file_cache.clear()
            rprint("[cyan]🗑️ 已清空文件缓存[/cyan]")


# 单例：ASR 分段循环里每段都会新建 VolcanoASR，但底层 TOS 客户端与缓存应当共享，
# 否则"同一文件只上传一次"的缓存会失效、也无法统一清理。
_tos_service: Optional[TOSService] = None


def get_tos_service() -> TOSService:
    """获取全局 TOSService 单例。"""
    global _tos_service
    if _tos_service is None:
        _tos_service = TOSService()
    return _tos_service


def reset_tos_service():
    """丢弃单例（配置变更或测试时使用）。"""
    global _tos_service
    _tos_service = None


def test_tos_service():
    """测试TOS服务"""
    try:
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

        tos_service = TOSService()

        if not tos_service.is_enabled():
            rprint("[red]❌ TOS服务未启用[/red]")
            return

        success, object_key, public_url = tos_service.upload_file(test_file, name_hint="tos_selftest")
        if success:
            rprint("[green]✅ 上传测试成功[/green]")
            rprint(f"[cyan]对象键: {object_key}[/cyan]")
            rprint(f"[cyan]公共URL: {public_url}[/cyan]")

            tos_service.delete_file(object_key)
            rprint("[green]✅ 删除测试成功[/green]")
        else:
            rprint("[red]❌ 上传测试失败[/red]")

        if os.path.exists(test_file):
            os.remove(test_file)

        rprint("[green]✅ TOS服务测试完成[/green]")

    except Exception as e:
        rprint(f"[red]❌ 测试失败: {str(e)}[/red]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_tos_service()

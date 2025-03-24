import os
import subprocess
from typing import List, Tuple
from rich.console import Console
from rich.panel import Panel

console = Console()

class AudioExtractor:
    def __init__(self, input_dir: str, output_dir: str):
        """
        初始化音频提取器
        
        Args:
            input_dir (str): 输入目录路径
            output_dir (str): 输出目录路径
        """
        self.input_dir = os.path.abspath(input_dir)
        self.output_dir = os.path.abspath(output_dir)
        
        # 支持的视频格式
        self.video_extensions = (
            # 常见视频容器格式
            '.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm',
            # 其他常见视频格式
            '.m4v', '.mpg', '.mpeg', '.mp2', '.m2v', '.m4v', '.3gp', '.3g2',
            '.mxf', '.ts', '.mts', '.m2ts', '.vob', '.ogv', '.dv', '.qt',
            '.asf', '.amv', '.rm', '.rmvb', '.ogm', '.divx', '.xvid',
            # 专业视频格式
            '.mxf', '.r3d', '.braw', '.arri', '.dpx',
            # 网络视频格式
            '.f4v', '.m2t', '.m2ts', '.mts', '.yuv',
            # 苹果设备格式
            '.m4v', '.mov',
            # 安全监控格式
            '.h264', '.h265', '.264', '.265',
            # 其他格式（大小写变体）
            '.MP4', '.AVI', '.MOV', '.MKV', '.FLV', '.WMV', '.WEBM',
            '.M4V', '.MPG', '.MPEG', '.VOB', '.TS', '.MTS'
        )
        
        # 确保输出目录存在
        os.makedirs(self.output_dir, exist_ok=True)
        
        # 处理标志
        self.process_subdirs = False
        
    def get_video_files(self, directory: str) -> List[Tuple[str, str]]:
        """
        获取指定目录下的视频文件
        
        Args:
            directory (str): 要扫描的目录
            
        Returns:
            List[Tuple[str, str]]: 包含(视频文件完整路径, 相对路径)的列表
        """
        video_files = []
        for file in os.listdir(directory):
            full_path = os.path.join(directory, file)
            if os.path.isfile(full_path) and file.endswith(self.video_extensions):
                # 计算相对于输入目录的路径
                rel_path = os.path.relpath(full_path, self.input_dir)
                video_files.append((full_path, rel_path))
        return video_files
    
    def extract_audio(self, video_path: str, output_path: str) -> bool:
        """
        从视频中提取音频
        
        Args:
            video_path (str): 视频文件路径
            output_path (str): 输出音频文件路径
            
        Returns:
            bool: 提取是否成功
        """
        try:
            # 确保输出目录存在
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # 使用ffmpeg提取音频
            cmd = [
                'ffmpeg', '-y',  # 覆盖已存在的文件
                '-i', video_path,  # 输入文件
                '-vn',  # 不处理视频
                '-acodec', 'libmp3lame',  # 使用mp3编码器
                '-q:a', '2',  # 音质设置（0-9，2是高质量）
                output_path
            ]
            
            # 执行命令
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8'
            )
            
            return result.returncode == 0
            
        except Exception as e:
            console.print(f"[red]处理文件 {video_path} 时出错: {str(e)}[/red]")
            return False
    
    def process_directory(self) -> Tuple[int, int]:
        """
        处理目录中的所有视频文件
        
        Returns:
            Tuple[int, int]: (成功数量, 失败数量)
        """
        success_count = 0
        failed_count = 0
        
        # 获取所有需要处理的视频文件
        all_videos = []
        
        # 处理主目录
        all_videos.extend(self.get_video_files(self.input_dir))
        
        # 如果启用了子目录处理，递归处理子目录
        if self.process_subdirs:
            for root, _, _ in os.walk(self.input_dir):
                if root != self.input_dir:
                    all_videos.extend(self.get_video_files(root))
        
        # 处理每个视频文件
        total_files = len(all_videos)
        for i, (video_path, rel_path) in enumerate(all_videos, 1):
            # 构建输出路径
            output_filename = os.path.splitext(rel_path)[0] + '.mp3'
            output_path = os.path.join(self.output_dir, output_filename)
            
            # 显示进度
            console.print(f"\n[cyan]处理进度: [{i}/{total_files}] {rel_path}[/cyan]")
            
            # 提取音频
            if self.extract_audio(video_path, output_path):
                success_count += 1
                console.print(f"[green]✓ 成功提取: {rel_path}[/green]")
            else:
                failed_count += 1
                console.print(f"[red]✗ 提取失败: {rel_path}[/red]")
        
        return success_count, failed_count

def main():
    """命令行入口函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='从视频中提取音频')
    parser.add_argument('input_dir', help='输入视频目录')
    parser.add_argument('output_dir', help='输出音频目录')
    parser.add_argument('--recursive', '-r', action='store_true', help='是否处理子目录')
    
    args = parser.parse_args()
    
    # 创建提取器实例
    extractor = AudioExtractor(args.input_dir, args.output_dir)
    extractor.process_subdirs = args.recursive
    
    # 开始处理
    console.print(Panel("开始提取音频...", style="bold cyan"))
    success, failed = extractor.process_directory()
    
    # 显示结果
    console.print(Panel(
        f"处理完成!\n"
        f"成功: {success} 个文件\n"
        f"失败: {failed} 个文件",
        title="处理结果",
        style="bold green" if failed == 0 else "bold yellow"
    ))

if __name__ == "__main__":
    main() 
import os,sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import glob
import re
import subprocess
from core.config_utils import load_key

def sanitize_filename(filename):
    # Remove or replace illegal characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    # Ensure filename doesn't start or end with a dot or space
    filename = filename.strip('. ')
    # Use default name if filename is empty
    return filename if filename else 'video'

# 上传纯音频时，st_components.download_video_section.convert_audio_to_video()
# 会把音频包成一个黑底视频，文件名固定为 black_screen.mp4，再走与视频完全相同的流程。
AUDIO_PLACEHOLDER_NAME = 'black_screen.mp4'

def is_audio_placeholder(video_file: str) -> bool:
    """判断源文件是否由"上传音频"自动转换而来的黑底占位视频。

    这类文件本身就是一个**可播放的成片**（黑底 + 完整音频，时长与音频一致），
    因此下游不应再用 1 秒黑帧去覆盖它——那会丢掉音频。
    """
    return os.path.basename(video_file).lower() == AUDIO_PLACEHOLDER_NAME

def download_video_ytdlp(url, save_path='output', resolution='1080', cutoff_time=None):
    allowed_resolutions = ['360', '1080', 'best']
    if resolution not in allowed_resolutions:
        resolution = '360'
    
    os.makedirs(save_path, exist_ok=True)
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best' if resolution == 'best' else f'bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]',
        'outtmpl': f'{save_path}/%(title)s.%(ext)s',
        'noplaylist': True,
        'writethumbnail': True,
        'postprocessors': [{
            'key': 'FFmpegThumbnailsConvertor',
            'format': 'jpg',
        }],
    }

    # Update yt-dlp to avoid download failure due to API changes
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"])
    except subprocess.CalledProcessError as e:
        print(f"Warning: Failed to update yt-dlp: {e}")
    # Reload yt-dlp
    if 'yt_dlp' in sys.modules:
        del sys.modules['yt_dlp']
    from yt_dlp import YoutubeDL
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    
    # Check and rename files after download
    for file in os.listdir(save_path):
        if os.path.isfile(os.path.join(save_path, file)):
            filename, ext = os.path.splitext(file)
            new_filename = sanitize_filename(filename)
            if new_filename != filename:
                os.rename(os.path.join(save_path, file), os.path.join(save_path, new_filename + ext))

    # cut the video to make demo
    if cutoff_time:
        print(f"Cutoff time: {cutoff_time}, Now checking video duration...")
        video_file = find_video_files(save_path)
        
        # Use librosa to get video duration
        import librosa
        duration = librosa.get_duration(filename=video_file)
        
        if duration > cutoff_time:
            print(f"Video duration ({duration:.2f}s) is longer than cutoff time. Cutting the video...")
            file_name, file_extension = os.path.splitext(video_file)
            trimmed_file = f"{file_name}_trim{file_extension}"
            ffmpeg_cmd = ['ffmpeg', '-i', video_file, '-t', str(cutoff_time), '-c', 'copy', trimmed_file]
            print("🎬 Start cutting video...")
            process = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, encoding='utf-8')
            for line in process.stdout:
                print(line, end='')
            process.wait()
            print(f"✅ Video has been cut to the first {cutoff_time} seconds")
            
            # Remove the original file and rename the trimmed file
            os.remove(video_file)
            os.rename(trimmed_file, video_file)
            print(f"Original file removed and trimmed file renamed to {os.path.basename(video_file)}")
        else:
            print(f"Video duration ({duration:.2f}s) is not longer than cutoff time. No need to cut.")

def find_video_files(save_path='output'):
    """定位待处理的源视频，要求 output/ 下恰好一个视频文件。

    为避免"多了一个文件就整条流程跑不起来"，这里的行为是：
      - 0 个 → 抛 FileNotFoundError（提示先用 UI 下载/上传）
      - 1 个 → 返回该文件
      - 多个 → 打印告警并返回**修改时间最新**的那个（旧行为是直接抛错）
    """
    video_files = [file for file in glob.glob(save_path + "/*") if os.path.splitext(file)[1][1:].lower() in load_key("allowed_video_formats")]
    # change \\ to /, this happen on windows
    if sys.platform.startswith('win'):
        video_files = [file.replace("\\", "/") for file in video_files]
    video_files = [file for file in video_files if not file.startswith("output/output")]

    if len(video_files) == 0:
        raise FileNotFoundError(
            f"在 {save_path}/ 下没有找到任何支持的视频文件。"
            "请先在页面上下载或上传视频。"
        )
    if len(video_files) > 1:
        video_files = sorted(video_files, key=os.path.getmtime, reverse=True)
        print(f"⚠️ {save_path}/ 下检测到 {len(video_files)} 个视频文件，将使用最新的一个：{video_files[0]}")
        print(f"   其余文件：{video_files[1:]}（如需处理它们，请先归档或移走）")
    return video_files[0]

if __name__ == '__main__':
    # Example usage
    url = input('Please enter the URL of the video you want to download: ')
    resolution = input('Please enter the desired resolution (360/1080, default 1080): ')
    resolution = int(resolution) if resolution.isdigit() else 1080
    download_video_ytdlp(url, resolution=resolution)
    print(f"🎥 Video has been downloaded to {find_video_files()}")

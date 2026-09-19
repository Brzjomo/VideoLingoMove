import os,sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import glob
import json
import re
import subprocess
from core.config_utils import load_key, load_key_or

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

def probe_duration(media_file: str) -> float:
    """用 ffprobe 读取媒体时长（秒）。

    不依赖 librosa：`librosa.get_duration(filename=...)` 在 librosa 1.x 已被
    移除，而 ffmpeg/ffprobe 本来就是本项目的硬依赖。
    """
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=nw=1:nk=1', media_file],
        capture_output=True, text=True,
    )
    try:
        return float((result.stdout or '').strip())
    except ValueError:
        raise RuntimeError(
            f"无法用 ffprobe 读取时长：{media_file}（{result.stderr.strip()[:200]}）"
        )


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
        # 明确要求合并成 mp4。否则 bestvideo+bestaudio 可能被合并成 mkv/webm，
        # 后续 -c copy 与字幕烧录都按 mp4 假设，容易在这一步踩坑。
        'merge_output_format': 'mp4',
        'postprocessors': [{
            'key': 'FFmpegThumbnailsConvertor',
            'format': 'jpg',
        }],
    }

    # --- YouTube 专用设置（config 的 `youtube:` 段，缺失时视为未配置）---
    # 用 load_key_or 是因为旧 config.yaml 里可能还没有这个段：
    # config_utils 只在文件缺失时才从模板引导，已存在的旧文件不会自动补键。
    youtube = load_key_or("youtube") or {}
    if not isinstance(youtube, dict):
        youtube = {}

    # 需要登录 / 年龄限制 / 会员视频：填 Netscape 格式的 cookies.txt 路径
    cookies_path = youtube.get("cookies_path") or ""
    if cookies_path:
        if os.path.exists(cookies_path):
            ydl_opts["cookiefile"] = str(cookies_path)
        else:
            print(f"[yellow]⚠️ youtube.cookies_path 指向的文件不存在：{cookies_path}[/yellow]")

    # 代理语义（与 yt-dlp 约定一致）：
    #   缺省/None = 交给 yt-dlp 自己按系统与环境变量发现
    #   空串 ""   = 显式禁用代理（环境里有 HTTP_PROXY 时很有用）
    #   URL       = 强制使用该代理
    proxy = youtube.get("proxy")
    if proxy is not None:
        if not isinstance(proxy, str):
            raise ValueError("youtube.proxy 必须是 null、空字符串或代理 URL")
        ydl_opts["proxy"] = proxy.strip()

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

    # 记录输入清单：后续步骤据此区分音频/视频输入，不必再靠文件名特判
    write_input_manifest(find_video_files(save_path), "video", save_path)

    # cut the video to make demo
    if cutoff_time:
        print(f"Cutoff time: {cutoff_time}, Now checking video duration...")
        video_file = find_video_files(save_path)
        
        # 用 ffprobe 取时长，而不是 librosa.get_duration(filename=...)：
        # `filename=` 参数在 librosa 1.x 已被移除，且 ffmpeg/ffprobe 本就是硬依赖，
        # 没必要为一次时长查询引入音频解码库。
        duration = probe_duration(video_file)
        
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

# 本流程自己生成的成品，永远不算"待处理的源视频"。
# 旧实现用 `not file.startswith("output/output")` 过滤，方向是错的（两个方向都错）：
#   - 标题以 output 开头的合法输入（如 `output tutorial.mp4`）会被误排除；
#   - 自己生成的 `output/OUTPUT_SUB.MP4`（大写扩展名）反而绕过过滤被当成输入。
# 注意：`black_screen.mp4`（上传音频时包装出的占位视频）是**合法源文件**，不能排除。
GENERATED_VIDEO_NAMES = {"output_sub.mp4", "output_dub.mp4"}

def find_video_files(save_path='output'):
    """定位待处理的源视频，要求 output/ 下恰好一个视频文件。

    为避免"多了一个文件就整条流程跑不起来"，这里的行为是：
      - 0 个 → 抛 FileNotFoundError（提示先用 UI 下载/上传）
      - 1 个 → 返回该文件
      - 多个 → 打印告警并返回**修改时间最新**的那个（旧行为是直接抛错）
    """
    video_files = [
        file for file in glob.glob(save_path + "/*")
        if os.path.isfile(file)
        and os.path.splitext(file)[1][1:].lower() in load_key("allowed_video_formats")
    ]
    # change \\ to /, this happen on windows
    if sys.platform.startswith('win'):
        video_files = [file.replace("\\", "/") for file in video_files]
    video_files = [
        file for file in video_files
        if os.path.basename(file).lower() not in GENERATED_VIDEO_NAMES
    ]

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


# ================================================================
# 输入清单（input_manifest.json）
# ================================================================
# 旧做法：上传音频时用 ffmpeg 把它包成一个黑底视频 black_screen.mp4，再走与
# 视频完全相同的流程。代价是多一次转码、多一份磁盘，还要靠文件名特判。
# 现在下载/上传时写一份清单记录"哪个文件是输入、它是音频还是视频"，
# 音频直接按音频处理（见上游 a479d07 / 3b0fbab 的 audio-only flow）。
INPUT_MANIFEST = "input_manifest.json"
GENERATED_AUDIO_NAMES = {"dub.mp3", "normalized_dub.wav"}

def write_input_manifest(media_file: str, media_type: str, save_path='output'):
    """记录本次要处理的输入媒体。"""
    os.makedirs(save_path, exist_ok=True)
    media_path = media_file.replace("\\", "/") if sys.platform.startswith('win') else media_file
    with open(os.path.join(save_path, INPUT_MANIFEST), "w", encoding="utf-8") as f:
        json.dump({"path": media_path, "type": media_type}, f, ensure_ascii=False, indent=2)


def read_input_manifest(save_path='output'):
    """读取输入清单，返回 (路径, 类型)；不可用/已失效时返回 None。"""
    manifest_path = os.path.join(save_path, INPUT_MANIFEST)
    if not os.path.exists(manifest_path):
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    media_file = data.get("path")
    media_type = data.get("type")
    if media_type not in ("video", "audio") or not media_file or not os.path.exists(media_file):
        return None
    if sys.platform.startswith('win'):
        media_file = media_file.replace("\\", "/")
    return media_file, media_type


def find_audio_files(save_path='output'):
    """定位 output/ 下的源音频（与 find_video_files 同规则）。"""
    audio_files = [
        file for file in glob.glob(save_path + "/*")
        if os.path.isfile(file)
        and os.path.splitext(file)[1][1:].lower() in load_key("allowed_audio_formats")
        and os.path.basename(file).lower() not in GENERATED_AUDIO_NAMES
    ]
    if sys.platform.startswith('win'):
        audio_files = [file.replace("\\", "/") for file in audio_files]
    if not audio_files:
        raise FileNotFoundError(f"在 {save_path}/ 下没有找到任何支持的音频文件。")
    if len(audio_files) > 1:
        audio_files = sorted(audio_files, key=os.path.getmtime, reverse=True)
        print(f"⚠️ {save_path}/ 下检测到 {len(audio_files)} 个音频文件，将使用最新的一个：{audio_files[0]}")
    return audio_files[0]


def find_media_file(save_path='output'):
    """定位待处理的源媒体，返回 (路径, 'video'|'audio')。

    优先用 input_manifest.json（下载/上传时写入，能可靠区分音频与视频）；
    没有清单时按扩展名回退，先找视频再找音频。
    """
    manifest = read_input_manifest(save_path)
    if manifest:
        return manifest
    try:
        return find_video_files(save_path), "video"
    except FileNotFoundError:
        return find_audio_files(save_path), "audio"


def is_audio_only_input(save_path='output') -> bool:
    """输入是否是独立音频文件（没有视频轨）。

    这种情况下只产出字幕文件，不做压制。
    """
    try:
        _, media_type = find_media_file(save_path)
        return media_type == "audio"
    except Exception:
        return False

if __name__ == '__main__':
    # Example usage
    url = input('Please enter the URL of the video you want to download: ')
    resolution = input('Please enter the desired resolution (360/1080, default 1080): ')
    resolution = int(resolution) if resolution.isdigit() else 1080
    download_video_ytdlp(url, resolution=resolution)
    print(f"🎥 Video has been downloaded to {find_video_files()}")

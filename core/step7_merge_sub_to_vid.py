import os, subprocess, time, sys, shutil
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config_utils import load_key
from core.step1_ytdlp import find_media_file, is_audio_placeholder
from rich import print as rprint
import platform

SRC_FONT_SIZE = 15
TRANS_FONT_SIZE = 17
FONT_NAME = 'Arial'
TRANS_FONT_NAME = 'Arial'

# Linux need to install google noto fonts: apt-get install fonts-noto
if platform.system() == 'Linux':
    FONT_NAME = 'NotoSansCJK-Regular'
    TRANS_FONT_NAME = 'NotoSansCJK-Regular'
# macOS 使用自己的字体名；沿用 'Arial' 会让中日韩字幕烧录成豆腐块（tofu）
elif platform.system() == 'Darwin':
    FONT_NAME = 'Arial Unicode MS'
    TRANS_FONT_NAME = 'Arial Unicode MS'

SRC_FONT_COLOR = '&HFFFFFF'
SRC_OUTLINE_COLOR = '&H000000'
SRC_OUTLINE_WIDTH = 1
SRC_SHADOW_COLOR = '&H80000000'
TRANS_FONT_COLOR = '&H00FFFF'
TRANS_OUTLINE_COLOR = '&H000000'
TRANS_OUTLINE_WIDTH = 1 
TRANS_BACK_COLOR = '&H33000000'

OUTPUT_DIR = "output"
OUTPUT_VIDEO = f"{OUTPUT_DIR}/output_sub.mp4"
SRC_SRT = f"{OUTPUT_DIR}/src.srt"
TRANS_SRT = f"{OUTPUT_DIR}/trans.srt"

# 字幕阶段完成标记。
# 以前 UI 用"output_sub.mp4 是否存在"判断本阶段是否完成；但在 resolution=0x0
# 且输入是真实视频时我们**不再生成**任何成片，那个判据就会永远为假、按钮一直挂着。
# 因此改为显式写一个标记文件（见 devdocs 已知问题与技术债 §5.2 G5）。
STAGE_DONE_MARKER = f"{OUTPUT_DIR}/log/subtitle_stage_done.txt"

def check_gpu_available():
    try:
        result = subprocess.run(['ffmpeg', '-encoders'], capture_output=True, text=True)
        return 'h264_nvenc' in result.stdout
    except:
        return False

def _mark_stage_done():
    """写字幕阶段完成标记（UI 用它判断是否显示"已完成"）。"""
    os.makedirs(os.path.dirname(STAGE_DONE_MARKER), exist_ok=True)
    with open(STAGE_DONE_MARKER, 'w', encoding='utf-8') as f:
        f.write("subtitle stage finished\n")


def merge_subtitles_to_video():
    RESOLUTION = load_key("resolution")
    TARGET_WIDTH, TARGET_HEIGHT = RESOLUTION.split('x')
    media_file, media_type = find_media_file()
    os.makedirs(os.path.dirname(OUTPUT_VIDEO), exist_ok=True)

    # 输入是纯音频：没有任何视频轨可以压制，直接只交字幕文件。
    # 这是上传音频的新路径（input_manifest 记为 audio）；旧路径留下的
    # black_screen.mp4 也仍然按音频对待（见 is_audio_placeholder）。
    if media_type == "audio":
        rprint("[bold green]🎵 输入为音频：跳过视频压制，字幕文件已就绪。[/bold green]")
        _mark_stage_done()
        return

    video_file = media_file

    # resolution 为 0x0 等价于侧边栏的 "Burn-in Subtitles" 开关处于关闭状态：
    # 只出字幕、不做压制，静默跳过（不打印提示）。侧边栏那个 toggle 就是通过
    # 把 resolution 写成 '0x0' / 具体值来表达开关的，见 st_components/sidebar_setting.py。
    if RESOLUTION == '0x0':
        if is_audio_placeholder(video_file):
            # 兼容旧产物：早期"上传音频"会把它包成 black_screen.mp4
            # （黑底 + 完整音频）。它本身就是一份可播放的成片，**必须原样保留**：
            # 若用 1 秒黑帧覆盖 output_sub.mp4，用户就无法播放、也就无法校对字幕与音频的对齐。
            shutil.copy2(video_file, OUTPUT_VIDEO)
            rprint(f"[bold green]输入为纯音频：已保留 {video_file} 作为成片 {OUTPUT_VIDEO}"
                   f"（黑底 + 完整音频），不再生成占位视频。[/bold green]")
        elif os.path.exists(OUTPUT_VIDEO):
            # 输入是真实视频且未开启 Burn-in：不需要任何成片。
            # 顺手清掉上一次遗留的占位/成片，避免使用者误以为这次也压制了。
            os.remove(OUTPUT_VIDEO)
        _mark_stage_done()
        return

    if not os.path.exists(SRC_SRT) or not os.path.exists(TRANS_SRT):
        # 抛 Exception 而不是 exit(1)：SystemExit 继承 BaseException，
        # 会绕过批处理模式的重试逻辑（只捕获 Exception），见 devdocs 已知问题 P3-35。
        raise FileNotFoundError(
            f"字幕文件不存在，请先运行 step6 生成字幕。期望: {SRC_SRT}, {TRANS_SRT}"
        )

    ffmpeg_cmd = [
        'ffmpeg', '-i', video_file,
        '-vf', (
            f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
            f"subtitles={SRC_SRT}:force_style='FontSize={SRC_FONT_SIZE},FontName={FONT_NAME}," 
            f"PrimaryColour={SRC_FONT_COLOR},OutlineColour={SRC_OUTLINE_COLOR},OutlineWidth={SRC_OUTLINE_WIDTH},"
            f"ShadowColour={SRC_SHADOW_COLOR},BorderStyle=1',"
            f"subtitles={TRANS_SRT}:force_style='FontSize={TRANS_FONT_SIZE},FontName={TRANS_FONT_NAME},"
            f"PrimaryColour={TRANS_FONT_COLOR},OutlineColour={TRANS_OUTLINE_COLOR},OutlineWidth={TRANS_OUTLINE_WIDTH},"
            f"BackColour={TRANS_BACK_COLOR},Alignment=2,MarginV=27,BorderStyle=4'"
        ).encode('utf-8'),
    ]

    gpu_available = check_gpu_available()
    if gpu_available:
        rprint("[bold green]NVIDIA GPU encoder detected, will use GPU acceleration.[/bold green]")
        ffmpeg_cmd.extend(['-c:v', 'h264_nvenc'])
    else:
        rprint("[bold yellow]No NVIDIA GPU encoder detected, will use CPU instead.[/bold yellow]")
    
    ffmpeg_cmd.extend(['-y', OUTPUT_VIDEO])

    print("🎬 Start merging subtitles to video...")
    start_time = time.time()
    process = subprocess.Popen(ffmpeg_cmd)

    try:
        process.wait()
        if process.returncode == 0:
            print(f"\n✅ Done! Time taken: {time.time() - start_time:.2f} seconds")
            _mark_stage_done()
        else:
            # 认真检查 returncode：此前失败只打印一行 ❌ 但流程仍当作成功，
            # 使用者会以为压制好了（见 devdocs 已知问题与技术债 §5.2 G5）。
            raise RuntimeError(
                f"FFmpeg 压制失败（returncode={process.returncode}）。"
                f"请检查 output/src.srt 与 output/trans.srt 是否存在且格式正确。"
            )
    except Exception as e:
        print(f"\n❌ Error occurred: {e}")
        if process.poll() is None:
            process.kill()
        raise

if __name__ == "__main__":
    merge_subtitles_to_video()
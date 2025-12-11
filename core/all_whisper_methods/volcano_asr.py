"""
火山引擎ASR服务类
用于调用火山引擎大模型录音文件识别API
"""

import os
import sys
import json
import time
import uuid
import requests
import tempfile
import subprocess
from typing import Dict, List, Optional, Tuple
from rich import print as rprint

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core.config_utils import load_key
from core.all_whisper_methods.tos_service import TOSService


class VolcanoASR:
    """火山引擎ASR服务类"""

    def __init__(self):
        """初始化火山引擎ASR配置"""
        self.app_id = load_key("volcano_asr.app_id")
        self.access_token = load_key("volcano_asr.access_token")
        self.resource_id = load_key("volcano_asr.resource_id")
        self.language = load_key("volcano_asr.language")
        self.enable_punc = load_key("volcano_asr.enable_punc")
        self.enable_itn = load_key("volcano_asr.enable_itn")
        self.enable_ddc = load_key("volcano_asr.enable_ddc")
        self.enable_speaker_info = load_key("volcano_asr.enable_speaker_info")
        self.show_utterances = load_key("volcano_asr.show_utterances")
        self.enable_channel_split = load_key("volcano_asr.enable_channel_split")
        self.vad_segment = load_key("volcano_asr.vad_segment")
        self.model_version = load_key("volcano_asr.model_version")

        # API endpoints
        self.submit_url = "https://openspeech-direct.zijieapi.com/api/v3/auc/bigmodel/submit"
        self.query_url = "https://openspeech-direct.zijieapi.com/api/v3/auc/bigmodel/query"

        # 初始化TOS服务
        self.tos_service = TOSService()
        self.use_tos = self.tos_service.is_enabled()

        # 跟踪最近上传的文件信息
        self.last_uploaded_file_info = None

        # 验证配置
        self._validate_config()

    def _validate_config(self):
        """验证配置是否完整"""
        if not self.app_id:
            raise ValueError("火山引擎ASR配置错误: app_id为空")
        if not self.access_token:
            raise ValueError("火山引擎ASR配置错误: access_token为空")
        if not self.resource_id:
            raise ValueError("火山引擎ASR配置错误: resource_id为空")

    def _upload_audio_to_temp_url(self, audio_file: str) -> str:
        """
        将音频文件上传到TOS并获取可公开访问的URL

        Args:
            audio_file: 本地音频文件路径

        Returns:
            str: 可公开访问的URL
        """
        # 检查文件是否存在
        if not os.path.exists(audio_file):
            raise FileNotFoundError(f"音频文件不存在: {audio_file}")

        abs_path = os.path.abspath(audio_file)
        file_size = os.path.getsize(audio_file) / (1024 * 1024)  # MB

        rprint(f"[cyan]📁 准备上传音频文件: {abs_path}[/cyan]")
        rprint(f"[cyan]文件大小: {file_size:.2f} MB[/cyan]")

        # 使用TOS服务上传文件
        if self.use_tos:
            rprint("[green]🚀 使用火山引擎TOS上传文件...[/green]")
            try:
                success, object_key, public_url = self.tos_service.upload_file(audio_file)
                if success:
                    rprint(f"[green]✅ 文件上传到TOS成功[/green]")
                    rprint(f"[cyan]TOS URL: {public_url}[/cyan]")
                    # 保存上传的文件信息
                    self.last_uploaded_file_info = {
                        'object_key': object_key,
                        'public_url': public_url,
                        'local_path': audio_file
                    }
                    return public_url
                else:
                    rprint("[yellow]⚠️ TOS上传失败，回退到file:// URL[/yellow]")
            except Exception as e:
                rprint(f"[red]❌ TOS上传异常: {str(e)}[/red]")
                rprint("[yellow]⚠️ 回退到file:// URL[/yellow]")

        # 回退方案：返回file:// URL
        rprint("[yellow]⚠️ 使用file:// URL（可能不被火山引擎ASR接受）[/yellow]")
        file_url = f"file://{abs_path}"
        rprint(f"[cyan]file:// URL: {file_url}[/cyan]")
        rprint("[yellow]如果火山引擎API报错，请检查TOS配置[/yellow]")

        return file_url

    def _convert_audio_for_volcano(self, audio_file: str) -> str:
        """
        转换音频格式为火山引擎支持的格式
        火山引擎支持: mp3, wav, ogg等格式

        注意：如果已经是WAV格式且符合火山引擎要求（16kHz, 单声道, 16-bit PCM），
        则直接返回原文件，避免重复转换。
        """
        # 检查文件格式
        ext = os.path.splitext(audio_file)[1].lower()
        supported_formats = ['.mp3', '.wav', '.ogg', '.flac', '.m4a']

        if ext in supported_formats:
            # 如果是WAV文件，检查是否需要转换
            if ext == '.wav':
                try:
                    # 使用ffprobe检查WAV文件参数
                    cmd = [
                        'ffprobe', '-v', 'error',
                        '-select_streams', 'a:0',
                        '-show_entries', 'stream=sample_rate,channels,bits_per_sample',
                        '-of', 'csv=p=0', audio_file
                    ]
                    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                    sample_rate, channels, bits_per_sample = result.stdout.strip().split(',')

                    # 检查是否符合火山引擎要求：16kHz, 单声道, 16-bit
                    if (int(sample_rate) == 16000 and
                        int(channels) == 1 and
                        int(bits_per_sample) == 16):
                        rprint(f"[green]WAV文件已符合火山引擎要求: 16kHz, 单声道, 16-bit PCM[/green]")
                        return audio_file
                    else:
                        rprint(f"[yellow]WAV文件参数不符合要求: {sample_rate}Hz, {channels}声道, {bits_per_sample}bit[/yellow]")
                        rprint(f"[yellow]需要转换为: 16kHz, 单声道, 16-bit PCM[/yellow]")
                except Exception as e:
                    rprint(f"[yellow]无法检查WAV文件参数: {str(e)}[/yellow]")
                    rprint(f"[yellow]将进行格式转换[/yellow]")

            # 对于其他支持的格式或不符合要求的WAV，直接返回
            return audio_file

        # 转换为mp3格式（对于不支持的格式）
        temp_file = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
        temp_path = temp_file.name
        temp_file.close()

        rprint(f"[cyan]转换音频格式为MP3: {audio_file} -> {temp_path}[/cyan]")
        cmd = [
            'ffmpeg', '-y', '-i', audio_file,
            '-vn', '-acodec', 'libmp3lame', '-b:a', '128k',
            '-ar', '16000', '-ac', '1', temp_path
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return temp_path
        except subprocess.CalledProcessError as e:
            rprint(f"[red]音频转换失败: {e.stderr.decode()}[/red]")
            raise

    def submit_task(self, audio_url: str, audio_file: str = None) -> Tuple[str, str]:
        """
        提交音频识别任务

        Args:
            audio_url: 音频文件URL
            audio_file: 音频文件路径（可选，用于确定文件格式）

        Returns:
            tuple: (task_id, log_id)
        """
        task_id = str(uuid.uuid4())

        headers = {
            "X-Api-App-Key": self.app_id,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": task_id,
            "X-Api-Sequence": "-1"
        }

        # 根据文件扩展名确定格式
        audio_format = "mp3"  # 默认格式
        if audio_file:
            ext = os.path.splitext(audio_file)[1].lower()
            if ext == '.wav':
                audio_format = "wav"
            elif ext == '.ogg':
                audio_format = "ogg"
            elif ext == '.flac':
                audio_format = "flac"
            elif ext == '.m4a':
                audio_format = "m4a"

        # 构建请求体
        request_body = {
            "user": {
                "uid": "videolingo_user"
            },
            "audio": {
                "url": audio_url,
                "format": audio_format
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": self.enable_itn,
                "enable_punc": self.enable_punc,
                "enable_ddc": self.enable_ddc,
                "enable_speaker_info": self.enable_speaker_info,
                "show_utterances": self.show_utterances,
                "enable_channel_split": self.enable_channel_split,
                "vad_segment": self.vad_segment
            }
        }

        # 添加语言设置（如果指定）
        if self.language:
            request_body["audio"]["language"] = self.language

        # 添加模型版本（如果指定）
        if self.model_version and self.model_version != "310":
            request_body["request"]["model_version"] = self.model_version

        rprint(f"[cyan]提交火山引擎ASR任务: {task_id}[/cyan]")
        rprint(f"[cyan]音频URL: {audio_url}[/cyan]")

        try:
            response = requests.post(
                self.submit_url,
                data=json.dumps(request_body),
                headers=headers,
                timeout=30
            )

            if 'X-Api-Status-Code' in response.headers:
                status_code = response.headers["X-Api-Status-Code"]
                message = response.headers.get("X-Api-Message", "")

                if status_code == "20000000":
                    log_id = response.headers.get("X-Tt-Logid", "")
                    rprint(f"[green]任务提交成功[/green]")
                    rprint(f"[cyan]状态码: {status_code}[/cyan]")
                    rprint(f"[cyan]消息: {message}[/cyan]")
                    rprint(f"[cyan]Log ID: {log_id}[/cyan]")
                    return task_id, log_id
                else:
                    rprint(f"[red]任务提交失败[/red]")
                    rprint(f"[red]状态码: {status_code}[/red]")
                    rprint(f"[red]消息: {message}[/red]")
                    raise RuntimeError(f"火山引擎ASR任务提交失败: {status_code} - {message}")
            else:
                rprint(f"[red]响应头中缺少状态码[/red]")
                rprint(f"[red]响应头: {response.headers}[/red]")
                raise RuntimeError("火山引擎ASR响应异常")

        except requests.exceptions.RequestException as e:
            rprint(f"[red]网络请求失败: {str(e)}[/red]")
            raise

    def query_task(self, task_id: str, log_id: str) -> Dict:
        """
        查询任务结果

        Args:
            task_id: 任务ID
            log_id: 日志ID

        Returns:
            dict: 识别结果
        """
        headers = {
            "X-Api-App-Key": self.app_id,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": task_id,
            "X-Tt-Logid": log_id
        }

        try:
            response = requests.post(
                self.query_url,
                data=json.dumps({}),
                headers=headers,
                timeout=30
            )

            if 'X-Api-Status-Code' in response.headers:
                status_code = response.headers["X-Api-Status-Code"]
                message = response.headers.get("X-Api-Message", "")

                rprint(f"[cyan]查询状态码: {status_code}[/cyan]")
                rprint(f"[cyan]查询消息: {message}[/cyan]")

                if status_code == "20000000":  # 任务完成
                    result = response.json()
                    rprint(f"[green]任务处理完成[/green]")
                    return result
                elif status_code in ["20000001", "20000002"]:  # 处理中或排队中
                    return {"status": "processing", "code": status_code}
                elif status_code == "20000003":  # 静音音频
                    rprint(f"[yellow]静音音频，需要重新提交[/yellow]")
                    return {"status": "silent", "code": status_code}
                else:  # 失败
                    rprint(f"[red]任务处理失败: {status_code} - {message}[/red]")
                    return {"status": "failed", "code": status_code, "message": message}
            else:
                rprint(f"[red]查询响应异常[/red]")
                return {"status": "error", "message": "响应头中缺少状态码"}

        except requests.exceptions.RequestException as e:
            rprint(f"[red]查询请求失败: {str(e)}[/red]")
            return {"status": "error", "message": str(e)}

    def transcribe_audio(self, audio_file: str, start: float = 0, end: float = None) -> Dict:
        """
        转录音频文件

        Args:
            audio_file: 音频文件路径
            start: 开始时间（秒）
            end: 结束时间（秒），None表示整个文件

        Returns:
            dict: 转录结果，格式与WhisperX兼容
        """
        # 1. 转换音频格式
        converted_audio = self._convert_audio_for_volcano(audio_file)

        # 2. 获取音频URL（TODO: 实际部署时需要上传）
        audio_url = self._upload_audio_to_temp_url(converted_audio)

        # 3. 提交任务
        task_id, log_id = self.submit_task(audio_url, converted_audio)

        # 4. 轮询查询结果
        rprint(f"[cyan]开始轮询查询结果...[/cyan]")
        max_attempts = 300  # 最大尝试次数（5分钟）
        attempt = 0

        while attempt < max_attempts:
            attempt += 1
            rprint(f"[cyan]查询尝试 {attempt}/{max_attempts}...[/cyan]")

            result = self.query_task(task_id, log_id)

            if result.get("status") == "processing":
                time.sleep(5)  # 等待5秒后重试
                continue
            elif "result" in result:
                # 转换结果格式
                whisper_result = self._convert_to_whisper_format(result, start)
                # 保存原始ASR结果为JSON文件
                self._save_asr_result_to_json(result, whisper_result, task_id, audio_file, start)

                # ASR返回结果后删除TOS文件
                self._cleanup_tos_file_after_result()

                return whisper_result
            elif result.get("status") in ["silent", "failed", "error"]:
                raise RuntimeError(f"火山引擎ASR处理失败: {result.get('message', '未知错误')}")

        raise TimeoutError("火山引擎ASR处理超时")

    def _convert_to_whisper_format(self, volcano_result: Dict, start_offset: float = 0) -> Dict:
        """
        将火山引擎结果转换为WhisperX格式

        Args:
            volcano_result: 火山引擎返回结果
            start_offset: 时间偏移量（秒）

        Returns:
            dict: WhisperX兼容格式的结果
        """
        if "result" not in volcano_result:
            raise ValueError("火山引擎结果中缺少result字段")

        result_data = volcano_result["result"]

        # 构建WhisperX格式的结果
        whisper_result = {
            "segments": [],
            "language": self._detect_language_from_result(result_data)
        }

        # 如果有utterances信息，使用utterances
        if "utterances" in result_data and result_data["utterances"]:
            for utterance in result_data["utterances"]:
                segment = {
                    "start": (utterance.get("start_time", 0) / 1000.0) + start_offset,  # 毫秒转秒
                    "end": (utterance.get("end_time", 0) / 1000.0) + start_offset,      # 毫秒转秒
                    "text": utterance.get("text", "").strip(),
                    "words": []
                }

                # 如果有words信息，添加单词级时间戳
                if "words" in utterance and utterance["words"]:
                    for word in utterance["words"]:
                        word_text = word.get("text", "")
                        # 跳过空格单词（时间戳为-0.001）
                        if word_text and word_text.strip() == "":
                            continue
                        word_info = {
                            "word": word_text,
                            "start": (word.get("start_time", 0) / 1000.0) + start_offset,
                            "end": (word.get("end_time", 0) / 1000.0) + start_offset
                        }
                        segment["words"].append(word_info)

                whisper_result["segments"].append(segment)
        elif "text" in result_data:
            # 如果没有utterances，只有完整文本，创建一个segment
            segment = {
                "start": start_offset,
                "end": start_offset + (volcano_result.get("audio_info", {}).get("duration", 0) / 1000.0),
                "text": result_data["text"].strip(),
                "words": []
            }
            whisper_result["segments"].append(segment)

        return whisper_result

    def _save_asr_result_to_json(self, volcano_result: Dict, whisper_result: Dict, task_id: str, audio_file: str, start_offset: float = 0):
        """
        保存火山引擎ASR原始结果为JSON文件

        Args:
            volcano_result: 火山引擎返回的原始结果
            whisper_result: 转换后的Whisper格式结果
            task_id: 任务ID
            audio_file: 音频文件路径
            start_offset: 时间偏移量（秒）
        """
        try:
            # 创建输出目录
            output_dir = "output/log/asr_results"
            os.makedirs(output_dir, exist_ok=True)

            # 生成文件名
            import datetime
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            audio_filename = os.path.basename(audio_file)
            audio_name = os.path.splitext(audio_filename)[0]

            json_filename = f"volcano_asr_{audio_name}_{timestamp}_task_{task_id[:8]}.json"
            json_path = os.path.join(output_dir, json_filename)

            # 准备要保存的数据
            save_data = {
                "metadata": {
                    "task_id": task_id,
                    "audio_file": audio_file,
                    "audio_filename": audio_filename,
                    "start_offset": start_offset,
                    "save_timestamp": timestamp,
                    "save_time": datetime.datetime.now().isoformat(),
                    "asr_engine": "volcano",
                    "model_version": self.model_version,
                    "language": self.language
                },
                "original_result": volcano_result,
                "converted_result": whisper_result
            }

            # 保存为JSON文件
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)

            rprint(f"[green]✅ 火山引擎ASR原始结果已保存为JSON文件: {json_path}[/green]")
            rprint(f"[cyan]文件大小: {os.path.getsize(json_path)} 字节[/cyan]")

            # 同时保存一个简化的版本
            simplified_path = os.path.join(output_dir, f"volcano_asr_{audio_name}_{timestamp}_simplified.json")
            simplified_data = {
                "metadata": save_data["metadata"],
                "segments": save_data["converted_result"]["segments"]
            }

            with open(simplified_path, 'w', encoding='utf-8') as f:
                json.dump(simplified_data, f, ensure_ascii=False, indent=2)

            rprint(f"[green]✅ 简化版结果已保存: {simplified_path}[/green]")

        except Exception as e:
            rprint(f"[yellow]⚠️ 保存ASR结果为JSON时出错: {str(e)}[/yellow]")
            import traceback
            rprint(f"[yellow]详细错误: {traceback.format_exc()}[/yellow]")

    def _cleanup_tos_file_after_result(self):
        """ASR返回结果后删除TOS文件"""
        if not self.use_tos or not self.last_uploaded_file_info:
            return

        try:
            object_key = self.last_uploaded_file_info['object_key']
            # 调用TOS服务的清理方法
            self.tos_service.cleanup_uploaded_file(object_key)
            # 清理后重置
            self.last_uploaded_file_info = None
            rprint(f"[green]✅ ASR处理完成，已删除TOS文件[/green]")
        except Exception as e:
            rprint(f"[yellow]⚠️ 清理TOS文件时出错: {str(e)}[/yellow]")

    def _detect_language_from_result(self, result_data: Dict) -> str:
        """
        从结果中检测语言

        Args:
            result_data: 结果数据

        Returns:
            str: 语言代码
        """
        # 如果有语言检测信息，使用检测到的语言
        # 否则使用配置的语言或默认英语
        if self.language:
            # 将火山引擎语言代码转换为Whisper格式
            lang_map = {
                "en-US": "en",
                "zh-CN": "zh",
                "ja-JP": "ja",
                "ko-KR": "ko",
                "fr-FR": "fr",
                "de-DE": "de",
                "es-MX": "es",
                "pt-BR": "pt",
                "id-ID": "id",
                "th-TH": "th",
                "ar-SA": "ar"
            }
            return lang_map.get(self.language, "en")

        return "en"  # 默认英语

    def cleanup(self):
        """清理临时文件"""
        # 清理TOS上的文件
        if self.use_tos:
            try:
                self.tos_service.cleanup_old_files()
                rprint("[green]✅ 已清理TOS上的旧文件[/green]")
            except Exception as e:
                rprint(f"[yellow]⚠️ 清理TOS文件时出错: {str(e)}[/yellow]")

        # 清理本地临时文件（如果有）
        # 注意：_convert_audio_for_volcano可能会创建临时文件
        # 这些文件应该在转换完成后立即清理


def test_volcano_asr():
    """测试火山引擎ASR功能"""
    try:
        # 加载配置
        from core.config_utils import load_key
        app_id = load_key("volcano_asr.app_id")
        access_token = load_key("volcano_asr.access_token")

        if not app_id or not access_token:
            rprint("[yellow]⚠️ 火山引擎ASR配置不完整，跳过测试[/yellow]")
            rprint("[yellow]请在config.yaml中配置volcano_asr.app_id和volcano_asr.access_token[/yellow]")
            return

        # 创建测试音频文件（1秒静音）
        import numpy as np
        import soundfile as sf

        test_audio = "test_volcano.wav"
        sample_rate = 16000
        duration = 1.0  # 1秒
        t = np.linspace(0, duration, int(sample_rate * duration), False)
        audio_data = 0.01 * np.sin(2 * np.pi * 440 * t)  # 440Hz正弦波

        sf.write(test_audio, audio_data, sample_rate)

        # 测试ASR
        asr = VolcanoASR()
        rprint("[cyan]开始测试火山引擎ASR...[/cyan]")

        # 注意：实际测试需要有效的app_id和access_token
        # 这里只是演示代码结构
        rprint("[green]火山引擎ASR服务类创建成功[/green]")

        # 清理测试文件
        if os.path.exists(test_audio):
            os.remove(test_audio)

    except Exception as e:
        rprint(f"[red]测试失败: {str(e)}[/red]")


if __name__ == "__main__":
    test_volcano_asr()
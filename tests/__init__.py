"""VideoLingo 回归测试。

用标准库 unittest 编写 —— dev 没有引入 pytest，测试不该为此新增依赖。

运行全部：
    python -m unittest discover -s tests -v

说明：
  * test_source_language 与 test_prompt_contract 会先把 VIDEOLINGO_CONFIG
    指向临时配置文件，因此不会读写你的 config.yaml。
  * test_audio_extraction 中需要 ffmpeg 的用例会自动跳过（未安装时）。
"""

# VideoLingo 容器镜像（torch 2.8 / whisperx 3.8 新栈）
#
# 相对旧版的三处实质改动（见 devdocs/05-guides/08-环境大升级方案.md 第七节第 8 条）：
#   1. 基底从 cuda 12.4 + ubuntu20.04 换成 **12.8.1 + cudnn runtime + ubuntu24.04**，
#      Python 直接用系统 3.11（ubuntu24.04 自带），不再加 deadsnakes PPA；
#   2. 不再 build 时 `git clone`，改为 `COPY . .` —— 镜像里跑的就是当前这份代码；
#   3. 依赖安装走 `setup_env.py`（uv + 项目内 .venv），与本地安装同一条路径，
#      不再出现"Dockerfile 装 torch 2.0.0、requirements 装 2.8.0"两套口径。
#
# 注意：容器内没有 nvidia-smi（runtime 镜像默认不带），installer.py 的
# 算力探测会判定为无 GPU → 选 cpu 轮子。所以构建/运行时用
# `--torch-backend cu128` 显式指定（ARG TORCH_BACKEND）。
ARG CUDA_VERSION=12.8.1
FROM nvidia/cuda:${CUDA_VERSION}-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ARG PYTHON_VERSION=3.11
ARG TORCH_BACKEND=cu128

# 系统依赖：python3.11 + venv、ffmpeg（大版本必须 4–7，ubuntu24.04 是 6.x）、
# curl 用于装 uv，fonts-noto-cjk 是**中文**字幕必需的（旧版装的 fonts-noto
# 不含中日韩字形，烧录出来是豆腐块）。
RUN apt-get update && apt-get install -y --no-install-recommends \
        python${PYTHON_VERSION} python${PYTHON_VERSION}-venv python3-pip \
        ffmpeg fonts-noto-cjk git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python${PYTHON_VERSION} /usr/bin/python3

# 装 uv（与 setup_env.py 期望的一致）
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && cp /root/.local/bin/uv /usr/local/bin/uv \
    && uv --version

WORKDIR /app

# 把当前代码整份放进镜像（取代旧的 git clone）
COPY . .

# 建项目内 .venv 并把依赖装进它。HF_HOME/TORCH_HOME 指向项目内，保持可搬移。
ENV HF_HOME=/app/_model_cache \
    TORCH_HOME=/app/_model_cache/torch \
    UV_CACHE_DIR=/app/.uv-cache \
    PIP_CACHE_DIR=/app/.pip-cache
RUN python3 setup_env.py --python ${PYTHON_VERSION} \
        --torch-backend ${TORCH_BACKEND}

# CUDA 相关环境变量（保留旧镜像的语义）
ENV CUDA_HOME=/usr/local/cuda
ENV PATH=${CUDA_HOME}/bin:${PATH}
ENV LD_LIBRARY_PATH=${CUDA_HOME}/lib64:${LD_LIBRARY_PATH}

EXPOSE 8501

# 用项目内解释器启动：绕开 shell 的 PATH 解析，确保跑的就是 .venv 里那套依赖
CMD ["/app/.venv/bin/python", "-m", "streamlit", "run", "st.py", \
     "--server.port=8501", "--server.address=0.0.0.0"]

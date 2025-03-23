import os
import platform
import subprocess
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

ascii_logo = """
__     ___     _            _     _                    
\ \   / (_) __| | ___  ___ | |   (_)_ __   __ _  ___  
 \ \ / /| |/ _` |/ _ \/ _ \| |   | | '_ \ / _` |/ _ \ 
  \ V / | | (_| |  __/ (_) | |___| | | | | (_| | (_) |
   \_/  |_|\__,_|\___|\___/|_____|_|_| |_|\__, |\___/ 
                                          |___/        
"""

def install_package(*packages):
    # 添加项目目录下的Python环境到PATH环境变量
    project_dir = os.path.dirname(os.path.abspath(__file__))
    python_path = os.path.join(project_dir, "runtime", "python")
    os.environ["PATH"] = python_path + os.pathsep + os.environ["PATH"]

    # 使用项目目录下的Python环境安装包
    subprocess.check_call([sys.executable, "-m", "pip", "install", *packages])

def check_nvidia_gpu():
    try:
        import pynvml
    except ImportError:
        print("pynvml is not installed, attempting to install...")
        try:
            install_package("pynvml")
            import pynvml
        except:
            print("pynvml could not be installed or imported.")
            return False
    try:
        pynvml.nvmlInit()
        device_count = pynvml.nvmlDeviceGetCount()
        if device_count > 0:
            print(f"Detected NVIDIA GPU(s)")
            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                name = pynvml.nvmlDeviceGetName(handle)
                print(f"GPU {i}: {name}")
            return True
        else:
            print("No NVIDIA GPU detected")
            return False
    except pynvml.NVMLError:
        print("No NVIDIA GPU detected or NVIDIA drivers not properly installed")
        return False

def download_ffmpeg_windows(target_dir):
    import zipfile
    from pathlib import Path
    from urllib.request import urlretrieve
    from rich.console import Console
    from rich.panel import Panel

    """
    从 FFmpeg-Builds 下载最新版本的 FFmpeg 并解压到目标目录。
    """
    ffmpeg_url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    ffmpeg_zip = os.path.join(target_dir, "ffmpeg.zip")
    console = Console()
    console.print(f"🚀 Downloading FFmpeg from {ffmpeg_url}...")
    try:
        # 下载 FFmpeg
        urlretrieve(ffmpeg_url, ffmpeg_zip)
        console.print("✅ FFmpeg downloaded successfully.", style="green")
        # 解压 FFmpeg
        with zipfile.ZipFile(ffmpeg_zip, 'r') as zip_ref:
            zip_ref.extractall(target_dir)
        console.print(f"✅ FFmpeg extracted to {target_dir}.", style="green")
        # 删除压缩包
        os.remove(ffmpeg_zip)
        console.print("🗑️ FFmpeg zip file removed.", style="yellow")
        # 将 FFmpeg 可执行文件路径添加到系统环境变量
        ffmpeg_path = os.path.join(target_dir, "ffmpeg-master-latest-win64-gpl", "bin")
        if ffmpeg_path not in os.environ["PATH"]:
            os.environ["PATH"] += os.pathsep + ffmpeg_path
        console.print(f"🌐 Added FFmpeg to PATH: {ffmpeg_path}", style="bold cyan")
        return True
    except Exception as e:
        console.print(f"❌ Failed to download or extract FFmpeg: {e}", style="red")


def check_ffmpeg():
    from rich.console import Console
    from rich.panel import Panel
    console = Console()
    
    try:
        # Check if ffmpeg is installed
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        console.print(Panel("✅ FFmpeg is already installed", style="green"))
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        system = platform.system()
        install_cmd = ""
        
        if system == "Windows":
           target_dir = os.getcwd()  # 获取当前工作目录
           os.makedirs(target_dir, exist_ok=True)  # 确保目录存在

           console.print(Panel.fit(
               f"❌ FFmpeg not found\n\n"
               f"🛠️ Downloading and installing FFmpeg automatically to:\n[bold cyan]{target_dir}[/bold cyan]...",
               style="red"
           ))

           if download_ffmpeg_windows(target_dir):
               console.print(Panel(
                   f"✅ FFmpeg installed successfully!\n\n"
                   f"📁 FFmpeg is located in: [bold cyan]{target_dir}[/bold cyan]\n\n"
                   f"🔄 Please restart your terminal and run the installer again.",
                   style="green"
               ))
           else:
               console.print(Panel("❌ Failed to install FFmpeg. Please try manually downloading from https://github.com/BtbN/FFmpeg-Builds/releases.", style="red"))
        elif system == "Darwin":
            install_cmd = "brew install ffmpeg"
            extra_note = "Install Homebrew first (https://brew.sh/)"
        elif system == "Linux":
            install_cmd = "sudo apt install ffmpeg  # Ubuntu/Debian\nsudo yum install ffmpeg  # CentOS/RHEL"
            extra_note = "Use your distribution's package manager"
        
        console.print(Panel.fit(
            f"❌ FFmpeg not found\n\n"
            f"🛠️ Install using:\n[bold cyan]{install_cmd}[/bold cyan]\n\n"
            f"💡 Note: {extra_note}\n\n"
            f"🔄 After installing FFmpeg, please run this installer again: [bold cyan]python install.py[/bold cyan]",
            style="red"
        ))
        raise SystemExit("FFmpeg is required. Please install it and run the installer again.")

def main():
    install_package("requests", "rich", "ruamel.yaml")
    from rich.console import Console
    from rich.panel import Panel
    from rich.box import DOUBLE
    console = Console()

    width = max(len(line) for line in ascii_logo.splitlines()) + 4
    welcome_panel = Panel(
        ascii_logo,
        width=width,
        box=DOUBLE,
        title="[bold green]🌏[/bold green]",
        border_style="bright_blue"
    )
    console.print(welcome_panel)

    console.print(Panel.fit("🚀 Starting Installation", style="bold magenta"))

    # Configure mirrors
    from core.pypi_autochoose import main as choose_mirror
    choose_mirror()

    # Detect system and GPU
    has_gpu = platform.system() != 'Darwin' and check_nvidia_gpu()
    if has_gpu:
        console.print(Panel("🎮 NVIDIA GPU detected, installing CUDA version of PyTorch...", style="cyan"))
        torch_file = "torch-2.1.2+cu118-cp310-cp310-win_amd64.whl"
        if os.path.exists(torch_file):
            # https://download.pytorch.org/whl/cu118/torch-2.1.2%2Bcu118-cp310-cp310-win_amd64.whl#sha256=0ddfa0336d678316ff4c35172d85cddab5aa5ded4f781158e725096926491db9
            subprocess.check_call([sys.executable, "-m", "pip", "install", "torch==2.1.2", "torchaudio==2.1.2", torch_file])
        else:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "torch==2.1.2", "torchaudio==2.1.2", "--index-url", "https://download.pytorch.org/whl/cu118"])
    else:
        system_name = "🍎 MacOS" if platform.system() == 'Darwin' else "💻 No NVIDIA GPU"
        console.print(Panel(f"{system_name} detected, installing CPU version of PyTorch... However, it would be extremely slow for transcription.", style="cyan"))
        torch_file = "torch-2.1.2+cu118-cp310-cp310-win_amd64.whl"
        if os.path.exists(torch_file):
            # https://download.pytorch.org/whl/cu118/torch-2.1.2%2Bcu118-cp310-cp310-win_amd64.whl#sha256=0ddfa0336d678316ff4c35172d85cddab5aa5ded4f781158e725096926491db9
            subprocess.check_call([sys.executable, "-m", "pip", "install", "torch==2.1.2", "torchaudio==2.1.2", torch_file])
        else:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "torch==2.1.2", "torchaudio==2.1.2", "--index-url", "https://download.pytorch.org/whl/cu118"])
            # subprocess.check_call([sys.executable, "-m", "pip", "install", "torch==2.1.2", "torchaudio==2.1.2"])

    def install_requirements():
        try:
            subprocess.check_call([
                sys.executable, 
                "-m", 
                "pip", 
                "install", 
                "-r", 
                "requirements.txt"
            ], env={**os.environ, "PIP_NO_CACHE_DIR": "0", "PYTHONIOENCODING": "utf-8"})
        except subprocess.CalledProcessError as e:
            console.print(Panel(f"❌ Failed to install requirements: {str(e)}", style="red"))

    def install_noto_font():
        # Detect Linux distribution type
        if os.path.exists('/etc/debian_version'):
            # Debian/Ubuntu systems
            cmd = ['sudo', 'apt-get', 'install', '-y', 'fonts-noto']
            pkg_manager = "apt-get"
        elif os.path.exists('/etc/redhat-release'):
            # RHEL/CentOS/Fedora systems
            cmd = ['sudo', 'yum', 'install', '-y', 'google-noto*']
            pkg_manager = "yum"
        else:
            console.print("⚠️ Unrecognized Linux distribution, please install Noto fonts manually", style="yellow")
            return
            
        try:
            subprocess.run(cmd, check=True)
            console.print(f"✅ Successfully installed Noto fonts using {pkg_manager}", style="green")
        except subprocess.CalledProcessError:
            console.print("❌ Failed to install Noto fonts, please install manually", style="red")

    if platform.system() == 'Linux':
        install_noto_font()
    
    install_requirements()
    check_ffmpeg()
    
    console.print(Panel.fit("Installation completed", style="bold green"))
    console.print("To start the application, run:")
    console.print("[bold cyan]streamlit run st.py[/bold cyan]")
    console.print("[yellow]Note: First startup may take up to 1 minute[/yellow]")
    
    # Add troubleshooting tips
    console.print("\n[yellow]If the application fails to start:[/yellow]")
    console.print("1. [yellow]Check your network connection[/yellow]")
    console.print("2. [yellow]Re-run the installer: [bold]python install.py[/bold][/yellow]")

    # start the application
    subprocess.Popen(["streamlit", "run", "st.py"])

if __name__ == "__main__":
    main()

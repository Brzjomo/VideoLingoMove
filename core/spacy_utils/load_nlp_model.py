import os,sys
import spacy
from spacy.cli import download
from rich import print
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core.config_utils import load_key, get_source_language

SPACY_MODEL_MAP = load_key("spacy_model_map")

def get_spacy_model(language: str):
    key = language.lower()
    model = SPACY_MODEL_MAP.get(key, "en_core_web_md")
    if key not in SPACY_MODEL_MAP:
        print(f"[yellow]Spacy model does not support '{language}', using en_core_web_md model as fallback...[/yellow]")
    return model

def init_nlp():
    # 语言解析刻意放在 try 之外：
    # 1) 旧逻辑"优先 detected_language"会让用户在侧边栏切换识别语言后仍加载
    #    上一个视频的旧语种模型（见 core.config_utils.get_source_language）。
    # 2) 若把 get_source_language() 放进下面的 except，它抛出的
    #    "源语言未知" 会被吞掉并变成一条误导性的 NLP 加载失败信息，
    #    而此时 model 甚至还没赋值。
    language = get_source_language()
    model = get_spacy_model(language)
    try:
        print(f"[blue]⏳ Loading NLP Spacy model: <{model}> ...[/blue]")
        try:
            nlp = spacy.load(model)
        except Exception:
            print(f"[yellow]Downloading {model} model...[/yellow]")
            print("[yellow]If download failed, please check your network and try again.[/yellow]")
            download(model)
            nlp = spacy.load(model)
    except Exception as e:
        raise ValueError(f"❌ Failed to load NLP Spacy model: {model} ({e})")
    print(f"[green]✅ NLP Spacy model loaded successfully![/green]")
    return nlp
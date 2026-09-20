import os,sys,json
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config_utils import load_key, get_source_language, align_allow_rewrite

## ================================================================
# @ step4_splitbymeaning.py
def get_split_prompt(sentence, num_parts = 2, word_limit = 20):
    language = get_source_language()
    split_prompt = f"""
### Role
You are a professional Netflix subtitle splitter in {language}.

### Task
Split the given subtitle text into {num_parts} parts, each less than {word_limit} words.

### Instructions
1. Maintain sentence meaning coherence according to Netflix subtitle standards
2. MOST IMPORTANT: Keep parts roughly equal in length (minimum 3 words each)
3. Split at natural points like punctuation marks or conjunctions
4. If provided text is repeated words, simply split at the middle of the repeated words.
5. STRONGLY prefer clause/sentence boundaries: punctuation first, then connectives
   (Japanese て-form / が / ので / から / けど; English and/but/so/because/which/when).
   NEVER cut in the middle of a word or right after a bare particle. Unequal part lengths
   are acceptable when that keeps each part syntactically whole — a clean clause boundary
   matters more than equal lengths.

### Steps
1. Analyze the sentence structure, complexity, and key splitting challenges
2. Generate two alternative splitting approaches with [br] tags at split positions
3. Compare both approaches, highlighting their strengths and weaknesses
4. Choose the better approach

### Output Format in JSON
{{
    "analysis": "Brief description of sentence structure, complexity, and key splitting challenges",
    "split1": "First splitting approach with [br] tags at split positions",
    "split2": "Alternative splitting approach with [br] tags at split positions",
    "assess": "Comparison of both approaches, highlighting their strengths and weaknesses",
    "choice": "1 or 2"
}}

### Given Text
<split_this_sentence>
{sentence}
</split_this_sentence>

### Your Answer, Provide ONLY a valid JSON object:
""".strip()
    return split_prompt


## ================================================================
# @ step4_1_summarize.py
def get_summary_prompt(source_content, custom_terms_json=None):
    src_lang = get_source_language()
    tgt_lang = load_key("target_language")
    
    # add custom terms note
    terms_note = ""
    if custom_terms_json:
        terms_list = []
        for term in custom_terms_json['terms']:
            terms_list.append(f"- {term['src']}: {term['tgt']} ({term['note']})")
        terms_note = "\n### Existing Terms\nPlease exclude these terms in your extraction:\n" + "\n".join(terms_list)
    
    summary_prompt = f"""
### Role
You are a video translation expert and terminology consultant, specializing in {src_lang} comprehension and {tgt_lang} expression optimization.

### Task
For the provided {src_lang} video text:
1. Summarize main topic in two sentences
2. Extract professional terms/names with {tgt_lang} translations (excluding existing terms)
3. Provide brief explanation for each term{terms_note}

### Steps
1. Topic Summary:
   - Quick scan for general understanding
   - Write two sentences: first for main topic, second for key point
2. Term Extraction:
   - Mark professional terms and names (excluding those listed in Existing Terms)
   - Provide {tgt_lang} translation or keep original
   - Add brief explanation
   - Extract less than 15 terms

### Output Format
Please output your analysis results in the following JSON format, where <> represents placeholders:
{{
    "topic": "Two-sentence video summary",
    "terms": [
        {{
            "src": "{src_lang} term",
            "tgt": "{tgt_lang} translation or original",
            "note": "Brief explanation"
        }},
        ...
    ]
}}

### Example
{{
    "topic": "本视频介绍人工智能在医疗领域的应用现状。重点展示了AI在医学影像诊断和药物研发中的突破性进展。",
    "terms": [
        {{
            "src": "Machine Learning",
            "tgt": "机器学习",
            "note": "AI的核心技术，通过数据训练实现智能决策"
        }},
        {{
            "src": "CNN",
            "tgt": "CNN",
            "note": "卷积神经网络，用于医学图像识别的深度学习模型"
        }}
    ]
}}

### Source Text
<text>
{source_content}
</text>
""".strip()
    return summary_prompt

## ================================================================
# @ step5_translate.py & translate_lines.py
def generate_shared_prompt(previous_content_prompt, after_content_prompt, summary_prompt, things_to_note_prompt):
    return f'''### Context Information
<previous_content>
{previous_content_prompt}
</previous_content>

<subsequent_content>
{after_content_prompt}
</subsequent_content>

### Content Summary
{summary_prompt}

### Points to Note
{things_to_note_prompt}'''

def get_prompt_faithfulness(lines, shared_prompt):
    TARGET_LANGUAGE = load_key("target_language")
    # Split lines by \n
    line_splits = lines.split('\n')
    
    # Create JSON return format example
    json_format = {}
    for i, line in enumerate(line_splits, 1):
        json_format[i] = {
            "origin": line,
            "direct": f"<<direct {TARGET_LANGUAGE} translation>>"
        }
    
    src_language = get_source_language()
    prompt_faithfulness = f'''
### Role Definition
You are a professional Netflix subtitle translator, fluent in both {src_language} and {TARGET_LANGUAGE}, as well as their respective cultures. Your expertise lies in accurately understanding the semantics and structure of the original {src_language} text and faithfully translating it into {TARGET_LANGUAGE} while preserving the original meaning.

### Task Background
We have a segment of original {src_language} subtitles that need to be directly translated into {TARGET_LANGUAGE}. These subtitles come from a specific context and may contain specific themes and terminology.

### Task Description
1. Translate the original {src_language} subtitles into {TARGET_LANGUAGE} line by line
2. Ensure the translation is faithful to the original, accurately conveying the original meaning
3. Consider the context and professional terminology

{shared_prompt}

### Translation Principles
1. Faithful to the original: Accurately convey the content and meaning of the original text, without arbitrarily changing, adding, or omitting content.
2. Accurate terminology: Use professional terms correctly and maintain consistency in terminology.
3. Understand the context: Fully comprehend and reflect the background and contextual relationships of the text.

### Subtitle Data
<subtitles>
{lines}
</subtitles>

### Output Format
Please complete the following JSON data, where << >> represents placeholders that should not appear in your answer, and return your translation results in JSON format:
{json.dumps(json_format, ensure_ascii=False, indent=4)}
'''
    return prompt_faithfulness.strip()


def get_prompt_expressiveness(faithfulness_result, lines, shared_prompt):
    TARGET_LANGUAGE = load_key("target_language")
    json_format = {}
    for key, value in faithfulness_result.items():
        json_format[key] = {
            "origin": value['origin'],
            "direct": value['direct'],
            "reflection": "reflection on the direct translation version",
            "free": f"retranslated result, aiming for fluency and naturalness, conforming to {TARGET_LANGUAGE} expression habits, DO NOT leave empty line here!"
        }

    src_language = get_source_language()
    prompt_expressiveness = f'''
### Role Definition
You are a professional Netflix subtitle translator and language consultant. Your expertise lies not only in accurately understanding the original {src_language} but also in optimizing the {TARGET_LANGUAGE} translation to better suit the target language's expression habits and cultural background.

### Task Background
We already have a direct translation version of the original {src_language} subtitles. Now we need you to reflect on and improve these direct translations to create more natural and fluent {TARGET_LANGUAGE} subtitles.

### Task Description
1. Analyze the direct translation results line by line, pointing out existing issues
2. Provide detailed modification suggestions
3. Perform free translation based on your analysis
4. Do not add comments or explanations in the translation, as the subtitles are for the audience to read

{shared_prompt}

### Translation Analysis Steps
Please use a two-step thinking process to handle the text line by line:

1. Direct Translation Reflection:
   - Evaluate language fluency
   - Check if the language style is consistent with the original text
   - Check the conciseness of the subtitles, point out where the translation is too wordy

2. {TARGET_LANGUAGE} Free Translation:
   - Aim for contextual smoothness and naturalness, conforming to {TARGET_LANGUAGE} expression habits
   - Ensure it's easy for {TARGET_LANGUAGE} audience to understand and accept
   - Adapt the language style to match the video's theme (e.g., use casual language for tutorials, professional terminology for technical content, formal language for documentaries)

### Subtitle Data
<subtitles>
{lines}
</subtitles>

### Output in the following JSON format, repeat "origin" and "direct" in the JSON format
{json.dumps(json_format, ensure_ascii=False, indent=4)}
'''
    return prompt_expressiveness.strip()


## ================================================================
# @ step6_splitforsub.py
#: 对齐提示词第 3 条的两种版本（由 `subtitle.align_allow_rewrite` 选择）。
#: 关键区别只在"允许改什么"：轻改写版允许**移动边界/移动虚词**，严格版一个字都不许动。
#: 两版都不允许"把裸从句补成完整句" —— 用户 2026-09-20 指出："字幕往往几句连起来看才是
#: 完整的句子"，要求每行自足会诱发跨行语义重复与凭空增补，所以第 3 条只约束**衔接是否悬空**。
_ALIGN_RULE3_LIGHT = """**You may only fix how the two cues attach to each other — never complete a cue.**
   A cue is one breath of a sentence: the audience reads part 1 and part 2 in a row, so a bare clause
   as a cue is normal and correct (`…作为自由原型师约6年，` followed by `一直从事手办造型工作。` is a
   good split) — do NOT "improve" it by adding a subject, object or verb. What is wrong is a cue that
   dangles:
   * a part must never **start** with a token that depends on the previous part: a floating particle
     (て/で/に/を/は/が/の), a stranded connective (也/而/但/却/就/还/又/并且/而且/然后), or a bare
     "and/but/or/so/which/that/who";
   * a part must never **end** in the middle of a phrase (modifier | head noun, preposition | object,
     a dangling 的/の).
   Fix those two cases by **moving the boundary** — or by moving one function word from one side to the
   other. Beyond that you may only: drop a connective that would otherwise be stranded at a cue edge,
   and add at most one **function word / auxiliary** (也/已经/了/着, "also"/"already") when the cue is
   unidiomatic without it. You may NOT add content: no subject, object, verb, tense meaning,
   explanation or summary that the {target_language} Original does not have — and never restate in
   part 2 what part 1 already says.
   * keep the concatenated length close to the {target_language} Original (within about ±20%);
   * a verbatim split is the default and is always valid: when both cues attach correctly, split
     literally without changing a single word."""

_ALIGN_RULE3_STRICT = """**DO NOT rewrite, add or drop a single word.** Concatenating all `target_part_*` in order MUST
   reproduce the {target_language} Original **character for character** (punctuation aside). The
   audience reads part 1 and part 2 in a row, so partial sentences are fine and a bare clause as a cue
   is normal. Choose a better boundary if the current one leaves a dangling cue — moving the boundary
   is not rewriting — but never change, repeat, add or drop words. In particular never duplicate a
   connective at the boundary (an extra "同时"/"also"/"そして" in part 2 because part 1 already ended
   with it)."""


def get_align_prompt(src_sub, tr_sub, src_part):
    TARGET_LANGUAGE = load_key("target_language")
    src_language = get_source_language()
    src_splits = src_part.split('\n')
    num_parts = len(src_splits)
    src_part = src_part.replace('\n', ' [br] ')
    align_prompt = '''
### Role Definition
You are a Netflix subtitle alignment expert fluent in both {src_language} and {target_language}.

### Task Background
We have {src_language} and {target_language} original subtitles for a Netflix program, as well as a pre-processed split version of {src_language} subtitles. Your task is to create the best splitting scheme for the {target_language} subtitles based on this information.

### Task Description
1. Analyze the word order and structural correspondence between {src_language} and {target_language} subtitles
2. Split the {target_language} subtitles according to the pre-processed {src_language} split version
3. {rule3}
4. Never leave empty lines.
5. **NEVER cut inside a noun phrase — above all never between a modifier (relative clause / adjective)
   and the head noun it modifies**: e.g. Japanese `…作ることができる | ソフト`, Chinese `…的数字 | 软件`,
   English `…the digital | software`. If the {target_language} word order puts the head noun on the other
   side of the boundary, move the cut to the nearest whole-phrase boundary; unequal part lengths are
   acceptable, a broken phrase is not.
6. Keep each part reasonably sized: at least ~3 words (3–4 characters in CJK). Never leave a single
   short word alone in a cue unless the corresponding source part is that short too.
7. Each part must be **parsable** on its own: never start a part with a dependent particle or a
   stranded connective left over from the previous part, and never end a part mid-phrase. Being an
   *incomplete sentence* is fine — being unparseable is not.
8. Do not add comments or explanations in the translation, as the subtitles are for the audience to read

### Subtitle Data
<subtitles>
{src_language} Original: "{src_sub}"
{target_language} Original: "{tr_sub}"
Pre-processed {src_language} Subtitles ([br] indicates split points): {src_part}
</subtitles>

### Output in JSON
{{
    "analysis": "Brief analysis of word order, structure, and semantic correspondence between {src_language} and {target_language} subtitles",
    "align": [
        {align_parts_json}
    ]
}}

### Your Answer, Provide ONLY a valid JSON object:
'''

    align_parts_json = ','.join(
        f'''
        {{
            "src_part_{i+1}": "{src_splits[i]}",
            "target_part_{i+1}": "Corresponding aligned {TARGET_LANGUAGE} subtitle part"
        }}''' for i in range(num_parts)
    )

    # 第 3 条按开关分支：轻改写版允许"移动边界/移动虚词"，严格版一个字都不许动。
    # 两个版本都以 {target_language} 为占位符，所以在传进 format 之前先各自 format 一次。
    rule3 = (_ALIGN_RULE3_LIGHT if align_allow_rewrite() else _ALIGN_RULE3_STRICT)

    return align_prompt.format(
        src_language=src_language,
        target_language=TARGET_LANGUAGE,
        src_sub=src_sub,
        tr_sub=tr_sub,
        src_part=src_part,
        align_parts_json=align_parts_json,
        rule3=rule3.format(target_language=TARGET_LANGUAGE),
    )

## ================================================================
# @ core/subtitle_trim.py（配音链路已移除，此提示词仅服务于字幕压缩）
def get_subtitle_trim_prompt(text, duration):
 
    rule = '''Consider a. Reducing filler words without modifying meaningful content. b. Omitting unnecessary modifiers or pronouns, for example:
    - "Please explain your thought process" can be shortened to "Please explain thought process"
    - "We need to carefully analyze this complex problem" can be shortened to "We need to analyze this problem"
    - "Let's discuss the various different perspectives on this topic" can be shortened to "Let's discuss different perspectives on this topic"
    - "Can you describe in detail your experience from yesterday" can be shortened to "Can you describe yesterday's experience" '''

    trim_prompt = '''
### Role
You are a professional subtitle editor, editing and optimizing lengthy subtitles that exceed voiceover time before handing them to voice actors. Your expertise lies in cleverly shortening subtitles slightly while ensuring the original meaning and structure remain unchanged.

### Subtitle Data
<subtitles>
Subtitle: "{text}"
Duration: {duration} seconds
</subtitles>

### Processing Rules
{rule}

### Processing Steps
Please follow these steps and provide the results in the JSON output:
1. Analysis: Briefly analyze the subtitle's structure, key information, and filler words that can be omitted.
2. Trimming: Based on the rules and analysis, optimize the subtitle by making it more concise according to the processing rules.

### Output in JSON
{{
    "analysis": "Brief analysis of the subtitle, including structure, key information, and potential processing locations",
    "result": "Optimized and shortened subtitle in the original subtitle language"
}}

### Your Answer, Provide ONLY a valid JSON object:
'''.strip()
    return trim_prompt.format(
        text=text,
        duration=duration,
        rule=rule
    )


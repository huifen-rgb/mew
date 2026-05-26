# ==========================

# app_visual_director_v24_2_core_patch.py

# 核心 PATCH（直接貼進 app.py）

# ==========================

import re

# =========================================================

# v24.2 HEADLINE COMPILER

# =========================================================

def extract_headline_block(script: str):
lines = script.splitlines()

```
capture = False
headline_lines = []

for raw in lines:
    line = raw.rstrip()

    if line.strip().startswith("大標:"):
        capture = True

        after = line.split("大標:", 1)[1].strip()

        if after:
            headline_lines.append(after)

        continue

    if capture:
        if not line.strip():
            break

        if line.strip().startswith("("):
            break

        headline_lines.append(line.strip())

headline_lines = [x for x in headline_lines if x.strip()]

return headline_lines
```

def compile_headline(script: str, frame_type: str):
headline_lines = extract_headline_block(script)

```
if not headline_lines:
    return {
        "mode": "single",
        "lines": []
    }

# =====================================================
# 標大框 = 強制保留兩行
# =====================================================
if frame_type == "標大框":

    if len(headline_lines) == 1:
        text = headline_lines[0]

        half = max(1, len(text) // 2)

        headline_lines = [
            text[:half],
            text[half:]
        ]

    return {
        "mode": "2line",
        "lines": headline_lines[:2]
    }

# =====================================================
# 框訊 = 強制單行
# =====================================================
if frame_type == "框訊":

    merged = " ".join(headline_lines)

    merged = re.sub(r"\s+", " ", merged)

    return {
        "mode": "single",
        "lines": [merged]
    }

return {
    "mode": "single",
    "lines": headline_lines[:1]
}
```

# =========================================================

# HIGHLIGHT GRAMMAR

# =========================================================

def apply_highlight_grammar(text: str):

```
text = re.sub(
    r"<([^>]+)>",
    r'<span class="shock-keyword">\\1</span>',
    text
)

text = re.sub(
    r'"([^\"]+)"',
    r'<span class="highlight-keyword">\\1</span>',
    text
)

text = re.sub(
    r"\(([^\)]+)\)",
    r'<span class="emotion-keyword">\\1</span>',
    text
)

return text
```

# =========================================================

# SLOT PARSER

# =========================================================

def parse_editor_tokens(script: str):

```
tokens = []

lines = script.splitlines()

for line in lines:

    text = line.strip()

    # IMAGE SLOT
    if re.match(r"^\(.+圖\)$", text):

        tokens.append({
            "type": "image_slot",
            "label": text
        })

    # QUOTE BOX
    elif "對話框" in text:

        tokens.append({
            "type": "quote_box",
            "label": text
        })

    # COLOR CARD
    elif "色塊" in text:

        tokens.append({
            "type": "color_card",
            "label": text
        })

    # FLOW
    elif "箭頭" in text:

        tokens.append({
            "type": "flow_arrow",
            "label": text
        })

return tokens
```

# =========================================================

# HTML HEADLINE RENDER

# =========================================================

def render_headline_html(headline):

```
if headline["mode"] == "2line":

    line1 = apply_highlight_grammar(headline["lines"][0])
    line2 = apply_highlight_grammar(headline["lines"][1])

    return f'''
    <div class="mega-headline two-line">
        <div class="headline-line line-1">{line1}</div>
        <div class="headline-line line-2">{line2}</div>
    </div>
    '''

line = apply_highlight_grammar(headline["lines"][0])

return f'''
<div class="mega-headline single-line">
    <div class="headline-line">{line}</div>
</div>
'''
```

# =========================================================

# CONSTRAINT LAYOUT PROMPT

# =========================================================

CONSTRAINT_LAYOUT_PROMPT = """

[HEADLINE CONSTRAINTS]

標大框:

* headline MUST ALWAYS stay TWO LINES
* preserve original user line breaks
* NEVER merge headline lines
* NEVER auto-wrap
* NEVER auto-compress into single line
* headline occupies top 35~45% of canvas

框訊:

* headline MUST ALWAYS stay SINGLE LINE
* NEVER split headline into multiple rows

[SLOT CONSTRAINTS]

* NEVER invent additional image frames
* ONLY render image slots explicitly declared by user
* NEVER auto-generate extra white frames
* Preserve user sketch structure
* Preserve user layout flow

[ROLL CONSTRAINTS]

* main roll remains dominant
* roll cannot be split into multiple boxes
* no cards may overlap roll

"""

# =========================================================

# 使用方式

# =========================================================

# frame_type = auto_detect_frame_type(script)

# headline = compile_headline(script, frame_type)

# tokens = parse_editor_tokens(script)

# headline_html = render_headline_html(headline)

# final_prompt = CONSTRAINT_LAYOUT_PROMPT + original_prompt

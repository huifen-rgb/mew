from __future__ import annotations

# -*- coding: utf-8 -*-
"""
Visual Director v22｜Senior News CG Designer Mode

重點：
1. 保留 Gemini API 串接
2. 保留框訊模板庫
3. 保留完整 HOLE_PUNCHER_V66，不簡化
4. v20.3 導演系統：自動判斷版型大類、記者說新聞子類型、風格、密度、語氣、必要圖區
5. 結論模組：自動偵測結論句，判斷筆刷 / 蓋章 / 不使用，並給出安全位置
6. 記者說新聞子類型可手動覆寫，並直接影響 CG preview 與 prompt
7. 加入安全區硬邊界、資訊密度控制、視覺層級、結論模組、防呆檢查
8. 所有圖區在成品必須刪除標籤文字，且文字、icon、UI、蓋章、筆刷不得壓入
"""

import os
import re
import textwrap
import html
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    import google.generativeai as genai
except Exception:
    genai = None

NL = chr(10)

# =========================================================
# v22：雙模型手選 + 20年新聞台CG美術總監模式 + 只排版不腦補 + Prompt 成本監控 + 防亂生文字稽核 + Asset Protection Zone + 中文白名單 + AI自由風格排版 + Explicit Brush Only + Zero Assumption
# =========================================================
AI_MODELS: Dict[str, str] = {
    "💸 最省｜Gemini 3.1 Flash Lite Preview": "gemini-3.1-flash-lite-preview",
    "⚡ 中間｜Gemini 2.5 Flash": "gemini-2.5-flash",
}

UI_FRAME_OPTIONS = ["標大框", "框訊", "記者說新聞"]
ASSET_ASPECT_OPTIONS = ["AI自動配置排版", "4:3 橫式素材框", "4:5 直式素材框"]


def normalize_frame_for_ui(frame_type: str) -> str:
    """把內部框訊細分類收斂成 UI 只顯示的三大類。"""
    if frame_type.startswith("框訊"):
        return "框訊"
    if frame_type == "記者說新聞":
        return "記者說新聞"
    return "標大框"


def resolve_frame_for_engine(ui_frame_type: str, script: str = "") -> str:
    """
    UI 只讓使用者選三種；內部仍沿用 v19.6 的細分類引擎。
    - 標大框：不動，維持原本 MEGA LARGE 兩行大標邏輯。
    - 記者說新聞：不動，維持 subtype / safe zone / 結論模組。
    - 框訊：交給 AI/規則自動判斷多圖對比、對打時間軸、數據分析、流程關係。
    """
    if ui_frame_type == "框訊":
        detected = auto_detect_frame_type(script) if script.strip() else "框訊・多圖對比"
        if detected == "記者說新聞" or detected == "標大框":
            return "框訊・多圖對比"
        return detected
    return ui_frame_type

def resolve_asset_aspect(aspect_label: str) -> Dict[str, str]:
    """ROLL / 圖區版面尺寸選項。"""
    if "AI" in str(aspect_label) or "自動" in str(aspect_label):
        return {
            "label": "AI自動配置排版",
            "ratio": "auto",
            "css_class": "ratio-auto",
            "directive": "AI may choose proportions only for the existing user-requested protected blank zones. Choose ratio silently by material type: horizontal for standard footage, vertical for portrait/mobile material, wider for document/evidence boards, and mixed ratios when multiple asset types appear. Do not create any new media/photo/video zones, do not write ratio text on screen, and never distort the 1920x1080 canvas.",
        }
    if "4:5" in str(aspect_label):
        return {
            "label": "4:5 直式素材框",
            "ratio": "4:5",
            "css_class": "ratio-45",
            "directive": "Use 4:5 vertical portrait ratio for all ROLL/photo/video asset zones. Keep each protected zone tall and vertical, suitable for portrait photos, screenshots, or mobile-style visuals.",
        }
    return {
        "label": "4:3 橫式素材框",
        "ratio": "4:3",
        "css_class": "ratio-43",
        "directive": "Use 4:3 horizontal landscape ratio for all ROLL/photo/video asset zones. Keep each protected zone wide and stable, suitable for standard video stills and news footage.",
    }


def resolve_asset_aspect_for_tag(tag: str, global_aspect: str) -> Dict[str, str]:
    """
    v22.3：支援單一圖區覆寫比例。
    使用方式：在圖區標籤內寫 4:3 或 4:5，例如：
    [圖-左ROLL 4:3]、[圖-右人物 4:5]、(#定監視器畫面 4:3)、(LINE截圖 4:5)
    未標註的圖區才吃全域選項。
    """
    text = str(tag or "")
    if re.search(r"4\s*[:：]\s*5", text):
        return resolve_asset_aspect("4:5 直式素材框")
    if re.search(r"4\s*[:：]\s*3", text):
        return resolve_asset_aspect("4:3 橫式素材框")
    return resolve_asset_aspect(global_aspect)

STRICT_NO_EXTRA_FACTS = """
【防亂生文字規則｜絕對遵守】
- 嚴禁新增使用者未提供的人名、地名、機構名、數字、日期、事件、背景、原因、結論。
- 嚴禁為了畫面好看而補寫新聞內容、補標題、補小標、補說法。
- 只能重排、精簡、分類、標註使用者原文中已存在的資訊。
- 若原文沒有的資訊，請留空或使用「未提供」，不得自行推測。
- [圖]、[圖-xxx]、(#定xxx)、(定xxx圖) 只代表留白圖區，不得替它補照片說明文字。
"""



# =========================================================
# 0. Streamlit 基本設定
# =========================================================
st.set_page_config(
    page_title="Visual Director v22｜Senior News CG Designer",
    layout="wide",
    initial_sidebar_state="expanded",
)


# =========================================================
# 1. 風格庫
# =========================================================
STYLE_CONFIG: Dict[str, Dict[str, str]] = {
    "民生消費 (Fluid Analytics)": {
        "theme": "Consumer Lifestyle Trends",
        "ui": "Organic fluid shapes, Frosted glass panels, Soft depth shadows",
        "palette": "Soft Beige, Lifestyle Blue",
        "highlight": "Vibrant Sunburst Orange",
    },
    "社會案件 (Justice Alert)": {
        "theme": "Crime Scene Noir",
        "ui": "CCTV grain textures, High-contrast forensic lighting, Caution tape motifs",
        "palette": "Concrete Grey, Police Blue",
        "highlight": "Safety Orange",
    },
    "體育競技 (Victory Orange)": {
        "theme": "High-Energy Sports Broadcast",
        "ui": "Carbon fiber textures, Kinetic speed lines, Stadium spotlights",
        "palette": "Graphite Grey, Stark White",
        "highlight": "Electric Orange",
    },
    "全球財經 (Elite Obsidian)": {
        "theme": "High-end Financial Dashboard",
        "ui": "Brushed Aluminum frames, Holographic data streams, Prism glass refractions",
        "palette": "Deep Navy, Gold",
        "highlight": "Electric Cyan",
    },
    "突發重磅 (Breaking Alert)": {
        "theme": "Emergency Alert High-Gloss",
        "ui": "Radial motion blur, Glossy UI panels with internal red glow",
        "palette": "Signal Red, Black",
        "highlight": "Bright Vivid Yellow",
    },
    "選情政論 (Democracy Grey)": {
        "theme": "Political Election Studio",
        "ui": "Matte Metallic finishes, Star patterns, Marble textures, Studio spotlights",
        "palette": "Slate Grey, Navy Blue",
        "highlight": "Vibrant Scarlet Red",
    },
    "科技政策 (Cyber Policy)": {
        "theme": "Digital Policy & Tech Hub",
        "ui": "Poly-grid overlays, Ray-traced glass, Semi-transparent data nodes",
        "palette": "Steel Blue, Silver",
        "highlight": "Neon Cyan",
    },
    "綠能永續 (Eco-Future)": {
        "theme": "Sustainability & ESG Focus",
        "ui": "Natural leaf vein textures, Soft outdoor bokeh, Organic glass panels",
        "palette": "Emerald Green, Leaf Green",
        "highlight": "Sunlight Gold",
    },
    "現代民俗 (Modern Festive)": {
        "theme": "Modern Folk Aesthetic",
        "ui": "Lacquered wood finish, Silk textures, Traditional cloud patterns",
        "palette": "Vermilion Red, Deep Charcoal",
        "highlight": "Imperial Gold",
    },
    "生醫科技 (Clinical White)": {
        "theme": "Medical Innovation",
        "ui": "Sanitized surfaces, DNA helix motifs, Hexagonal laboratory grids",
        "palette": "Pristine White, Navy Blue",
        "highlight": "Bright Sky Blue",
    },
}


# =========================================================
# 2. 框訊模板庫
# =========================================================
FRAME_TEMPLATES: Dict[str, Dict[str, str]] = {
    "框訊": {
        "summary": "框訊整併入口；使用者只選框訊，內部自動判斷多圖對比、對打時間軸、數據分析、流程關係。",
        "structure": "AI decides the most suitable broadcast information-card layout based on content; preserve all image placeholders as protected blank zones.",
        "recommended_tags": "[圖-左主] / [圖-右主] / (色塊) / (對話框) / (數據框) / #筆刷",
    },
    "記者說新聞": {
        "summary": "記者說新聞解釋型 CG，常見左右欄資訊拆解；右下角必鎖跑馬安全區。",
        "structure": "Top headline always anchored at the very top; left explanation/data/image column; right analysis/reason column; bottom-right ticker safe zone locked.",
        "recommended_tags": "[放左邊] / [放右邊] / [圖---素材] / [日曆效果] / [icon] / [蓋章效果]",
    },
    "標大框": {
        "summary": "上方超大標題，下方主圖＋資訊卡，適合衝突型事件整理。",
        "structure": "Top headline 30-40%; headline always anchored at the very top; lower content modules; main image zone usually left or right; strong headline dominance.",
        "recommended_tags": "[圖-左主] / [圖-右主] / (色塊) / (對話框) / #筆刷",
    },
    "框訊・多圖對比": {
        "summary": "多個真實素材區＋右下對比模組，適合工程、政策、爭議拆解。",
        "structure": "Top headline always anchored at the very top; upper multi-image row; left main image; right quote; bottom comparison module.",
        "recommended_tags": "[圖-上左] / [圖-上中] / [圖-上右] / [圖-左主] / [模組-對比]",
    },
    "框訊・對打時間軸": {
        "summary": "人物攻防＋事件時間軸＋主畫面，適合政論攻防。",
        "structure": "Top headline always anchored at the very top; people quote zones; right main image; bottom timeline images; conclusion quote.",
        "recommended_tags": "[圖-右主] / [圖-左人A] / [圖-中人B] / [圖-下左] / [圖-下中] / (大對話框)",
    },
    "框訊・數據分析": {
        "summary": "以數據框、重點色塊、專家說法為主，圖片不是主體。",
        "structure": "Top headline always anchored at the very top; left narrative and data blocks; right expert quote; bottom conclusion data block.",
        "recommended_tags": "(數據框) / (色塊-主敘事) / [圖-右人] / #筆刷",
    },
    "框訊・流程關係": {
        "summary": "角色群組、分支節點、關係線，適合招標、利益迴避、組織關係。",
        "structure": "Top headline always anchored at the very top; left relationship diagram; right main image; bottom conclusion and data.",
        "recommended_tags": "[圖-右主] / [群組-評審] / (關係-節點A) / (關係-節點B) / (數據框)",
    },
}


AI_FREE_STYLE_NAME = "AI自由創作風格（不受固定風格庫限制）"

def get_style_config(style_name: str) -> Dict[str, str]:
    """取得題材風格設定。
    v22.7 起：style_name 代表「新聞題材風格 / WHAT」，不再拿 AI自由模式覆蓋。
    AI自由變化改由 visual_variation_mode 控制「畫法 / HOW」。
    """
    return STYLE_CONFIG.get(style_name, STYLE_CONFIG["民生消費 (Fluid Analytics)"])


def build_visual_variation_policy(style_name: str, visual_variation_mode: str) -> str:
    """把「自動判定風格」與「AI自由變化」拆開：
    - style_name：社會案件 / 財經 / 民生等題材框架。
    - visual_variation_mode：在同一題材框架內，固定套版或自由變化底圖、構圖、材質。
    """
    style = get_style_config(style_name)
    if str(visual_variation_mode).startswith("AI自由"):
        return f"""
[VISUAL VARIATION MODE｜AI自由變化但不脫離題材]
CONTENT STYLE LOCK: {style_name}
- Keep the semantic base of this detected/selected news style.
- Theme base: {style['theme']}
- Texture base: {style['ui']}
- Palette base: {style['palette']} / accent {style['highlight']}
- AI may freely vary the background composition, lighting, texture, card shapes, depth, gradients, visual rhythm, and headline treatment WITHIN this content style.
- Do not make every output look like the same template.
- Do not jump to unrelated genres or unrelated moods.
- Freedom applies only to visual treatment; never add facts, words, labels, people, logos, photos, or extra asset boxes.
""".strip()
    return f"""
[VISUAL VARIATION MODE｜固定風格庫]
CONTENT STYLE LOCK: {style_name}
- Use the selected Visual Director style library consistently.
- Theme: {style['theme']}
- UI texture: {style['ui']}
- Palette: {style['palette']}
- Highlight: {style['highlight']}
- Keep the visual result stable and close to the preset style.
""".strip()


@dataclass
class ParsedInput:
    title: str
    body: str
    image_tags: List[str]
    module_tags: List[str]
    warnings: List[str]


# =========================================================
# 3. v18 導演系統：版型 / 子類型 / 風格 / 密度 / 語氣 / 防呆
# =========================================================
def _contains_any(script: str, words: List[str]) -> bool:
    return any(word in script for word in words)


ASSET_PROTECTION_KEYWORDS = [
    "圖", "圖片", "定圖", "定", "開框", "ROLL", "roll", "Roll", "LINE截圖", "截圖",
    "監視器", "畫面", "外觀照", "照片", "空拍", "地圖", "示意", "素材", "影像",
]

ROLL_ALIAS_RE = re.compile(
    r"(?:開框\s*)?(?:左|右|中|中央|左邊|右邊|中間)?\s*(?:ROLL|roll|Roll)\s*(?:框)?",
    re.IGNORECASE,
)

def _has_roll_alias(text: str) -> bool:
    """支援 (開框roll)、++ROLL++、左ROLL=、右ROLL=、右邊 roll框 4:5 等寫法。"""
    raw = str(text or "")
    compact = re.sub(r"[+＋#＃\-—_＝=＊*\s()（）\[\]【】]+", "", raw)
    if re.search(r"(?:開框)?(?:左|右|中|中央)?(?:ROLL|roll|Roll)(?:框)?", compact, flags=re.IGNORECASE):
        return True
    return bool(ROLL_ALIAS_RE.search(raw))


def is_asset_protection_tag(tag: str) -> bool:
    """
    v20.5.2：判斷新聞台素材保護區。
    這些不是要畫出來的文字，而是後製塞真實照片/影片/截圖的硬留白框。
    例如：(#定圖)、(圖片)、(定國防部外觀照)、(#開框roll)、(LINE截圖)。
    """
    if not tag:
        return False
    t = tag.strip()
    if _has_roll_alias(t):
        return True
    if t.startswith("[圖"):
        return True
    if "#定" in t or "＃定" in t or "#開框" in t or "＃開框" in t:
        return True
    if any(k in t for k in ASSET_PROTECTION_KEYWORDS):
        # 避免把「圖利」這類純文字誤判成圖區。
        if "圖利" in t and not any(x in t for x in ["定", "圖", "圖片", "ROLL", "截圖", "畫面"]):
            return False
        return True
    return False



def _canonical_asset_zone_key(tag: str) -> str:
    """
    v22.9：素材區去重 key。
    同一個原始註記可能同時被括號 parser、ROLL alias parser、spatial parser 抓到，
    例如 (左ROLL 4:5=) 會變成 (左ROLL 4:5=) 與 ((左ROLL 4:5=))。
    這裡把外層符號、比例、等號、加號等都正規化，確保同一個 ROLL/圖區只算一次。
    """
    raw = str(tag or "").strip()
    raw = raw.replace("（", "(").replace("）", ")").replace("＋", "+").replace("＝", "=").replace("：", ":")
    # 去掉重複外層括號/方括號
    previous = None
    while raw and raw != previous:
        previous = raw
        raw = raw.strip()
        if (raw.startswith("(") and raw.endswith(")")) or (raw.startswith("[") and raw.endswith("]")):
            raw = raw[1:-1].strip()
    raw = _normalize_spatial_alias_text(raw) if '_normalize_spatial_alias_text' in globals() else raw
    raw = re.sub(r"\s+", "", raw)
    raw = re.sub(r"[+＋#＃\-—_＝=＊*]+", "", raw)
    raw = re.sub(r"4\s*[:：]\s*[35]", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"(?i)roll", "ROLL", raw)
    raw = raw.replace("邊", "")
    return raw.lower()


def _dedupe_asset_zone_list(zones: List[str]) -> List[str]:
    """保留順序去重；同一個原始素材註記只准產生一個 protected blank zone。"""
    deduped: List[str] = []
    seen = set()
    for z in zones:
        if not z:
            continue
        key = _canonical_asset_zone_key(z)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(str(z).strip())
    return deduped


def _is_control_or_effect_visible_text(line: str) -> bool:
    """不可進 Approved visible text 的控制字 / 特效字 / 欄位字。"""
    raw = str(line or "").strip()
    if not raw:
        return True
    compact = re.sub(r"\s+", "", raw)
    if re.fullmatch(r"(主標|大標|標題|標|小標)\s*[=:：＝]?", raw):
        return True
    if re.fullmatch(r"(左|右|中|中央|上|下|左上|左下|右上|右下|中上|中下)\s*[=:：＝]?", raw):
        return True
    if re.search(r"(打卡符號|爆炸效果|閃光效果|特效|效果|筆刷效果|蓋章效果)", raw):
        return True
    if _is_layout_helper_line(raw) or _has_roll_alias(raw):
        return True
    if re.fullmatch(r"[()（）!！\s]+", raw):
        return True
    # 比例/placeholder/內部提示字不可進白名單
    if re.search(r"(4\s*[:：]\s*[35]|ROLL|roll|圖區|圖片區|編輯圖片區|placeholder|image box)", raw, flags=re.IGNORECASE):
        return True
    return False

def _count_image_intents(script: str) -> int:
    markers = ["[圖", "(#定", "(＃定", "(定", "(圖片", "圖片", "截圖", "ROLL", "roll", "Roll", "左ROLL", "右ROLL", "開框ROLL", "開框roll", "++ROLL", "定圖", "外觀照", "開框"]
    return sum(script.count(marker) for marker in markers)


def is_reporter_news(script: str) -> bool:
    """記者說新聞不是看左右欄，而是看『把資訊講清楚』的內容結構。"""
    score = 0
    if _contains_any(script, ["原因", "影響", "效應", "因為", "導致", "因素", "反思", "觀點", "視角", "回望", "看到", "難怪", "就是"]):
        score += 1
    if _contains_any(script, ["排名", "消費", "億", "%", "表格", "門檻", "扣除額", "年所得", "新制", "政策", "調高", "減除"]):
        score += 1
    if script.count("●") >= 3 or script.count("*") >= 2 or _contains_any(script, ["以下請出成表格", "以下●請分別出一框"]):
        score += 1
    if _contains_any(script, ["太空人", "任務", "拍下", "導師", "黃金週", "旅遊", "海外旅遊", "赴日", "綜所稅", "免稅"]):
        score += 1
    return score >= 2


def detect_reporter_subtype(script: str) -> str:
    if _contains_any(script, ["表格", "免稅門檻", "扣除額", "年所得", "以下請出成表格"]):
        return "表格數據型"
    if script.count("●") >= 4 or _contains_any(script, ["以下●請分別出一框", "六大新制"]):
        return "卡片條列型"
    if _contains_any(script, ["看到", "回望", "拍下", "難怪", "就是", "凝視", "反思", "太空人"]):
        return "敘事觀點型"
    return "左右解釋型"


def density_score(script: str) -> str:
    length = len(script.strip())
    modules = max(1, script.count("●") + script.count("*") + _count_image_intents(script) + script.count("["))
    score = length / modules
    if score >= 85:
        return "高密度"
    if score >= 45:
        return "中密度"
    return "低密度"


def auto_detect_tone(script: str, frame_type: str) -> str:
    if frame_type == "記者說新聞":
        return "口語解釋型"
    if _contains_any(script, ["爆", "怒", "控", "痛批", "驚", "涉", "疑雲", "法律戰"]):
        return "強烈衝突型"
    return "穩重資訊型"


def has_explicit_brush_tag(script: str) -> bool:
    """v20.6.3 CORE LAW：只有使用者明確標註筆刷，才允許生成筆刷效果。"""
    if not script:
        return False
    patterns = [
        r"\(#?筆刷[^)]*\)",
        r"\[筆刷[^\]]*\]",
        r"[#＃]筆刷",
        r"---+\s*筆刷",
        r"筆刷效果",
    ]
    return any(re.search(p, script) for p in patterns)


def has_explicit_stamp_tag(script: str) -> bool:
    """只有明確標註蓋章，才允許生成蓋章效果。"""
    if not script:
        return False
    patterns = [
        r"\(#?蓋章[^)]*\)",
        r"\[蓋章[^\]]*\]",
        r"[#＃]蓋章",
        r"---+\s*蓋章",
        r"蓋章效果",
    ]
    return any(re.search(p, script) for p in patterns)


EXPLICIT_BROADCAST_UI_PATTERNS = [
    r"\(#?跑馬[^)]*\)",
    r"\(#?快訊[^)]*\)",
    r"\(#?ticker[^)]*\)",
    r"\(#?LIVE[^)]*\)",
    r"\(#?台標[^)]*\)",
    r"\(#?時間[^)]*\)",
    r"\(#?日期[^)]*\)",
    r"\(#?lower[-_ ]?third[^)]*\)",
    r"[#＃](跑馬|快訊|ticker|LIVE|台標|時間|日期|lower[-_ ]?third)",
]

def has_explicit_broadcast_ui_tag(script: str) -> bool:
    """v20.6.4 CORE LAW：只有使用者明確標註，才允許生成跑馬/快訊/LIVE/台標/時間等電視 UI。"""
    if not script:
        return False
    return any(re.search(p, script, flags=re.IGNORECASE) for p in EXPLICIT_BROADCAST_UI_PATTERNS)


def build_zero_assumption_policy(script: str = "") -> str:
    """禁止圖片模型自行補完整電視畫面 UI。內容 logo 仍可依使用者原稿明確提供處理。"""
    return """
[CORE LAW #03｜ZERO ASSUMPTION MODE]
- 不得自行推論、補齊或裝飾任何使用者未提供的電視台 UI。
- 禁止自行生成：跑馬、快訊、BREAKING NEWS、LIVE、台標、頻道 logo、時間、日期、浮水印、lower-third、字幕條、新聞爬蟲、底部資訊帶。
- 只有使用者明確標註 (#跑馬)、(#快訊)、(#ticker)、(#LIVE)、(#台標)、(#時間)、(#日期)、(#lower-third) 才允許生成相對應 UI。
- 若未標註：寧可留白或只放背景，不得腦補。
- EMPTY > ASSUMPTION. Never decorate automatically.
""".strip()


def audit_extra_ui(text: str) -> Dict[str, Any]:
    """檢查文字/Prompt 內是否出現未授權的常見電視 UI 詞。"""
    raw = text or ""
    allowed = has_explicit_broadcast_ui_tag(raw)
    risky_terms = [
        "BREAKING", "Breaking", "breaking", "LIVE", "Live", "live",
        "快訊", "即時頭條", "頭條", "跑馬", "ticker", "Ticker",
        "NEWS", "News", "新聞爬蟲", "字幕條", "lower-third", "watermark", "浮水印",
    ]
    found = sorted(set(term for term in risky_terms if term in raw))
    return {
        "status": "warning" if found and not allowed else "ok",
        "found_terms": found,
        "allowed_by_user_tag": allowed,
        "note": "若未明確標註跑馬/快訊/LIVE/台標/時間等 UI，Gemini Image Prompt 不應自行生成。",
    }


def render_ui_audit(audit: Optional[Dict[str, Any]]) -> None:
    st.markdown("### 🧯 UI 零推論稽核")
    if not audit:
        st.caption("尚未執行 UI 稽核。")
        return
    if audit.get("status") == "ok":
        st.success("✅ 未偵測到未授權的跑馬／快訊／LIVE／台標等 UI。")
    else:
        st.warning("⚠ 偵測到疑似未授權的電視 UI 詞，請確認是否為你明確標註。")
        st.write("、".join(audit.get("found_terms", [])))
    st.caption(audit.get("note", ""))


def _clean_effect_text(line: str) -> str:
    """移除效果標籤本身，保留真正要上畫面的文字。"""
    cleaned = line
    tokens = [
        "[蓋章效果]", "[筆刷效果]", "(#蓋章)", "(#筆刷)", "(蓋章)", "(筆刷)",
        "#蓋章", "#筆刷", "---蓋章", "---筆刷", "--------蓋章", "--------筆刷",
        "蓋章效果", "筆刷效果",
    ]
    for token in tokens:
        cleaned = cleaned.replace(token, "")
    cleaned = re.sub(r"\([^)]*(?:蓋章|筆刷)[^)]*\)", "", cleaned)
    cleaned = cleaned.strip('-—= ：:[]\" ')
    return cleaned


def extract_conclusion_candidates(script: str) -> List[str]:
    """v20.6.3：只抓使用者明確標註筆刷/蓋章的句子；不再從普通內文自動抽成筆刷。"""
    candidates: List[str] = []
    lines = [line.strip() for line in script.splitlines() if line.strip()]
    for line in lines:
        if re.search(r"(筆刷|蓋章)", line):
            cleaned = _clean_effect_text(line)
            if 2 <= len(cleaned) <= 40:
                candidates.append(cleaned)
    return list(dict.fromkeys(candidates))[:3]


def detect_conclusion_module(script: str, frame_type: str, reporter_subtype: str = "") -> Dict[str, str]:
    """v20.6.3：效果模組採 explicit only；沒有標註筆刷/蓋章就不自動產生。"""
    explicit_brush = has_explicit_brush_tag(script)
    explicit_stamp = has_explicit_stamp_tag(script)
    candidates = extract_conclusion_candidates(script)
    sentence = candidates[0] if candidates else ""

    if not explicit_brush and not explicit_stamp:
        return {
            "type": "不使用",
            "sentence": "",
            "position": "不產生結論模組",
            "reason": "未標註筆刷或蓋章；v20.6.3 禁止 AI 自行把內文升級成筆刷/蓋章",
        }

    module_type = "蓋章" if explicit_stamp else "筆刷"

    if frame_type == "記者說新聞":
        if reporter_subtype == "表格數據型":
            position = "表格下方左側或中下方；必須避開右下跑馬安全區"
        elif reporter_subtype == "卡片條列型":
            position = "卡片群下方或右側留白；不可壓卡片、圖區與跑馬"
        elif reporter_subtype == "敘事觀點型":
            position = "右欄結論文字旁或背景留白處；不可壓人物圖與跑馬"
        else:
            position = "右欄中下方、跑馬安全區上方；不可進右下588×90"
    elif frame_type.startswith("框訊") or frame_type == "框訊":
        position = "主資訊卡底部或右側背景留白；不可壓圖、人物、對話框"
    else:
        position = "標題下方的資訊區邊緣或右側留白；不可壓主圖"

    return {
        "type": module_type,
        "sentence": sentence,
        "position": position,
        "reason": "使用者明確標註效果；允許生成，但不可壓素材保護區，也不可重複抽取普通內文",
    }


def auto_detect_frame_type(script: str) -> str:
    """自動判斷版型大類。優先順序：記者說新聞 → 框訊 → 標大框。"""
    s = script.lower()
    image_count = _count_image_intents(script)

    if is_reporter_news(script):
        return "記者說新聞"

    if _contains_any(script, ["評審", "委員", "利益迴避", "所屬公司", "關係", "節點", "招標", "異議", "暫緩公告"]):
        return "框訊・流程關係"
    if _contains_any(s, ["vs", "v.s", "對比", "比較", "工法", "差異", "價差", "破碎", "切割", "圖利"]):
        return "框訊・多圖對比"
    if _contains_any(script, ["痛批", "回應", "反擊", "控", "遭控", "喊話", "對話框", "黨團", "議員", "市長"]) and image_count >= 2:
        return "框訊・對打時間軸"
    if sum(1 for x in ["點", "元", "漲", "跌", "%", "營業額", "權重", "大盤", "台股", "億", "萬"] if x in script) >= 3:
        return "框訊・數據分析"
    if image_count >= 3:
        return "框訊・多圖對比"
    return "標大框"


def auto_detect_style(script: str) -> str:
    """依新聞題材自動挑選視覺風格；仍可在 UI 手動覆寫。"""
    if _contains_any(script, ["黃金週", "旅遊", "海外旅遊", "首爾", "曼谷", "台北", "高雄", "九份", "旗津", "天燈", "美食", "避風港", "消費", "百貨", "新光", "微風", "北車", "商場"]):
        return "民生消費 (Fluid Analytics)"
    if _contains_any(script, ["股", "台股", "大盤", "營業額", "權重", "財經", "億", "漲", "跌"]):
        return "全球財經 (Elite Obsidian)"
    if _contains_any(script, ["柯", "議員", "市長", "黨", "選舉", "民眾黨", "國民黨", "立院"]):
        return "選情政論 (Democracy Grey)"
    if _contains_any(script, ["AI", "OpenAI", "Meta", "科技", "晶片", "台積電", "聯發科"]):
        return "科技政策 (Cyber Policy)"
    if _contains_any(script, ["招標", "霸凌", "偷拍", "申訴", "警", "警方", "北檢", "廉政署", "偵辦", "圖利", "肇事", "逃逸", "毒駕", "自撞", "翻覆", "電箱", "嫌犯", "投案"]):
        return "社會案件 (Justice Alert)"
    return "民生消費 (Fluid Analytics)"


def auto_detect_headline_mode(script: str) -> str:
    """保留相容用：v17 不再自動決定一行/兩行，UI 改由手動選擇。"""
    return "兩行大標題"


def auto_should_safe_zone(script: str, frame_type: str) -> bool:
    # 記者說新聞一律鎖定右下角跑馬安全區，不可關閉。
    if frame_type == "記者說新聞":
        return True
    return "跑馬" in script or "記者說新聞" in script


def auto_patch_missing_image_zones(script: str, frame_type: str) -> str:
    """缺少圖區但框型明顯需要主畫面時，自動在最終指令補圖區，不改原文。"""
    cleaned = script.strip()
    if _count_image_intents(cleaned) > 0:
        return cleaned
    if frame_type == "記者說新聞":
        return cleaned
    if frame_type in ["框訊・流程關係", "標大框"]:
        return cleaned + NL + NL + "[圖-右主]" + NL + "（主畫面 / ROLL / 後製真實圖）"
    if frame_type == "框訊・對打時間軸":
        return cleaned + NL + NL + "[圖-右主]" + NL + "（主畫面 / ROLL）"
    if frame_type == "框訊・多圖對比":
        return cleaned + NL + NL + "[圖-左主]" + NL + "（主畫面 / 後製真實圖）"
    return cleaned


def build_director_report(script: str) -> Dict[str, str]:
    frame = auto_detect_frame_type(script)
    subtype = detect_reporter_subtype(script) if frame == "記者說新聞" else "非記者說新聞"
    density = density_score(script)
    tone = auto_detect_tone(script, frame)
    conclusion = detect_conclusion_module(script, frame, subtype)
    return {
        "frame_type": frame,
        "reporter_subtype": subtype,
        "style_name": auto_detect_style(script),
        "headline_mode": "手動選擇",
        "density": density,
        "tone": tone,
        "conclusion_type": conclusion["type"],
        "conclusion_sentence": conclusion["sentence"],
        "conclusion_position": conclusion["position"],
        "safe_zone": "啟用" if auto_should_safe_zone(script, frame) else "不啟用",
    }


def build_quality_check(parsed: ParsedInput, frame_type: str, reporter_subtype: str, use_safe_zone: bool) -> List[str]:
    checks: List[str] = []
    checks.append("標題鎖定最上方：必檢")
    if frame_type == "記者說新聞" or use_safe_zone:
        checks.append("右下跑馬安全區 588×90：容器、陰影、蓋章、筆刷全部不得進入")
    if parsed.image_tags:
        checks.append("所有圖區：文字、icon、蓋章、筆刷、對話框不可壓入，20px buffer")
    if frame_type == "記者說新聞":
        checks.append(f"記者說新聞子類型：{reporter_subtype}，允許較高資訊密度但不可壓安全區")
    checks.append("視覺層級：主標 300%，小標 160%，內文 100%")
    checks.append("v20.6.3 結論模組：只有明確標註筆刷/蓋章才生成；不可壓圖、人物、表格與跑馬")
    return checks


def run_self_tests() -> List[str]:
    """本機快速測試：確認自動導演與補圖區不再產生破碎字串。"""
    results: List[str] = []

    case_reporter = "【標】台灣成日本黃金週熱點" + NL + "[放左邊]" + NL + "[圖---九份放天燈]" + NL + "[放右邊]" + NL + "【台灣避風港效應】"
    assert auto_detect_frame_type(case_reporter) == "記者說新聞"
    assert auto_should_safe_zone(case_reporter, "記者說新聞") is True
    results.append("記者說新聞＋跑馬安全區判斷 OK")

    case_relation = "標:<北車商場經營權>激戰! (數個假人icon) 評審委員 所屬公司 利益迴避"
    assert auto_detect_frame_type(case_relation) == "框訊・流程關係"
    results.append("流程關係判斷 OK")

    case_data = "大標:<聯發科>領軍 台股盤中觸及40101.23點 終場下跌94.9點 收在39521.73點"
    assert auto_detect_frame_type(case_data) == "框訊・數據分析"
    results.append("數據分析判斷 OK")

    case_travel = "台灣成日本黃金週熱點 日本海外旅遊排名 首爾 曼谷 台北 高雄 九份放天燈 高雄旗津 美食"
    assert auto_detect_style(case_travel) == "民生消費 (Fluid Analytics)"
    results.append("旅遊民生風格判斷 OK")

    case_conclusion = "[蓋章效果]去墾丁不如去沖繩"
    conclusion = detect_conclusion_module(case_conclusion, "記者說新聞", "左右解釋型")
    assert conclusion["type"] == "蓋章"
    assert "沖繩" in conclusion["sentence"]
    results.append("v20.3 結論模組判斷 OK")

    patched = auto_patch_missing_image_zones("標:測試新聞", "標大框")
    assert "[圖-右主]" in patched
    assert "主畫面" in patched
    results.append("自動補圖區 OK")

    prompt, parsed = build_final_prompt_v18(
        script=patched,
        frame_type="標大框",
        style_name="社會案件 (Justice Alert)",
        icon_style="3D",
        headline_mode="MEGA LARGE 兩行/三行",
        layout_mode="GRID",
        use_safe_zone=False,
        ai_color=True,
        notes="測試",
    )
    assert "VISUAL DIRECTOR v18" in prompt
    assert "[圖-右主]" in prompt
    assert "TOP HEADLINE LOCK" in prompt
    assert parsed.image_tags
    results.append("最終指令生成 OK")

    return results


# =========================================================
# 4. API KEY 與 Gemini
# =========================================================
def get_api_key() -> str:
    """Render 環境變數優先，手動輸入作為備用。"""
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    input_key = st.session_state.get("manual_api_key", "").strip()
    return input_key or env_key


def configure_gemini(api_key: str) -> bool:
    if not genai:
        st.error("❌ 尚未安裝 google-generativeai，請在 requirements.txt 加入 google-generativeai")
        return False
    if not api_key:
        st.error("❌ 找不到 Gemini API KEY。請設定 Render 環境變數 GEMINI_API_KEY，或在側邊欄手動輸入。")
        return False
    genai.configure(api_key=api_key)
    return True


# =========================================================
# v20.4 Prompt 成本監控 + 防亂生文字稽核
# =========================================================
MODEL_PRICE_TABLE: Dict[str, Dict[str, float]] = {
    # 單位：USD / 1M tokens；這裡做「粗估監控」，實際請以 Google Billing 為準。
    "gemini-3.1-flash-lite-preview": {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
}


def _read_usage_value(usage: Any, key: str, default: int = 0) -> int:
    if usage is None:
        return default
    if isinstance(usage, dict):
        return int(usage.get(key, default) or default)
    return int(getattr(usage, key, default) or default)


def build_usage_report(response: Any, model_name: str, model_label: str = "") -> Dict[str, Any]:
    usage = getattr(response, "usage_metadata", None)
    input_tokens = _read_usage_value(usage, "prompt_token_count")
    output_tokens = _read_usage_value(usage, "candidates_token_count")
    total_tokens = _read_usage_value(usage, "total_token_count", input_tokens + output_tokens)

    price = MODEL_PRICE_TABLE.get(model_name, {"input": 0.0, "output": 0.0})
    estimated_cost = (input_tokens / 1_000_000 * price["input"]) + (output_tokens / 1_000_000 * price["output"])

    return {
        "model_label": model_label,
        "model_name": model_name,
        "prompt_token_count": input_tokens,
        "candidates_token_count": output_tokens,
        "total_token_count": total_tokens,
        "estimated_cost_usd": round(estimated_cost, 6),
        "price_note": "粗估值；實際費用以 Google Billing / AI Studio API Spend 為準。",
    }


def _normalize_token(token: str) -> str:
    return token.strip().strip("，。；：、,.!?！？()（）[]【】<>《》『』「」\"\'\n\t ")


def extract_high_risk_facts(text: str) -> Dict[str, List[str]]:
    text = text or ""
    number_pattern = r"(?:\d+(?:\.\d+)?|[０-９]+)(?:\s?(?:%|％|元|萬|億|兆|人|件|天|年|月|日|小時|分鐘|公里|公尺|坪|歲|點|度|名|位|成|倍))?"
    numbers = re.findall(number_pattern, text)

    quoted = re.findall(r"[「『《【<]([^」』》】>]{2,20})[」』》】>]", text)
    suffix_pattern = r"[\u4e00-\u9fffA-Za-z0-9]{2,18}(?:市|縣|區|鄉|鎮|村|里|路|街|橋|站|港|機場|醫院|學校|大學|公司|集團|基金會|協會|委員會|部|署|局|院|府|黨|台|臺|銀行|商場|百貨|車站|捷運|法院|地檢署|北檢|地院|警局|分局)"
    suffix_terms = re.findall(suffix_pattern, text)

    latin_terms = re.findall(r"\b[A-Z][A-Za-z0-9&._-]{1,24}\b", text)

    facts = {
        "numbers": sorted(set(_normalize_token(x) for x in numbers if _normalize_token(x))),
        "named_terms": sorted(set(_normalize_token(x) for x in (quoted + suffix_terms + latin_terms) if _normalize_token(x))),
    }
    return facts


def audit_extra_facts(source_text: str, ai_output: str) -> Dict[str, Any]:
    source = source_text or ""
    output = ai_output or ""
    source_facts = extract_high_risk_facts(source)
    output_facts = extract_high_risk_facts(output)

    added_numbers = [x for x in output_facts["numbers"] if x and x not in source]
    added_named_terms = [x for x in output_facts["named_terms"] if x and x not in source]

    return {
        "status": "warning" if added_numbers or added_named_terms else "ok",
        "added_numbers": added_numbers[:50],
        "added_named_terms": added_named_terms[:50],
        "note": "這是保守型事後稽核：抓新增數字、引號/括號詞、地名機構名等高風險詞；仍需人工確認新聞事實。",
    }


def render_usage_report(report: Optional[Dict[str, Any]]) -> None:
    st.markdown("### 💰 Prompt 成本監控")
    if not report:
        st.caption("尚未取得 token 用量。")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Input Tokens", f"{report.get('prompt_token_count', 0):,}")
    c2.metric("Output Tokens", f"{report.get('candidates_token_count', 0):,}")
    c3.metric("Total Tokens", f"{report.get('total_token_count', 0):,}")
    c4.metric("估算 USD", f"${report.get('estimated_cost_usd', 0):.6f}")
    st.caption(f"模型：`{report.get('model_name', '')}`｜{report.get('price_note', '')}")


def render_fact_audit(audit: Optional[Dict[str, Any]]) -> None:
    st.markdown("### 🛡 防亂生文字稽核")
    if not audit:
        st.caption("尚未執行稽核。")
        return
    if audit.get("status") == "ok":
        st.success("✅ 未偵測到新增高風險事實（數字／疑似人名地名機構名）。")
    else:
        st.warning("⚠ 偵測到 AI 可能新增了原稿沒有的高風險資訊，請人工確認。")
        if audit.get("added_numbers"):
            st.markdown("**新增數字 / 單位：**")
            st.write("、".join(audit["added_numbers"]))
        if audit.get("added_named_terms"):
            st.markdown("**新增疑似人名 / 地名 / 機構名：**")
            st.write("、".join(audit["added_named_terms"]))
    st.caption(audit.get("note", ""))


def generate_ai_frame_content(news_text: str, frame_type: str, api_key: str, model_name: str, model_label: str = "") -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """AI 只負責拆稿，不負責決定圖區壓縮或侵入。"""
    if not configure_gemini(api_key):
        return None

    template = FRAME_TEMPLATES.get(frame_type, FRAME_TEMPLATES["標大框"])

    system_instruction = f"""
你是一位資深電視新聞製作人，負責把新聞稿整理成「框訊」CG 文字稿。

【最重要原則】
AI 只負責內容拆解，不得破壞版面安全。
{STRICT_NO_EXTRA_FACTS}
所有 [圖]、[圖-xxx]、(#定xxx)、(定xxx圖) 都代表後製真實圖片區。
這些標籤在輸入稿階段要保留，讓系統知道要留空；但最終成品必須刪除標籤文字。

【目前框型】
{frame_type}
{template['summary']}
建議標記：{template['recommended_tags']}

【輸出格式】
請只輸出以下格式，不要額外解釋：
[TYPE]框訊
[FRAME]{frame_type}
[TITLE]主標題
[BODY]整理後內容

【符號規則】
- 雙引號內文字：重點變色，最後刪除引號。
- <文字>：高權重關鍵字，可做最大色塊或強調。
- 【文字】：小標題色塊，最後刪除括號。
- [圖-xxx]：硬留白圖片區，最後刪除標籤文字。
- (色塊)、(對話框)、(數據框)、#筆刷：版面模組指令，最後刪除指令字樣。
"""

    try:
        model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=textwrap.dedent(system_instruction).strip(),
        )
        response = model.generate_content(
            f"請整理這則新聞稿：{NL}{NL}{news_text}",
            generation_config=genai.types.GenerationConfig(temperature=0.05),
        )
        usage_report = build_usage_report(response, model_name, model_label)
        return response.text.strip(), usage_report
    except Exception as e:
        st.error(f"AI 生成失敗：{e}")
        return None, None


# =========================================================
# 5. 文字解析器
# =========================================================
def clean_inline_text(text: str) -> str:
    return " ".join(str(text).replace(NL, " ").split()).strip()


def extract_section(raw: str, tag: str, default: str = "") -> str:
    marker = f"[{tag}]"
    if marker not in raw:
        return default
    after = raw.split(marker, 1)[1]
    known_markers = ["[TYPE]", "[FRAME]", "[TITLE]", "[BODY]"]
    cut = len(after)
    for item in known_markers:
        if item == marker:
            continue
        idx = after.find(item)
        if idx >= 0:
            cut = min(cut, idx)
    return after[:cut].strip()


def _collect_parenthesis_tags(script: str) -> List[str]:
    tags: List[str] = []
    start = 0
    while True:
        left = script.find("(", start)
        if left < 0:
            break
        right = script.find(")", left + 1)
        if right < 0:
            break
        tags.append(script[left:right + 1])
        start = right + 1
    return tags


def _collect_square_tags(script: str) -> List[str]:
    tags: List[str] = []
    start = 0
    while True:
        left = script.find("[", start)
        if left < 0:
            break
        right = script.find("]", left + 1)
        if right < 0:
            break
        tags.append(script[left:right + 1])
        start = right + 1
    return tags




def _collect_column_inline_tags(script: str) -> List[str]:
    """
    支援欄位級寫法：
    左：(圖 4:3) / 中：文字色塊 / 右：ROLL框 4:5
    若沒有括號，也會包成內部 tag 讓後續圖區數量與模組數量正確。
    """
    tags: List[str] = []
    for raw in (script or "").splitlines():
        line = raw.strip()
        m = re.match(r"^(左|中|中央|右)\s*[：:]\s*(.+)$", line)
        if not m:
            continue
        col, content = m.group(1), m.group(2).strip()
        # 已有括號/方括號時，原本 collect 會抓；這裡只補無括號寫法。
        if _collect_parenthesis_tags(content) or _collect_square_tags(content):
            continue
        if re.search(r"(圖|ROLL|roll|影片|照片|截圖|畫面|定圖)", content):
            tags.append(f"({col}{content})")
        elif re.search(r"(文字|色塊|方框|對話框|數據框|卡|模組)", content):
            tags.append(f"({col}{content})")
    return tags

def extract_headline_lines(script: str) -> List[str]:
    """
    v22.7：大標最多只吃兩行。
    大標:
    第一行
    第二行
    => 若選一行大標，合併成同一條一行大標，中間保留半形空格。
    第三行開始不再吃，避免把內文吞進 headline。
    """
    lines = (script or "").splitlines()
    headline_lines: List[str] = []
    collecting = False

    HEADLINE_KEYS = [
    "大標:", "大標：", "大標=", "大標＝",
    "主標:", "主標：", "主標=", "主標＝",
    "標題:", "標題：", "標題=", "標題＝",
    "標:", "標：", "標=", "標＝",
]
    headline_keys = HEADLINE_KEYS
    stop_prefixes = ("小標:", "小標：", "小標=", "內容:", "內容：", "內容=", "內文:", "內文：", "內文=")

    for raw in lines:
        line = raw.strip()

        if not collecting:
            for key in headline_keys:
                if line.startswith(key):
                    collecting = True
                    after = line.split(key, 1)[1].strip()
                    if after:
                        headline_lines.append(after)
                    break
            if len(headline_lines) >= 2:
                break
            continue

        # 大標區遇到空行、素材/模組標籤、小標/內容欄位，就結束。
        if not line:
            break
        if line.startswith(stop_prefixes):
            break
        if line.startswith("(") or line.startswith("["):
            break
        if re.fullmatch(r"[-—=]{3,}", line):
            break

        headline_lines.append(line)
        if len(headline_lines) >= 2:
            break

    return [_clean_visual_text(x) for x in headline_lines[:2] if _clean_visual_text(x)]


def extract_full_headline(script: str) -> str:
    """大標預設合併成單一 headline 字串；框訊/一行大標會用這個字串強制一行。"""
    lines = extract_headline_lines(script)
    if lines:
        return " ".join(lines[:2]).strip()[:120]
    fallback = next((line.strip() for line in (script or "").splitlines() if line.strip()), "")
    return _clean_visual_text(fallback)[:120]


def build_headline_display_text(script: str, headline_mode: str) -> str:
    """
    v22.8：依標題行數決定 headline 給影像模型的權威文字。
    - 一行大標題：大標後最多兩行合併成一條，中間保留半形空格。
    - 兩行大標題：只有使用者真的分成兩行時，才做兩行；若使用者本來寫一行，就保留一行。
    """
    lines = extract_headline_lines(script)
    if not lines:
        return extract_full_headline(script)
    if headline_mode == "兩行大標題":
        return "\n".join(lines[:2]).strip()
    return " ".join(lines[:2]).strip()


def build_headline_mode_brief(script: str, headline_mode: str, frame_type: str = "") -> str:
    """生成給 Gemini Image 的標題硬規則，避免兩行大標只抓到第一行。"""
    lines = extract_headline_lines(script)
    line1 = lines[0] if len(lines) >= 1 else extract_full_headline(script)
    line2 = lines[1] if len(lines) >= 2 else ""

    if headline_mode == "兩行大標題" and not (frame_type.startswith("框訊") or frame_type == "框訊"):
        if line2:
            return f"""[HEADLINE TEXT LOCK]
Headline mode is TWO-LINE MEGA HEADLINE.
Render exactly two stacked headline lines at the top:
LINE 1: {line1}
LINE 2: {line2}
Do not drop LINE 2. Do not merge LINE 2 into body text. Do not turn LINE 2 into a subtitle card.
Both headline lines must be huge broadcast-style headline typography.
""".strip()
        return f"""[HEADLINE TEXT LOCK]
Headline mode is TWO-LINE MEGA HEADLINE, but only one headline line was provided.
Render the provided headline at the top as the dominant mega headline:
LINE 1: {line1}
Do not invent a second headline line.
""".strip()

    merged = " ".join(lines[:2]) if lines else line1
    return f"""[HEADLINE TEXT LOCK]
Headline mode is SINGLE-LINE HEADLINE.
Render the complete headline as one single line at the top:
{merged}
If the user supplied two headline lines, merge them into this one headline line with one half-width space between phrases.
Do not drop the second phrase. Do not wrap. Do not create a second headline bar.
""".strip()


def parse_user_script(script: str) -> ParsedInput:
    """抓出標題、圖區、模組與警告。"""
    warnings: List[str] = []

    title = extract_full_headline(script)

    square_tags = _collect_square_tags(script)
    paren_tags = _collect_parenthesis_tags(script)
    inline_column_tags = _collect_column_inline_tags(script)

    image_tags: List[str] = []
    for tag in square_tags + paren_tags + inline_column_tags:
        if is_asset_protection_tag(tag):
            image_tags.append(tag)

    # v22.8 ROLL alias reserve fix:
    # Bare newsroom shorthand such as ++ROLL++, +++右ROLL+++, 左ROLL=, 右ROLL：, (開框roll)
    # must create protected blank asset zones, but the literal marker text must never render.
    # _extract_asset_zones() also scans line-level aliases beyond brackets/parentheses.
    try:
        for tag in _extract_asset_zones(script):
            if is_asset_protection_tag(tag):
                image_tags.append(tag)
    except Exception:
        pass

    image_tags = _dedupe_asset_zone_list(list(dict.fromkeys([tag.strip() for tag in image_tags if tag.strip()])))

    module_tags: List[str] = []
    module_words = ["色塊", "方框", "對話框", "數據框", "小標", "蓋章", "假人", "icon", "筆刷", "關係", "群組", "頭+字"]
    for tag in square_tags + paren_tags + inline_column_tags:
        if _contains_any(tag, module_words):
            module_tags.append(tag)
    if "#筆刷" in script:
        module_tags.append("#筆刷")
    module_tags = list(dict.fromkeys([tag.strip() for tag in module_tags if tag.strip()]))

    if "[圖" not in script and not image_tags:
        warnings.append("這份稿件沒有明確 [圖] 標記；若需要後製塞真實圖片，建議補上 [圖-左主] 或 [圖-右主]。")

    return ParsedInput(title=title, body=script.strip(), image_tags=image_tags, module_tags=module_tags, warnings=warnings)


# =========================================================
# 6. v17 核心規則文字
# =========================================================
def build_symbol_matrix_v17() -> str:
    return """
[STRICT SYMBOL MATRIX v17]
- "雙引號" => Highlight with dynamic priority color. CRITICAL: DELETE all quote marks in final artwork.
- <尖括號> => Highest-priority keyword emphasis. Remove angle brackets in final artwork.
- 【方頭括號】 => Bold color-block subheading. Remove brackets in final artwork.
- (圓括號內容) => Treat as layout instruction or preserved factual parenthesis depending on context.
- [圖] / [圖-xxx] / (#定xxx) / (定xxx圖) / (LINE截圖) => HARD EMPTY IMAGE ZONE.

[HARD EMPTY IMAGE ZONE RULE - ABSOLUTE]
1. Every image placeholder creates a protected bounding box.
2. The protected box must remain 100% EMPTY for post-production real photos.
3. NO text, NO icons, NO UI cards, NO arrows, NO shadows, NO decorations may overlap the image zone.
4. NO stamp effects, brush strokes, arrows, speech bubbles, labels, shadows, or decorative frames may overlap the image zone.
5. Stamp effects such as (蓋章字), --------蓋章, or "6天完工" stamps must render OUTSIDE image zones only.
6. Maintain at least 20px clean safety buffer around every image zone.
7. DELETE all placeholder text, slug text, and labels from final pixels.
8. If layout conflict occurs, shrink/reflow text modules; NEVER invade image zones.
9. Image zones have higher priority than text completeness and all visual effects.

[MODULE TAGS]
- (色塊) / (方框) => Render as news information card. Delete instruction text.
- (對話框) / (拉對話框) / (+對話框) => Render as speech bubble. Delete instruction text.
- (數據框) => Render as layered data block. Delete instruction text.
- #筆刷 / ---筆刷 => Render as brush-stroke emphasis. Delete literal instruction text.
- (蓋章字) / --------蓋章 => Render as distressed stamp overlay OUTSIDE protected image zones only. Delete instruction text.
- (icon假人大頭) / (數個假人icon) => Render as simple person icon group. Delete instruction text.
""".strip()


def build_frame_rules(frame_type: str) -> str:
    common = """
[FRAME COMMON RULES]
- Canvas: 1920x1080 Full HD.
- Language: Traditional Chinese only.
- Output must look like polished professional TV news CG.
- Text hierarchy must be strong and readable on broadcast.
- TOP HEADLINE LOCK: headline must always be placed at the very top edge area of the canvas.
- One-line headline: keep it in the top headline band, centered or left-weighted, never moved to middle.
- Two-line headline is allowed ONLY for 標大框 or when explicitly selected in non-框訊 layouts.
- For every 框訊 layout, headline must be a single line; never stack or auto-wrap.
- Do not leave accidental blank spaces, except protected image zones and ticker safe zone.
"""

    rules = {
        "記者說新聞": """
[FRAME: 記者說新聞]
- Top: headline must stay at the very top.
- Left column: main facts, dates, rankings, images, maps, or data.
- Right column: explanation, reason, interpretation, conclusions, and bullet points.
- RIGHT-BOTTOM TICKER SAFE ZONE IS LOCKED: X > 1332 and Y > 990 must be background only.
- Do not place any text, icons, stamps, brush effects, cards, or image placeholders inside the ticker safe zone.
- Content should fill downward but stop before the safe zone boundary.
- All [圖] placeholders must remain untouched hard empty zones.
""",
        "標大框": """
[FRAME: 標大框]
- Top 30-40%: MEGA headline, forced 2-line or 3-line if stronger.
- Lower area: main image zone plus information modules.
- Main visual zone must be large and untouched.
- Suitable for conflict or breaking-style news summaries.
""",
        "框訊・多圖對比": """
[FRAME: 框訊・多圖對比]
- Top: SINGLE LINE headline only.
- Never split headline into two lines.
- Never stack headline vertically.
- Never create a second headline/subtitle bar from headline fragments.
- If the Chinese headline is too long, reduce font size, tracking, or side margins instead of wrapping.
- Upper row: multiple real-material image zones, such as construction, documents, people.
- Left/lower: main image hole if present.
- Right/lower: quote, price difference, investigation or explanation blocks.
- Bottom-right: comparison module if [模組-對比] appears.
- All image zones remain completely empty.
""",
        "框訊・對打時間軸": """
[FRAME: 框訊・對打時間軸]
- Top: SINGLE LINE headline only, with opposing keywords emphasized within the same line.
- Never split headline into two lines.
- Never stack headline vertically.
- Never create a second headline/subtitle bar from headline fragments.
- If the Chinese headline is too long, reduce font size, tracking, or side margins instead of wrapping.
- Upper/lateral: two-person debate or attack/response zones.
- Main ROLL/video zone usually on the right and must stay empty.
- Bottom: timeline/event images arranged in equal-width blocks.
- Conclusion quote may appear bottom-right but must not overlap images.
""",
        "框訊・數據分析": """
[FRAME: 框訊・數據分析]
- Top: SINGLE LINE headline only.
- Never split headline into two lines.
- Never stack headline vertically.
- Never create a second headline/subtitle bar from headline fragments.
- If the Chinese headline is too long, reduce font size, tracking, or side margins instead of wrapping.
- Main body is data hierarchy, not image hierarchy.
- Use clear data cards, stacked rows, and strong numerical emphasis.
- Person image, if any, is secondary and protected.
- Left side: event/narrative data. Right side: expert quote and conclusion data.
""",
        "框訊・流程關係": """
[FRAME: 框訊・流程關係]
- Top: SINGLE LINE strong conflict headline only.
- Never split headline into two lines.
- Never stack headline vertically.
- Never create a second headline/subtitle bar from headline fragments.
- If the Chinese headline is too long, reduce font size, tracking, or side margins instead of wrapping.
- Left side: relationship diagram with role group and branch nodes.
- Right side: main image/ROLL protected zone.
- Use connector lines from role icons to relationship nodes.
- Relationship nodes must not overlap each other or image zones.
""",
    }
    return textwrap.dedent(common + NL + rules.get(frame_type, rules["標大框"])).strip()


def _safe_zone_ratio_label(tag: str, default_aspect: str = "AI自動配置排版") -> str:
    """把使用者圖區註記轉成不會被 Gemini 畫出來的安全描述。"""
    zcfg = resolve_asset_aspect_for_tag(tag, default_aspect) if 'resolve_asset_aspect_for_tag' in globals() else {"ratio": "auto"}
    ratio = zcfg.get("ratio", "auto")
    if re.search(r"4\s*[:：]\s*3", tag or ""):
        ratio = "4:3"
    elif re.search(r"4\s*[:：]\s*5", tag or ""):
        ratio = "4:5"
    return f"protected blank media area, internal aspect ratio instruction: {ratio}; ratio words are not visible text"

# =========================================================
# v23 GROUP LOCK
# =========================================================

GROUP_SEPARATOR_RE = re.compile(
    r"^\s*[-—=]{6,}\s*$"
)


def split_editorial_groups(
    script: str
) -> List[str]:

    if not script:
        return []

    groups = []
    current = []

    for raw in script.splitlines():

        if GROUP_SEPARATOR_RE.match(
            raw.strip()
        ):

            if current:
                groups.append(
                    NL.join(current).strip()
                )
                current = []

            continue

        current.append(raw)

    if current:
        groups.append(
            NL.join(current).strip()
        )

    return [
        x for x in groups
        if x.strip()
    ]

def _extract_location_badges(script: str) -> List[str]:
    """
    抽出可上畫面的打卡地點文字。

    重要：
    - 「打卡LOGO / 打卡地點」是導演註記，不可上畫面。
    - 後面的地名才是合法可渲染文字。
    - 支援：
      打卡地點:新北市永和區
      (打卡LOGO)新北市永和區
      打卡LOGO:新北市永和區
      (右ROLL框 4:5+ 換行 打卡地點:新北市永和區)
    """
    if not script:
        return []

    badges: List[str] = []

    patterns = [
        # 高雄三民區---打卡符號 / 台南---打卡符號
        r"([^\n()（）]+?)\s*[-—]{2,}\s*打卡符號",
        # 高雄三民區+++打卡符號 / 高雄三民區 + 打卡符號
        r"([^\n()（）]+?)\s*[+＋]{1,}\s*打卡符號",
        # 打卡地點:新北市永和區 / 打卡地點：新北市永和區
        r"打卡地點\s*[：:]\s*([^\)\n]+)",
        # 地點:新北市永和區
        r"(?<!打卡)地點\s*[：:]\s*([^\)\n]+)",
        # 打卡LOGO:新北市永和區 / 打卡LOGO：新北市永和區
        r"打卡\s*LOGO\s*[：:]\s*([^\)\n]+)",
        # (打卡LOGO)新北市永和區 / （打卡LOGO）新北市永和區
        r"[（\(]\s*打卡\s*LOGO\s*[）\)]\s*([^\(\)（）\n]+)",
        # (打卡)台南 / （打卡）台南
        r"[（\(]\s*打卡\s*[）\)]\s*([^\(\)（）\n]+)",
        # 打卡:台南 / 打卡：台南
        r"(?<!LOGO)打卡\s*[：:]\s*([^\)\n]+)",
    ]

    for pat in patterns:
        for m in re.finditer(pat, script, flags=re.IGNORECASE):
            val = _normalize_token(m.group(1))
            # 清掉可能被一起抓進來的導演註記，只保留地名本體
            val = re.sub(r"^(打卡地點|地點|打卡\s*LOGO)\s*[：:]?", "", val, flags=re.IGNORECASE).strip()
            val = re.sub(r"[-—+＋\s]*打卡符號.*$", "", val, flags=re.IGNORECASE).strip()
            val = val.strip(" +＋-—,，。；;：:()（）[]【】 ")
            if val and not re.search(r"(4:3|4:5|ROLL|圖|色塊|LOGO|打卡地點|打卡符號)", val, flags=re.IGNORECASE):
                badges.append(val)

    return list(dict.fromkeys(badges))


def build_roll_location_badge_lock(script: str) -> str:
    """
    v22.10：把「高雄三民區---打卡符號」這類寫法綁到 ROLL 框。
    地點文字可見；「打卡符號」不可見；不可新增圖框。
    """
    badges = _extract_location_badges(script)
    roll_zones = [z for z in _extract_asset_zones(script) if _has_roll_alias(z)]
    if not badges:
        return ""
    target = "nearest requested ROLL/media blank zone" if roll_zones else "nearest requested blank media zone"
    lines = [f"- Location badge {i}: render ONLY `{loc}` with a map-pin/check-in icon as a small colored strip attached to the {target}." for i, loc in enumerate(badges, start=1)]
    return (
        "[ROLL LOCATION BADGE LOCK]\n"
        "If a line contains `---打卡符號`, `+++打卡符號`, `(打卡LOGO)地名`, `(打卡)地名`, or `打卡地點:地名`, treat the location as an accessory badge for the ROLL/media frame.\n"
        "Do NOT create an extra asset zone, photo box, ROLL box, or independent text card for this badge.\n"
        "Render only the location text itself; never render 打卡符號, 打卡LOGO, 打卡地點, or 地點 as words.\n"
        "The badge may sit inside the ROLL frame edge or attached to the frame border as a colored label, but it must not cover the protected photo/video area content region.\n"
        + "\n".join(lines)
    ).strip()


def _style_render_ban_text() -> str:
    """風格名稱只給設計方向，不可成為畫面文字。"""
    return (
        "Style names and category labels are INTERNAL DESIGN REFERENCES ONLY. "
        "Never render any style/category words as visible text, including: "
        "社會案件, 民生消費, 體育競技, 全球財經, 突發重磅, 選情政論, 科技政策, 綠能永續, 現代民俗, 生醫科技, "
        "Justice Alert, Crime Scene Noir, Fluid Analytics, Elite Obsidian, Breaking Alert, Democracy Grey, Cyber Policy."
    )


def _forbidden_helper_text_ban() -> str:
    """所有內部註記、比例字樣、假 placeholder 標籤都不得出現在成品。"""
    return (
        "Never render internal layout/helper text, ratio labels, or placeholder labels, including: "
        "左上圖, 左下圖, 右上圖, 右下圖, 中上文字色塊, 中下文字色塊, 左：, 中：, 右：, 左:, 中:, 右:, 右ROLL框, 左ROLL框, 右ROLL, 左ROLL, 右ROLL=, 左ROLL=, 右ROLL：, 左ROLL：, 右邊ROLL框, 左邊ROLL框, 右邊roll框, 左邊roll框, +++ROLL+++, ++ROLL++, +++右ROLL+++, +++左ROLL+++, 開框ROLL, 開框roll, (開框roll), ROLL框, "
        "4:3, 4:5, 圖, 色塊, 打卡LOGO, 打卡地點, 打卡, 假人ICON, 假人icon, 假人大頭, 人形ICON, 人物ICON, 對話框, 說話框, speech bubble, 後封保用照片, 後製確認照片, 後製保留照片, "
        "真實視頻ROLL插投, 真實影片ROLL插投, 真實視頻ROLL, 真實影片ROLL, ROLL/視頻, ROLL/視ideo, 視頻, 影片, video, Video, 編輯圖片區, 圖片區, 4:5商比, 4:3商比, 商比, placeholder, image box, photo, section, card, source marker, debug label."
    )




def _is_layout_helper_line(text: str) -> bool:
    """判斷這一行是不是只給導演/AI看的排版註記，不可進入可見文字池。"""
    raw = str(text or "").strip()
    if not raw:
        return False
    compact = re.sub(r"\s+", "", raw)
    # +++右ROLL+++、---左圖--- 這類裝飾式版位標記
    if re.fullmatch(r"[+＋#＃\-—_＝=＊*\s]*(左|右|中|中央|左邊|右邊|中間|上|下|左上|左下|右上|右下|中上|中下)?[+＋#＃\-—_＝=＊*\s]*(圖|圖片|開框ROLL|開框roll|ROLL|roll|Roll|ROLL框|roll框|影片|視頻|色塊|文字色塊|方框|框)[+＋#＃\-—_＝=＊*\s]*(4[:：][35])?[+＋#＃\-—_＝=＊*\s]*", compact, flags=re.IGNORECASE):
        return True
    # 左：(圖 4:3)、中：文字色塊、右邊 roll框 4:5
    if re.fullmatch(r"(左|右|中|中央|左邊|右邊|中間)[:：]?[\s　]*(\(?\s*)?(圖|圖片|開框ROLL|開框roll|ROLL|roll|Roll|ROLL框|roll框|影片|視頻|色塊|文字色塊|方框|框)[^\u4e00-\u9fffA-Za-z0-9]*(4\s*[:：]\s*[35])?\)?", raw, flags=re.IGNORECASE):
        return True
    # 括號內是純排版/素材標記
    stripped = raw.strip('()（）[]【】 ')
    if re.fullmatch(r"(左|右|中|中央|左邊|右邊|中間|左上|左下|右上|右下|中上|中下)?\s*(圖|圖片|開框ROLL|開框roll|ROLL|roll|Roll|ROLL框|roll框|影片|視頻|色塊|文字色塊|方框|框)\s*(4\s*[:：]\s*[35])?", stripped, flags=re.IGNORECASE):
        return True
    return False


def _normalize_spatial_alias_text(text: str) -> str:
    """把右邊/中間/+++右ROLL+++、左ROLL=、右ROLL=、開框roll 等口語寫法標準化給 spatial parser 使用。"""
    raw = str(text or "")
    raw = raw.replace("＋", "+").replace("＝", "=")
    raw = raw.replace("左邊", "左").replace("右邊", "右").replace("中間", "中").replace("中央", "中")
    raw = re.sub(r"\broll\b", "ROLL", raw, flags=re.IGNORECASE)
    raw = re.sub(r"(?i)開框\s*ROLL", "開框ROLL", raw)
    raw = re.sub(r"[+]{2,}", " ", raw)
    raw = re.sub(r"(左|右|中)\s*ROLL\s*(?:框)?\s*[=:：]", r"\1ROLL框 ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"(左|右|中)\s*ROLL\s*(?:框)?", r"\1ROLL框", raw, flags=re.IGNORECASE)
    raw = re.sub(r"開框ROLL\s*(?:框)?", "開框ROLL框", raw, flags=re.IGNORECASE)
    return raw

def _approved_text_block_for_prompt(script: str) -> str:
    """取代 raw CONTENT SCRIPT，避免把導演註記餵給 Gemini Image。"""
    parsed = parse_user_script(script)
    approved = _extract_approved_text_whitelist(script)
    approved = list(dict.fromkeys(approved + _extract_location_badges(script)))
    lines = [f"Headline: {parsed.title or '未提供'}"]
    if approved:
        lines.append("Approved visible text:")
        lines.extend([f"- {x}" for x in approved])
    else:
        lines.append("Approved visible text: none")
    return "\n".join(lines)


# =========================================================
# v22.10 Mandatory Graphic Module Lock：假人ICON / 對話框 / 蓋章 / 筆刷
# - 假人 ICON 改成數量感知：女假人ICON = 1 個女性圖示；男假人ICON = 1 個男性圖示
# - 只有明確寫「數個 / 多個 / 群 / 2個 / 3個...」才允許生成人群
# =========================================================
FAKE_PERSON_ICON_PATTERN = re.compile(
    r"(?:數個\s*假人\s*(?:ICON|icon)|假人\s*(?:ICON|icon)|假人大頭|人形\s*(?:ICON|icon)|人物\s*(?:ICON|icon)|人形圖示|人物圖示|女假人\s*(?:ICON|icon)|男假人\s*(?:ICON|icon)|做女假人\s*(?:ICON|icon)|做男假人\s*(?:ICON|icon))",
    re.IGNORECASE,
)
SPEECH_BUBBLE_PATTERN = re.compile(
    r"(?:大對話框|小對話框|拉對話框|\+對話框|對話框|說話框|speech\s*bubble)",
    re.IGNORECASE,
)
STAMP_PATTERN = re.compile(
    r"(?:\(#?蓋章[^)]*\)|\(\+蓋章[^)]*\)|\[#?蓋章[^\]]*\]|[#＃]蓋章|蓋章效果|蓋章字|---\s*蓋章|——\s*蓋章)",
    re.IGNORECASE,
)
BRUSH_PATTERN = re.compile(
    r"(?:\(#?筆刷[^)]*\)|\(\+筆刷[^)]*\)|\[#?筆刷[^\]]*\]|[#＃]筆刷|筆刷效果|\+筆刷字|---\s*筆刷|——\s*筆刷)",
    re.IGNORECASE,
)

ICON_NUMBER_MAP: Dict[str, int] = {
    "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _count_pattern_from_script(script: str, pattern: re.Pattern) -> int:
    """計算原稿裡的 graphic module 次數；至少抓出 1，避免高密度稿件被 Gemini 當裝飾省略。"""
    matches = pattern.findall(script or "")
    return len(matches)


def _normalize_graphic_marker_key(marker: str) -> str:
    """同一個圖形標記只算一次；避免 (#做男假人ICON) 又被整行再次掃到。"""
    t = str(marker or "").strip()
    t = t.replace("（", "(").replace("）", ")").replace("＋", "+").replace("＃", "#")
    t = re.sub(r"\s+", "", t)
    # 去掉外層括號與常見符號，只保留真正的控制詞。
    t = t.strip("()[]【】{}<>+-=：:;；,，。 ")
    return t.lower()


def _extract_parenthetical_and_line_markers(script: str) -> List[str]:
    """
    抓出圖形模組標記，供數量感知 parser 使用。
    v22 patch：優先吃括號/方括號內的明確標記；整行只在沒有括號標記時才當作 fallback，
    避免「46歲車手 (#做女假人ICON)」被算成第二個女假人。
    """
    if not script:
        return []
    markers: List[str] = []
    seen: set[str] = set()

    def add_marker(m: str) -> None:
        key = _normalize_graphic_marker_key(m)
        if not key or key in seen:
            return
        if not any(k in m for k in ["假人", "人形ICON", "人物ICON", "對話框", "說話框", "蓋章", "筆刷"]):
            return
        seen.add(key)
        markers.append(m.strip())

    for m in _collect_parenthesis_tags(script) + _collect_square_tags(script):
        add_marker(m)

    # fallback：支援使用者真的把「#做男假人ICON」單獨寫一行、不加括號的情況。
    for raw in script.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "(" in line or ")" in line or "[" in line or "]" in line:
            # 這類行的標記已由括號 parser 處理，不再整行重複計算。
            continue
        if any(k in line for k in ["假人", "人形ICON", "人物ICON", "對話框", "說話框", "蓋章", "筆刷"]):
            add_marker(line)

    return markers


def _parse_icon_quantity(marker: str) -> int:
    """沒有寫數量時，預設就是 1；不再把假人ICON預設成 group。"""
    t = marker or ""
    m = re.search(r"([0-9０-９]+)\s*(?:個|位|名)?\s*(?:男|女)?\s*假人", t, flags=re.IGNORECASE)
    if m:
        raw = m.group(1).translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        try:
            return max(1, min(10, int(raw)))
        except Exception:
            return 1
    m2 = re.search(r"([一二兩三四五六七八九十])\s*(?:個|位|名)?\s*(?:男|女)?\s*假人", t)
    if m2:
        return ICON_NUMBER_MAP.get(m2.group(1), 1)
    if re.search(r"(數個|多個|一群|群組|一排|多人)", t):
        return 3
    return 1


def _parse_person_icon_modules(script: str) -> List[Dict[str, Any]]:
    """假人ICON 數量/性別感知：女/男各自維持 1 個，除非使用者明確寫數量或數個。"""
    modules: List[Dict[str, Any]] = []
    for marker in _extract_parenthetical_and_line_markers(script):
        if not re.search(r"假人\s*(?:ICON|icon)|假人大頭|人形\s*(?:ICON|icon)|人物\s*(?:ICON|icon)|人形圖示|人物圖示", marker, flags=re.IGNORECASE):
            continue
        gender = "neutral"
        if re.search(r"女", marker):
            gender = "female"
        elif re.search(r"男", marker):
            gender = "male"
        qty = _parse_icon_quantity(marker)
        type_name = {
            "female": "FEMALE_PERSON_ICON",
            "male": "MALE_PERSON_ICON",
            "neutral": "PERSON_ICON",
        }[gender]
        gender_zh = {"female": "女性", "male": "男性", "neutral": "中性"}[gender]
        plural_rule = "exactly ONE" if qty == 1 else f"exactly {qty}"
        modules.append({
            "type": type_name,
            "count": qty,
            "name": f"{gender_zh}假人ICON",
            "source_marker": marker,
            "instruction": (
                f"Render {plural_rule} {gender_zh} simple person silhouette icon(s). "
                "If count is 1, render a single standalone person icon, NOT a group and NOT multiple people. "
                "Must be visible as icon artwork, not text. Do not omit. Keep outside protected image zones."
            ),
        })
    return modules


def detect_mandatory_graphic_modules(
    script: str
) -> List[Dict[str, Any]]:

    modules = []

    groups = split_editorial_groups(
        script
    )

    for gid, group_text in enumerate(
        groups
    ):

        if "女假人" in group_text:

            count = 1

            if re.search(
                r"[3３三]\s*個",
                group_text,
            ):
                count = 3

            modules.append({
                "group": gid,
                "type": "FEMALE_ICON",
                "count": count,
            })

        if "男假人" in group_text:

            count = 1

            if re.search(
                r"[3３三]\s*個",
                group_text,
            ):
                count = 3

            modules.append({
                "group": gid,
                "type": "MALE_ICON",
                "count": count,
            })

        if "對話框" in group_text:

            modules.append({
                "group": gid,
                "type": "SPEECH_BUBBLE",
                "count": 1,
                "bind": True,
            })

        if has_explicit_brush_tag(
            group_text
        ):

            modules.append({
                "group": gid,
                "type": "BRUSH",
                "count": 1,
            })

        if has_explicit_stamp_tag(
            group_text
        ):

            modules.append({
                "group": gid,
                "type": "STAMP",
                "count": 1,
            })

    return modules

def _block_has_person_icon(block_text: str) -> Tuple[bool, str]:
    if re.search(r"女.*假人|做女假人|女假人", block_text, flags=re.IGNORECASE):
        return True, "female"
    if re.search(r"男.*假人|做男假人|男假人", block_text, flags=re.IGNORECASE):
        return True, "male"
    if re.search(r"假人\s*(?:ICON|icon)|假人大頭|人形\s*(?:ICON|icon)|人物\s*(?:ICON|icon)", block_text, flags=re.IGNORECASE):
        return True, "neutral"
    return False, ""


def _clean_block_visible_preview(block_lines: List[str]) -> str:
    cleaned: List[str] = []
    for raw in block_lines:
        line = raw.strip()
        if not line or re.fullmatch(r"[-—=]{3,}", line):
            continue
        # 移除圖形/版型標記，只留下此組真正文字，用於提示模型綁定位置。
        line = re.sub(r"\([^)]*(?:假人|ICON|icon|對話框|說話框|筆刷|蓋章|色塊|方框)[^)]*\)", "", line)
        line = re.sub(r"[#＃][^\s]+", "", line)
        line = _clean_visual_text(line) if '_clean_visual_text' in globals() else line.strip()
        if line:
            cleaned.append(line)
    return " / ".join(cleaned[:6])


def build_graphic_module_binding_lock(script: str) -> str:
    """
    把「某段文字 + 假人ICON + 對話框」綁成同一組，避免對話框飛到別處、假人被重複生。
    以 ------ 分段為主；同一段內的 graphic module 必須貼著該段文字卡。
    """
    blocks = _split_script_blocks(script) if '_split_script_blocks' in globals() else []
    groups: List[str] = []
    icon_total = 0
    bubble_total = 0
    for block in blocks:
        block_text = "\n".join(block)
        has_icon, gender = _block_has_person_icon(block_text)
        has_bubble = bool(SPEECH_BUBBLE_PATTERN.search(block_text))
        if not has_icon and not has_bubble:
            continue
        icon_label = {"female": "ONE FEMALE person icon", "male": "ONE MALE person icon", "neutral": "ONE person icon", "": "no person icon"}[gender]
        if has_icon:
            icon_total += 1
        if has_bubble:
            bubble_total += 1
        preview = _clean_block_visible_preview(block) or "this text block"
        parts = []
        if has_icon:
            parts.append(icon_label)
        if has_bubble:
            parts.append("ONE speech bubble attached to this same block/icon")
        groups.append(f"- Module group {len(groups)+1}: {', '.join(parts)} → bind to text block: {preview}")

    if not groups:
        return ""

    return f"""
[GRAPHIC MODULE BINDING LOCK]
Graphic modules belong to their own dashed-script block. Do not detach them and do not move them to unrelated cards.
{NL.join(groups)}

Binding rules:
- Do not duplicate person icons outside the listed module groups. Total person-icon groups from explicit markers: {icon_total}.
- Do not create extra person icons from words like 車手、丈夫、老闆娘、被害人、嫌犯; semantic role words are text only unless an explicit 假人ICON marker exists in that block.
- If a speech bubble appears in the same block as a male/female/person icon, the speech bubble must visually attach to that same icon/block with its tail pointing toward it.
- Never place a requested speech bubble in a separate unrelated card.
- Never infer additional icons from nearby text blocks.
""".strip()


def build_mandatory_graphic_module_lock(
    script: str
) -> str:

    modules = detect_mandatory_graphic_modules(
        script
    )

    groups = split_editorial_groups(
        script
    )

    if not modules:

        return """
[MANDATORY GRAPHIC MODULES]
No explicit graphic modules detected.
""".strip()

    lines = []

    for m in modules:

        bind = ""

        if m.get(
            "bind"
        ):
            bind = " (bind)"

        lines.append(
            f"- GROUP {m['group'] + 1}: "
            f"{m['type']} "
            f"x{m['count']}"
            f"{bind}"
        )

    return f"""
[EDITORIAL GROUP LOCK v23]

The user separated the script into {len(groups)} editorial groups.

Elements inside the same group must stay together.

Do not move modules across groups.

{NL.join(lines)}

RULES:

- Explicit icon beats semantic icon detection.

- Never duplicate people because of:
車手、丈夫、嫌犯、老闆娘。

- One icon means exactly one person.

- Speech bubble must stay attached to its icon.

- Brush / stamp stay inside their group.

- Do not scatter grouped modules.
""".strip()



def build_explicit_graphic_effect_lock(
    script: str
) -> str:

    has_brush = has_explicit_brush_tag(
        script
    )

    has_stamp = has_explicit_stamp_tag(
        script
    )

    brush_line = (
        "Explicit brush tag detected: render brush effect ONLY for the text directly attached to that marker."
        if has_brush
        else
        "No explicit brush tag detected: BRUSH EFFECTS ARE FORBIDDEN."
    )

    stamp_line = (
        "Explicit stamp tag detected: render stamp effect ONLY for the text directly attached to that marker."
        if has_stamp
        else
        "No explicit stamp tag detected: STAMP EFFECTS ARE FORBIDDEN."
    )

    return f"""
[EXPLICIT GRAPHIC EFFECT LOCK]

Brush and stamp effects are explicit-only.

- {brush_line}

- {stamp_line}

- If the user did not explicitly write:
筆刷 / #筆刷 / (+筆刷)

Do NOT render brush effects.

- If the user did not explicitly write:
蓋章 / #蓋章 / (+蓋章)

Do NOT render stamp effects.
""".strip()



def build_layout_diagnostics(parsed: ParsedInput, frame_type: str) -> str:
    """v22 修補：輸出給 Gemini Image 的版面診斷區塊。

    這個函式只產生 prompt 內部診斷文字，不會影響 Streamlit UI。
    目的：讓 build_final_prompt_v18() 已有的呼叫不再 NameError，
    同時強化圖區 / 模組 / 跑馬安全區 / 框訊單行標題規則。
    """
    image_tags = getattr(parsed, "image_tags", []) or []
    module_tags = getattr(parsed, "module_tags", []) or []
    title = clean_inline_text(getattr(parsed, "title", "") or "")

    image_lines: List[str] = []
    if image_tags:
        for i, tag in enumerate(image_tags, 1):
            image_lines.append(
                f"- Asset zone {i}: {tag} => reserve one clean protected blank area; delete the helper text from final pixels."
            )
    else:
        image_lines.append("- No explicit asset zone detected. Do not invent any photo / video / ROLL box.")

    module_lines: List[str] = []
    if module_tags:
        for i, tag in enumerate(module_tags, 1):
            module_lines.append(
                f"- Module {i}: {tag} => render only the intended graphic structure; delete the literal instruction text."
            )
    else:
        module_lines.append("- No explicit extra graphic module detected. Do not invent stickers, stamps, brush strokes, or extra cards.")

    frame_notes: List[str] = []
    if frame_type == "記者說新聞":
        frame_notes.extend([
            "- Reporter-news layout: headline stays at the absolute top; explanation modules fill left/right columns.",
            "- Right-bottom ticker hardware safe zone X>1332, Y>990 is background-only.",
            "- No text, icon, card, stamp, brush, shadow, or asset zone may enter the ticker safe zone.",
        ])
    elif str(frame_type).startswith("框訊") or frame_type == "框訊":
        frame_notes.extend([
            "- Framed-news layout: headline is single-line only, even if user supplied two headline phrases.",
            "- Keep all information cards aligned to a clean broadcast grid.",
            "- Do not create a second headline bar, subtitle bar, or duplicated headline fragment.",
        ])
    else:
        frame_notes.extend([
            "- Mega-headline layout: headline dominates the top band.",
            "- Lower modules must avoid every protected image zone with at least 20px buffer.",
        ])

    warnings = getattr(parsed, "warnings", []) or []
    warning_lines = [f"- {w}" for w in warnings] if warnings else ["- No parser warning."]

    return f"""
[LAYOUT DIAGNOSTICS v22 PATCH]
Parsed headline: {title if title else '未提供'}
Frame type: {frame_type}
Detected protected asset zones: {len(image_tags)}
Detected graphic / layout modules: {len(module_tags)}

Asset-zone diagnostics:
{NL.join(image_lines)}

Module diagnostics:
{NL.join(module_lines)}

Frame-specific diagnostics:
{NL.join(frame_notes)}

Parser warnings:
{NL.join(warning_lines)}

Hard diagnostic rules:
- Preserve every detected asset zone as an empty post-production area.
- Delete all helper words from final pixels: 圖, ROLL, 圖片區, 編輯圖片區, 4:3, 4:5, 色塊, 對話框, 數據框, 筆刷, 蓋章, 打卡LOGO, 打卡地點.
- Never add unrequested people, icons, logo, ticker, LIVE, dates, numbers, subtitles, labels, or decorative UI.
- If space is tight, shrink typography or rearrange modules; never overlap protected zones.
""".strip()

def build_final_prompt_v18(
    script: str,
    frame_type: str,
    style_name: str,
    icon_style: str,
    headline_mode: str,
    layout_mode: str,
    use_safe_zone: bool,
    ai_color: bool,
    notes: str,
    reporter_subtype_override: str = "",
    asset_aspect: str = "4:3 橫式素材框",
    visual_variation_mode: str = "固定風格庫",
) -> Tuple[str, ParsedInput]:
    parsed = parse_user_script(script)
    style = get_style_config(style_name)
    aspect_cfg = resolve_asset_aspect(asset_aspect)

    safe_zone_text = (
        """
[TICKER SAFETY - LOCKED FOR 記者說新聞]
Right-bottom hardware ticker safe zone: X > 1332 and Y > 990.
This 588x90 zone must contain background texture only.
No text, no icon, no UI cards, no stamps, no brush effects, no image zones.
Blend naturally with background.
Do not leave unnecessary empty space above it; fill content downward until Y=990 when possible.
""".strip()
        if frame_type == "記者說新聞"
        else """
[TICKER SAFETY]
Right-bottom hardware ticker safe zone: X > 1332 and Y > 990.
This 588x90 zone must contain background texture only.
No text, no icon, no UI cards. Blend naturally with background.
Do not leave unnecessary empty space above it; fill content downward until Y=990 when possible.
""".strip()
        if (use_safe_zone or frame_type == "記者說新聞")
        else "[TICKER SAFETY]" + NL + "Full canvas access. No ticker exclusion zone required."
    )

    icon_logic = "3D Volumetric / PBR-like depth" if icon_style == "3D" else "Flat 2D clean vector"
    variation_policy = build_visual_variation_policy(style_name, visual_variation_mode)
    if str(visual_variation_mode).startswith("AI自由"):
        color_logic = (
            f"AI free visual variation WITHIN the content style '{style_name}'. "
            "Use the detected/selected topic style as the semantic base, but vary background, lighting, card shapes, texture, and composition so outputs do not all look alike. "
            "Do not leave the topic style and do not add unapproved text or facts."
        )
    else:
        color_logic = (
            f"Dynamic contextual color based on headline sentiment: {clean_inline_text(parsed.title)}"
            if ai_color
            else f"Fixed palette: {style['palette']} | Highlight: {style['highlight']}"
        )

    prompt = f"""
[VISUAL DIRECTOR v18 DIRECTOR SYSTEM | BROADCAST NEWS CG]
CANVAS: 1920x1080 Full HD
LANGUAGE: Traditional Chinese ONLY

[STYLE - INTERNAL DESIGN REFERENCE ONLY, DO NOT RENDER AS TEXT]
CONTENT STYLE / TOPIC STYLE: {style_name}
SELECTED STYLE PRESET: internal visual style reference only, never visible typography.
{_style_render_ban_text()}
THEME DIRECTION: {style['theme']}
UI TEXTURE DIRECTION: {style['ui']}
VISUAL VARIATION SELECTION: {visual_variation_mode}
{variation_policy}
ICON STYLE: {icon_logic}
COLOR STRATEGY: {color_logic}

{safe_zone_text}

[HEADLINE]
Mode: {headline_mode}
TOP HEADLINE LOCK: The headline must always sit at the very top of the 1920x1080 canvas.
If Mode is 一行大標題: render the headline as one single line in the top headline band.
If Mode is 兩行大標題: force a two-line stacked headline in the top headline band.
Never place the headline in the middle or lower content area.
Headline must dominate the design. Use huge broadcast-style typography, strong outline, shadow, and layered emphasis.

{build_headline_mode_brief(script, headline_mode, frame_type)}

[HARD HEADLINE LOCK FOR 框訊]
If Frame Type contains "框訊":
- MUST use SINGLE LINE headline only.
- Never split Chinese headline into two lines.
- Never auto-wrap the headline.
- Never create secondary headline bars.
- Never duplicate headline fragments into a subtitle bar.
- If overflow occurs, reduce font size, tracking, outline thickness, or side margins; keep the headline on one baseline.

[LAYOUT]
Layout mode: {layout_mode}
{build_frame_rules(frame_type)}

{build_symbol_matrix_v17()}

[ROLL / IMAGE ASPECT SETTING]
Selected default asset-zone layout mode: {aspect_cfg['label']}
Rule: {aspect_cfg['directive']}
- Per-zone override is allowed and has highest priority.
- To mix ratios in the same CG, write the ratio inside each image tag: [圖-左ROLL 4:3], [圖-右人物 4:5], (#定監視器畫面 4:3), (LINE截圖 4:5).
- If a specific image tag contains 4:3, that protected zone must be 4:3 even when the global mode is 4:5 or AI自動配置排版.
- If a specific image tag contains 4:5, that protected zone must be 4:5 even when the global mode is 4:3 or AI自動配置排版.
- Image tags without explicit 4:3/4:5 use the selected default mode.
- If the default mode is AI自動配置排版, choose the best ratio ONLY for each existing unmarked asset zone. Do not add new asset zones for balance. Do not render ratio labels such as 4:3 or 4:5.
- Preserve clean empty interiors and safety buffers; do not stretch the overall 1920x1080 canvas.

[ASSET ZONE COUNT LOCK]
- Render exactly the asset zones explicitly provided by the user: {len(parsed.image_tags)} protected blank asset zone(s).
- Do not create extra photo boxes, video boxes, ROLL boxes, portrait boxes, placeholder boxes, document boxes, or empty frames for design balance.
- Do not split or duplicate requested asset zones.
- Never render helper words such as ROLL, video, 視頻, 影片, 圖片區, 編輯圖片區, 4:3, 4:5, 商比.
- IMPORTANT: deleting the helper text is not enough. Each ROLL/image helper must still reserve its protected blank space.

{build_roll_alias_reserve_lock(script, asset_aspect)}

{build_roll_location_badge_lock(script)}

{build_visual_token_compiler_block(script, frame_type, headline_mode, asset_aspect)}

{build_layout_diagnostics(parsed, frame_type)}

[v19.6 DIRECTOR DECISION]
- Frame Type: {frame_type}
- Reporter Subtype: {reporter_subtype_override if frame_type == '記者說新聞' and reporter_subtype_override else (detect_reporter_subtype(script) if frame_type == '記者說新聞' else 'N/A')}
- Information Density: {density_score(script)}
- Tone: {auto_detect_tone(script, frame_type)}
- Visual hierarchy: Main headline 300%, module title 160%, body text 100%.
- If density is high, use senior newsroom compact layout: smaller font, tighter line spacing, and stronger hierarchy; do not delete user-provided text.
- Conclusion Module Type: {detect_conclusion_module(script, frame_type, reporter_subtype_override if reporter_subtype_override else detect_reporter_subtype(script))['type']}
- Conclusion Sentence: {detect_conclusion_module(script, frame_type, reporter_subtype_override if reporter_subtype_override else detect_reporter_subtype(script))['sentence']}
- Conclusion Safe Position: {detect_conclusion_module(script, frame_type, reporter_subtype_override if reporter_subtype_override else detect_reporter_subtype(script))['position']}
- Conclusion module must be placed outside all image zones and outside ticker safe zone.

[FINAL IMAGE RESTRICTIONS - CRITICAL]
{STRICT_NO_EXTRA_FACTS.strip()}
- {_style_render_ban_text()}
- {_forbidden_helper_text_ban()}
- If the user provides a location badge, it is MANDATORY: render ONLY the location text itself, for example 新北市永和區; never render 打卡LOGO or 打卡地點 as words.
- DELETE ALL literal instruction tags, including [圖], [圖-xxx], (#定xxx), (色塊), (對話框), #筆刷.
- DELETE ALL double quotes and angle brackets after applying visual emphasis.
- Every detected image placeholder must become a clean empty protected zone for real post-production photos.
- No text/icon/UI/decoration/stamp/brush effect may touch or overlap protected image zones.
- Internal helper annotations are forbidden in final pixels: 左上圖, 左下圖, 右ROLL框, 中上文字色塊, 中下文字色塊, 4:3, 4:5, 圖, ROLL框, ROLL, 視頻, 影片, video, 圖片區, 編輯圖片區, 4:5商比, 商比, 色塊, 打卡LOGO.
- STAMP EFFECT IS EXPLICIT ONLY: Do not create stamp effects unless the user explicitly writes (#蓋章), (+蓋章), #蓋章, ---蓋章, or 蓋章效果.
- Stamp effects must be outside [圖] placeholders; stamps may sit on card borders, date labels, or background only ONLY when explicitly requested.
- BRUSH EFFECT IS EXPLICIT ONLY: Do not create brush effects unless the user explicitly writes (#筆刷), (+筆刷), (+筆刷字), (筆刷), #筆刷, ---筆刷, or 筆刷效果.
- Do not convert <emphasis>, quotes, numbers, conflict words, or normal body text into brush strokes.
- Do not duplicate body text into a separate brush/stamp/sticker module unless explicitly tagged. Only promote a sentence once.
- For all 框訊 layouts: two-line headline is forbidden; stacked headline is forbidden; secondary headline bars made from headline fragments are forbidden.
- For all 框訊 layouts: the full 大標 block must be rendered; do not drop the second headline phrase.
- For all 框訊 layouts: if the headline is long, reduce font size or tighten spacing; do not wrap.
{build_zero_assumption_policy(script)}
- If any image zone and text compete for space, preserve both by reducing spacing, reducing font size, or rearranging modules.
- Final result must be a professional TV news CG, not a poster, not a webpage.

[APPROVED CONTENT SUMMARY - DO NOT RENDER THIS SECTION TITLE]
{_approved_text_block_for_prompt(script)}

[DIRECTOR NOTES]
{notes.strip()}
"""
    return textwrap.dedent(prompt).strip(), parsed




def build_senior_news_cg_designer_policy() -> str:
    """
    v22 核心：把 Gemini Image 當作 20 年台灣新聞台 CG 美術總監。
    只排版使用者給的字，圖區留白，不腦補新聞內容。
    """
    return """
[ROLE｜20-YEAR SENIOR TAIWANESE NEWS CG DESIGNER]
You are not an AI illustrator, web UI designer, poster designer, or magazine designer.
You are a senior Taiwanese TV news CG visual designer with 20 years of newsroom experience.

Your job:
Turn the exact user-provided Traditional Chinese text into a professional broadcast-ready Taiwanese TV news CG.
Do not create new story content.
Do not add new facts.
Do not rewrite or summarize the news.
Do not delete user-provided text because the layout is crowded.

[NEWSROOM CG WORKFLOW]
Think like a real TV news visual staff member:
- make the headline dominant and readable
- arrange high-density Chinese text with clear hierarchy
- use broadcast-style headline bands, evidence boards, warning strips, stamps, portrait strips, callout bands, and hard news composition when appropriate
- reserve clean blank image areas for post-production photos/video/documents
- make the result suitable for on-air Taiwanese TV news

[TEXT TRUTH LOCK]
Use ONLY the Traditional Chinese text provided by the user.
Never invent names, places, institutions, dates, numbers, charges, conclusions, captions, labels, or extra facts.
Never translate.
Never add English.
Never add random numbers.
Never create fake Chinese-like glyphs.
Never duplicate names, numbers, keywords, or headline fragments.

If space is tight:
reduce font size,
reduce line spacing,
reduce padding,
rearrange modules,
but do not delete user-provided text.

[PHOTO / VIDEO ZONE LOCK]
Any user-marked image/photo/video/document area is for post-production.
It must stay clean and empty.
No fake photos.
No fake screenshots.
No icons.
No labels.
No words like IMAGE BOX, PHOTO, PLACEHOLDER, SECTION, CARD, DEBUG.
No text, stamps, brush strokes, shadows, or decorations may enter the blank image area.
Keep a clean safety buffer.

[STYLE LOCK]
Final image must look like a Taiwanese TV news CG.
Not a dashboard.
Not an app UI.
Not a webpage.
Not a magazine cover.
Not a social media post.
Not a movie poster.
Avoid clean SaaS-card/dashboard aesthetics unless the news content explicitly requires a data dashboard.

[OUTPUT DISCIPLINE]
The final artwork should look like a human newsroom CG designer made it,
not like a parser rendered a specification.
Do not show prompt syntax, brackets, debug labels, section names, image-box names, placeholder labels, or instruction labels.
""".strip()


# =========================================================
# v20.5 CG Prompt Translator：把導演規格轉成 Gemini Image 看得懂的乾淨影像 prompt
# =========================================================
def _extract_asset_zones(script: str) -> List[str]:
    """抓出所有素材保護區語法。

    支援：
    - (右ROLL框 4:5) / [圖-左主 4:3]
    - 右邊 roll框 4:5
    - +++右ROLL+++
    - 左：(圖 4:3) / 右：ROLL框
    """
    if not script:
        return []
    tags = _collect_square_tags(script) + _collect_parenthesis_tags(script)
    zones = [tag.strip() for tag in tags if is_asset_protection_tag(tag)]

    for raw_line in (script or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalized = _normalize_spatial_alias_text(line) if '_normalize_spatial_alias_text' in globals() else line

        # 欄位寫法：左：(圖 4:3)、右：ROLL框
        m = re.match(r"^(左|中|右)\s*[：:]\s*(.+)$", normalized, flags=re.IGNORECASE)
        if m and re.search(r"(圖|圖片|ROLL|roll|影片|視頻)", m.group(2), flags=re.IGNORECASE):
            zones.append(f"({m.group(1)}{m.group(2)})")
            continue

        # 口語/裝飾寫法：右邊 roll框 4:5、+++右ROLL+++、++ROLL++、左ROLL=、開框roll
        if re.search(r"(打卡符號|打卡LOGO|打卡地點)", line, flags=re.IGNORECASE):
            # 地點打卡是 ROLL 的附屬 badge，不是新的圖區。
            continue

        if re.search(r"(圖|圖片|ROLL|roll|Roll|影片|視頻|開框)", normalized, flags=re.IGNORECASE) or _has_roll_alias(normalized):
            # 排除含真正新聞文字的小標/內文，避免誤抓。純 helper line 才補進圖區。
            if _is_layout_helper_line(line) or _has_roll_alias(line) or re.fullmatch(r"[+＋#＃\-—_＝=＊*\s]*(左|中|右)?.*?(圖|圖片|ROLL|roll|Roll|開框ROLL|開框roll|影片|視頻).*?[+＋#＃\-—_＝=＊*\s]*(4\s*[:：]\s*[35])?[+＋#＃\-—_＝=＊*\s]*", normalized, flags=re.IGNORECASE):
                zones.append(f"({line})")

    return _dedupe_asset_zone_list(list(dict.fromkeys([z for z in zones if z])))


def build_roll_alias_reserve_lock(script: str, asset_aspect: str = "AI自動配置排版") -> str:
    """
    v22.8：ROLL alias is an asset-zone instruction, not visible text.
    Ensures shorthand like ++ROLL++, 左ROLL=, 右ROLL：, (開框roll) reserves blank space.
    """
    zones = _extract_asset_zones(script)
    roll_zones = [z for z in zones if _has_roll_alias(z)]
    if not roll_zones:
        return ""

    lines = []
    for idx, tag in enumerate(roll_zones, start=1):
        col = _detect_column_from_text(tag) or "AUTO"
        vert = _detect_vertical_from_text(tag) or "AUTO"
        ratio = resolve_asset_aspect_for_tag(tag, asset_aspect).get("ratio", "auto")
        col_label = {"LEFT": "left column", "CENTER": "center column", "RIGHT": "right column", "AUTO": "AI-selected column"}.get(col, col)
        vert_label = {"TOP": "top", "BOTTOM": "bottom", "AUTO": "AI-selected vertical position"}.get(vert, vert)
        open_note = "open-frame ROLL" if re.search(r"開框", tag, flags=re.IGNORECASE) else "ROLL/media"
        lines.append(f"- Requested {open_note} blank asset zone {idx}: reserve clean empty space, {ratio} ratio, {col_label}, {vert_label}. Do NOT render the source marker text `{tag}`.")

    return (
        "[ROLL ALIAS RESERVE LOCK]\n"
        "The following shorthand markers are INTERNAL LAYOUT INSTRUCTIONS that MUST reserve visible blank media space.\n"
        "They are not visible text and must never be printed on the artwork.\n"
        f"{NL.join(lines)}\n"
        "Rules:\n"
        "- Every ROLL alias marker creates one protected blank ROLL/media area.\n"
        "- Do not ignore these aliases. Do not merely delete them. Reserve the space.\n"
        "- Do not render ROLL, roll, 開框roll, 左ROLL, 右ROLL, ++ROLL++, +++右ROLL+++, 4:3, or 4:5 as text.\n"
        "- Keep interiors empty for post-production footage/photo insertion."
    ).strip()


def _extract_approved_text_whitelist(script: str) -> List[str]:
    """
    v20.6.7：抽出允許 Gemini Image 生成的繁體中文字白名單。

    修正重點：
    - 保留時間格式，例如 14:30、08:05，不再切成兩行。
    - 欄位控制用冒號，例如「標:」、「內容：」，仍會轉成換行。
    - 只拿使用者原稿中真正要上畫面的文字；移除素材保護區與版型指令。
    """
    if not script:
        return []

    text = script

    # 移除素材保護區標籤，避免把「定國防部外觀照」這種素材說明當成要上字。
    for tag in _extract_asset_zones(text):
        text = text.replace(tag, "\n")

    # 移除純版型指令，但保留 <高權重文字>、【小標文字】 內文。
    text = re.sub(
        r"\([^\)]*(?:色塊|對話框|數據框|筆刷|蓋章|icon|假人|頭\+字|打卡)[^\)]*\)",
        "\n",
        text,
    )
    text = re.sub(
        r"[#＃](?:色塊|對話框|數據框|筆刷|蓋章|icon|假人|打卡|開框roll|開框ROLL|定圖)",
        "\n",
        text,
    )

    text = text.replace("<", "").replace(">", "")
    text = text.replace("【", "").replace("】", "")
    text = text.replace("[", "").replace("]", "")
    text = text.replace('"', "")
    text = text.replace("---", "\n")

    # v20.6.7 BUG FIX：
    # 保留 14:30、08:05 這種時間格式；
    # 只有左右不是數字的欄位冒號才切行，例如「標:」、「內容：」。
    text = re.sub(
        r"(?<!\d)[：:](?!\d)",
        "\n",
        text,
    )

    candidates: List[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.strip().split())
        if not line:
            continue
        # 欄位 key / 特效 / ROLL / 圖區註記只給導演理解，不可成為畫面文字。
        line = re.sub(r"^(主標|大標|標題|標|小標)\s*[=:：＝]\s*", "", line).strip()
        if not line:
            continue
        if _is_control_or_effect_visible_text(line):
            continue
        if _is_layout_helper_line(line):
            continue

        # 過濾欄位控制字。
        if line in ["左", "右", "中", "中央", "上", "下", "大標", "主標", "標", "標題", "內容", "框訊", "圖片"]:
            continue

        if len(line) > 34:
            # 長句保留，但避免塞爆 prompt；影像模型只需要知道文字白名單。
            line = line[:34]

        # 至少包含中文字、數字或百分比，才視為可上畫面文字。
        if re.search(r"[\u4e00-\u9fff0-9%％]", line):
            candidates.append(line)

    return list(dict.fromkeys(candidates))[:24]


def _strip_director_syntax(text: str) -> str:
    """把新聞台控制語法轉成語意提示，不把素材/版型標籤當成要畫出的文字。"""
    text = text or ""
    for tag in _extract_asset_zones(text):
        text = text.replace(tag, " protected empty photo/video asset zone ")
    text = re.sub(r"\([^\)]*(?:色塊|對話框|數據框|筆刷|蓋章|icon|假人|打卡)[^\)]*\)", " broadcast graphic module ", text)
    text = re.sub(r"[#＃][\w\u4e00-\u9fff]+", " broadcast effect module ", text)
    text = re.sub(r"\[(TYPE|FRAME|TITLE|BODY|STYLE|LAYOUT|HEADLINE|CONTENT SCRIPT|DIRECTOR NOTES)[^\]]*\]", " ", text, flags=re.I)
    text = text.replace("<", "").replace(">", "")
    text = text.replace("【", "").replace("】", "")
    text = text.replace("[", "").replace("]", "")
    text = text.replace("(", "").replace(")", "")
    return " ".join(text.split())


# =========================================================
# v20.6.6 Visual Token Compiler：把人類 DSL 先編譯成圖片模型懂的區塊
# =========================================================
def _clean_visual_text(text: str) -> str:
    """移除導演符號，只留下可上畫面的純文字；避免 < > 造成 token 重複。"""
    if not text:
        return ""
    t = text.strip()
    for tag in _extract_asset_zones(t):
        t = t.replace(tag, " ")
    t = re.sub(r"\([^)]*(?:色塊|方框|對話框|數據框|筆刷|蓋章|icon|假人|打卡)[^)]*\)", " ", t)
    t = re.sub(r"^[\-—=]{2,}$", " ", t)
    for _hk in ["標題:", "標題：", "標題=", "標題＝", "大標:", "大標：", "大標=", "大標＝", "主標:", "主標：", "主標=", "主標＝", "標:", "標：", "標=", "標＝"]:
        t = t.replace(_hk, "")
    t = t.replace("小標:", "").replace("小標=", "")
    t = t.replace("標:", "").replace("標=", "")
    t = t.replace("<", "").replace(">", "")
    t = t.replace("【", "").replace("】", "")
    t = t.replace("[", "").replace("]", "")
    t = t.replace('"', "").replace("“", "").replace("”", "")
    return " ".join(t.split()).strip()


def _split_script_blocks(script: str) -> List[List[str]]:
    """用 ---- / 空行切出視覺卡片區塊；分隔線永遠不渲染。"""
    blocks: List[List[str]] = []
    current: List[str] = []
    for raw in (script or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"[-—=]{3,}", line):
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _asset_zone_spatial_hint(tag: str, index: int, total: int, frame_type: str) -> str:
    """
    v22：把圖區翻成新聞台美術語言，不輸出 debug 字。
    若使用者在 tag 裡寫左/中/右/上/下，這裡只保留為位置語意，
    不把原始標籤文字交給影像模型當成可見文字。
    """
    t = (tag or "").lower()
    pos_hint = ""
    raw = tag or ""
    if "左上" in raw:
        pos_hint = " placed in the upper-left area as requested, "
    elif "左下" in raw:
        pos_hint = " placed in the lower-left area as requested, "
    elif "右上" in raw:
        pos_hint = " placed in the upper-right area as requested, "
    elif "右下" in raw:
        pos_hint = " placed in the lower-right area as requested, "
    elif "中上" in raw:
        pos_hint = " placed in the upper-center area as requested, "
    elif "中下" in raw:
        pos_hint = " placed in the lower-center area as requested, "
    elif "左" in raw:
        pos_hint = " placed in the left column as requested, "
    elif "中" in raw:
        pos_hint = " placed in the center column as requested, "
    elif "右" in raw:
        pos_hint = " placed in the right column as requested, "

    if any(k in tag for k in ["常如山圖", "工程師圖", "特助圖", "人圖", "人物圖"]):
        return (
            "a vertical portrait photo slot with newsroom-style border, "
            "part of a portrait strip if multiple portrait slots are requested, "
            "blank interior reserved for real portrait insertion"
        )

    if "roll" in t or "roll" in tag or "ROLL" in tag:
        if "1/3" in tag or "三分之一" in tag:
            return (
                "a large main blank media area, about one third of the canvas, "
                f"{pos_hint or 'placed in a main visual zone, '}"
                "blank interior reserved for real post-production material insertion"
            )
        if "1/4" in tag or "四分之一" in tag:
            return (
                "a large main blank media area, about one quarter of the canvas, "
                f"{pos_hint or 'placed in a main visual zone, '}"
                "blank interior reserved for real post-production material insertion"
            )
        return (
            f"a large main blank media area {pos_hint or 'in a main visual zone, '}"
            "blank interior reserved for real post-production material insertion"
        )

    if any(k in tag for k in ["簽約", "合約", "文件", "文書", "書面"]):
        return (
            f"a document evidence area {pos_hint or 'near the related text module, '}"
            "clean blank interior reserved for a real document image"
        )

    if frame_type == "記者說新聞":
        return (
            "a clean editorial photo zone inside the explanatory layout, "
            "away from ticker-safe zone, blank for post-production"
        )

    return (
        f"a clean editorial blank media area {pos_hint or 'clearly separated from text, '}"
        "blank for post-production real image insertion"
    )



def _detect_column_from_text(text: str) -> str:
    raw = _normalize_spatial_alias_text(text or "") if '_normalize_spatial_alias_text' in globals() else str(text or "")
    if "左" in raw:
        return "LEFT"
    if "中" in raw:
        return "CENTER"
    if "右" in raw:
        return "RIGHT"
    return ""


def _detect_vertical_from_text(text: str) -> str:
    raw = str(text or "")
    if "上" in raw:
        return "TOP"
    if "下" in raw:
        return "BOTTOM"
    return ""


def detect_spatial_mode(script: str) -> str:
    """
    三層位置模式：
    1. EXACT_POSITION：左上/左下/中上/中下/右上/右下 → 欄位與上下都硬鎖。
    2. COLUMN_POSITION：左：/中：/右： 或 左圖/右ROLL/中文字色塊 → 只鎖欄位，欄內上下交給 AI。
    3. AI_FREE：沒有任何位置標記 → 保留 AI 自動配置排版。
    """
    raw = _normalize_spatial_alias_text(script or "")
    if re.search(r"(左上|左下|右上|右下|中上|中下)", raw):
        return "EXACT_POSITION"
    if re.search(r"(^|\n)\s*(左|中|右)\s*[：:]", raw):
        return "COLUMN_POSITION"
    if re.search(r"[(\[]\s*(左|中|右)(?!上|下)[^\]\)]*(圖|ROLL|roll|色塊|文字|框)", raw, flags=re.IGNORECASE):
        return "COLUMN_POSITION"
    if re.search(r"(^|\n)\s*[+＋#＃\-—_＝=＊*\s]*(左|中|右).*?(圖|ROLL|roll|Roll|開框ROLL|色塊|文字|框).*?[+＋#＃\-—_＝=＊*\s]*(?=\n|$)", raw, flags=re.IGNORECASE):
        return "COLUMN_POSITION"
    if _has_roll_alias(raw) and re.search(r"(左|右|中)", raw):
        return "COLUMN_POSITION"
    return "AI_FREE"


def _clean_spatial_visible_text(text: str) -> str:
    """空間解析用：只取語意，不輸出導演 helper 字。"""
    if _is_layout_helper_line(text):
        return ""
    t = _clean_visual_text(_normalize_spatial_alias_text(text or ""))
    t = re.sub(r"^(左|中|右)\s*[：:]", "", t).strip()
    t = re.sub(r"(左上|左下|右上|右下|中上|中下|左|中|右)", "", t)
    t = re.sub(r"(圖|圖片|ROLL框|ROLL|roll|影片|視頻|文字色塊|色塊|4\s*[:：]\s*[35]|[+＋#＃\-—_＝=＊*]+)", "", t, flags=re.IGNORECASE)
    return " ".join(t.split()).strip()


def _extract_spatial_items(script: str, asset_aspect: str = "AI自動配置排版") -> List[Dict[str, str]]:
    """從原稿抽出位置註記，但不把註記字樣當成畫面文字。"""
    items: List[Dict[str, str]] = []

    # 先從完整 tag 抽一次，這樣多行括號如：
    # (右ROLL框 4:5
    # +打卡LOGO:新北市永和區)
    # 也能正確保留「右」與 4:5，不會被逐行 parser 漏掉。
    for tag in _extract_asset_zones(script):
        col = _detect_column_from_text(tag)
        vert = _detect_vertical_from_text(tag)
        if col or vert:
            zcfg = resolve_asset_aspect_for_tag(tag, asset_aspect)
            items.append({
                "kind": "asset",
                "column": col or "AUTO",
                "vertical": vert or "AUTO",
                "ratio": zcfg.get("ratio", "auto"),
                "source": tag,
                "visible_text": "",
            })

    for tag in parse_user_script(script).module_tags:
        if is_asset_protection_tag(tag):
            continue
        col = _detect_column_from_text(tag)
        vert = _detect_vertical_from_text(tag)
        if col or vert:
            items.append({
                "kind": "text",
                "column": col or "AUTO",
                "vertical": vert or "AUTO",
                "ratio": "text",
                "source": tag,
                "visible_text": "",
            })

    lines = (script or "").splitlines()
    pending_text_position: Optional[Tuple[str, str]] = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        normalized_line = _normalize_spatial_alias_text(line)

        # 欄位級寫法：左：(圖 4:3) / 中：文字色塊 / 右：ROLL框
        prefix_col = ""
        prefix_match = re.match(r"^(左|中|右)\s*[：:]\s*(.*)$", normalized_line)
        content = normalized_line
        if prefix_match:
            prefix_col = {"左": "LEFT", "中": "CENTER", "中央": "CENTER", "右": "RIGHT"}.get(prefix_match.group(1), "")
            content = prefix_match.group(2).strip()

        tags = _collect_parenthesis_tags(content) + _collect_square_tags(content)
        handled = False
        for tag in tags:
            is_asset = is_asset_protection_tag(tag)
            is_text_module = bool(re.search(r"(文字|色塊|方框|對話框|數據框)", tag)) and not is_asset
            if not (is_asset or is_text_module):
                continue
            col = prefix_col or _detect_column_from_text(tag)
            vert = _detect_vertical_from_text(tag)
            if not col and not vert:
                continue
            kind = "asset" if is_asset else "text"
            zcfg = resolve_asset_aspect_for_tag(tag, asset_aspect) if kind == "asset" else {"ratio": "text"}
            items.append({
                "kind": kind,
                "column": col or "AUTO",
                "vertical": vert or "AUTO",
                "ratio": zcfg.get("ratio", "auto"),
                "source": tag,
                "visible_text": "",
            })
            handled = True
            if kind == "text":
                pending_text_position = (col or "AUTO", vert or "AUTO")

        # 無括號欄位寫法：中：文字色塊 / 右：ROLL框
        if prefix_col and not handled:
            if re.search(r"(圖|ROLL|roll|影片|照片)", content):
                pseudo = f"({content})"
                zcfg = resolve_asset_aspect_for_tag(pseudo, asset_aspect)
                items.append({"kind": "asset", "column": prefix_col, "vertical": "AUTO", "ratio": zcfg.get("ratio", "auto"), "source": pseudo, "visible_text": ""})
            elif re.search(r"(文字|色塊|方框|卡|模組)", content):
                items.append({"kind": "text", "column": prefix_col, "vertical": "AUTO", "ratio": "text", "source": content, "visible_text": ""})
                pending_text_position = (prefix_col, "AUTO")
            else:
                vt = _clean_spatial_visible_text(content)
                if vt:
                    items.append({"kind": "text", "column": prefix_col, "vertical": "AUTO", "ratio": "text", "source": "column text", "visible_text": vt})
                    pending_text_position = (prefix_col, "AUTO")

        # 無前綴但有口語/裝飾位置：右邊 roll框 4:5 / +++右ROLL+++ / 中間文字色塊
        elif not prefix_col and re.search(r"(左|中|右)", normalized_line) and re.search(r"(圖|圖片|ROLL|roll|影片|視頻|文字|色塊|方框|框)", normalized_line, flags=re.IGNORECASE):
            if re.search(r"(圖|圖片|ROLL|roll|影片|視頻)", normalized_line, flags=re.IGNORECASE):
                zcfg = resolve_asset_aspect_for_tag(f"({line})", asset_aspect)
                items.append({"kind": "asset", "column": _detect_column_from_text(normalized_line) or "AUTO", "vertical": _detect_vertical_from_text(normalized_line) or "AUTO", "ratio": zcfg.get("ratio", "auto"), "source": line, "visible_text": ""})
            elif re.search(r"(文字|色塊|方框|卡|模組)", normalized_line):
                col = _detect_column_from_text(normalized_line) or "AUTO"
                vert = _detect_vertical_from_text(normalized_line) or "AUTO"
                items.append({"kind": "text", "column": col, "vertical": vert, "ratio": "text", "source": line, "visible_text": ""})
                pending_text_position = (col, vert)

        # 接在「(中上文字色塊)」後面的真正小標/內文，綁回該文字色塊的位置。
        elif pending_text_position and not line.startswith(("(", "[", "+", "＋", "大標", "標題", "標:")):
            if re.search(r"(打卡LOGO|打卡地點|地點\s*[：:])", line, flags=re.IGNORECASE):
                continue
            vt = _clean_spatial_visible_text(line)
            if vt and not re.fullmatch(r"[-—=]{3,}", line):
                col, vert = pending_text_position
                items.append({"kind": "text_content", "column": col, "vertical": vert, "ratio": "text", "source": "text content", "visible_text": vt})

    # 去重：保留順序；asset 用 canonical key 去重，避免同一 ROLL/圖區被重複算成多個洞。
    deduped: List[Dict[str, str]] = []
    seen = set()
    for it in items:
        if it.get("kind") == "asset":
            key = ("asset", _canonical_asset_zone_key(it.get("source", "")))
        else:
            key = (it["kind"], it["column"], it["vertical"], it["ratio"], it.get("visible_text", ""), it.get("source", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    return deduped


def build_spatial_layout_lock(script: str, asset_aspect: str = "AI自動配置排版") -> str:
    """條件式位置鎖：有標位置才鎖；沒標位置就保留 AI 自主排版。"""
    mode = detect_spatial_mode(script)
    items = _extract_spatial_items(script, asset_aspect)

    if mode == "AI_FREE":
        return """
[SPATIAL LAYOUT MODE]
No explicit left/center/right/top/bottom spatial markers were found.
AI may freely choose the best newsroom composition for the existing requested text modules and protected blank asset areas.
Do not create extra asset zones and do not render helper labels.
""".strip()

    col_name = {"LEFT": "LEFT COLUMN", "CENTER": "CENTER COLUMN", "RIGHT": "RIGHT COLUMN", "AUTO": "UNSPECIFIED COLUMN"}
    vert_name = {"TOP": "top position", "BOTTOM": "bottom position", "AUTO": "AI may choose vertical order inside this column"}
    lines: List[str] = []
    for it in items:
        kind = {
            "asset": "protected blank asset area",
            "text": "text information block",
            "text_content": "visible text belonging to the previous text block",
        }.get(it["kind"], it["kind"])
        extra = f"; internal ratio {it['ratio']}" if it["kind"] == "asset" else ""
        visible = f"; visible text: {it['visible_text']}" if it.get("visible_text") else ""
        lines.append(f"- {col_name.get(it['column'], it['column'])}: {vert_name.get(it['vertical'], it['vertical'])} → {kind}{extra}{visible}")

    if not lines:
        lines.append("- Spatial markers were detected, but no valid module was extracted. Respect the left/center/right semantics from the user script silently.")

    if mode == "EXACT_POSITION":
        header = "[SPATIAL LAYOUT LOCK - EXACT POSITION]"
        rule = "Use a strict three-column / upper-lower newsroom grid. User-specified column and top/bottom positions are mandatory."
        freedoms = "Do not rebalance, swap columns, move left assets to center, move right media to left, or move center text cards to another column."
    else:
        header = "[SPATIAL LAYOUT LOCK - COLUMN POSITION]"
        rule = "Respect user-specified LEFT / CENTER / RIGHT columns. Vertical order inside each column may be optimized by AI unless top/bottom is explicitly written."
        freedoms = "Do not swap columns. Do not move left-column items to center/right, center-column items to left/right, or right-column items to left/center."

    return f"""
{header}
{rule}

Extracted internal spatial map, NOT visible text:
{NL.join(lines)}

Hard spatial rules:
- Position words such as 左、右、中、上、下 are layout instructions only; never render those helper words on screen.
- Keep the final artwork consistent with the extracted spatial map.
- {freedoms}
- If the layout is crowded, reduce font size, spacing, or module padding; do not violate the requested spatial positions.
""".strip()

def build_visual_token_compiler_block(script: str, frame_type: str, headline_mode: str, asset_aspect: str = "4:3 橫式素材框") -> str:
    """
    v22：內部仍解析 headline / text groups / asset zones，
    但給 Gemini Image 的是新聞台資深美術 briefing。
    不輸出 SECTION_CARD_01 / IMAGE_BOX_01 / compiler count 這類機器語言。
    """
    parsed = parse_user_script(script)
    aspect_cfg = resolve_asset_aspect(asset_aspect)
    headline_lines = extract_headline_lines(script)
    headline_line_set = set(headline_lines)
    title = build_headline_display_text(script, headline_mode)
    title_for_inline = _clean_visual_text(title.replace("\n", ""))
    asset_zones = _extract_asset_zones(script)
    blocks = _split_script_blocks(script)

    text_groups: List[List[str]] = []
    for block in blocks:
        clean_lines: List[str] = []
        for line in block:
            cleaned_for_headline_check = _clean_visual_text(line)
            if line.strip() == parsed.title.strip() or cleaned_for_headline_check in headline_line_set or line.strip().startswith(("標:", "標：", "標=", "標＝", "大標:", "大標：", "大標=", "大標＝", "主標:", "主標：", "主標=", "主標＝", "標題:", "標題：", "標題=", "標題＝")):
                continue
            if is_asset_protection_tag(line) or _is_layout_helper_line(line):
                continue
            if re.fullmatch(r"[-—=]{3,}", line):
                continue
            cleaned = _clean_visual_text(line)
            if cleaned:
                clean_lines.append(cleaned)
        if clean_lines:
            text_groups.append(clean_lines[:6])

    portrait_tags = [z for z in asset_zones if any(k in z for k in ["常如山圖", "工程師圖", "特助圖", "人圖", "人物圖"])]
    roll_tags = [z for z in asset_zones if ("roll" in z.lower() or "ROLL" in z)]
    document_tags = [z for z in asset_zones if any(k in z for k in ["文件", "簽約", "合約", "文書"])]
    other_tags = [z for z in asset_zones if z not in portrait_tags + roll_tags + document_tags]

    text_group_lines: List[str] = []
    for idx, lines in enumerate(text_groups, start=1):
        joined = " / ".join(lines)
        text_group_lines.append(f"- News text module {idx}: {joined}")

    if not text_group_lines:
        text_group_lines.append("- No body text module provided. Keep the content area clean and do not invent text.")

    location_badges = _extract_location_badges(script)
    if location_badges:
        for loc in location_badges:
            text_group_lines.append(f"- MANDATORY ROLL-attached location badge: render ONLY the location text `{loc}` with a map-pin/check-in icon as a small colored strip attached to the requested ROLL/media frame. Do not render 打卡符號, 打卡LOGO, 打卡地點, or 地點 as words. Do not create an extra asset zone for this badge.")

    visual_zone_lines: List[str] = []
    if portrait_tags:
        portrait_ratios = ", ".join([resolve_asset_aspect_for_tag(z, asset_aspect)['ratio'] for z in portrait_tags])
        visual_zone_lines.append(
            f"- Internal layout instruction: reserve {len(portrait_tags)} portrait-style blank area(s) with clean borders and empty interiors. Per-area ratio instructions: {portrait_ratios}. Do not write any label or ratio on screen."
        )
    for tag in roll_tags:
        zcfg = resolve_asset_aspect_for_tag(tag, asset_aspect)
        visual_zone_lines.append(f"- Internal layout instruction: reserve one requested blank media area with {zcfg['ratio']} ratio. {_asset_zone_spatial_hint(tag, 0, len(asset_zones), frame_type)} Do not render the original marker text or ratio words.")
    for tag in document_tags:
        zcfg = resolve_asset_aspect_for_tag(tag, asset_aspect)
        visual_zone_lines.append(f"- Internal layout instruction: reserve one requested blank document/evidence area with {zcfg['ratio']} ratio. {_asset_zone_spatial_hint(tag, 0, len(asset_zones), frame_type)} Do not render the original marker text or ratio words.")
    for i, tag in enumerate(other_tags, start=1):
        zcfg = resolve_asset_aspect_for_tag(tag, asset_aspect)
        visual_zone_lines.append(f"- Internal layout instruction: reserve one requested blank media area with {zcfg['ratio']} ratio. {_asset_zone_spatial_hint(tag, i, len(asset_zones), frame_type)} Do not render the original marker text or ratio words.")

    if not visual_zone_lines:
        visual_zone_lines.append("- No explicit blank asset area was provided. Do not invent fake media frames or unnecessary placeholders.")

    if any(word in script for word in ["罪嫌", "性剝削", "聲押", "報案", "偷拍", "偵辦", "羈押"]):
        suggested_layout = (
            "Use hard crime-news composition: huge layered headline, strong warning colors, "
            "portrait strip when portrait slots exist, prosecutor/evidence board for charges or legal items, "
            "callout stamp or brush only when explicitly requested, and clean blank editorial photo zones."
        )
    elif any(word in script for word in ["股", "億", "%", "營收", "財經", "漲", "跌"]):
        suggested_layout = (
            "Use financial broadcast composition with strong number hierarchy and clean data panels, "
            "but still avoid web dashboard aesthetics."
        )
    else:
        suggested_layout = (
            "Use professional Taiwanese TV news composition with strong headline hierarchy, "
            "clear broadcast modules, and clean post-production image zones."
        )

    return f"""
[SENIOR NEWS CG DESIGN BRIEF v22]
You are a 20-year Taiwanese TV news CG visual designer.
This is a newsroom layout brief, not a UI spec.
Do not render any instruction labels, module labels, placeholder labels, debug labels, bracket syntax, or prompt syntax.

Headline treatment:
- Use {headline_mode}.
{build_headline_mode_brief(script, headline_mode, frame_type)}
- Place the headline at the top only.
- If this is a 框訊 layout, the COMPLETE headline must appear on ONE SINGLE LINE: no missing second phrase, no wrapping, no stacked headline, no second headline bar.
- For 框訊, fit the full headline on one baseline by reducing font size, tracking, outline thickness, or side margins.
- If this is 標大框 and headline mode is 兩行大標題, keep LINE 1 and LINE 2 as two separate mega headline rows.
- Make it visually dominant, like an on-air Taiwanese TV news mega headline.
- Use layered Chinese broadcast typography: thick strokes, outline, shadow, strong contrast.
- Render the headline once only. Do not duplicate names or headline fragments.

User-provided text to arrange:
{NL.join(text_group_lines)}

{build_mandatory_graphic_module_lock(script)}

{build_explicit_graphic_effect_lock(script)}

Text arrangement rules:
- Use only the user-provided Traditional Chinese text above.
- Do not add any words, captions, labels, English, random numbers, or fake Chinese.
- Do not rewrite or summarize the text.
- Do not delete text because the layout is crowded.
- If crowded, reduce font size, line spacing, or module padding, or rearrange the layout.

{build_spatial_layout_lock(script, asset_aspect)}

Protected blank asset areas to leave blank:
- Total requested blank asset area count: {len(asset_zones)}. Final artwork must contain exactly this count; do not add extra frames.
- Asset zone layout mode: {aspect_cfg['label']} ({aspect_cfg['ratio']}). Apply ratio instructions only to existing requested blank areas, silently; never display ratio words.
{NL.join(visual_zone_lines)}

Image-zone rules:
- These are blank areas for later post-production.
- ROLL aliases such as ++ROLL++, +++右ROLL+++, 左ROLL=, 右ROLL：, and (開框roll) MUST reserve actual blank media areas here; never treat them as text-only notes.
- A line like 高雄三民區---打卡符號 is a location badge attached to the ROLL frame; it must not create an extra blank zone.
- Keep interiors clean and empty.
- No fake photo, no fake screenshot, no icon, no text, no label, no decoration inside.
- Do not show words such as photo, image box, placeholder, section, source marker, or debug label.
- Keep text and effects outside these blank zones.

Broadcast layout direction:
- {suggested_layout}
- Think like a real news CG artist, not a parser.
- Do not make it look like a website, app UI, SaaS dashboard, magazine cover, social media card, or movie poster.
- The final image should look ready for a Taiwanese TV news broadcast.
""".strip()

def _frame_visual_intent(frame_type: str, reporter_subtype: str, headline_mode: str) -> str:
    if frame_type == "標大框":
        return (
            "top 35 to 45 percent reserved for a dominant mega headline; "
            "lower area should be arranged like a real Taiwanese news CG, not a web UI; "
            "use portrait strips, evidence boards, warning callouts, legal item boards, and editorial blank image zones when the script implies them; "
            "keep all provided text and image zones visible; never delete content because of crowding"
        )
    if frame_type == "記者說新聞":
        if reporter_subtype == "表格數據型":
            return (
                "top headline band, central table-like infographic area, clean rows and columns, "
                "right-bottom ticker exclusion area kept as background only"
            )
        if reporter_subtype == "卡片條列型":
            return (
                "top headline band, six compact information cards arranged in a clean grid, "
                "right-bottom ticker exclusion area kept as background only"
            )
        if reporter_subtype == "敘事觀點型":
            return (
                "top headline band, two-column explanatory news layout with calm narrative visual rhythm, "
                "left protected asset zone and right analysis card, right-bottom ticker exclusion area kept as background only"
            )
        return (
            "top headline band, left facts/data column and right explanation column, "
            "right-bottom 588x90 ticker exclusion area kept as background only"
        )
    if frame_type == "框訊・數據分析":
        return "single-line top headline only; data-driven news infographic layout with large number cards, chart-like blocks, expert quote panel, clean hierarchy"
    if frame_type == "框訊・流程關係":
        return "single-line top headline only; relationship flow diagram layout with role nodes, connector lines, one protected large photo placeholder, clean investigative news graphics"
    if frame_type == "框訊・對打時間軸":
        return "single-line top headline only; debate and timeline news layout with two opposing quote zones, one large protected media placeholder, bottom timeline strip"
    if frame_type == "框訊・多圖對比":
        return "single-line top headline only; multi-image comparison news layout with several clean protected photo placeholders, comparison cards, clear left-right contrast"
    return "professional modular TV news graphic layout with clear headline band and protected asset placeholders"


def _style_visual_intent(style_name: str) -> str:
    if style_name == AI_FREE_STYLE_NAME or str(style_name).startswith("AI自由"):
        return (
            "AI FREE STYLE MODE: do not restrict the image model to 民生消費 / 社會案件 / 體育競技 / 全球財經 / "
            "突發重磅 / 選情政論 / 科技政策 / 綠能永續 / 現代民俗 / 生醫科技. "
            "The model may choose any suitable broadcast-news color palette, typography style, background texture, "
            "lighting mood, card shape, faction color coding, headline treatment, and composition based on the content. "
            "Keep it professional Taiwanese TV news CG, high readability, and obey all Asset Protection Zones and text whitelist."
        )
    style = get_style_config(style_name)
    return (
        f"style theme: {style.get('theme', 'professional TV news')}; "
        f"visual texture: {style.get('ui', 'clean broadcast graphics')}; "
        f"palette direction: {style.get('palette', 'broadcast blue and neutral dark')}; "
        f"accent: {style.get('highlight', 'clean highlight color')}"
    )



def _content_palette_hint(script: str, frame_type: str) -> str:
    """依內容給 Gemini Image 一個可自由發揮的底圖/配色方向，不新增新聞事實。"""
    s = script
    if _contains_any(s, ["共諜", "起訴", "貪污", "羈押", "檢", "司法", "三重罪", "求刑", "洗錢"]):
        return "legal/investigative palette: deep navy, black, warning yellow, restrained red accents, serious prosecution mood"
    if _contains_any(s, ["國防", "軍購", "軍史", "營區", "國軍", "海馬士", "軍", "統戰"]):
        return "defense/political-security palette: dark blue, steel gray, military olive, alert red, metallic broadcast texture"
    if _contains_any(s, ["鼠", "蟑", "環境", "防治", "衛生", "市府"]):
        return "public-safety/civic issue palette: gritty dark gray, urban green, caution red, yellow highlights, textured city background"
    if _contains_any(s, ["股", "財經", "億", "匯率", "營收", "市場", "投資"]):
        return "financial palette: deep navy, black, gold, cyan data glow, premium dashboard texture"
    if _contains_any(s, ["旅遊", "消費", "美食", "百貨", "黃金週", "民生"]):
        return "consumer/lifestyle palette: clean blue, warm orange, soft beige, frosted glass, lighter energetic background"
    if frame_type == "記者說新聞":
        return "explanatory news palette: clean blue-gray, calm contrast, readable cards, restrained accent color"
    if frame_type == "標大框":
        return "breaking-news mega headline palette: high contrast dark texture, strong red/yellow/white accents, bold broadcast energy"
    return "professional Taiwanese TV news palette: dark broadcast texture, blue-gray base, red/yellow accents, high readability"


def _background_visual_directive(script: str, frame_type: str, style_name: str, background_mode: str) -> str:
    """v20.6：底圖風格配色。AI 自動時，交給 Gemini Image 依內容自由配色與生成底圖。"""
    base_style = _style_visual_intent(style_name)
    content_hint = _content_palette_hint(script, frame_type)

    if background_mode.startswith("AI自由") or background_mode.startswith("AI自動"):
        return f"""
AI FREE STYLE / BACKGROUND / PALETTE / TYPOGRAPHY MODE is ENABLED.
Do NOT restrict the design to the fixed Visual Director style library. The model may freely choose any professional Taiwanese broadcast-news style based on the story content, conflict level, topic, and emotional tone.
Suggested content-sensitive direction: {content_hint}.
The model may choose its own color palette, headline treatment, card shapes, faction labels, lighting, background texture, motion texture, typography weight, and layout rhythm.
The background may include abstract broadcast textures such as newsroom gradients, metallic panels, civic map texture, data grid, subtle smoke, caution pattern, glass panels, paper texture, court/investigation texture, military texture, or dark studio lighting when appropriate.
CRITICAL: freedom only applies to style, palette, background, and layout arrangement. It must never create extra people, extra objects, extra facts, extra logos, extra text, random labels, fake screenshots, or fake photos inside asset zones.
CRITICAL: background and palette must respect all Asset Protection Zones; no texture, icon, stamp, brush, shadow, or text may invade the empty photo/video placeholders.
Keep approved Traditional Chinese typography readable and accurate.
""".strip()

    if background_mode.startswith("沿用"):
        return f"""
Use the selected Visual Director style library as the background and palette source.
{base_style}.
Create a polished broadcast background consistent with this style, but do not invent text or facts.
All background textures must stay outside protected asset interiors and preserve clean 40px safety buffers.
""".strip()

    return f"""
Use a stable dark professional TV news background: charcoal, deep navy, subtle metallic texture, soft vignette, restrained red/yellow highlights.
Avoid experimental colors. Keep high readability and strict asset protection.
Reference style if needed: {base_style}.
""".strip()


def _asset_zone_prompt(asset_zones: List[str], transparent_holes: bool) -> str:
    requested_count = len(asset_zones)
    if not asset_zones:
        return (
            "ASSET ZONE COUNT LOCK: The user provided 0 asset markers. Do not create any blank media/photo/video frame unless the user explicitly requests one."
        )
    zone_list = "\n".join([f"- Internal requested blank area {i}: {_safe_zone_ratio_label(z)}" for i, z in enumerate(asset_zones[:12], start=1)])
    fill_style = (
        "plain transparent-looking or light neutral empty rectangles"
        if transparent_holes else
        "clean dark or neutral empty rectangles with subtle broadcast frame"
    )
    return f"""
[ASSET ZONE COUNT LOCK]
The user explicitly requested {requested_count} protected blank asset area(s).
Final artwork must contain EXACTLY {requested_count} blank asset frame(s), no more and no fewer.
Do not create extra photo frames, video frames, ROLL frames, portrait boxes, document boxes, placeholder boxes, or empty frames for design balance.
Do not split one requested area into multiple frames.
Do not duplicate any requested area.
Ratio instructions apply ONLY to the existing requested blank areas below, silently. Ratio text must never appear on screen.
{zone_list}

For each requested marker, reserve one clean blank area in the CG layout.
These areas are for real post-production material, not AI-generated content.
Interior must be {fill_style}.
No fake photos, no fake screenshots, no icons, no labels, no text, no stamps, no brush strokes, no arrows, no decorations inside.
Keep at least 40px clean safety buffer around every blank area.
Never render helper annotations or ratio labels, including 左上圖, 左下圖, 右ROLL框, 中上文字色塊, 中下文字色塊, 圖, ROLL框, ROLL, 視頻, 影片, video, 色塊, 4:3, 4:5, 商比, IMAGE, PHOTO, PLACEHOLDER, SECTION, CARD, SOURCE, 編輯圖片區, 圖片區.
If the layout is crowded, rearrange modules or reduce font size. Do not invade blank areas and do not delete user-provided text.
""".strip()

def build_image_prompt_translator(
    script: str,
    frame_type: str,
    style_name: str,
    headline_mode: str,
    reporter_subtype: str,
    use_safe_zone: bool,
    no_text_mode: bool = False,
    transparent_holes: bool = False,
    background_mode: str = "AI自動判斷（依內容自由配色＋生成底圖）",
    asset_aspect: str = "4:3 橫式素材框",
) -> Dict[str, str]:
    """
    產生 Gemini Image 專用 prompt。
    v20.5.2 重點：
    1) 使用者仍可輸入 (#定圖)、(圖片)、(定國防部外觀照)、#筆刷、#蓋章。
    2) 這些會被轉成圖片模型可理解的 Asset Protection Zone / broadcast module。
    3) 中文採白名單：只允許生成使用者稿內提供的繁體中文，不允許補字、亂碼、翻譯。
    """
    parsed = parse_user_script(script)
    asset_zones = _extract_asset_zones(script)
    approved_text = _extract_approved_text_whitelist(script)
    approved_text = list(dict.fromkeys(approved_text + _extract_location_badges(script)))
    title = _strip_director_syntax(parsed.title)[:60]
    visual_compiler = build_visual_token_compiler_block(script, frame_type, headline_mode, asset_aspect)
    # v20.6.6：不要再把原始 DSL 長文直接餵給圖片模型；改用已去符號、去繼承的 compiler 摘要。
    content_hint = _strip_director_syntax(script)[:260]

    if no_text_mode:
        text_policy = (
            "Do not render any readable text, Chinese characters, English letters, numbers, captions, labels, watermarks, logos, or fake UI text. "
            "Leave headline and all text areas as clean graphic blocks/placeholders for post-production typography."
        )
        text_whitelist_block = "Approved text whitelist: NONE, pure background/layout mode."
        negative_text = "no readable text, no Chinese characters, no English letters, no numbers, no typography"
    else:
        whitelist = "\n".join([f"- {t}" for t in approved_text]) or "- （使用者未提供可上畫面文字，請保持文字區空白）"
        text_policy = (
            "Render ONLY the exact Traditional Chinese text in the approved whitelist below. "
            "Use the whitelist as the only source of readable Chinese typography. "
            "Never add, rewrite, translate, summarize, or invent any text. "
            "Never duplicate names, numbers, keywords, or headline fragments. "
            "Do not omit approved text from the user-provided layout. "
            "Do not render internal instruction labels, debug labels, card labels, image-box labels, source-marker labels, prompt syntax, style/category labels, or English labels. Never render layout helper words such as 左上圖, 左下圖, 右ROLL框, 中上文字色塊, 中下文字色塊, 4:3, 4:5, 圖, ROLL框, 色塊, 打卡LOGO, 打卡地點, 打卡, 假人ICON, 假人icon, 假人大頭, 人形ICON, 人物ICON, 對話框, 說話框, speech bubble, 後封保用照片, 後製確認照片, 真實視頻ROLL插投, ROLL/視頻, 視頻, video, 編輯圖片區, 圖片區, 4:5商比, 商比. Never render style/category words such as 社會案件, Justice Alert, Crime Scene Noir. "
            "If spacing is tight, reduce font size, line spacing, or rearrange modules instead of deleting text. "
            "No fake Chinese glyphs, no random numbers, no random English letters."
        )
        text_whitelist_block = f"Approved Traditional Chinese text whitelist:\n{whitelist}"
        negative_text = "no extra text beyond whitelist, no missing approved text, no internal labels, no section labels, no card labels, no image box labels, no placeholder labels, no source marker labels, no compiler words, no debug labels, no fake photo inside blank image zones, no icon inside blank image zones, no text inside blank image zones, no gibberish Chinese, no fake characters, no random English letters, no random numbers"

    safe_zone_policy = (
        "Keep the bottom-right 588x90 ticker-safe zone as background texture only, no cards, no icons, no text, no decoration."
        if use_safe_zone or frame_type == "記者說新聞" else
        "No hardware ticker exclusion zone is required, but keep generous margins and avoid clutter."
    )

    senior_news_cg_policy = build_senior_news_cg_designer_policy()

    clean_render_policy = f"""
{_style_render_ban_text()}
{_forbidden_helper_text_ban()}

CLEAN RENDER POLICY:
Use the blueprint as layout guidance, not as visible text.
Never render internal words such as section card, information card, image box, asset box, mandatory, invalid render, whitelist, compiler, source marker, debug label, or placeholder label.
Keep all user-approved Chinese text visible, but do not show rule labels.
Preserve the requested empty media/photo areas as blank frames.
If the page is crowded, improve spacing, reduce font size, or rearrange; do not omit cards or approved text.
""".strip()

    zero_assumption_policy = """
ZERO ASSUMPTION MODE.
Do not infer missing broadcast UI.
Do not create ticker, breaking news strips, live tags, channel logos, timestamps, watermarks, lower thirds, news crawlers, extra banners, fake subtitles, or bottom information bars.
Only render broadcast UI explicitly tagged by the user.
If not explicitly tagged: leave the area empty.
EMPTY > ASSUMPTION. Never decorate automatically.
Content-specific logos explicitly provided by the user script may be rendered as content icons, but do not invent channel branding.
""".strip()

    if has_explicit_brush_tag(script):
        brush_policy = (
            "Explicit brush tag detected. Use at most 1 to 2 brush effects, only for the text directly attached to the brush tag, "
            "never for normal body text, never over asset zones."
        )
    else:
        brush_policy = (
            "No explicit brush tag detected. Brush effects are completely forbidden on this page. "
            "Do not create brush strokes, brush banners, smeared paint highlights, or brush-style summary labels."
        )

    negative_fx = "" if has_explicit_brush_tag(script) else ", no brush stroke, no brush banner, no paint smear highlight"

    positive_prompt = f"""
Professional Taiwanese TV news CG, 1920x1080 horizontal 16:9, polished broadcast graphics, clean composition, high readability, not a poster, not a web page.

{senior_news_cg_policy}

{visual_compiler}

Layout intent: {_frame_visual_intent(frame_type, reporter_subtype, headline_mode)}.
Headline area: {headline_mode}; title meaning reference only: {title or 'news headline'}.
Background and color strategy: {_background_visual_directive(script, frame_type, style_name, background_mode)}.

ASSET PROTECTION ZONE POLICY:
{_asset_zone_prompt(asset_zones, transparent_holes)}

Broadcast module translation:
- (#色塊) / (色塊) means information card blocks.
- (#蓋章) means a stamp-style emphasis module placed OUTSIDE all asset zones; do not infer stamp from ordinary text.
- BRUSH EFFECT IS EXPLICIT ONLY: create brush strokes ONLY when the user explicitly writes (#筆刷), (筆刷), #筆刷, ---筆刷, or 筆刷效果.
- If no explicit brush tag exists in the user script, brush strokes are forbidden.
- Do not convert <文字>, 「quotes」, numbers, conflict words, emotional words, or body text into brush strokes.
- Do not duplicate any body sentence into a separate brush/stamp/sticker module unless that exact line is explicitly tagged. Only promote a sentence once.
- If the user provides 打卡地點 / 打卡LOGO / (打卡LOGO)地名, the location badge is MANDATORY: render ONLY the exact approved location text outside asset zones; never write 打卡LOGO, 打卡地點, 打卡, or any helper label.
- <文字> means high-priority headline emphasis or impact typography; render the text exactly if it appears in the whitelist, but it is NOT a brush trigger.

Explicit brush policy for this page:
{brush_policy}

Ticker/safe zone: {safe_zone_policy}

{clean_render_policy}

ZERO ASSUMPTION / NO EXTRA UI:
{zero_assumption_policy}

TEXT POLICY:
{text_policy}
{text_whitelist_block}

Use layered broadcast panels, subtle shadows, crisp edges, strong hierarchy, high contrast, newsroom CG design.
No horror, no distorted faces, no creepy anatomy, no surreal artifacts. Real photos are NOT generated inside asset zones; those areas stay empty for post-production insertion.
""".strip()

    negative_prompt = f"""
{negative_text}{negative_fx}, no unrequested ticker, no unrequested breaking news strip, no unrequested LIVE tag, no unrequested channel logo, no unrequested timestamp, no unrequested lower-third, no unrequested bottom news bar, no watermark, no QR code, no extra news facts, no creepy face, no distorted human, no horror mood, no movie poster, no magazine cover, no social media post, no clutter, no overlapping text on asset zones, no labels inside empty asset zones, no brackets, no prompt syntax, no [圖], no (#定圖), no (圖片), no UI debug labels, no stamp or brush overlapping protected image boxes, no duplicated headline text, no duplicated names, no repeated keywords, no concatenating body text into headline
""".strip()

    return {
        "positive_prompt": positive_prompt,
        "negative_prompt": negative_prompt,
        "copy_prompt": positive_prompt + "\n\nNEGATIVE PROMPT:\n" + negative_prompt,
        "note": f"v20.6.6 會先做 Visual Token Compiler：標題/卡片/圖區分層，素材標記轉成 Asset Protection Zone，文字採白名單；筆刷只有明確標註才生成；Zero Assumption 禁止自行補跑馬/快訊/LIVE/台標；底圖配色模式：{background_mode}",
    }

def build_cg_preview_html(script: str, frame_type: str, headline_mode: str, reporter_subtype_override: str = "", conclusion: Dict[str, str] | None = None, asset_aspect: str = "4:3 橫式素材框") -> str:
    """產生 16:9 CG 版面預覽。這不是成品圖，是用來檢查標題、圖區、模組是否會互相壓到。"""
    parsed = parse_user_script(script)
    headline_preview_text = build_headline_display_text(script, headline_mode)
    title = html.escape(headline_preview_text or "主標題").replace("\n", "<br>")
    tags = parsed.image_tags or (["[圖-右主]"] if frame_type in ["標大框", "框訊・流程關係"] else [])
    safe_tags = [html.escape(tag) for tag in tags]
    mode_class = "one-line" if headline_mode == "一行大標題" else "two-line"
    aspect_cfg = resolve_asset_aspect(asset_aspect)

    def img_box(label: str, cls: str = "") -> str:
        zcfg = resolve_asset_aspect_for_tag(label, asset_aspect)
        ratio_class = zcfg["css_class"]
        ratio_label = zcfg["ratio"]
        return f'<div class="img-zone {cls} {ratio_class}"><span>{label}<br>{ratio_label}｜後製真實圖片留白區<br>禁止文字 / icon / 筆刷 / 蓋章壓入</span></div>'

    def module_box(label: str, cls: str = "") -> str:
        return f'<div class="module {cls}">{label}</div>'

    image_boxes = "".join(img_box(tag, f"z{i}") for i, tag in enumerate(safe_tags[:6]))

    if frame_type == "框訊・數據分析":
        layout_html = f"""
        <div class="grid data">
            <div class="leftcol">
                {module_box('主敘事 / 重點色塊')}
                {module_box('數據框：漲跌 / 點數 / 金額')}
            </div>
            <div class="rightcol">
                {img_box(safe_tags[0] if safe_tags else '[圖-右人]', 'person')}
                {module_box('專家說法 / 結論數據')}
            </div>
        </div>
        """
    elif frame_type == "記者說新聞":
        subtype = reporter_subtype_override or detect_reporter_subtype(script)
        conclusion = conclusion or detect_conclusion_module(script, frame_type, subtype)
        if subtype == "表格數據型":
            layout_html = f"""
            <div class="grid reporter-table">
                <div class="fullrow">{module_box('表格數據區：欄位比較 / 門檻 / 數字')}</div>
                <div class="bottomrow">{module_box('底部結論筆刷：不可進右下跑馬區', 'stamp-warning')}</div>
            </div>
            """
        elif subtype == "卡片條列型":
            layout_html = f"""
            <div class="grid reporter-cards">
                {module_box('1 卡片')}{module_box('2 卡片')}{module_box('3 卡片')}{module_box('4 卡片')}{module_box('5 卡片')}{module_box('6 卡片')}
            </div>
            """
        elif subtype == "敘事觀點型":
            layout_html = f"""
            <div class="grid reporter">
                <div class="leftcol">{img_box(safe_tags[0] if len(safe_tags)>0 else '[圖-人物/主視覺]', 'person')}{module_box('人物故事 / 視角 / 口語觀點')}</div>
                <div class="rightcol">{img_box(safe_tags[1] if len(safe_tags)>1 else '[圖-輔助視覺]', 'person')}{module_box('結論觀點 / 反思')}</div>
            </div>
            """
        else:
            layout_html = f"""
            <div class="grid reporter">
                <div class="leftcol">
                    {module_box('左欄：日期 / 排名 / 數據')}
                    {img_box(safe_tags[0] if len(safe_tags)>0 else '[圖-左上]', 'person')}
                    {img_box(safe_tags[1] if len(safe_tags)>1 else '[圖-左下]', 'person')}
                </div>
                <div class="rightcol">
                    {module_box('右欄：原因解釋 / 重點條列')}
                    {module_box('右下跑馬安全區上方停止；安全區內只能背景', 'stamp-warning')}
                </div>
            </div>
            """
    elif frame_type == "框訊・流程關係":
        layout_html = f"""
        <div class="grid relation">
            <div class="leftcol">
                {module_box('事件方框')}
                {module_box('評審 / 角色群組')}
                {module_box('關係節點 A → 不可壓圖')}
                {module_box('關係節點 B → 不可壓圖')}
                {module_box('筆刷 / 蓋章效果只能在圖區外', 'stamp-warning')}
            </div>
            <div class="rightcol">{img_box(safe_tags[0] if safe_tags else '[圖-右主]', 'main')}</div>
        </div>
        """
    elif frame_type == "框訊・對打時間軸":
        layout_html = f"""
        <div class="grid debate">
            <div class="leftcol">
                {img_box(safe_tags[0] if len(safe_tags)>0 else '[圖-左人A]', 'person')}
                {module_box('人物 A 對話框')}
                {img_box(safe_tags[1] if len(safe_tags)>1 else '[圖-中人B]', 'person')}
                {module_box('人物 B 對話框')}
            </div>
            <div class="rightcol">{img_box(safe_tags[2] if len(safe_tags)>2 else '[圖-右主]', 'main')}</div>
            <div class="bottomrow">{module_box('時間軸 / 結論對話框')}</div>
        </div>
        """
    elif frame_type == "框訊・多圖對比":
        layout_html = f"""
        <div class="grid compare">
            <div class="toprow">{image_boxes or img_box('[圖-上排素材]', 'small')}</div>
            <div class="leftcol">{img_box(safe_tags[0] if safe_tags else '[圖-左主]', 'main')}</div>
            <div class="rightcol">{module_box('說法 / 爭議重點')}{module_box('對比模組 VS')}</div>
        </div>
        """
    else:
        layout_html = f"""
        <div class="grid standard">
            <div class="leftcol">{img_box(safe_tags[0] if safe_tags else '[圖-左主]', 'main')}</div>
            <div class="rightcol">{module_box('資訊卡 A')}{module_box('資訊卡 B')}{module_box('蓋章 / 筆刷效果放這裡，不可壓圖', 'stamp-warning')}</div>
        </div>
        """

    return f"""
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<style>
body {{ margin:0; background:#101010; font-family:'Noto Sans TC','Microsoft JhengHei',sans-serif; }}
.preview-wrap {{ width:100%; display:flex; justify-content:center; padding:16px 0; }}
.canvas {{ position:relative; width:960px; height:540px; background:linear-gradient(135deg,#18202b,#2e3440 45%,#111); overflow:hidden; border:1px solid #444; box-shadow:0 10px 30px rgba(0,0,0,.5); }}
.headline {{ position:absolute; top:0; left:0; right:0; min-height:112px; padding:16px 24px 10px; box-sizing:border-box; background:linear-gradient(90deg,#050505,#401010); color:#fff; font-weight:900; font-size:42px; line-height:1.05; text-shadow:3px 3px 0 #000; z-index:10; border-bottom:4px solid #f3d34a; }}
.headline.one-line {{ white-space:nowrap; font-size:44px; display:flex; align-items:center; }}
.headline.two-line {{ font-size:38px; display:flex; align-items:center; }}
.body {{ position:absolute; top:126px; left:18px; right:18px; bottom:18px; }}
.grid {{ width:100%; height:100%; display:grid; gap:12px; }}
.standard {{ grid-template-columns: 42% 58%; }}
.reporter {{ grid-template-columns: 58% 42%; }}
.reporter-table {{ grid-template-rows: 1fr 90px; }}
.reporter-table .fullrow {{ min-height:300px; }}
.reporter-cards {{ grid-template-columns: 1fr 1fr; grid-template-rows: repeat(3,1fr); }}
.canvas::after {{ content:'跑馬安全區 588×90：背景only'; position:absolute; right:0; bottom:0; width:294px; height:45px; border:2px dashed rgba(255,255,255,.75); color:#fff; font-size:11px; display:flex; align-items:center; justify-content:center; background:rgba(0,0,0,.28); z-index:20; pointer-events:none; }}
.relation {{ grid-template-columns: 38% 62%; }}
.data {{ grid-template-columns: 48% 52%; }}
.debate {{ grid-template-columns: 52% 48%; grid-template-rows: 1fr 90px; }}
.compare {{ grid-template-columns: 48% 52%; grid-template-rows: 92px 1fr; }}
.compare .toprow {{ grid-column:1/3; display:flex; gap:10px; }}
.debate .bottomrow {{ grid-column:1/3; }}
.leftcol,.rightcol {{ display:flex; flex-direction:column; gap:10px; min-width:0; min-height:0; }}
.img-zone {{ flex:1; min-height:110px; background:#f2f2f2; border:4px solid #fff; box-shadow:inset 0 0 0 3px #bbb; display:flex; align-items:center; justify-content:center; color:#111; font-weight:900; text-align:center; border-radius:6px; position:relative; }}
.img-zone::after {{ content:'HARD EMPTY ZONE'; position:absolute; top:6px; right:8px; font-size:11px; color:#c00; }}
.img-zone.main {{ min-height:260px; }}
.img-zone.person {{ min-height:128px; border-radius:12px; }}
.img-zone.small {{ min-height:76px; }}
.img-zone.ratio-auto {{ aspect-ratio:auto; }}
.img-zone.ratio-auto.z0 {{ aspect-ratio:4 / 3; }}
.img-zone.ratio-auto.z1 {{ aspect-ratio:4 / 5; max-width:68%; align-self:center; }}
.img-zone.ratio-auto.z2 {{ aspect-ratio:16 / 9; }}
.img-zone.ratio-43 {{ aspect-ratio:4 / 3; }}
.img-zone.ratio-45 {{ aspect-ratio:4 / 5; max-width:68%; align-self:center; }}
.img-zone.main.ratio-45 {{ min-height:310px; max-width:58%; }}
.img-zone.person.ratio-45 {{ min-height:190px; max-width:58%; }}
.module {{ background:rgba(255,230,80,.95); color:#111; font-weight:900; font-size:24px; line-height:1.2; padding:14px; border-radius:6px; border:3px solid rgba(0,0,0,.35); box-sizing:border-box; }}
.module.stamp-warning {{ background:#cf2d2d; color:#fff; font-size:20px; }}
.note {{ position:absolute; right:12px; bottom:8px; color:#ddd; font-size:12px; }}
</style>
</head>
<body>
<div class="preview-wrap">
  <div class="canvas">
    <div class="headline {mode_class}">{title}</div>
    <div class="body">{layout_html}</div>
    <div class="note">CG 版面預覽｜檢查用：圖區不可被蓋章/筆刷/文字壓入</div>
  </div>
</div>
</body>
</html>
"""


# =========================================================
# 7. 華視打洞機 v66（完整保留版，未簡化）
# =========================================================
HOLE_PUNCHER_V66 = r"""
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <title>華視打洞機 v66 - 操控優化版</title>
    <script src="https://cdn.jsdelivr.net/npm/@mediapipe/selfie_segmentation/selfie_segmentation.js"></script>
    <style>
        :root { --pink: #ff00ff; --panel: #1a1a1a; --blue: #2979ff; --green: #00c853; --cyan: #00e5ff; --yellow: #ffeb3b; --orange: #ff9800; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0a0a; color: #fff; margin: 0; padding: 10px; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
        .toolbar { background: var(--panel); padding: 10px 20px; border-radius: 12px; display: flex; align-items: center; gap: 10px; margin-bottom: 10px; border: 1px solid #333; flex-shrink: 0; box-shadow: 0 4px 20px rgba(0,0,0,0.5); flex-wrap: wrap; }
        .group { display: flex; align-items: center; gap: 8px; border-right: 1px solid #444; padding-right: 12px; }
        .main-layout { display: flex; gap: 10px; flex: 1; min-height: 0; }
        .view-panel { flex: 1; display: flex; flex-direction: column; position: relative; border-radius: 12px; background: #000; border: 1px solid #222; overflow: hidden; }
        .label { position: absolute; top: 12px; left: 12px; background: rgba(0,0,0,0.8); padding: 4px 10px; border-radius: 4px; font-size: 10px; font-weight: bold; z-index: 1000; color: #aaa; pointer-events: none; }
        .canvas-wrapper { position: relative; width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; overflow: hidden; }
        canvas { max-width: 100%; max-height: 100%; cursor: crosshair; }
        #previewCanvas { background-color: #000; }
        button { padding: 8px 12px; cursor: pointer; background: #2a2a2a; color: #ccc; border: 1px solid #444; border-radius: 6px; font-size: 11px; font-weight: 600; transition: 0.2s; }
        button.active { background: var(--pink) !important; color: #fff; }
        #aiProtectBtn { background: var(--blue); color: #fff; border: none; min-width: 90px; }
        .download-btn { background: #fff; color: #000; font-weight: bold; margin-left: auto; }
        label { font-size: 10px; color: #888; }
        .config-label { font-size: 11px; color: var(--yellow); display: flex; align-items: center; gap: 4px; cursor: pointer; }
    </style>
</head>
<body>

    <div id="loading-overlay" style="position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.8); display:none; flex-direction:column; align-items:center; justify-content:center; z-index:2000;">🤖 打洞機調整中...</div>

    <div class="toolbar">
        <div class="group">
            <button onclick="document.getElementById('upload').click()" style="color: var(--yellow);">📁 開啟主圖</button>
            <label class="config-label"><input type="checkbox" id="autoLayoutCheck"> 自動留邊+模糊背景</label>
            <input type="file" id="upload" accept="image/*" style="display:none">
        </div>
        <div class="group">
            <button onclick="document.getElementById('bgInput').click()">🖼️ 插入底圖</button>
            <input type="file" id="bgInput" accept="image/*" style="display:none" multiple>
            <button onclick="document.getElementById('fgInput').click()" style="color: var(--cyan);">➕ 插入前景</button>
            <input type="file" id="fgInput" accept="image/*" style="display:none" multiple>
            <button onclick="deleteSelectedItem()" style="color: var(--orange);">🗑️ 刪除選中</button>
        </div>
        <div class="group">
            <button id="aiProtectBtn">🔒 AI 鎖定</button>
            <button class="mode-btn" data-mode="refine_add" style="color:var(--cyan)">✨ 補回人像</button>
            <button class="mode-btn" data-mode="refine_sub" style="color:var(--orange)">🔪 裁切邊緣</button>
        </div>
        <div class="group">
            <button class="mode-btn active" data-mode="brush">粉紅筆 (B)</button>
            <button class="mode-btn" data-mode="rect">矩形 (R)</button>
            <button class="mode-btn" data-mode="circle">圓形 (C)</button>
            <button class="mode-btn" data-mode="eraser">橡皮擦 (E)</button>
        </div>
        <div class="group">
            <label>筆刷</label><input type="range" id="brushSize" min="1" max="250" value="50">
            <label>羽化</label><input type="range" id="featherSize" min="0" max="100" value="8">
        </div>
        <button onclick="exportManual()">📄 導出手冊</button>
        <button id="commitBtn" style="background:#444">✅ 定案</button>
        <button onclick="undo()">↶ Undo</button>
        <button id="downloadBtn" class="download-btn">導出 1080p HD</button>
    </div>

    <div class="main-layout">
        <div class="view-panel"><div class="label">WORKSPACE (作業區 - 即時透底)</div><div class="canvas-wrapper"><canvas id="workCanvas"></canvas></div></div>
        <div class="view-panel"><div class="label">PREVIEW (1920x1080 成品)</div><div class="canvas-wrapper"><canvas id="previewCanvas"></canvas></div></div>
    </div>

    <script>
        let img = new Image(), bgImages = [], fgImages = [], activeItem = { type: null, index: -1 }; 
        let isDrawing = false, isMovingItem = false, isResizingItem = false, isMovingShape = false;
        let currentMode = 'brush', mouseX = 0, mouseY = 0, startX, startY, lastMoveX, lastMoveY, activeShape = null, history = [];
        
        let layout = { outW: 1920, outH: 1080, padding: 180, drawW: 0, drawH: 0, drawX: 0, drawY: 0 };
        const HANDLE_SIZE = 24;

        const workCanvas = document.getElementById('workCanvas'), previewCanvas = document.getElementById('previewCanvas');
        const wCtx = workCanvas.getContext('2d'), pCtx = previewCanvas.getContext('2d');
        
        const cache = { manual: document.createElement('canvas'), ai: document.createElement('canvas'), mask: document.createElement('canvas'), mb: document.createElement('canvas'), pink: document.createElement('canvas') };
        const mCtx = cache.manual.getContext('2d'), aiCtx = cache.ai.getContext('2d'), tCtx = cache.mask.getContext('2d'), mbCtx = cache.mb.getContext('2d'), pkCtx = cache.pink.getContext('2d');

        const selfieSegmentation = new SelfieSegmentation({ locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/selfie_segmentation/${file}` });
        selfieSegmentation.setOptions({ modelSelection: 1 });
        
        selfieSegmentation.onResults(res => {
            document.getElementById('loading-overlay').style.display = 'none';
            aiCtx.clearRect(0, 0, 1920, 1080);
            aiCtx.save();
            aiCtx.filter = 'blur(2.5px)';
            aiCtx.drawImage(res.segmentationMask, layout.drawX, layout.drawY, layout.drawW, layout.drawH);
            aiCtx.restore();
            const data = aiCtx.getImageData(0,0,1920,1080);
            for(let i=0; i<data.data.length; i+=4) {
                let a = data.data[i]; 
                if(a < 128) a = (Math.pow(a/128, 1.8) * 128); else a = 255 - (Math.pow((255-a)/127, 1.8) * 127);
                data.data[i] = data.data[i+1] = data.data[i+2] = 255; data.data[i+3] = a; 
            }
            aiCtx.putImageData(data, 0, 0); render();
        });

        function render() {
            if(!img.src) return;
            const W = 1920, H = 1080;
            [wCtx, pCtx, tCtx, mbCtx, pkCtx].forEach(ctx => { ctx.globalCompositeOperation = 'source-over'; ctx.imageSmoothingEnabled = true; ctx.clearRect(0, 0, W, H); });

            const feather = document.getElementById('featherSize').value;
            if(feather > 0) tCtx.filter = `blur(${feather}px)`;
            tCtx.drawImage(cache.manual, 0, 0);
            let s = isDrawing && (currentMode==='rect'||currentMode==='circle') ? { type: currentMode, x: startX, y: startY, w: mouseX-startX, h: mouseY-startY } : activeShape;
            if (s) {
                tCtx.fillStyle = 'white';
                if (s.type === 'rect') tCtx.fillRect(s.x, s.y, s.w, s.h);
                else { tCtx.beginPath(); tCtx.ellipse(s.x+s.w/2, s.y+s.h/2, Math.abs(s.w/2), Math.abs(s.h/2), 0, 0, Math.PI*2); tCtx.fill(); }
            }
            tCtx.filter = 'none';
            tCtx.globalCompositeOperation = 'destination-out';
            tCtx.drawImage(cache.ai, 0, 0);
            tCtx.globalCompositeOperation = 'source-over';

            const drawStack = (targetCtx) => {
                targetCtx.save();
                if(document.getElementById('autoLayoutCheck').checked) {
                    targetCtx.filter = 'blur(40px) brightness(0.6)';
                    targetCtx.drawImage(img, -50, -50, W + 100, H + 100); targetCtx.filter = 'none';
                }
                bgImages.forEach(bg => targetCtx.drawImage(bg.img, bg.x, bg.y, bg.w, bg.h));
                mbCtx.clearRect(0, 0, W, H); mbCtx.globalCompositeOperation = 'source-over';
                mbCtx.drawImage(img, layout.drawX, layout.drawY, layout.drawW, layout.drawH);
                mbCtx.globalCompositeOperation = 'destination-out'; mbCtx.drawImage(cache.mask, 0, 0);
                targetCtx.drawImage(cache.mb, 0, 0);
                fgImages.forEach(fg => targetCtx.drawImage(fg.img, fg.x, fg.y, fg.w, fg.h));
                targetCtx.restore();
            };

            drawStack(pCtx);
            drawStack(wCtx);
            pkCtx.drawImage(cache.mask, 0, 0); pkCtx.globalCompositeOperation = 'source-in'; pkCtx.fillStyle = '#ff00ff'; pkCtx.fillRect(0,0,W,H);
            wCtx.save(); wCtx.globalAlpha = 0.4; wCtx.drawImage(cache.pink, 0, 0); wCtx.restore();
            
            drawUI(s);
        }

        function drawUI(s) {
            wCtx.save();
            let sel = activeItem.type === 'bg' ? bgImages[activeItem.index] : (activeItem.type === 'fg' ? fgImages[activeItem.index] : null);
            if(sel && !isDrawing) {
                wCtx.setLineDash([5, 5]); 
                wCtx.strokeStyle = activeItem.type === 'bg' ? '#ffeb3b' : '#00e5ff';
                wCtx.lineWidth = 2;
                wCtx.strokeRect(sel.x, sel.y, sel.w, sel.h);
                const hX = sel.x + sel.w;
                const hY = sel.y + sel.h;
                wCtx.beginPath();
                wCtx.arc(hX, hY, HANDLE_SIZE/2, 0, Math.PI*2);
                wCtx.fillStyle = wCtx.strokeStyle;
                wCtx.fill();
                wCtx.strokeStyle = "#fff";
                wCtx.lineWidth = 3;
                wCtx.stroke();
            }
            if (s) { 
                wCtx.setLineDash([8, 4]); wCtx.strokeStyle = '#FF0'; 
                if (s.type === 'rect') wCtx.strokeRect(s.x, s.y, s.w, s.h);
                else { wCtx.beginPath(); wCtx.ellipse(s.x+s.w/2, s.y+s.h/2, Math.abs(s.w/2), Math.abs(s.h/2), 0, 0, Math.PI*2); wCtx.stroke(); }
            }
            wCtx.restore();
        }

        function deleteSelectedItem() {
            if (activeItem.type === 'bg') bgImages.splice(activeItem.index, 1);
            else if (activeItem.type === 'fg') fgImages.splice(activeItem.index, 1);
            activeItem = { type: null, index: -1 }; render();
        }

        const getPos = e => { const r = workCanvas.getBoundingClientRect(); return { x: (e.clientX-r.left)*(1920/r.width), y: (e.clientY-r.top)*(1080/r.height) }; };
        function isInRect(p, r) { if(!r) return false; const xM=Math.min(r.x,r.x+r.w), xX=Math.max(r.x,r.x+r.w), yM=Math.min(r.y,r.y+r.h), yX=Math.max(r.y,r.y+r.h); return p.x>=xM && p.x<=xX && p.y>=yM && p.y<=yX; }

        function isOverHandle(p, sel) {
            if(!sel) return false;
            const dist = Math.sqrt(Math.pow(p.x - (sel.x + sel.w), 2) + Math.pow(p.y - (sel.y + sel.h), 2));
            return dist < HANDLE_SIZE;
        }

        workCanvas.onmousedown = e => {
            if(!img.src) return; const p = getPos(e);
            if (activeShape && isInRect(p, activeShape)) { isMovingShape = true; lastMoveX = p.x; lastMoveY = p.y; return; }
            let selItem = activeItem.type === 'bg' ? bgImages[activeItem.index] : (activeItem.type === 'fg' ? fgImages[activeItem.index] : null);
            if(isOverHandle(p, selItem)) { isResizingItem = true; return; }
            for (let i = fgImages.length - 1; i >= 0; i--) {
                if (isInRect(p, fgImages[i])) {
                    activeItem = { type: 'fg', index: i }; isMovingItem = true; lastMoveX = p.x; lastMoveY = p.y; render(); return;
                }
            }
            for (let i = bgImages.length - 1; i >= 0; i--) {
                if (isInRect(p, bgImages[i])) {
                    activeItem = { type: 'bg', index: i }; isMovingItem = true; lastMoveX = p.x; lastMoveY = p.y; render(); return;
                }
            }
            activeItem = { type: null, index: -1 }; commitShape(); isDrawing = true; startX = p.x; startY = p.y; saveHistory();
            if(['brush','eraser','refine_add','refine_sub'].includes(currentMode)){ 
                let targetCtx = currentMode.startsWith('refine') ? aiCtx : mCtx;
                targetCtx.beginPath(); targetCtx.moveTo(p.x, p.y); targetCtx.lineWidth = document.getElementById('brushSize').value; targetCtx.lineCap = targetCtx.lineJoin = 'round';
                targetCtx.globalCompositeOperation = (currentMode==='eraser'||currentMode==='refine_sub') ? 'destination-out' : 'source-over'; 
                targetCtx.strokeStyle = 'white'; targetCtx.lineTo(p.x, p.y); targetCtx.stroke(); 
            }
            render();
        };

        workCanvas.onmousemove = e => {
            const p = getPos(e); mouseX = p.x; mouseY = p.y;
            let sel = activeItem.type === 'bg' ? bgImages[activeItem.index] : (activeItem.type === 'fg' ? fgImages[activeItem.index] : null);
            if (isOverHandle(p, sel)) workCanvas.style.cursor = 'nwse-resize';
            else if (isInRect(p, activeShape || null)) workCanvas.style.cursor = 'move';
            else workCanvas.style.cursor = 'crosshair';

            if (isResizingItem && sel) { sel.w = p.x - sel.x; sel.h = sel.w / sel.aspectRatio; }
            else if (isMovingItem && sel) { sel.x += (p.x - lastMoveX); sel.y += (p.y - lastMoveY); lastMoveX = p.x; lastMoveY = p.y; }
            else if (isMovingShape) { activeShape.x += (p.x - lastMoveX); activeShape.y += (p.y - lastMoveY); lastMoveX = p.x; lastMoveY = p.y; }
            else if (isDrawing && ['brush','eraser','refine_add','refine_sub'].includes(currentMode)) { 
                let targetCtx = currentMode.startsWith('refine') ? aiCtx : mCtx; targetCtx.lineTo(p.x, p.y); targetCtx.stroke(); 
            }
            render();
        };

        window.onmouseup = () => {
            if (isDrawing && (currentMode==='rect'||currentMode==='circle')) { activeShape = { type: currentMode, x: startX, y: startY, w: mouseX-startX, h: mouseY-startY }; }
            isDrawing = isMovingItem = isResizingItem = isMovingShape = false; render();
        };

        function commitShape() { 
            if (!activeShape) return; saveHistory(); mCtx.globalCompositeOperation = 'source-over'; mCtx.fillStyle = 'white'; 
            if (activeShape.type === 'rect') mCtx.fillRect(activeShape.x, activeShape.y, activeShape.w, activeShape.h); 
            else { mCtx.beginPath(); mCtx.ellipse(activeShape.x+activeShape.w/2, activeShape.y+activeShape.h/2, Math.abs(activeShape.w/2), Math.abs(activeShape.h/2), 0, 0, Math.PI*2); mCtx.fill(); }
            activeShape = null; render(); 
        }
        function saveHistory() { history.push({ manual: mCtx.getImageData(0,0,1920,1080), ai: aiCtx.getImageData(0,0,1920,1080) }); if(history.length > 25) history.shift(); }
        function undo() { activeShape = null; if(history.length > 0) { let h = history.pop(); mCtx.putImageData(h.manual, 0, 0); aiCtx.putImageData(h.ai, 0, 0); render(); } }
        
        document.getElementById('upload').onchange = e => {
            const f = e.target.files[0];
            const autoLayout = document.getElementById('autoLayoutCheck').checked;
            if(f){ const r = new FileReader(); r.onload = ev => { img.onload = () => {
                [workCanvas, previewCanvas, cache.manual, cache.ai, cache.mask, cache.mb, cache.pink].forEach(c => { c.width = 1920; c.height = 1080; });
                const targetAreaW = autoLayout ? (1920 - 360) : 1920;
                const ratio = Math.min(targetAreaW / img.width, 1080 / img.height);
                layout.drawW = img.width * ratio; layout.drawH = img.height * ratio;
                layout.drawX = (1920 - layout.drawW) / 2; layout.drawY = (1080 - layout.drawH) / 2;
                bgImages=[]; fgImages=[]; activeItem={type:null, index:-1}; render(); 
            }; img.src = ev.target.result; }; r.readAsDataURL(f); }
        };
        document.getElementById('bgInput').onchange = e => { Array.from(e.target.files).forEach(f => { const r = new FileReader(); r.onload = ev => { const n = new Image(); n.onload = () => { bgImages.push({ img: n, x: 0, y: 0, w: 1920, h: 1080, aspectRatio: n.width/n.height }); activeItem = { type: 'bg', index: bgImages.length - 1 }; render(); }; n.src = ev.target.result; }; r.readAsDataURL(f); }); };
        document.getElementById('fgInput').onchange = e => { Array.from(e.target.files).forEach(f => { const r = new FileReader(); r.onload = ev => { const n = new Image(); n.onload = () => { fgImages.push({ img: n, x: 500, y: 300, w: 400, h: 400/(n.width/n.height), aspectRatio: n.width/n.height }); activeItem = { type: 'fg', index: fgImages.length - 1 }; render(); }; n.src = ev.target.result; }; r.readAsDataURL(f); }); };

        document.getElementById('aiProtectBtn').onclick = async function() { if(!img.src) return; document.getElementById('loading-overlay').style.display='flex'; await selfieSegmentation.send({image: img}); };
        document.querySelectorAll('.mode-btn').forEach(b => { b.onclick = () => { commitShape(); document.querySelectorAll('.mode-btn').forEach(x => x.classList.remove('active')); b.classList.add('active'); currentMode = b.dataset.mode; render(); }; });
        document.getElementById('commitBtn').onclick = commitShape;
        document.getElementById('downloadBtn').onclick = () => {
            commitShape(); const a = document.createElement('a'); a.download = '華視打洞機_v66_Final.png'; a.href = previewCanvas.toDataURL('image/png'); a.click();
        };

        function exportManual() {
            const manualText = `華視打洞機 v66 - 操控優化版

【操控更新】
1. 縮放優化：縮放手把加大至 24px，並改為圓形設計，更容易抓取。
2. 智能指標：滑鼠移到角落會顯示 ↘ 縮放圖示，移到圖中顯示 ✥ 移動圖示。
3. 即時透底：左側作業區即時預覽合成效果。`;
            const blob = new Blob([manualText], { type: 'text/plain' });
            const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = '華視打洞機_使用說明.txt'; a.click();
        }

        window.onkeydown = e => { 
            const k = e.key.toLowerCase(); if(k==='z'&&e.ctrlKey) undo(); if(k==='b') document.querySelector('[data-mode="brush"]').click(); if(k==='r') document.querySelector('[data-mode="rect"]').click(); if(k==='c') document.querySelector('[data-mode="circle"]').click(); if(k==='e') document.querySelector('[data-mode="eraser"]').click(); if(k==='enter') commitShape(); 
            if(k==='delete' || k==='backspace') { if (activeItem.index !== -1) deleteSelectedItem(); }
        };
    </script>
</body>
</html>
"""



# =========================================================
# 7.5 一鍵複製導演指令按鈕
# =========================================================
def render_one_click_copy(text: str, label: str, key: str, height: int = 74) -> None:
    """在 Streamlit 內嵌明顯的一鍵複製按鈕。"""
    safe_payload = json.dumps(text or "", ensure_ascii=False)
    safe_label = html.escape(label)
    safe_key = re.sub(r"[^A-Za-z0-9_-]", "_", key)
    components.html(
        f"""
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans TC',sans-serif;">
          <button id="copy_{safe_key}" style="
            width:100%;
            border:0;
            border-radius:14px;
            padding:16px 18px;
            cursor:pointer;
            font-size:18px;
            font-weight:900;
            letter-spacing:.04em;
            color:white;
            background:linear-gradient(135deg,#ff7a00,#ff2d55);
            box-shadow:0 10px 24px rgba(255,45,85,.28);
          ">📋 一鍵複製{safe_label}</button>
          <div id="copy_msg_{safe_key}" style="margin-top:8px;font-size:13px;color:#64748b;text-align:center;">
            點一下就會複製導演指令，可直接貼到 Gemini。
          </div>
          <textarea id="copy_text_{safe_key}" style="position:absolute;left:-9999px;top:-9999px;"> </textarea>
          <script>
            const payload_{safe_key} = {safe_payload};
            const btn_{safe_key} = document.getElementById('copy_{safe_key}');
            const msg_{safe_key} = document.getElementById('copy_msg_{safe_key}');
            const box_{safe_key} = document.getElementById('copy_text_{safe_key}');
            btn_{safe_key}.onclick = async () => {{
              try {{
                await navigator.clipboard.writeText(payload_{safe_key});
              }} catch (err) {{
                box_{safe_key}.value = payload_{safe_key};
                box_{safe_key}.select();
                document.execCommand('copy');
              }}
              btn_{safe_key}.innerText = '✅ 已複製{safe_label}';
              msg_{safe_key}.innerText = '已複製到剪貼簿，可以直接貼到 Gemini。';
              setTimeout(() => {{
                btn_{safe_key}.innerText = '📋 一鍵複製{safe_label}';
                msg_{safe_key}.innerText = '點一下就會複製導演指令，可直接貼到 Gemini。';
              }}, 1800);
            }};
          </script>
        </div>
        """,
        height=height,
        scrolling=False,
    )

# =========================================================
# 8. UI
# =========================================================
st.title("🎬 Visual Director v20.6.6｜Visual Token Compiler Mode")
st.caption("雙模型手選；左側不再放預設風格／預設模板；以 v20.5 導演系統頁面為唯一判斷來源；Prompt 成本監控；防亂生文字稽核；素材保護區＋中文白名單＋AI自由風格排版＋Explicit Brush Only＋Zero Assumption｜Producer Huifen Edition")

with st.sidebar:
    st.header("🔑 Gemini API")
    env_ready = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    if env_ready:
        st.success("已偵測 Render / 系統環境變數 GEMINI_API_KEY")
    else:
        st.warning("尚未偵測環境變數 GEMINI_API_KEY")

    st.text_input(
        "手動輸入 API Key（可留空，環境變數優先）",
        key="manual_api_key",
        type="password",
        placeholder="如果 Render 已設定，這裡不用填",
    )

    st.divider()
    st.header("🤖 模型設定")
    selected_model_label = st.radio(
        "AI 模型手選",
        list(AI_MODELS.keys()),
        index=0,
        help="💸 最省適合大量試稿；⚡ 中間適合一般修稿。左側不再提供預設風格/預設模板，避免與 v20.5 導演系統頁面互相打架。",
    )
    CURRENT_MODEL = AI_MODELS[selected_model_label]
    st.caption(f"目前使用模型：`{CURRENT_MODEL}`")

    with st.expander("💰 本次成本估算單價", expanded=False):
        price = MODEL_PRICE_TABLE.get(CURRENT_MODEL, {"input": 0.0, "output": 0.0})
        st.caption(f"Input：${price['input']} / 1M tokens")
        st.caption(f"Output：${price['output']} / 1M tokens")
        st.caption("實際金額仍以 Google Billing / API Spend 為準。")

    # v20.6.6 Visual Token Compiler Mode:
    # 左側不再放「預設風格 / 預設模板」。
    # 風格與模板只以 v20.5 導演系統頁面當下選擇與 AI 判斷為準。
    default_style = AI_FREE_STYLE_NAME
    default_frame_ui = "標大框"
    default_frame = resolve_frame_for_engine(default_frame_ui, "")

    st.divider()
    st.header("🧪 本機測試")
    if st.button("執行 v20.6.6 導演系統測試"):
        try:
            for message in run_self_tests():
                st.success(message)
        except AssertionError as err:
            st.error(f"測試失敗：{err}")


with st.expander("📘 v17 圖區不壓圖規則", expanded=False):
    st.markdown(
        """
**核心原則：**  
`[圖]`、`[圖-左主]`、`(#定圖)`、`(圖片)`、`(定國防部外觀照)`、`(#開框roll)`、`(LINE截圖)` 不是要顯示在成品上的文字，而是系統用來判斷「後製真實素材保護區」的版型語法。

最終成品必須：
- 刪除所有 `[圖]` 與說明文字。
- 保留乾淨空白區給後製塞圖。
- 任何文字、icon、色塊、陰影、箭頭、筆刷、AI 自動生成底圖紋理、蓋章都不能壓到素材框。
- 素材框優先權高於文字完整度；如果版面衝突，寧可縮字或改排版，也不能壓圖。
"""
    )
    st.table(
        pd.DataFrame(
            [
                {"語法": "[圖] / [圖-左主]", "系統動作": "建立硬留白圖區", "成品處理": "刪除標籤，只留空洞"},
                {"語法": "(#定忠孝橋) / (定國防部外觀照)", "系統動作": "視為指定真實素材保護區", "成品處理": "刪除標籤，只留後製素材框"},
                {"語法": "(圖片) / (#開框roll) / (LINE截圖)", "系統動作": "建立影片/照片/截圖保護框", "成品處理": "框內完全空白，不放文字或 icon"},
                {"語法": "(色塊)", "系統動作": "生成資訊卡", "成品處理": "刪除指令文字"},
                {"語法": "(對話框)", "系統動作": "生成說話框", "成品處理": "刪除指令文字"},
                {"語法": "#筆刷", "系統動作": "生成筆刷強調", "成品處理": "刪除指令文字"},
            ]
        )
    )


tab_ai, tab_prompt, tab_hole = st.tabs([
    "🤖 AI 拆稿",
    "🎬 v20.5 導演系統",
    "🖍️ 華視打洞機",
])


with tab_ai:
    st.subheader("🤖 AI 只負責拆稿，不負責壓版")
    news_text = st.text_area("貼上原始新聞稿 / 原始資料", height=220)
    ai_frame_type_ui = st.selectbox("AI 要整理成哪種模板", UI_FRAME_OPTIONS, index=UI_FRAME_OPTIONS.index(default_frame_ui))
    ai_frame_type = resolve_frame_for_engine(ai_frame_type_ui, news_text)

    if st.button("✨ AI 產生框訊文字稿", type="primary"):
        if not news_text.strip():
            st.warning("請先貼上新聞稿。")
        else:
            with st.spinner("AI 製作人拆稿中..."):
                result, usage_report = generate_ai_frame_content(
                    news_text,
                    ai_frame_type,
                    get_api_key(),
                    CURRENT_MODEL,
                    selected_model_label,
                )
                if result:
                    st.session_state["ai_frame_result"] = result
                    st.session_state["ai_usage_report"] = usage_report
                    st.session_state["ai_fact_audit"] = audit_extra_facts(news_text, result)
                    st.success("已生成，可複製到 v17 自動導演頁微調。")

    if st.session_state.get("ai_frame_result"):
        monitor_l, monitor_r = st.columns(2)
        with monitor_l:
            render_usage_report(st.session_state.get("ai_usage_report"))
        with monitor_r:
            render_fact_audit(st.session_state.get("ai_fact_audit"))
        st.text_area("AI 生成結果", st.session_state["ai_frame_result"], height=320)
        if st.button("➡️ 套用到指令編譯"):
            title = extract_section(st.session_state["ai_frame_result"], "TITLE")
            body = extract_section(st.session_state["ai_frame_result"], "BODY")
            st.session_state["manual_script"] = f"標:{title}{NL}{NL}{body}".strip()
            st.success("已套用到 v17 自動導演頁。")


with tab_prompt:
    st.subheader("🎬 v20.5 導演系統＋結論模組")

    c1, c2 = st.columns([1.25, 0.75])

    with c1:
        default_script = ""
        script = st.text_area(
            "框訊文字稿（預設空白，不會自帶範例文字）",
            key="manual_script",
            height=420,
            value=st.session_state.get("manual_script", default_script),
            placeholder="請貼上你自己的框訊文字稿；系統不會自動帶入北車/新光/微風等範例文字。",
        )
        if not script.strip():
            st.info("目前文字稿是空白。請貼上內容後再產生最終指令。")

    with c2:
        st.markdown("### 🛠️ 編譯設定")
        selected_template_ui = st.selectbox("模型模板選單", UI_FRAME_OPTIONS, index=UI_FRAME_OPTIONS.index(default_frame_ui))
        auto_director = st.toggle("🎬 啟動自動導演判斷", value=True)
        auto_patch = st.toggle("自動補必要 [圖] 區（預設關閉，避免自動生成你沒給的文字）", value=False)
        if script.strip():
            director = build_director_report(script)
        else:
            director = {
                "frame_type": default_frame,
                "reporter_subtype": "左右解釋型",
                "style_name": default_style,
                "headline_mode": "手動選擇",
                "density": "低密度",
                "tone": "穩重資訊型",
                "conclusion_type": "不使用",
                "conclusion_sentence": "",
                "conclusion_position": "不產生結論模組",
                "safe_zone": "不啟用",
            }

        if auto_director:
            if selected_template_ui == "框訊":
                frame_type = resolve_frame_for_engine("框訊", script)
                st.success(f"模板：框訊｜AI 自主判斷細類：{frame_type}")
            else:
                frame_type = selected_template_ui
                st.success(f"模板：{frame_type}")
            detected_style_name = director["style_name"]
            st.info(f"自動判斷風格：{detected_style_name}")
        else:
            frame_type = resolve_frame_for_engine(selected_template_ui, script)
            detected_style_name = default_style
            if selected_template_ui == "框訊":
                st.caption(f"框訊細類由系統判斷：{frame_type}")

        st.markdown("### 🎨 風格來源 / 視覺變化")
        style_source_mode = st.radio(
            "風格來源（決定新聞類型 WHAT）",
            ["自動判定題材風格庫（依新聞內容套用）", "手動選擇固定風格庫"],
            index=0,
            horizontal=True,
            help="這層只決定新聞題材：社會案件、財經、科技、民生等。AI自由模式不會再覆蓋這個判斷。",
        )
        if style_source_mode.startswith("自動判定"):
            style_name = detected_style_name
            st.success(f"內容風格已自動判定：{style_name}")
        else:
            style_name = st.selectbox(
                "手動選擇內容風格",
                list(STYLE_CONFIG.keys()),
                index=list(STYLE_CONFIG.keys()).index(detected_style_name) if detected_style_name in STYLE_CONFIG else 0,
            )
            st.info(f"內容風格手動指定：{style_name}")

        visual_variation_mode = st.radio(
            "視覺變化（決定畫法 HOW）",
            ["固定風格庫", "AI自由變化（同一題材風格內變化底圖／構圖／材質）"],
            index=1,
            horizontal=False,
            help="固定風格庫：穩定套用預設風格。AI自由變化：保留題材風格，但讓底圖、構圖、卡片造型、光影材質有更多變化。",
        )
        layout_mode = "DYNAMIC" if visual_variation_mode.startswith("AI自由") else "GRID"
        ai_color = True
        if visual_variation_mode.startswith("AI自由"):
            st.success(f"AI自由變化啟用：在「{style_name}」風格框架內變化底圖與畫法，不會覆蓋自動判定風格。")
        else:
            st.caption(f"固定風格庫：穩定沿用「{style_name}」的預設視覺語彙。")

        if frame_type == "記者說新聞":
            st.markdown("### 🧠 記者說新聞子類型")
            auto_subtype = director.get("reporter_subtype", "左右解釋型")
            st.info(f"自動判斷：{auto_subtype}")
            manual_override_subtype = st.toggle("手動改子類型", value=False)
            subtype_options = ["左右解釋型", "表格數據型", "卡片條列型", "敘事觀點型"]
            if manual_override_subtype:
                reporter_subtype = st.selectbox(
                    "選擇子類型",
                    subtype_options,
                    index=subtype_options.index(auto_subtype) if auto_subtype in subtype_options else 0,
                )
            else:
                reporter_subtype = auto_subtype
        else:
            reporter_subtype = "N/A"

        if frame_type == "標大框":
            headline_mode = "兩行大標題"
            st.info("標大框：維持原本 MEGA LARGE 兩行大標設定。")
        elif frame_type.startswith("框訊") or frame_type == "框訊":
            headline_mode = "一行大標題"
            st.info("框訊：已鎖定一行大標；不允許自動換成兩行或第二層副標。")
        else:
            headline_mode = st.radio(
                "標題行數（手動選擇）",
                ["一行大標題", "兩行大標題"],
                index=0,
                horizontal=True,
                help="不管選一行或兩行，標題都會鎖在版面最上方。",
            )

        if auto_patch:
            script_for_prompt = auto_patch_missing_image_zones(script, frame_type)
            if script_for_prompt != script:
                st.warning("自動導演已補上必要 [圖] 區；不會改你的輸入框，只會放進最終指令。")
        else:
            script_for_prompt = script

        asset_aspect = st.radio(
            "ROLL框 / 圖區版面尺寸",
            ASSET_ASPECT_OPTIONS,
            index=0,
            horizontal=True,
            help="全域預設比例；同一張版面若要混用，直接在圖區標籤寫 4:3 或 4:5，例如 [圖-左ROLL 4:3]、[圖-右人物 4:5]。標籤內的比例會優先於這裡的全域設定。",
        )
        st.caption("混合比例寫法：`[圖-左ROLL 4:3]`、`[圖-右人物 4:5]`、`(#定監視器畫面 4:3)`、`(LINE截圖 4:5)`；未標註的圖區才吃上面的全域選項。")
        icon_style = st.radio("ICON 質感", ["2D", "3D"], index=1, horizontal=True)
        if frame_type == "記者說新聞":
            use_safe_zone = True
            st.warning("記者說新聞：右下跑馬安全區 588×90 已強制鎖定，不能關閉。")
        else:
            use_safe_zone = st.toggle("啟用右下跑馬安全區 588×90", value=auto_should_safe_zone(script, frame_type) if auto_director else False)
        conclusion = detect_conclusion_module(script_for_prompt if 'script_for_prompt' in locals() else script, frame_type, reporter_subtype)
        st.markdown("### 🎯 結論模組")
        st.caption(f"類型：{conclusion['type']}｜句子：{conclusion['sentence'] or '無'}")
        st.caption(f"建議位置：{conclusion['position']}")

        notes = st.text_area("補充導演備註", value="所有圖區都要留白，後製會塞真實照片；文字絕對不能壓到圖。", height=110)


    final_prompt, parsed = build_final_prompt_v18(
        script=script_for_prompt,
        frame_type=frame_type,
        style_name=style_name,
        icon_style=icon_style,
        headline_mode=headline_mode,
        layout_mode=layout_mode,
        use_safe_zone=use_safe_zone,
        ai_color=ai_color,
        notes=notes,
        reporter_subtype_override=reporter_subtype,
        asset_aspect=asset_aspect,
        visual_variation_mode=visual_variation_mode,
    )

    st.divider()
    diag_l, diag_r = st.columns(2)
    with diag_l:
        st.markdown("### 🖼️ 偵測到的圖區")
        if parsed.image_tags:
            st.success(f"共偵測 {len(parsed.image_tags)} 個圖區 / 圖素材指令")
            for tag in parsed.image_tags:
                st.code(tag, language="text")
        else:
            st.warning("沒有偵測到明確圖區。若要後製塞圖，請加 [圖-左主] / [圖-右主]。")

    with diag_r:
        st.markdown("### 🧩 偵測到的模組")
        if parsed.module_tags:
            st.info(f"共偵測 {len(parsed.module_tags)} 個模組指令")
            for tag in parsed.module_tags:
                st.code(tag, language="text")
        else:
            st.caption("尚未偵測到色塊 / 對話框 / 數據框等模組。")

    if parsed.warnings:
        for warning in parsed.warnings:
            st.warning(warning)

    st.markdown("### 🧪 v20.6.6 防呆檢查")
    for item in build_quality_check(parsed, frame_type, reporter_subtype, use_safe_zone):
        st.checkbox(item, value=True, disabled=True)

    st.markdown("### 🖼️ CG 版面直接預覽")
    components.html(
        build_cg_preview_html(
            script_for_prompt,
            frame_type,
            headline_mode,
            reporter_subtype_override=reporter_subtype,
            conclusion=conclusion,
            asset_aspect=asset_aspect,
        ),
        height=620,
        scrolling=False,
    )

    st.markdown("### 🔥 導演指令")
    render_one_click_copy(final_prompt, "導演指令", "final_prompt_copy")

    if auto_director:
        with st.expander("🎬 自動導演判斷報告（只顯示，不複製）", expanded=False):
            director["reporter_subtype"] = reporter_subtype
            director["conclusion_type"] = conclusion["type"]
            director["conclusion_sentence"] = conclusion["sentence"]
            director["conclusion_position"] = conclusion["position"]
            director["asset_aspect"] = asset_aspect
            st.json(director)

    st.code(final_prompt, language="markdown")


with tab_hole:
    st.subheader("🖍️ 華視打洞機 v66｜完整嵌入版")
    st.caption("完整保留：AI 鎖定、補回、裁切、底圖、前景、定案、Undo、HD 輸出。")
    components.html(HOLE_PUNCHER_V66, height=940, scrolling=True)

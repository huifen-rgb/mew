from __future__ import annotations

# -*- coding: utf-8 -*-
"""
Visual Director v19.5｜結論模組導演完整版

重點：
1. 保留 Gemini API 串接
2. 保留框訊模板庫
3. 保留完整 HOLE_PUNCHER_V66，不簡化
4. v19.5 導演系統：自動判斷版型大類、記者說新聞子類型、風格、密度、語氣、必要圖區
5. 結論模組：自動偵測結論句，判斷筆刷 / 蓋章 / 不使用，並給出安全位置
6. 記者說新聞子類型可手動覆寫，並直接影響 CG preview 與 prompt
7. 加入安全區硬邊界、資訊密度控制、視覺層級、結論模組、防呆檢查
8. 所有圖區在成品必須刪除標籤文字，且文字、icon、UI、蓋章、筆刷不得壓入
"""

import os
import textwrap
import html
from dataclasses import dataclass
from typing import Dict, List, Tuple

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    import google.generativeai as genai
except Exception:
    genai = None

NL = chr(10)


# =========================================================
# 0. Streamlit 基本設定
# =========================================================
st.set_page_config(
    page_title="Visual Director v19.5｜結論模組導演完整版",
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


def _count_image_intents(script: str) -> int:
    markers = ["[圖", "(#定", "(定", "截圖", "ROLL", "roll", "定圖", "[圖-"]
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


def extract_conclusion_candidates(script: str) -> List[str]:
    """v19.5：抓可能適合當結論模組的句子。優先找筆刷/蓋章標記，其次找最後短強句。"""
    candidates: List[str] = []
    lines = [line.strip() for line in script.splitlines() if line.strip()]

    trigger_words = [
        "蓋章", "筆刷", "恐", "預計", "不如", "關鍵", "影響", "走向", "效應",
        "核心", "亮點", "警訊", "反思", "安全", "近程", "美食", "完工", "法律戰",
    ]
    remove_tokens = ["[蓋章效果]", "[筆刷效果]", "#筆刷", "---筆刷", "--------蓋章", "[", "]", "\""]

    for line in lines:
        if _contains_any(line, trigger_words):
            cleaned = line
            for token in remove_tokens:
                cleaned = cleaned.replace(token, "")
            cleaned = cleaned.strip("-—= ：:")
            if 4 <= len(cleaned) <= 36:
                candidates.append(cleaned)

    if not candidates:
        for line in reversed(lines):
            cleaned = line.replace("\"", "").strip("-—= ：:")
            if 4 <= len(cleaned) <= 30 and not cleaned.startswith("[") and not cleaned.startswith("("):
                candidates.append(cleaned)
                break

    return list(dict.fromkeys(candidates))[:3]


def detect_conclusion_module(script: str, frame_type: str, reporter_subtype: str = "") -> Dict[str, str]:
    """v19.5：自動判斷結論模組類型與安全位置。"""
    candidates = extract_conclusion_candidates(script)
    sentence = candidates[0] if candidates else ""

    if not sentence:
        return {
            "type": "不使用",
            "sentence": "",
            "position": "不產生結論模組",
            "reason": "未偵測到適合獨立強調的結論句",
        }

    strong_stamp_words = ["不如", "法律戰", "爆", "怒", "疑雲", "失能", "狀況外", "人血饅頭", "完工", "大勝", "慘輸"]
    brush_words = ["預計", "關鍵", "效應", "影響", "走向", "反思", "核心", "亮點", "警訊", "安全", "近程", "美食"]

    if "蓋章" in script or _contains_any(sentence, strong_stamp_words):
        module_type = "蓋章"
    elif "筆刷" in script or _contains_any(sentence, brush_words):
        module_type = "筆刷"
    elif frame_type == "記者說新聞" and reporter_subtype == "敘事觀點型":
        module_type = "淡筆刷"
    else:
        module_type = "筆刷"

    if frame_type == "記者說新聞":
        if reporter_subtype == "表格數據型":
            position = "表格下方左側或中下方；必須避開右下跑馬安全區"
        elif reporter_subtype == "卡片條列型":
            position = "卡片群下方或右側留白；不可壓卡片、圖區與跑馬"
        elif reporter_subtype == "敘事觀點型":
            position = "右欄結論文字旁或背景留白處；不可壓人物圖與跑馬"
        else:
            position = "右欄中下方、跑馬安全區上方；不可進右下588×90"
    elif frame_type.startswith("框訊"):
        position = "主資訊卡底部或右側背景留白；不可壓圖、人物、對話框"
    else:
        position = "標題下方的資訊區邊緣或右側留白；不可壓主圖"

    return {
        "type": module_type,
        "sentence": sentence,
        "position": position,
        "reason": "偵測到可作為畫面收束的結論句",
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
    if _contains_any(script, ["招標", "霸凌", "偷拍", "申訴", "警", "警方", "北檢", "廉政署", "偵辦", "圖利"]):
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
    checks.append("v19.5 結論模組：筆刷/蓋章只能放在背景或卡片外緣，不可壓圖、人物、表格與跑馬")
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
    results.append("v19.5 結論模組判斷 OK")

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


def generate_ai_frame_content(news_text: str, frame_type: str, api_key: str) -> str | None:
    """AI 只負責拆稿，不負責決定圖區壓縮或侵入。"""
    if not configure_gemini(api_key):
        return None

    template = FRAME_TEMPLATES.get(frame_type, FRAME_TEMPLATES["標大框"])

    system_instruction = f"""
你是一位資深電視新聞製作人，負責把新聞稿整理成「框訊」CG 文字稿。

【最重要原則】
AI 只負責內容拆解，不得破壞版面安全。
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
            model_name="gemini-3.1-flash-lite-preview",
            system_instruction=textwrap.dedent(system_instruction).strip(),
        )
        response = model.generate_content(
            f"請整理這則新聞稿：{NL}{NL}{news_text}",
            generation_config=genai.types.GenerationConfig(temperature=0.45),
        )
        return response.text.strip()
    except Exception as e:
        st.error(f"AI 生成失敗：{e}")
        return None


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


def parse_user_script(script: str) -> ParsedInput:
    """抓出標題、圖區、模組與警告。"""
    warnings: List[str] = []

    title = ""
    for key in ["大標:", "大標=", "標題:", "標題=", "標:", "標="]:
        if key in script:
            title = script.split(key, 1)[1].strip().splitlines()[0]
            break
    if not title:
        title = next((line.strip() for line in script.splitlines() if line.strip()), "")[:80]

    square_tags = _collect_square_tags(script)
    paren_tags = _collect_parenthesis_tags(script)

    image_tags: List[str] = []
    for tag in square_tags + paren_tags:
        if tag.startswith("[圖") or tag.startswith("(#定") or tag.startswith("(定") or _contains_any(tag, ["圖", "截圖", "ROLL", "roll", "定圖"]):
            image_tags.append(tag)
    image_tags = list(dict.fromkeys([tag.strip() for tag in image_tags if tag.strip()]))

    module_tags: List[str] = []
    module_words = ["色塊", "方框", "對話框", "數據框", "小標", "蓋章", "假人", "icon", "筆刷", "關係", "群組", "頭+字"]
    for tag in square_tags + paren_tags:
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
- TOP HEADLINE LOCK: headline must always be placed at the very top edge area of the canvas, whether it is one line or two lines.
- One-line headline: keep it in the top headline band, centered or left-weighted, never moved to middle.
- Two-line headline: stack both lines in the top headline band, never placed in the center body area.
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
- Top: mega headline.
- Upper row: multiple real-material image zones, such as construction, documents, people.
- Left/lower: main image hole if present.
- Right/lower: quote, price difference, investigation or explanation blocks.
- Bottom-right: comparison module if [模組-對比] appears.
- All image zones remain completely empty.
""",
        "框訊・對打時間軸": """
[FRAME: 框訊・對打時間軸]
- Top: headline with opposing keywords.
- Upper/lateral: two-person debate or attack/response zones.
- Main ROLL/video zone usually on the right and must stay empty.
- Bottom: timeline/event images arranged in equal-width blocks.
- Conclusion quote may appear bottom-right but must not overlap images.
""",
        "框訊・數據分析": """
[FRAME: 框訊・數據分析]
- Main body is data hierarchy, not image hierarchy.
- Use clear data cards, stacked rows, and strong numerical emphasis.
- Person image, if any, is secondary and protected.
- Left side: event/narrative data. Right side: expert quote and conclusion data.
""",
        "框訊・流程關係": """
[FRAME: 框訊・流程關係]
- Top: strong conflict headline.
- Left side: relationship diagram with role group and branch nodes.
- Right side: main image/ROLL protected zone.
- Use connector lines from role icons to relationship nodes.
- Relationship nodes must not overlap each other or image zones.
""",
    }
    return textwrap.dedent(common + NL + rules.get(frame_type, rules["標大框"])).strip()


def build_layout_diagnostics(parsed: ParsedInput, frame_type: str) -> str:
    image_list = NL.join([f"- {tag}" for tag in parsed.image_tags]) or "- No explicit image tags detected."
    module_list = NL.join([f"- {tag}" for tag in parsed.module_tags]) or "- No explicit module tags detected."

    return f"""
[DETECTED LAYOUT INTENT]
Frame Type: {frame_type}
Detected Image Zones:
{image_list}

Detected Modules:
{module_list}
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
) -> Tuple[str, ParsedInput]:
    parsed = parse_user_script(script)
    style = STYLE_CONFIG[style_name]

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
    color_logic = (
        f"Dynamic contextual color based on headline sentiment: {clean_inline_text(parsed.title)}"
        if ai_color
        else f"Fixed palette: {style['palette']} | Highlight: {style['highlight']}"
    )

    prompt = f"""
[VISUAL DIRECTOR v18 DIRECTOR SYSTEM | BROADCAST NEWS CG]
CANVAS: 1920x1080 Full HD
LANGUAGE: Traditional Chinese ONLY

[STYLE]
STYLE NAME: {style_name}
THEME: {style['theme']}
UI TEXTURE: {style['ui']}
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

[LAYOUT]
Layout mode: {layout_mode}
{build_frame_rules(frame_type)}

{build_symbol_matrix_v17()}

{build_layout_diagnostics(parsed, frame_type)}

[v19.5 DIRECTOR DECISION]
- Frame Type: {frame_type}
- Reporter Subtype: {reporter_subtype_override if frame_type == '記者說新聞' and reporter_subtype_override else (detect_reporter_subtype(script) if frame_type == '記者說新聞' else 'N/A')}
- Information Density: {density_score(script)}
- Tone: {auto_detect_tone(script, frame_type)}
- Visual hierarchy: Main headline 300%, module title 160%, body text 100%.
- If density is high, prefer table/card compression; if density is low, use narrative spacing.
- Conclusion Module Type: {detect_conclusion_module(script, frame_type, reporter_subtype_override if reporter_subtype_override else detect_reporter_subtype(script))['type']}
- Conclusion Sentence: {detect_conclusion_module(script, frame_type, reporter_subtype_override if reporter_subtype_override else detect_reporter_subtype(script))['sentence']}
- Conclusion Safe Position: {detect_conclusion_module(script, frame_type, reporter_subtype_override if reporter_subtype_override else detect_reporter_subtype(script))['position']}
- Conclusion module must be placed outside all image zones and outside ticker safe zone.

[FINAL IMAGE RESTRICTIONS - CRITICAL]
- DELETE ALL literal instruction tags, including [圖], [圖-xxx], (#定xxx), (色塊), (對話框), #筆刷.
- DELETE ALL double quotes and angle brackets after applying visual emphasis.
- Every detected image placeholder must become a clean empty protected zone for real post-production photos.
- No text/icon/UI/decoration/stamp/brush effect may touch or overlap protected image zones.
- Stamp effects must be outside [圖] placeholders; stamps may sit on card borders, date labels, or background only.
- If any image zone and text/effects compete for space, image zone wins.
- Final result must be a professional TV news CG, not a poster, not a webpage.

[CONTENT SCRIPT]
{script.strip()}

[DIRECTOR NOTES]
{notes.strip()}
"""
    return textwrap.dedent(prompt).strip(), parsed


def build_cg_preview_html(script: str, frame_type: str, headline_mode: str, reporter_subtype_override: str = "", conclusion: Dict[str, str] | None = None) -> str:
    """產生 16:9 CG 版面預覽。這不是成品圖，是用來檢查標題、圖區、模組是否會互相壓到。"""
    parsed = parse_user_script(script)
    title = html.escape(parsed.title or "主標題")
    tags = parsed.image_tags or (["[圖-右主]"] if frame_type in ["標大框", "框訊・流程關係"] else [])
    safe_tags = [html.escape(tag) for tag in tags]
    mode_class = "one-line" if headline_mode == "一行大標題" else "two-line"

    def img_box(label: str, cls: str = "") -> str:
        return f'<div class="img-zone {cls}"><span>{label}<br>後製真實圖片留白區<br>禁止文字 / icon / 筆刷 / 蓋章壓入</span></div>'

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
# 8. UI
# =========================================================
st.title("🎬 Visual Director v19.5｜結論模組導演完整版")
st.caption("API 串接＋版型/子類型/密度/語氣判斷＋安全區防呆＋CG預覽＋完整打洞機 v66｜Producer Huifen Edition")

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
    st.header("🎛️ 預設規格")
    default_style = st.selectbox("預設風格", list(STYLE_CONFIG.keys()), index=1)
    default_frame = st.selectbox("預設框型", list(FRAME_TEMPLATES.keys()), index=0)

    st.divider()
    st.header("🧪 本機測試")
    if st.button("執行 v19.5 導演系統測試"):
        try:
            for message in run_self_tests():
                st.success(message)
        except AssertionError as err:
            st.error(f"測試失敗：{err}")


with st.expander("📘 v17 圖區不壓圖規則", expanded=False):
    st.markdown(
        """
**核心原則：**  
`[圖]`、`[圖-左主]`、`(#定xxx)`、`(LINE截圖)` 不是要顯示在成品上的文字，而是系統用來判斷「後製真實照片留白區」的版型語法。

最終成品必須：
- 刪除所有 `[圖]` 與說明文字。
- 保留乾淨空白區給後製塞圖。
- 任何文字、icon、色塊、陰影、箭頭都不能壓到圖區。
"""
    )
    st.table(
        pd.DataFrame(
            [
                {"語法": "[圖] / [圖-左主]", "系統動作": "建立硬留白圖區", "成品處理": "刪除標籤，只留空洞"},
                {"語法": "(#定忠孝橋)", "系統動作": "視為指定真實素材區", "成品處理": "刪除文字，不壓圖"},
                {"語法": "(色塊)", "系統動作": "生成資訊卡", "成品處理": "刪除指令文字"},
                {"語法": "(對話框)", "系統動作": "生成說話框", "成品處理": "刪除指令文字"},
                {"語法": "#筆刷", "系統動作": "生成筆刷強調", "成品處理": "刪除指令文字"},
            ]
        )
    )


tab_ai, tab_prompt, tab_hole = st.tabs([
    "🤖 AI 拆稿",
    "🎬 v19.5 導演系統",
    "🖍️ 華視打洞機",
])


with tab_ai:
    st.subheader("🤖 AI 只負責拆稿，不負責壓版")
    news_text = st.text_area("貼上原始新聞稿 / 原始資料", height=220)
    ai_frame_type = st.selectbox("AI 要整理成哪種框訊", list(FRAME_TEMPLATES.keys()), index=list(FRAME_TEMPLATES.keys()).index(default_frame))

    if st.button("✨ AI 產生框訊文字稿", type="primary"):
        if not news_text.strip():
            st.warning("請先貼上新聞稿。")
        else:
            with st.spinner("AI 製作人拆稿中..."):
                result = generate_ai_frame_content(news_text, ai_frame_type, get_api_key())
                if result:
                    st.session_state["ai_frame_result"] = result
                    st.success("已生成，可複製到 v17 自動導演頁微調。")

    if st.session_state.get("ai_frame_result"):
        st.text_area("AI 生成結果", st.session_state["ai_frame_result"], height=320)
        if st.button("➡️ 套用到指令編譯"):
            title = extract_section(st.session_state["ai_frame_result"], "TITLE")
            body = extract_section(st.session_state["ai_frame_result"], "BODY")
            st.session_state["manual_script"] = f"標:{title}{NL}{NL}{body}".strip()
            st.success("已套用到 v17 自動導演頁。")


with tab_prompt:
    st.subheader("🎬 v19.5 導演系統＋結論模組＋最終產圖指令")

    c1, c2 = st.columns([1.25, 0.75])

    with c1:
        default_script = "標:<北車商場經營權>激戰! <新光三越>1分險勝<微風>" + NL + NL + "[圖-右主]" + NL + NL + "(方框)" + NL + "北車商場招標" + NL + "新光三越險勝" + NL + "微風提出異議" + NL + "台鐵暫緩公告" + NL + NL + "(數個假人icon)" + NL + "評審委員" + NL + NL + "(關係-節點A)" + NL + "唯一具工務專業背景" + NL + "缺席甄審會議" + NL + NL + "#筆刷" + NL + "恐走向法律戰"
        script = st.text_area(
            "框訊文字稿",
            key="manual_script",
            height=420,
            value=st.session_state.get("manual_script", default_script),
        )

    with c2:
        st.markdown("### 🛠️ 編譯設定")
        auto_director = st.toggle("🎬 啟動自動導演判斷", value=True)
        auto_patch = st.toggle("自動補必要 [圖] 區", value=True)
        director = build_director_report(script)

        if auto_director:
            frame_type = director["frame_type"]
            detected_style_name = director["style_name"]
            st.success(f"自動判斷框型：{frame_type}")
            st.info(f"自動判斷風格：{detected_style_name}")
        else:
            frame_type = st.selectbox("框型", list(FRAME_TEMPLATES.keys()), index=list(FRAME_TEMPLATES.keys()).index(default_frame))
            detected_style_name = default_style

        manual_style_override = st.toggle("手動改風格", value=False, help="自動判斷錯時打開，例如旅遊新聞改成民生消費。")
        if manual_style_override:
            style_name = st.selectbox("手動選擇風格", list(STYLE_CONFIG.keys()), index=list(STYLE_CONFIG.keys()).index(detected_style_name) if detected_style_name in STYLE_CONFIG else 0)
        else:
            style_name = detected_style_name
            st.caption(f"目前使用風格：{style_name}")

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

        headline_mode = st.radio(
            "標題行數（手動選擇）",
            ["一行大標題", "兩行大標題"],
            index=1,
            horizontal=True,
            help="不管選一行或兩行，標題都會鎖在版面最上方。",
        )

        if auto_patch:
            script_for_prompt = auto_patch_missing_image_zones(script, frame_type)
            if script_for_prompt != script:
                st.warning("自動導演已補上必要 [圖] 區；不會改你的輸入框，只會放進最終指令。")
        else:
            script_for_prompt = script

        layout_mode = st.radio("排版模式", ["GRID", "DYNAMIC"], horizontal=True)
        icon_style = st.radio("ICON 質感", ["2D", "3D"], index=1, horizontal=True)
        ai_color = st.toggle("AI 視覺主權：依新聞情緒配色", value=True)
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

    st.markdown("### 🧪 v19.5 防呆檢查")
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
        ),
        height=620,
        scrolling=False,
    )

    st.markdown("### 🔥 最終產圖指令")
    if auto_director:
        st.markdown("### 🎬 自動導演判斷報告")
        director["reporter_subtype"] = reporter_subtype
        director["conclusion_type"] = conclusion["type"]
        director["conclusion_sentence"] = conclusion["sentence"]
        director["conclusion_position"] = conclusion["position"]
        st.json(director)
    st.code(final_prompt, language="markdown")


with tab_hole:
    st.subheader("🖍️ 華視打洞機 v66｜完整嵌入版")
    st.caption("完整保留：AI 鎖定、補回、裁切、底圖、前景、定案、Undo、HD 輸出。")
    components.html(HOLE_PUNCHER_V66, height=940, scrolling=True)

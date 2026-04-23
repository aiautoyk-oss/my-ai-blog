import os
import sys
import re
import json
import time
import secrets
import datetime
from pathlib import Path

import feedparser
from dotenv import load_dotenv
from google import genai
from google.genai import types


# --------------------------------------------------
# Windows / PowerShell 文字化け対策
# --------------------------------------------------
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# --------------------------------------------------
# 基本設定
# --------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "src" / "content" / "blog"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(PROJECT_ROOT / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
if not GEMINI_API_KEY:
    print("[ERROR] GEMINI_API_KEY が .env に設定されていません")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_API_KEY)

# 安定重視で lite を採用
MODEL_NAME = "gemini-2.5-flash-lite"

HATENA_IT_RSS = "https://b.hatena.ne.jp/hotentry/it.rss"


# --------------------------------------------------
# Structured Output 用 JSON Schema
# --------------------------------------------------
ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "SEOを意識した日本語の記事タイトル。35〜45文字程度。"
        },
        "description": {
            "type": "string",
            "description": "メタディスクリプション。80〜120文字程度。"
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "記事タグ。3〜5個。"
        },
        "body": {
            "type": "string",
            "description": "Markdown形式の本文。H2/H3、箇条書き、比較表、まとめを含む。"
        }
    },
    "required": ["title", "description", "tags", "body"]
}


# --------------------------------------------------
# ユーティリティ
# --------------------------------------------------
def yaml_quote(value: str) -> str:
    """
    YAML frontmatter 用に安全なダブルクオート文字列を生成
    """
    return json.dumps(str(value), ensure_ascii=False)


def strip_control_chars(text: str) -> str:
    """
    JSONパース妨害になりやすい制御文字を除去
    """
    if not isinstance(text, str):
        text = str(text)
    return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)


def extract_json_fallback(text: str) -> dict:
    """
    万一 structured output が崩れた場合の保険
    """
    if not text:
        raise ValueError("空のレスポンスです")

    cleaned = text.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError("JSONオブジェクトが見つかりませんでした")

    candidate = strip_control_chars(match.group(0))

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return json.loads(candidate, strict=False)


def parse_retry_seconds(error_message: str, default_seconds: int = 65) -> int:
    """
    429系のメッセージから待機秒数を推定
    """
    msg = error_message or ""

    patterns = [
        r"retry in ([0-9]+(?:\.[0-9]+)?)s",
        r"retry after ([0-9]+(?:\.[0-9]+)?)s",
        r"try again in ([0-9]+(?:\.[0-9]+)?)s",
        r"([0-9]+(?:\.[0-9]+)?)s"
    ]

    for p in patterns:
        m = re.search(p, msg, flags=re.IGNORECASE)
        if m:
            try:
                return max(5, int(float(m.group(1))) + 5)
            except Exception:
                pass

    return default_seconds


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def ensure_sentence_breaks(paragraph: str, max_len: int = 110) -> str:
    """
    長すぎる1段落を軽く分割してスマホで読みやすくする
    """
    paragraph = paragraph.strip()
    if len(paragraph) <= max_len:
        return paragraph

    parts = re.split(r"(。|\!|\?)", paragraph)
    if len(parts) < 3:
        return paragraph

    rebuilt = []
    current = ""

    for i in range(0, len(parts), 2):
        sentence = parts[i]
        punct = parts[i + 1] if i + 1 < len(parts) else ""
        chunk = sentence + punct

        if len(current) + len(chunk) > max_len and current:
            rebuilt.append(current.strip())
            current = chunk
        else:
            current += chunk

    if current.strip():
        rebuilt.append(current.strip())

    return "\n\n".join(rebuilt)


def normalize_body(body: str) -> str:
    body = normalize_whitespace(body)

    lines = body.split("\n")
    normalized_lines = []

    for line in lines:
        stripped = line.strip()

        if not stripped:
            normalized_lines.append("")
            continue

        # 見出し・コード・表・リストはそのまま
        if (
            stripped.startswith("#")
            or stripped.startswith("|")
            or stripped.startswith("- ")
            or stripped.startswith("* ")
            or re.match(r"^\d+\.\s", stripped)
            or stripped.startswith("```")
            or stripped.startswith("<")
        ):
            normalized_lines.append(stripped)
            continue

        # 長文段落だけ軽く整形
        normalized_lines.append(ensure_sentence_breaks(stripped))

    body = "\n".join(normalized_lines)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    # H2前後の余白を整理
    body = re.sub(r"\n(##\s)", r"\n\n\1", body)
    body = re.sub(r"\n(###\s)", r"\n\n\1", body)

    return body.strip()


def ensure_required_sections(body: str, topic: str) -> str:
    """
    モデル出力が弱かった場合の最低限補強
    """
    body = body.strip()

    if "## まとめ" not in body and "## この記事のまとめ" not in body:
        body += (
            "\n\n## まとめ\n\n"
            f"{topic}は、今後の実用性や話題性の面でも注目度が高いテーマです。"
            "導入メリットだけでなく、使いどころや注意点も押さえて、自分に合う活用方法を見極めることが重要です。"
        )

    has_table = "|" in body and "---" in body
    if not has_table:
        body += (
            "\n\n## 比較早見表\n\n"
            "| 項目 | 内容 |\n"
            "|---|---|\n"
            f"| テーマ | {topic} |\n"
            "| 注目ポイント | 実用性・話題性・将来性 |\n"
            "| 向いている人 | IT・AI・ガジェットに関心がある人 |\n"
            "| チェックすべき点 | コスト、導入しやすさ、継続利用のしやすさ |\n"
        )

    has_list = re.search(r"^[-*]\s", body, re.MULTILINE) or re.search(r"^\d+\.\s", body, re.MULTILINE)
    if not has_list:
        body += (
            "\n\n## チェックポイント\n\n"
            "- 導入する目的が明確か\n"
            "- 無料で試せるか\n"
            "- 継続コストに見合う価値があるか\n"
            "- 他の選択肢と比較して優位性があるか\n"
        )

    return body.strip()


# --------------------------------------------------
# トレンド取得
# --------------------------------------------------
def fetch_trends() -> list[str]:
    feed = feedparser.parse(HATENA_IT_RSS)

    topics = []
    for entry in feed.entries[:15]:
        title = str(getattr(entry, "title", "")).strip()
        if title:
            topics.append(title)

    return topics


def score_topic(title: str) -> int:
    t = title.lower()

    positive_keywords = [
        "ai", "gemini", "chatgpt", "claude", "copilot", "notion",
        "microsoft", "google", "openai", "gpu", "cpu", "windows",
        "mac", "iphone", "android", "vscode", "github", "api",
        "llm", "生成ai", "人工知能", "ガジェット", "ツール", "アプリ",
        "レビュー", "比較", "新機能", "正式版", "アップデート"
    ]

    negative_keywords = [
        "芸能", "野球", "サッカー", "アイドル", "映画", "音楽",
        "グルメ", "クーポン", "不倫", "事件", "事故", "災害",
        "選挙", "政治", "セール情報だけ", "懸賞"
    ]

    score = 0

    for kw in positive_keywords:
        if kw in t:
            score += 2

    for kw in negative_keywords:
        if kw in t:
            score -= 5

    # 長すぎるタイトルは少し減点
    if len(title) > 80:
        score -= 1

    return score


def pick_best_topic(topics: list[str]) -> str:
    if not topics:
        raise ValueError("トピックがありません")

    ranked = sorted(topics, key=score_topic, reverse=True)
    return ranked[0]


# --------------------------------------------------
# Gemini プロンプト
# --------------------------------------------------
def build_prompt(topic: str) -> str:
    today = datetime.date.today().isoformat()

    return f"""
あなたは、日本のIT・AI・ガジェット分野に強いプロのWebライターです。
次のトピックをもとに、SEOと可読性を両立した日本語ブログ記事を作成してください。

【今日の日付】
{today}

【トピック】
{topic}

【記事の目的】
- 初心者でも流れが分かること
- スマホでも読みやすいこと
- 情報を整理して比較しやすいこと
- 過剰に煽らず、実用的で自然な文章にすること

【必須ルール】
- 日本語で書く
- body は Markdown 形式
- H2見出しを3〜5個入れる
- 必要に応じてH3見出しを入れる
- 1段落は2〜4文程度に抑える
- 箇条書きを1つ以上入れる
- 比較表を1つ以上入れる
- 最後に「まとめ」見出しを入れる
- 誇大広告・断定表現は避ける
- 未確認情報を事実のように断定しない
- 他社誹謗や危険な煽りは避ける
- アフィリエイト記事として使いやすいよう、ツール選び・比較・注意点の観点を自然に含める
- コードブロックは不要
- body の先頭にタイトルや description を繰り返さない

【body の望ましい構成】
1. 導入
2. 概要や背景
3. メリット・注目点
4. 注意点・向いている人
5. 比較表
6. まとめ

【description のルール】
- 80〜120文字程度
- 検索結果で読みたくなる自然な説明

【tags のルール】
- 3〜5個
- 短い日本語または一般的な英単語
- 例: AI, ガジェット, Windows, Microsoft, 生産性 など

JSONだけを返してください。説明文や前置きは不要です。
""".strip()


# --------------------------------------------------
# 記事生成
# --------------------------------------------------
def generate_article_structured(topic: str, max_retries: int = 3) -> dict:
    prompt = build_prompt(topic)

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                    response_json_schema=ARTICLE_SCHEMA,
                ),
            )

            text = getattr(response, "text", "") or ""

            if not text.strip():
                raise ValueError("モデル応答が空でした")

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = extract_json_fallback(text)

            if not isinstance(data, dict):
                raise ValueError("JSONオブジェクト形式ではありません")

            return data

        except Exception as e:
            error_message = str(e)

            # 429 / quota 系は待って再試行
            if "429" in error_message or "RESOURCE_EXHAUSTED" in error_message or "quota" in error_message.lower():
                wait_seconds = parse_retry_seconds(error_message, default_seconds=65)
                if attempt < max_retries:
                    print(f"[WARN] API制限に達しました。{wait_seconds}秒待って再試行します... ({attempt}/{max_retries})")
                    time.sleep(wait_seconds)
                    continue

            # 最終試行時だけ fallback を試す
            if attempt == max_retries:
                print("[WARN] structured output での生成に失敗したため、フォールバック生成を試します...")
                return generate_article_fallback(topic)

            print(f"[WARN] 生成に失敗しました。再試行します... ({attempt}/{max_retries})")
            time.sleep(5)

    raise RuntimeError("記事生成に失敗しました")


def generate_article_fallback(topic: str) -> dict:
    """
    structured output がうまくいかない場合の保険
    """
    prompt = build_prompt(topic) + "\n\n必ずJSONオブジェクトのみを返してください。"

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=8192,
        ),
    )

    text = getattr(response, "text", "") or ""
    data = extract_json_fallback(text)

    if not isinstance(data, dict):
        raise ValueError("フォールバック生成でもJSONオブジェクトが得られませんでした")

    return data


def sanitize_title(title: str, topic: str) -> str:
    title = strip_control_chars(title).strip()
    title = re.sub(r"\s+", " ", title)
    if not title:
        title = f"{topic}をわかりやすく解説"
    return title[:80]


def sanitize_description(description: str, topic: str) -> str:
    description = strip_control_chars(description).strip()
    description = re.sub(r"\s+", " ", description)
    if not description:
        description = f"{topic}のポイントや注目点、比較の観点を初心者向けにわかりやすく解説します。"
    return description[:140]


def sanitize_tags(tags) -> list[str]:
    if not isinstance(tags, list):
        return ["AI", "IT", "レビュー"]

    cleaned = []
    for tag in tags:
        t = strip_control_chars(str(tag)).strip()
        if not t:
            continue
        if len(t) > 20:
            t = t[:20]
        cleaned.append(t)

    # 重複除去
    unique = list(dict.fromkeys(cleaned))

    if len(unique) < 3:
        fallback = ["AI", "IT", "レビュー", "ガジェット", "ツール"]
        for t in fallback:
            if t not in unique:
                unique.append(t)

    return unique[:5]


def sanitize_body(body: str, topic: str) -> str:
    body = strip_control_chars(str(body)).strip()

    if not body:
        body = (
            f"## {topic}とは\n\n"
            f"{topic}について整理して理解するために、注目ポイントや実用面をわかりやすくまとめます。\n\n"
            "## 注目ポイント\n\n"
            "- 話題性が高い\n"
            "- 実用面で比較しやすい\n"
            "- 今後の活用方法を考えやすい\n\n"
            "## 比較早見表\n\n"
            "| 項目 | 内容 |\n"
            "|---|---|\n"
            f"| テーマ | {topic} |\n"
            "| 特徴 | 注目度が高い |\n"
            "| 確認点 | 実用性と継続性 |\n\n"
            "## まとめ\n\n"
            "特徴と注意点を確認しながら、自分に合う使い方を見極めることが大切です。"
        )

    body = normalize_body(body)
    body = ensure_required_sections(body, topic)

    return body


def validate_article_data(data: dict, topic: str) -> dict:
    title = sanitize_title(data.get("title", ""), topic)
    description = sanitize_description(data.get("description", ""), topic)
    tags = sanitize_tags(data.get("tags", []))
    body = sanitize_body(data.get("body", ""), topic)

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "body": body,
    }


# --------------------------------------------------
# 保存
# --------------------------------------------------
def save_article(data: dict) -> Path:
    today = datetime.date.today().isoformat()
    uid = secrets.token_hex(3)
    slug = f"{today}-{uid}"

    file_path = OUTPUT_DIR / f"{slug}.md"

    tags_yaml = "\n".join([f"  - {yaml_quote(tag)}" for tag in data["tags"]])

    frontmatter = (
        "---\n"
        f"title: {yaml_quote(data['title'])}\n"
        f"description: {yaml_quote(data['description'])}\n"
        f"pubDate: {yaml_quote(today)}\n"
        "tags:\n"
        f"{tags_yaml}\n"
        f"heroImage: {yaml_quote(f'/images/posts/{slug}.jpg')}\n"
        "---\n\n"
    )

    content = frontmatter + data["body"].strip() + "\n"
    file_path.write_text(content, encoding="utf-8")

    print(f"[OK] 記事を保存しました: {file_path}")
    return file_path


# --------------------------------------------------
# メイン処理
# --------------------------------------------------
def main():
    print("[INFO] トレンド取得中...")
    topics = fetch_trends()

    if not topics:
        print("[ERROR] トレンドが取得できませんでした")
        sys.exit(1)

    print(f"[INFO] 候補トピック数: {len(topics)}")
    topic = pick_best_topic(topics)
    print(f"[INFO] 選択されたトピック: {topic}")
    print(f"[INFO] 記事生成中（モデル: {MODEL_NAME}）...")

    raw_data = generate_article_structured(topic)
    data = validate_article_data(raw_data, topic)

    print(f"[INFO] タイトル: {data['title']}")
    save_article(data)
    print("[DONE] 記事生成完了")


if __name__ == "__main__":
    main()

# ファイル: scripts/generate_image.py
# 記事タイトルからアイキャッチ画像を自動生成（Pollinations.ai / 無料）

import os
import sys
import io
import re
import time
import urllib.parse
from pathlib import Path

import requests
from google import genai
from dotenv import load_dotenv
from slugify import slugify

# Windows PowerShell 文字化け対策
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("[ERROR] GEMINI_API_KEY が .env に設定されていません")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_API_KEY)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = PROJECT_ROOT / "src" / "content" / "blog"
IMAGE_DIR = PROJECT_ROOT / "public" / "images" / "posts"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def title_to_image_prompt(title: str) -> str:
    """
    日本語タイトルを、画像生成向けの英語プロンプトに変換
    Pollinations は日本語プロンプトも受け付けるが、英語の方が品質が安定
    """
    prompt = f"""以下の日本語ブログ記事タイトルを、アイキャッチ画像生成用の英語プロンプトに変換してください。

【タイトル】
{title}

【要件】
- 英語で80単語以内
- モダン・プロフェッショナル・ブログアイキャッチ向けのスタイル
- 具体的なビジュアル要素（色、物、シーン）を含める
- "professional blog hero image, modern, clean, vibrant colors, 16:9" を末尾に必ず追加
- 人物の顔は含めない（権利リスク回避）
- 出力は英語プロンプトのみ。説明文・引用符は不要。
"""
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
    )
    return response.text.strip().strip('"').strip("'")


def download_image(prompt: str, output_path: Path, width: int = 1280, height: int = 720):
    """Pollinations.ai から画像をダウンロード"""
    encoded = urllib.parse.quote(prompt)
    # nologo=true でウォーターマーク除去、seed固定で再現性確保
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={width}&height={height}&nologo=true&model=flux"
    )
    print(f"[INFO] 画像生成URL: {url[:100]}...")

    # Pollinations は初回アクセスで生成するため、タイムアウト長め
    response = requests.get(url, timeout=120)
    response.raise_for_status()

    output_path.write_bytes(response.content)
    print(f"[OK] 画像を保存しました: {output_path} ({len(response.content) // 1024} KB)")


def extract_title_and_slug(md_file: Path) -> tuple[str, str]:
    """Markdownファイルからタイトルとスラッグを抽出"""
    content = md_file.read_text(encoding="utf-8")

    # タイトル抽出
    title_match = re.search(r'^title:\s*"([^"]+)"', content, re.MULTILINE)
    if not title_match:
        raise ValueError(f"タイトルが抽出できません: {md_file}")
    title = title_match.group(1)

    # heroImage からスラッグを抽出（これが正）
    hero_match = re.search(r'^heroImage:\s*"/images/posts/([^"]+)\.jpg"', content, re.MULTILINE)
    if not hero_match:
        raise ValueError(f"heroImageが見つかりません: {md_file}")
    slug = hero_match.group(1)

    return title, slug



def find_articles_without_image() -> list[Path]:
    """画像がまだ存在しない記事を抽出"""
    targets = []
    for md_file in BLOG_DIR.glob("*.md"):
        try:
            _, slug = extract_title_and_slug(md_file)
            image_path = IMAGE_DIR / f"{slug}.jpg"
            if not image_path.exists():
                targets.append(md_file)
        except Exception as e:
            print(f"[WARN] {md_file.name}: {e}")
    return targets


def main():
    print("[INFO] 画像未生成の記事を検索中...")
    articles = find_articles_without_image()

    if not articles:
        print("[INFO] 画像生成が必要な記事はありません")
        return

    print(f"[INFO] {len(articles)}件の記事に画像を生成します")

    for i, md_file in enumerate(articles, 1):
        title, slug = extract_title_and_slug(md_file)
        image_path = IMAGE_DIR / f"{slug}.jpg"

        print(f"\n[{i}/{len(articles)}] {title}")

        try:
            en_prompt = title_to_image_prompt(title)
            print(f"[INFO] 英語プロンプト: {en_prompt[:120]}...")
            download_image(en_prompt, image_path)
            # Pollinations への連続アクセスを避ける
            time.sleep(3)
        except Exception as e:
            print(f"[ERROR] {title}: {e}")
            continue

    print("\n[DONE] 画像生成処理完了")


if __name__ == "__main__":
    main()

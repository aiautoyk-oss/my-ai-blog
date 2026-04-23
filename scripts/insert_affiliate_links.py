import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BLOG_DIR = PROJECT_ROOT / "src" / "content" / "blog"
DICT_PATH = PROJECT_ROOT / "scripts" / "affiliate-links.json"

PR_NOTICE = """<div class="pr-note"><strong>広告・PR表記:</strong> この記事には広告・PRを含みます。</div>"""

SUPPORTED_EXTENSIONS = {".md", ".mdx"}


def load_affiliate_dict() -> dict:
    if not DICT_PATH.exists():
        print(f"[ERROR] 辞書ファイルが見つかりません: {DICT_PATH}")
        sys.exit(1)

    raw = DICT_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        print(f"[ERROR] 辞書ファイルが空です: {DICT_PATH}")
        sys.exit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSONの形式が壊れています: {DICT_PATH}")
        print(f"        {e}")
        sys.exit(1)

    if not isinstance(data, dict):
        print(f"[ERROR] 辞書ファイルの最上位はオブジェクト形式である必要があります: {DICT_PATH}")
        sys.exit(1)

    return data


def get_target_files():
    files = []
    if not BLOG_DIR.exists():
        return files

    for path in sorted(BLOG_DIR.iterdir()):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.append(path)
    return files


def split_frontmatter(content: str):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", content, re.DOTALL)
    if not m:
        return "", content

    frontmatter_body = m.group(1).strip()
    frontmatter = f"---\n{frontmatter_body}\n---\n"
    body = content[m.end():].lstrip("\n")
    return frontmatter, body


def remove_legacy_pr_notice(body: str) -> str:
    # 既存のHTML PRボックスを削除
    body = re.sub(
        r'\s*<div class="pr-note">.*?</div>\s*',
        '\n',
        body,
        flags=re.DOTALL
    )

    # 古い Markdown NOTE ブロックを削除
    body = re.sub(
        r'(?ms)^\s*>\s*\[!NOTE\]\s*\n(?:^\s*>.*\n?)*',
        '',
        body
    )

    # 余分な空行を整理
    body = re.sub(r'\n{3,}', '\n\n', body).strip()
    return body


def ensure_single_pr_notice(body: str) -> str:
    body = remove_legacy_pr_notice(body)
    return PR_NOTICE + "\n\n" + body


def is_line_skippable(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if s.startswith("#"):
        return True
    if s.startswith("```"):
        return True
    if s.startswith(">"):
        return True
    if s.startswith("|"):
        return True
    if s.startswith("<"):
        return True
    if "](" in s:
        return True
    if "<a " in s.lower():
        return True
    if "<img" in s.lower():
        return True
    if "http://" in s or "https://" in s:
        return True
    return False


def make_link_html(keyword: str, info: dict) -> str:
    url = str(info.get("url", "")).strip()
    label = str(info.get("label", keyword)).strip() or keyword
    mode = str(info.get("mode", "manual")).strip() or "manual"

    if not url:
        return keyword

    rel = "nofollow sponsored noopener"
    return (
        f'<a href="{url}" target="_blank" rel="{rel}" '
        f'data-aff-mode="{mode}" class="affiliate-link">{label}</a>'
    )


def replace_keyword_once(line: str, keyword: str, replacement: str):
    if re.fullmatch(r"[A-Za-z0-9 .+_\-]+", keyword):
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(keyword)}(?![A-Za-z0-9_])"
    else:
        pattern = re.escape(keyword)

    new_line, count = re.subn(pattern, replacement, line, count=1)
    return new_line, count > 0


def insert_affiliate_links(body: str, aff_dict: dict) -> str:
    lines = body.splitlines()
    inserted_keywords = set()

    items = sorted(aff_dict.items(), key=lambda x: len(x[0]), reverse=True)

    for i, line in enumerate(lines):
        if is_line_skippable(line):
            continue

        if 'class="affiliate-link"' in line:
            continue

        for keyword, info in items:
            if keyword in inserted_keywords:
                continue

            if keyword not in line:
                continue

            replacement = make_link_html(keyword, info)
            new_line, changed = replace_keyword_once(line, keyword, replacement)
            if changed:
                lines[i] = new_line
                inserted_keywords.add(keyword)

    return "\n".join(lines)


def process_markdown_file(md_path: Path, aff_dict: dict):
    content = md_path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(content)

    if not frontmatter:
        print(f"[WARN] frontmatter が見つからないためスキップ: {md_path.name}")
        return

    body = ensure_single_pr_notice(body)
    body = insert_affiliate_links(body, aff_dict)

    final_content = frontmatter + "\n" + body.strip() + "\n"

    if final_content == content:
        print(f"[SKIP] 変更なし: {md_path.name}")
        return

    md_path.write_text(final_content, encoding="utf-8")
    print(f"[OK] アフィリエイトリンクを挿入しました: {md_path.name}")


def main():
    aff_dict = load_affiliate_dict()
    files = get_target_files()

    if not files:
        print("[INFO] 対象記事がありません")
        return

    print(f"[INFO] {len(files)}件の記事を処理します")

    for file_path in files:
        process_markdown_file(file_path, aff_dict)

    print("[DONE] アフィリエイトリンク挿入処理が完了しました")


if __name__ == "__main__":
    main()

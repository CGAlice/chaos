"""
Wayback MachineのHTMLページからデッキレシピをテキスト抽出してrecipes.jsonに追加・更新する。

- 新規URL: レシピを追加
- 既存でincompleteフラグ付き: HTMLデータで上書き（精度改善）
- 既存でincompleteなし: スキップ
"""

import re
import json
import time
import urllib.request
import urllib.parse
from html.parser import HTMLParser

URLS_FILE = "/tmp/recipe_urls_clean.txt"
RECIPES_JSON = "/home/alice26/chaos/chaos-sim/recipes.json"
OUTPUT_JSON = "/home/alice26/chaos/chaos-sim/recipes.json"
DELAY = 1.5  # リクエスト間隔（秒）


# ---------------------------------------------------------------------------
# メタデータ推定
# ---------------------------------------------------------------------------

def parse_slug(slug: str) -> dict:
    name = slug.lower()
    year_m = re.search(r"(\d{4})", name)
    year = int(year_m.group(1)) if year_m else None

    if "wgp" in name or "wpg" in name:
        event_type = "WGP"
    elif "bcf" in name:
        event_type = "BCF"
    elif "chaosfes" in name or "chaos_fes" in name:
        event_type = "ChaosFes"
    elif "character1" in name or "chara1" in name:
        event_type = "キャラ1"
    elif "shirokurofes" in name or "shirokuro" in name:
        event_type = "白黒フェス"
    elif "murap" in name:
        event_type = "むらやまP杯"
    elif "nico" in name:
        event_type = "ニコ生"
    elif "column" in name:
        event_type = "コラム"
    elif re.match(r".*recipe_\d{4}", name) or re.match(r"^\d{4}_", name):
        event_type = "日本選手権"
    elif re.match(r".*recipe_gr\d", name):
        event_type = "グレンダイザー"
    else:
        event_type = "その他"

    loc_map = {
        "akita": "秋田", "tokyo": "東京", "osaka": "大阪", "nagoya": "名古屋",
        "kyoto": "京都", "hamamatsu": "浜松", "kagoshima": "鹿児島",
        "hakata": "博多", "sapporo": "札幌", "sendai": "仙台",
        "okayama": "岡山", "kanazawa": "金沢", "yamagata": "山形",
        "makuhari": "幕張", "takamatsu": "高松", "chiba": "千葉",
        "fukuoka": "福岡", "hiroshima": "広島", "kobe": "神戸",
        "yokohama": "横浜", "niigata": "新潟", "zenkoku": "全国",
        "_oo": "大阪", "_tk": "東京", "_to": "東京", "_hi": "広島",
        "_ka": "神奈川", "_sa": "札幌", "_hs": "広島", "_sz": "静岡",
        "_se": "仙台", "_kyo": "京都", "_na": "名古屋", "_nag": "名古屋",
        "_ok": "岡山", "_yo": "横浜", "_ku": "久留米", "_ha": "博多",
    }
    location = ""
    for k in sorted(loc_map, key=len, reverse=True):
        if k in name:
            location = loc_map[k]
            break

    return {"year": year, "event_type": event_type, "location": location}


# ---------------------------------------------------------------------------
# HTMLパーサ
# ---------------------------------------------------------------------------

class RecipeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.card_numbers = []
        self.in_td = False
        self.td_text = ""
        self.current_row = []
        self.all_rows = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "a":
            href = attrs_dict.get("href", "")
            m = re.search(r"cardno=([^&\s\"']+)", href, re.IGNORECASE)
            if m:
                cardno = urllib.parse.unquote(m.group(1)).upper()
                self.card_numbers.append(cardno)
        elif tag == "td":
            self.in_td = True
            self.td_text = ""
        elif tag == "tr":
            pass

    def handle_endtag(self, tag):
        if tag == "td":
            self.in_td = False
            self.current_row.append(self.td_text.strip())
        elif tag == "tr":
            if self.current_row:
                self.all_rows.append(self.current_row)
                self.current_row = []

    def handle_data(self, data):
        if self.in_td:
            self.td_text += data


# ---------------------------------------------------------------------------
# Wayback Machine フェッチ
# ---------------------------------------------------------------------------

def fetch_wayback(timestamp: str, url: str, retries: int = 3) -> str | None:
    clean_url = re.sub(r":80/", "/", url)
    clean_url = re.sub(r"^http://", "https://", clean_url)
    wb_url = f"https://web.archive.org/web/{timestamp}/{clean_url}"

    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                wb_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; recipe-scraper/1.0)"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            for enc in ("utf-8", "shift_jis", "euc-jp", "iso-2022-jp"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                print(f"  ERROR: {e}")
    return None


# ---------------------------------------------------------------------------
# デッキ抽出
# ---------------------------------------------------------------------------

DECK_BREAK_RE = re.compile(
    r'(?:▲\s*デッキレシピ[^<]{0,30}に戻る|デッキレシピTOPに戻る)',
    re.IGNORECASE
)


def extract_decks_v2(html: str) -> list[list[dict]]:
    """hrefのcardnoと枚数セルをペアリングしてデッキを抽出"""
    sections = DECK_BREAK_RE.split(html)
    decks = []

    for sec_html in sections:
        parser = RecipeParser()
        parser.feed(sec_html)

        card_nums = parser.card_numbers
        counts = []

        for row in parser.all_rows:
            for cell in row:
                m = re.match(r'^\s*([1-4])\s*$', cell)
                if m:
                    counts.append(int(m.group(1)))
                    break

        if not card_nums:
            continue

        paired = []
        for i, num in enumerate(card_nums):
            cnt = counts[i] if i < len(counts) else 1
            paired.append({"number": num, "count": cnt})

        if len(paired) >= 8:  # 最低8枚のデッキのみ有効
            decks.append(paired)

    return decks


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def slug_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    return path.rstrip("/").split("/")[-1].replace(".html", "")


def existing_slug_map(recipes: list[dict]) -> dict[str, list[dict]]:
    """スラグ -> レシピリスト のマップ"""
    result = {}
    for r in recipes:
        sf = r["source_file"]
        slug = re.sub(r'^-recipe-', '', sf).replace('.png', '')
        result.setdefault(slug, []).append(r)
    return result


def main():
    # 既存データ読み込み
    with open(RECIPES_JSON, encoding="utf-8") as f:
        all_recipes = json.load(f)
    print(f"既存レシピ数: {len(all_recipes)}")

    slug_map = existing_slug_map(all_recipes)

    # URLリスト
    with open(URLS_FILE) as f:
        lines = [l.strip() for l in f if l.strip()]

    # URLをスラグで重複除去（最新タイムスタンプを優先）
    url_by_slug: dict[str, tuple[str, str]] = {}
    for line in lines:
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        ts, url = parts[0], parts[1]
        slug = slug_from_url(url)
        # タイムスタンプが新しい方を使う
        if slug not in url_by_slug or ts > url_by_slug[slug][0]:
            url_by_slug[slug] = (ts, url)

    print(f"ユニークURL数: {len(url_by_slug)}")

    new_recipes = []
    updated_slugs = []
    skipped = 0
    errors = []

    # 既存でincompleteなものも対象に
    incomplete_slugs = set()
    for slug, rs in slug_map.items():
        if any("incomplete" in r.get("flags", []) for r in rs):
            incomplete_slugs.add(slug)
    print(f"incompleteスラグ数: {len(incomplete_slugs)}")

    for i, (slug, (ts, url)) in enumerate(sorted(url_by_slug.items())):
        is_existing = slug in slug_map
        is_incomplete = slug in incomplete_slugs

        # スキップ判定
        if is_existing and not is_incomplete:
            skipped += 1
            continue

        print(f"[{i+1}/{len(url_by_slug)}] {slug} ({'update' if is_existing else 'new'}) ...", end=" ", flush=True)

        html = fetch_wayback(ts, url)
        if not html:
            print("SKIP")
            errors.append(slug)
            time.sleep(DELAY)
            continue

        decks = extract_decks_v2(html)
        if not decks:
            print("no decks")
            time.sleep(DELAY)
            continue

        meta = parse_slug(slug)

        deck_recipes = []
        for rank, cards in enumerate(decks, 1):
            if not cards:
                continue
            recipe_id = f"{slug}_{rank}"
            partner = cards[0]["number"] if cards else ""
            total = sum(c["count"] for c in cards)
            deck_recipes.append({
                "id": recipe_id,
                "source_file": slug,
                "source_url": f"https://web.archive.org/web/{ts}/{url}",
                "year": meta["year"],
                "event_type": meta["event_type"],
                "location": meta["location"],
                "rank": rank,
                "partner": partner,
                "cards": cards,
                "total": total,
            })

        if not deck_recipes:
            print("no valid decks")
            time.sleep(DELAY)
            continue

        if is_existing:
            # 既存レシピを削除して新しいものに置き換え
            all_recipes = [r for r in all_recipes if
                re.sub(r'^-recipe-', '', r["source_file"]).replace('.png', '') != slug]
            updated_slugs.append(slug)
        else:
            new_recipes.extend(deck_recipes)
            all_recipes.extend(deck_recipes)

        if is_existing:
            all_recipes.extend(deck_recipes)

        print(f"{len(deck_recipes)}デッキ")
        time.sleep(DELAY)

    print(f"\n新規: {len(new_recipes)}件, 更新: {len(updated_slugs)}件, スキップ: {skipped}, エラー: {len(errors)}")

    all_recipes.sort(key=lambda r: (r.get("year") or 0, r["source_file"], r["rank"]))
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(all_recipes, f, ensure_ascii=False, indent=2)
    print(f"保存完了: {len(all_recipes)}レシピ → {OUTPUT_JSON}")

    if errors:
        print(f"エラー: {errors[:20]}")


if __name__ == "__main__":
    main()

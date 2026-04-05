# scrape_turnbackhoax_fixed.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
TurnBackHoax Scraper (HTML) — 2025-12-02
- Mengambil daftar artikel dari /articles dengan dukungan:
  * Pagination (nav buttons .nav-pagination)
  * Filter bulan (dateRange=Month%20Year) dari dropdown
  * Filter kategori (category=all/politik/health/...). Default: all
- Mengambil detail artikel (judul, label [..], tanggal, author, kategori, sumber, narasi/penjelasan/kesimpulan, konten)
- Konkurensi untuk scraping detail (httpx + asyncio)
- Dedup URL dan opsi --max untuk membatasi jumlah artikel

Persiapan:
  pip install httpx selectolax python-dateutil tqdm

Contoh pakai:
  # Crawl semua bulan (paling lama -> terbaru) hingga habis
  python scrape_turnbackhoax_fixed.py --mode auto --out turnbackhoax_all.csv --concurrency 6

  # Crawl mundur 24 bulan mulai Desember 2025
  python scrape_turnbackhoax_fixed.py --mode archive --start 2025-12 --months 24 --out turnbackhoax_24mo.csv

  # Hanya kategori 'politik'
  python scrape_turnbackhoax_fixed.py --mode auto --category politik --out tbh_politik.csv
"""

from __future__ import annotations

import asyncio, argparse, csv, re, sys
from datetime import datetime
from urllib.parse import urljoin, urldefrag, urlparse, quote_plus

import httpx
from selectolax.parser import HTMLParser
from dateutil import parser as dtparser

BASE = "https://turnbackhoax.id"
LISTING = f"{BASE}/articles"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124 Safari/537.36 (+academic crawler)"
    ),
    "Accept-Language": "id,en;q=0.9",
}

MONTH_NAMES = [
    "January","February","March","April","May","June",
    "July","August","September","October","November","December"
]

ARTICLE_PAT = re.compile(r"^/articles/(\d+)(?:--[a-z0-9-]+)?/?$", re.I)
LABEL_BRACKET_RE = re.compile(r"\[([^\]]+)\]")

# ------------------------ helpers ------------------------

def clean(t: str|None) -> str:
    if not t: return ""
    t = t.replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()

def norm_url(href: str, base: str = BASE) -> str|None:
    if not href: return None
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "tel:", "#")): return None
    u = urljoin(base, href)
    u, _ = urldefrag(u)
    p = urlparse(u)
    if p.netloc.lower() not in ("turnbackhoax.id", "www.turnbackhoax.id"): return None
    return p._replace(scheme="https", netloc="turnbackhoax.id").geturl()

async def fetch_text(client: httpx.AsyncClient, url: str, attempts: int = 4) -> str|None:
    last = None
    for i in range(1, attempts+1):
        try:
            r = await client.get(url, headers=HEADERS, timeout=40.0, follow_redirects=True)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            await asyncio.sleep(0.9 * i)
    print(f"[ERROR] fetch failed {url}: {last}", file=sys.stderr)
    return None

# -------------------- listing scraping --------------------

def extract_article_links_from_listing(html: str) -> list[str]:
    tree = HTMLParser(html)
    urls: list[str] = []
    # Kartu artikel
    for a in tree.css(".news-card-h-alt a[href^='/articles/']"):
        href = a.attributes.get("href")
        u = norm_url(href)
        if not u: 
            continue
        p = urlparse(u)
        if ARTICLE_PAT.match(p.path):
            if u not in urls:
                urls.append(u)
    return urls

def get_total_pages(html: str) -> int:
    tree = HTMLParser(html)
    pages = []
    # data-page di tombol pagination
    for btn in tree.css(".nav-pagination .nav-item"):
        dp = btn.attributes.get("data-page", "")
        if dp.isdigit():
            pages.append(int(dp))
    if pages:
        return max(pages)
    # fallback: tombol "last"
    last_btn = tree.css_first(".nav-pagination .sprites-last")
    if last_btn:
        dp = last_btn.attributes.get("data-page")
        if dp and dp.isdigit():
            return int(dp)
    return 1

def build_listing_url(page: int, daterange: str|None, category: str = "all") -> str:
    if daterange and daterange.lower() != "all":
        return f"{LISTING}?category={category}&page={page}&dateRange={quote_plus(daterange)}"
    return f"{LISTING}?category={category}&page={page}"

def extract_months_dropdown(html: str) -> list[str]:
    """
    Ambil label bulan dari dropdown di halaman /articles.
    Contoh label: 'December 2025', 'November 2025', ..., 'November 2017'
    """
    tree = HTMLParser(html)
    out: list[str] = []
    for a in tree.css(".custom-selector__dropdown a[href*='dateRange=']"):
        label = clean(a.text())
        if re.search(r"\b(20\d{2})\b", label):
            out.append(label)
    # dedupe sambil pertahankan urutan
    seen, uniq = set(), []
    for m in out:
        if m not in seen:
            seen.add(m); uniq.append(m)
    return uniq

# -------------------- article parsing ---------------------

def parse_article(html: str, url: str) -> dict:
    tree = HTMLParser(html)
    # title
    h1 = tree.css_first("h1")
    title = clean(h1.text()) if h1 else clean(tree.css_first("title").text() if tree.css_first("title") else "")
    # label dari judul, seperti [SALAH], [BENAR], dst.
    mlabel = LABEL_BRACKET_RE.search(title or "")
    label = mlabel.group(1).strip().upper() if mlabel else ""
    # authored date / tanggal
    # 1) meta published_time
    date_iso = ""
    for sel in ["meta[property='article:published_time']",
                "meta[name='article:published_time']",
                "meta[itemprop='datePublished']"]:
        n = tree.css_first(sel)
        if n and n.attributes.get("content"):
            try:
                date_iso = dtparser.parse(n.attributes["content"]).isoformat()
                break
            except Exception:
                pass
    # 2) time tag
    if not date_iso:
        t = tree.css_first("time")
        if t:
            raw = t.attributes.get("datetime") or t.text()
            try:
                date_iso = dtparser.parse(raw).isoformat()
            except Exception:
                pass
    # author (opsional)
    author = ""
    for sel in ["meta[name='author']", "meta[property='article:author']", "a[rel='author']"]:
        n = tree.css_first(sel)
        if n:
            author = clean(n.attributes.get("content") or n.text())
            if author: break

    # kategori / topik (opsional)
    topic = ""
    # di WP biasanya a[rel='category tag']; pada situs baru bisa beda.
    cats = [clean(a.text()) for a in tree.css("a[rel='category tag']") if clean(a.text())]
    topic = cats[0] if cats else ""

    # blok artikel
    content_container = (tree.css_first("div.entry-content") 
                         or tree.css_first("article .entry-content") 
                         or tree.css_first(".entry") 
                         or tree.css_first("article"))
    raw_text = clean(content_container.text(separator="\n", strip=True)) if content_container else ""

    # pecah menjadi bagian
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    def find_idx(prefixes: list[str]) -> int|None:
        for i, l in enumerate(lines):
            low = l.lower().rstrip(":")
            if any(low.startswith(p) for p in prefixes):
                return i
        return None

    i_n = find_idx(["narasi", "kronologi", "klaim"])
    i_p = find_idx(["penjelasan", "klarifikasi", "fakta"])
    i_k = find_idx(["kesimpulan", "verdict", "kategori"])
    i_r = find_idx(["referensi", "rujukan", "sumber"])

    def slice_section(start: int|None, nexts: list[int|None]) -> str:
        if start is None: return ""
        ends = [x for x in nexts if x is not None and x > start]
        end = min(ends) if ends else len(lines)
        return "\n".join(lines[start+1:end]).strip()

    claim       = slice_section(i_n, [i_p, i_k, i_r])
    explanation = slice_section(i_p, [i_k, i_r])
    conclusion  = slice_section(i_k, [i_r])

    # fallback untuk konten penuh
    parts = [p for p in (claim, explanation, conclusion) if p]
    content = "\n\n".join(parts) if parts else "\n".join(lines)

    # slug numeric
    mslug = re.search(r"/articles/(\d+)", url)
    slug = mslug.group(1) if mslug else ""

    return {
        "url": url,
        "slug": slug,
        "title": title,
        "label": label,
        "date": date_iso or "",
        "author": author or "",
        "topic": topic or "",
        "source": "",  # bisa terisi jika heading "Sumber" ditemukan di i_r slice
        "claim": claim,
        "explanation": explanation,
        "conclusion": conclusion,
        "content": content,
    }

# ---------------- harvest per month/pages -----------------

async def harvest_month(client: httpx.AsyncClient, daterange: str, category: str = "all") -> list[str]:
    all_urls, seen = [], set()

    # page 1
    url = build_listing_url(page=1, daterange=daterange, category=category)
    html = await fetch_text(client, url)
    if not html:
        print(f"[HARVEST] {daterange}: gagal muat halaman 1")
        return []

    total_pages = get_total_pages(html)
    urls = extract_article_links_from_listing(html)
    for u in urls:
        if u not in seen: seen.add(u); all_urls.append(u)
    print(f"[HARVEST] {daterange} page 1/{total_pages}: +{len(urls)} (total {len(all_urls)})")

    # page 2..N
    for p in range(2, total_pages+1):
        lurl = build_listing_url(page=p, daterange=daterange, category=category)
        html = await fetch_text(client, lurl)
        if not html:
            print(f"[HARVEST] {daterange} page {p}: gagal/skip")
            continue
        urls = extract_article_links_from_listing(html)
        new = [u for u in urls if u not in seen]
        for u in new:
            seen.add(u); all_urls.append(u)
        print(f"[HARVEST] {daterange} page {p}/{total_pages}: +{len(new)} (total {len(all_urls)})")

    return all_urls

async def harvest_archive(client: httpx.AsyncClient, start_ym: str, months: int, category: str = "all") -> list[str]:
    y, m = map(int, start_ym.split("-"))
    base = y * 12 + (m - 1)
    all_urls: list[str] = []
    for k in range(months):
        cur = base - k
        if cur < 0: break
        yy = cur // 12
        mm = cur % 12 + 1
        daterange = f"{MONTH_NAMES[mm-1]} {yy}"
        urls = await harvest_month(client, daterange, category)
        all_urls.extend(urls)
    return all_urls

async def harvest_all_months_auto(client: httpx.AsyncClient, category: str = "all") -> list[str]:
    # Ambil daftar bulan dari dropdown
    first_html = await fetch_text(client, f"{LISTING}?category={category}&page=1")
    if not first_html:
        print("[HARVEST] gagal muat halaman awal /articles")
        return []
    months = extract_months_dropdown(first_html)
    if not months:
        print("[HARVEST] tidak menemukan daftar month di dropdown")
        return []

    # Urutkan dari paling lama -> terbaru agar cepat memenuhi kuota data besar
    months_ordered = list(reversed(months))

    all_urls, seen = [], set()
    for dr in months_ordered:
        urls = await harvest_month(client, dr, category=category)
        for u in urls:
            if u not in seen:
                seen.add(u); all_urls.append(u)
        print(f"[HARVEST] {dr}: akumulasi total {len(all_urls)}")
    return all_urls

# -------------------- scrape details ---------------------

async def scrape_details(client: httpx.AsyncClient, urls: list[str], concurrency: int = 6) -> list[dict]:
    sem = asyncio.Semaphore(max(1, concurrency))
    out: list[dict] = []

    async def worker(u: str):
        async with sem:
            html = await fetch_text(client, u)
        if not html: 
            return
        try:
            row = parse_article(html, u)
            if row:
                out.append(row)
        except Exception as e:
            print(f"[PARSE] gagal {u}: {e}", file=sys.stderr)

    tasks = [asyncio.create_task(worker(u)) for u in urls]
    done = 0
    for coro in asyncio.as_completed(tasks):
        await coro
        done += 1
        if done % 50 == 0 or done == len(tasks):
            print(f"[SCRAPE] {done}/{len(tasks)} artikel")
    return out

def write_csv(path: str, rows: list[dict]):
    if not rows:
        print("[WARN] tidak ada baris untuk disimpan"); return
    fields = [
        "url","slug","title","label","date","author",
        "topic","source","claim","explanation","conclusion","content"
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            safe = {k: (r.get(k) or "") for k in fields}
            w.writerow(safe)
    print(f"[DONE] CSV tersimpan: {path} (total {len(rows)} baris)")

# ------------------------- CLI --------------------------

async def main_async(args):
    async with httpx.AsyncClient(headers=HEADERS, http2=False) as client:
        if args.mode == "auto":
            urls = await harvest_all_months_auto(client, category=args.category)
        elif args.mode == "archive":
            if not args.start:
                print("[ERROR] --start YYYY-MM wajib pada mode archive"); return
            urls = await harvest_archive(client, args.start, args.months, category=args.category)
        else:
            print("[ERROR] mode tidak dikenal"); return

        # dedupe & limit
        seen, uniq = set(), []
        for u in urls:
            if u not in seen:
                seen.add(u); uniq.append(u)
        if args.max and args.max > 0:
            uniq = uniq[:args.max]
        print(f"[INFO] Total URL untuk di-scrape: {len(uniq)}")

        rows = await scrape_details(client, uniq, concurrency=args.concurrency)
        write_csv(args.out, rows)

def main():
    ap = argparse.ArgumentParser(description="Scraper TurnBackHoax (HTML listing + dropdown bulan)")
    ap.add_argument("--mode", choices=["auto", "archive"], required=True,
                    help="auto = scrape semua bulan dari dropdown; archive = dari --start mundur --months")
    ap.add_argument("--out", required=True, help="CSV output")
    ap.add_argument("--concurrency", type=int, default=6, help="Paralel fetch detail artikel")
    ap.add_argument("--max", type=int, default=0, help="Batasi jumlah artikel (0=tanpa batas)")
    ap.add_argument("--category", default="all", help="Kategori listing (default: all)")

    # archive only
    ap.add_argument("--start", help="(archive) YYYY-MM, misal 2022-12")
    ap.add_argument("--months", type=int, default=1, help="(archive) berapa bulan mundur")

    args = ap.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Dihentikan oleh pengguna.")

if __name__ == "__main__":
    main()
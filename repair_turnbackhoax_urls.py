# repair_turnbackhoax_urls_fixed.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Repair old TurnBackHoax URLs (WP era) -> new /articles/{id}--{slug}
Strategi:
1) Coba GET dengan follow_redirects (beberapa URL lama di-redirect otomatis).
2) Jika 404/tidak redirect:
   - Ekstrak YYYY/MM dari URL lama -> konversi ke "MonthName YYYY"
   - Muat listing bulan tersebut lalu cari link /articles/ yang slug-nya mengandung potongan slug lama.
3) Outputkan CSV dengan kolom: old_url, new_url

Persiapan:
  pip install httpx selectolax python-dateutil tqdm
"""
from __future__ import annotations

import argparse, csv, re, sys, asyncio
from urllib.parse import urljoin, urlparse, urldefrag, quote_plus

import httpx
from selectolax.parser import HTMLParser

BASE = "https://turnbackhoax.id"
LISTING = f"{BASE}/articles"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36 (+academic crawler)")
HEADERS = {"User-Agent": UA, "Accept-Language": "id,en;q=0.9"}

MONTHS = ["January","February","March","April","May","June",
          "July","August","September","October","November","December"]

ARTICLE_PAT = re.compile(r"^/articles/(\d+)(?:--[a-z0-9-]+)?/?$", re.I)

def month_label_from_path(path: str) -> str|None:
    # path: /2022/12/31/slug...
    m = re.search(r"/(20\d{2})/(\d{2})/", path)
    if not m:
        return None
    y = int(m.group(1)); mm = int(m.group(2))
    if mm < 1 or mm > 12:
        return None
    return f"{MONTHS[mm-1]} {y}"

def norm_url(u: str) -> str:
    u, _ = urldefrag(u)
    p = urlparse(u)
    return p._replace(scheme="https", netloc="turnbackhoax.id").geturl()

def extract_listing_links(html: str) -> list[str]:
    tree = HTMLParser(html)
    out = []
    for a in tree.css(".news-card-h-alt a[href^='/articles/']"):
        href = a.attributes.get("href")
        if not href: continue
        u = norm_url(urljoin(BASE, href))
        p = urlparse(u)
        if ARTICLE_PAT.match(p.path):
            out.append(u)
    # uniq
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u); uniq.append(u)
    return uniq

def get_total_pages(html: str) -> int:
    tree = HTMLParser(html)
    pages = []
    for btn in tree.css(".nav-pagination .nav-item"):
        dp = btn.attributes.get("data-page", "")
        if dp.isdigit():
            pages.append(int(dp))
    if pages: return max(pages)
    last_btn = tree.css_first(".nav-pagination .sprites-last")
    if last_btn:
        dp = last_btn.attributes.get("data-page")
        if dp and dp.isdigit():
            return int(dp)
    return 1

async def fetch_text(client: httpx.AsyncClient, url: str) -> str|None:
    try:
        r = await client.get(url, timeout=40.0, follow_redirects=True, headers=HEADERS)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text
    except Exception:
        return None

async def resolve_one(client: httpx.AsyncClient, old_url: str) -> str:
    # 1) direct follow redirects
    try:
        r = await client.get(old_url, headers=HEADERS, follow_redirects=True, timeout=40.0)
        if str(r.url).startswith(LISTING) or "/articles/" in str(r.url):
            return norm_url(str(r.url))
    except Exception:
        pass

    # 2) try from monthly listing
    p = urlparse(old_url)
    month_label = month_label_from_path(p.path)  # e.g., "December 2022"
    if not month_label:
        return ""

    # ambil komponen slug untuk pencocokan (bagian terakhir path tanpa angka tanggal)
    slug = p.path.rstrip("/").split("/")[-1]
    slug_core = re.sub(r"^\d{2}-\d{2}-\d{4}-", "", slug)  # hapus pattern tanggal di depan jika ada
    slug_core = slug_core.replace("-", " ").lower()

    # load listing pages dan cari yang mirip
    url0 = f"{LISTING}?category=all&page=1&dateRange={quote_plus(month_label)}"
    html = await fetch_text(client, url0)
    if not html:
        return ""

    total = get_total_pages(html)
    candidates = []
    def collect(h):
        for u in extract_listing_links(h):
            candidates.append(u)

    collect(html)
    for pno in range(2, total+1):
        u = f"{LISTING}?category=all&page={pno}&dateRange={quote_plus(month_label)}"
        h = await fetch_text(client, u)
        if h:
            collect(h)

    # cocokkan slug_core terhadap url kandidat
    best = ""
    slug_words = set([w for w in slug_core.split() if w])
    best_score = 0
    for u in candidates:
        parts = urlparse(u).path.split("--", 1)
        slug_new = parts[1] if len(parts) == 2 else ""
        words = set([w for w in slug_new.replace("-", " ").lower().split() if w])
        score = len(slug_words & words)
        if score > best_score:
            best_score = score; best = u
    return best

async def main_async(args):
    # baca input
    rows = []
    with open(args.input, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if "url" not in r.fieldnames:
            print("[ERROR] CSV harus memiliki kolom 'url' (old url).", file=sys.stderr)
            return
        for row in r:
            rows.append(row)

    async with httpx.AsyncClient() as client:
        out_rows = []
        for i, row in enumerate(rows, 1):
            old = row["url"]
            new = await resolve_one(client, old)
            out_rows.append({"old_url": old, "new_url": new})
            if i % 50 == 0 or i == len(rows):
                print(f"[REPAIR] {i}/{len(rows)} selesai")

    # tulis output
    with open(args.output, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["old_url","new_url"])
        w.writeheader()
        w.writerows(out_rows)
    print(f"[DONE] mapping tersimpan: {args.output}")

def main():
    ap = argparse.ArgumentParser(description="Perbaiki URL TurnBackHoax lama -> baru")
    ap.add_argument("--input", required=True, help="CSV lama (wajib ada kolom 'url')")
    ap.add_argument("--output", required=True, help="CSV mapping hasil perbaikan")
    args = ap.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Dihentikan oleh pengguna.")

if __name__ == "__main__":
    main()
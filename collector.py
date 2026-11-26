# -*- coding: utf-8 -*-
"""
이슈·밈 자동 수집기
- 국내 커뮤니티(더쿠 핫게, 디시 실베/미국주식갤 개념글, 에펨 포텐, 네이트판, 인스티즈 이슈, 루리웹 유머베스트)
- 트렌드(구글 트렌드 KR, 시그널 실시간 검색어 + 네이버 뉴스 랭킹, X 트렌드, 유튜브 인기)
- 해외 밈(Reddit, 9GAG)
- 인스타그램 참고 계정(공식 Graph API Business Discovery)
당일(한국시간) 올라온 글을 사진까지 저장하고, 텔레그램으로 알림을 보냅니다.

사용법:
  python collector.py                 # 한 번 수집 (예약 실행이 이걸 매시간 부름)
  python collector.py --get-chat-id   # 텔레그램 chat_id 찾기
  python collector.py --telegram-test # 텔레그램 테스트 메시지
  python collector.py --only theqoo,ruliweb   # 일부 소스만
  python collector.py --dry-run       # 저장/알림 없이 목록만 확인
"""
import argparse
import io
import datetime as dt
import html
import json
import logging
import os
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

try:
    import lxml  # noqa: F401
    PARSER = "lxml"
except ImportError:
    PARSER = "html.parser"

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
LOGS = os.path.join(BASE, "logs")
KST = dt.timezone(dt.timedelta(hours=9))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

if sys.stdout and hasattr(sys.stdout, "buffer"):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
os.makedirs(DATA, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(os.path.join(LOGS, "collector.log"), encoding="utf-8")]
    + ([logging.StreamHandler(sys.stdout)] if sys.stdout else []),
)
log = logging.getLogger("collector")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": UA,
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
})

CFG = {}
_last_hit = {}

SOURCE_NAMES = {
    "theqoo": "더쿠 핫게",
    "dc_best": "디시 실베",
    "dc_stockus": "디시 미국주식갤(개념글)",
    "fmkorea": "에펨 포텐",
    "natepann": "네이트판 톡커들의선택",
    "instiz": "인스티즈 이슈",
    "ruliweb": "루리웹 유머베스트",
    "reddit": "Reddit",
    "9gag": "9GAG",
    "instagram": "인스타 참고계정",
}


# ----------------------------------------------------------------- utils
def now():
    return dt.datetime.now(KST)


def today():
    return now().strftime("%Y-%m-%d")


def safe_name(s, n=40):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]', "", s or "").strip().rstrip(".")
    return (s[:n] or "untitled").strip()


def throttle(url):
    host = urlparse(url).netloc
    delay = CFG.get("request_delay_sec", 1.0)
    if "fmkorea" in host:
        delay = max(delay, CFG.get("fmkorea_delay_sec", 2.5))
    last = _last_hit.get(host, 0)
    wait = delay - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.time()


def get(url, referer=None, tries=3, **kw):
    headers = kw.pop("headers", {})
    if referer:
        headers["Referer"] = referer
    last_err = None
    for i in range(tries):
        throttle(url)
        try:
            r = SESSION.get(url, headers=headers, timeout=25, **kw)
            if r.status_code in (429, 430, 503):
                last_err = f"HTTP {r.status_code}"
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last_err = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"요청 실패 {url}: {last_err}")


def soup_of(url, referer=None):
    r = get(url, referer=referer)
    r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == "iso-8859-1" else r.encoding
    return BeautifulSoup(r.text, PARSER)


def is_hhmm(text):
    return bool(re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", (text or "").strip()))


def og_title(s):
    m = s.find("meta", property="og:title")
    return (m.get("content") or "").strip() if m else ""


IMG_ATTRS = ("data-original", "data-src", "data-lazy-src", "data-url", "src")


def extract_media(container, base_url):
    images, videos = [], []
    if container is None:
        return images, videos
    for img in container.find_all("img"):
        src = None
        for a in IMG_ATTRS:
            v = img.get(a)
            if v and not v.startswith("data:"):
                src = v
                break
        if not src:
            continue
        src = urljoin(base_url, src.strip())
        low = src.lower()
        if any(x in low for x in ("/emoticon", "/icon", "dccon", "/static/", "blank.gif", "spacer")):
            continue
        if src not in images:
            images.append(src)
    for v in container.find_all("video"):
        cands = [v.get("src"), v.get("data-src")] + [s.get("src") or s.get("data-src") for s in v.find_all("source")]
        for c in cands:
            if c:
                c = urljoin(base_url, c.strip())
                if c not in videos:
                    videos.append(c)
                break
    return images, videos


def text_of(container, limit=3000):
    if container is None:
        return ""
    t = container.get_text("\n", strip=True)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t[:limit]


# ------------------------------------------------------------- seen store
STATE = os.environ.get("COLLECTOR_STATE_DIR") or DATA
os.makedirs(STATE, exist_ok=True)
SEEN_PATH = os.path.join(STATE, "seen.json")


def load_seen():
    try:
        with open(SEEN_PATH, encoding="utf-8") as f:
            seen = json.load(f)
    except Exception:
        seen = {}
    cutoff = (now() - dt.timedelta(days=10)).strftime("%Y-%m-%d")
    return {src: {k: v for k, v in d.items() if v >= cutoff} for src, d in seen.items()}


def save_seen(seen):
    tmp = SEEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False)
    os.replace(tmp, SEEN_PATH)


# ------------------------------------------------------------ community
# 각 lister는 [{id, url, title, today(True/False/None), meta}] 를 페이지 단위로 yield
# today=None 이면 글 본문에서 날짜를 보고 판단

def list_theqoo(page):
    url = f"https://theqoo.net/hot?page={page}"
    s = soup_of(url)
    out = []
    for tr in s.select("table.theqoo_board_table tbody tr"):
        if "notice" in (tr.get("class") or []) or "notice_expand" in (tr.get("class") or []):
            continue
        a = tr.select_one("td.title a[href]")
        if not a:
            continue
        m = re.search(r"/hot/(\d+)", a["href"])
        if not m:
            continue
        cate = tr.select_one("td.cate")
        tm = tr.select_one("td.time")
        out.append({
            "id": m.group(1), "url": urljoin(url, f"/hot/{m.group(1)}"),
            "title": a.get_text(" ", strip=True),
            "today": is_hhmm(tm.get_text(strip=True) if tm else ""),
            "meta": {"category": cate.get_text(strip=True) if cate else ""},
        })
    return out


def parse_theqoo(item):
    s = soup_of(item["url"], referer="https://theqoo.net/hot")
    c = s.select_one(".rd_body")
    imgs, vids = extract_media(c, item["url"])
    return {"title": og_title(s) or item["title"], "text": text_of(c), "images": imgs, "videos": vids}


def _dc_lister(list_url_fmt):
    def lister(page):
        url = list_url_fmt.format(page=page)
        s = soup_of(url)
        out = []
        for tr in s.select("tr.ub-content.us-post"):
            num = tr.select_one("td.gall_num")
            a = tr.select_one("td.gall_tit a[href]")
            date = tr.select_one("td.gall_date")
            if not (num and a and date):
                continue
            pid = num.get_text(strip=True)
            if not pid.isdigit():
                continue
            full = date.get("title") or date.get_text(strip=True)
            href = urljoin(url, a["href"])
            q = parse_qs(urlparse(href).query)
            clean = f"https://gall.dcinside.com{urlparse(href).path}?id={q.get('id', [''])[0]}&no={q.get('no', [pid])[0]}"
            cnt = tr.select_one("td.gall_count")
            rec = tr.select_one("td.gall_recommend")
            out.append({
                "id": pid, "url": clean, "title": a.get_text(" ", strip=True),
                "today": full[:10] == today() if re.match(r"\d{4}-\d{2}-\d{2}", full) else is_hhmm(full),
                "meta": {"views": cnt.get_text(strip=True) if cnt else "",
                         "recommend": rec.get_text(strip=True) if rec else ""},
            })
        return out
    return lister


def parse_dc(item):
    s = soup_of(item["url"], referer="https://gall.dcinside.com/")
    c = s.select_one(".write_div")
    imgs, vids = extract_media(c, item["url"])
    t = s.select_one(".title_subject")
    return {"title": t.get_text(strip=True) if t else item["title"], "text": text_of(c),
            "images": imgs, "videos": vids}


def list_fmkorea(page):
    url = f"https://www.fmkorea.com/index.php?mid=best&page={page}"
    s = soup_of(url)
    out = []
    for li in s.select("li.li"):
        a = li.select_one("h3.title a[href]")
        if not a:
            continue
        href = urljoin(url, a["href"])
        q = parse_qs(urlparse(href).query)
        pid = (q.get("document_srl") or [None])[0]
        if not pid:
            m = re.search(r"/(\d{6,})", href)
            pid = m.group(1) if m else None
        if not pid:
            continue
        t = li.select_one(".ellipsis-target") or a
        cnt = li.select_one(".count")
        out.append({"id": pid, "url": f"https://www.fmkorea.com/{pid}", "title": t.get_text(" ", strip=True),
                    "today": None, "meta": {"recommend": cnt.get_text(strip=True) if cnt else ""}})
    return out


def parse_fmkorea(item):
    s = soup_of(item["url"], referer="https://www.fmkorea.com/best")
    c = s.select_one("article .xe_content") or s.select_one(".xe_content")
    imgs, vids = extract_media(c, item["url"])
    d = s.select_one(".top_area .date") or s.select_one("span.date")
    date = d.get_text(strip=True) if d else ""
    t = s.select_one("span.np_18px_span")
    return {"title": (t.get_text(strip=True) if t else "") or og_title(s) or item["title"],
            "text": text_of(c), "images": imgs, "videos": vids,
            "date": date.replace(".", "-")[:10] if date else ""}


def list_natepann(page):
    url = f"https://pann.nate.com/talk/ranking?rankingType=total&page={page}"
    s = soup_of(url)
    out, seen_ids = [], set()
    for a in s.select("a[href]"):
        m = re.fullmatch(r"/talk/(\d+)", urlparse(a["href"]).path)
        if not m or m.group(1) in seen_ids:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        seen_ids.add(m.group(1))
        out.append({"id": m.group(1), "url": f"https://pann.nate.com/talk/{m.group(1)}",
                    "title": title, "today": True, "meta": {}})  # 오늘의 랭킹이므로 전부 수집
    return out


def parse_natepann(item):
    s = soup_of(item["url"], referer="https://pann.nate.com/talk/ranking")
    c = s.select_one("#contentArea")
    imgs, vids = extract_media(c, item["url"])
    t = s.select_one(".post-tit-info h1")
    d = s.select_one(".info .date")
    return {"title": t.get_text(strip=True) if t else item["title"], "text": text_of(c),
            "images": imgs, "videos": vids, "posted": d.get_text(strip=True) if d else ""}


def list_instiz(page):
    url = f"https://www.instiz.net/pt?page={page}"
    s = soup_of(url)
    out = []
    for td in s.select("td.listsubject"):
        a = td.find("a", href=re.compile(r"/pt/\d+"))
        if not a:
            continue
        m = re.search(r"/pt/(\d+)", a["href"])
        full = td.get_text(" ", strip=True)
        tm = re.search(r"(\d{1,2}:\d{2})\s*l\s*조회", full)
        dm = re.search(r"(\d{2}\.\d{2})\s*l\s*조회", full)
        if not tm and not dm:
            continue  # 공지 등
        title = a.get_text(" ", strip=True)
        title = re.sub(r"\s*\d+$", "", title)  # 댓글수 꼬리 제거
        out.append({"id": m.group(1), "url": f"https://www.instiz.net/pt/{m.group(1)}",
                    "title": title, "today": bool(tm), "meta": {}})
    return out


def parse_instiz(item):
    s = soup_of(item["url"], referer="https://www.instiz.net/pt")
    c = s.select_one("#memo_content_1") or s.select_one(".memo_content")
    imgs, vids = extract_media(c, item["url"])
    return {"title": og_title(s) or item["title"], "text": text_of(c), "images": imgs, "videos": vids}


def list_ruliweb(page):
    url = f"https://bbs.ruliweb.com/best/humor?page={page}"
    s = soup_of(url)
    out = []
    for tr in s.select("tr.table_body"):
        if "notice" in (tr.get("class") or []):
            continue
        a = tr.select_one("a.subject_link[href]") or tr.select_one("td.subject a[href]")
        if not a:
            continue
        m = re.search(r"/read/(\d+)", a["href"])
        if not m:
            continue
        tm = tr.select_one("td.time")
        rec = tr.select_one("td.recomd")
        hit = tr.select_one("td.hit")
        title = a.get_text(" ", strip=True)
        out.append({"id": m.group(1), "url": urljoin(url, a["href"].split("?")[0]),
                    "title": title,
                    "today": is_hhmm(tm.get_text(strip=True) if tm else ""),
                    "meta": {"recommend": rec.get_text(strip=True) if rec else "",
                             "views": hit.get_text(strip=True) if hit else ""}})
    return out


def parse_ruliweb(item):
    s = soup_of(item["url"], referer="https://bbs.ruliweb.com/best/humor")
    c = s.select_one(".view_content")
    imgs, vids = extract_media(c, item["url"])
    return {"title": og_title(s) or item["title"], "text": text_of(c), "images": imgs, "videos": vids}


COMMUNITIES = {
    "theqoo": (list_theqoo, parse_theqoo),
    "dc_best": (_dc_lister("https://gall.dcinside.com/board/lists/?id=dcbest&page={page}"), parse_dc),
    "dc_stockus": (_dc_lister("https://gall.dcinside.com/mgallery/board/lists/?id=stockus&exception_mode=recommend&page={page}"), parse_dc),
    "fmkorea": (list_fmkorea, parse_fmkorea),
    "natepann": (list_natepann, parse_natepann),
    "instiz": (list_instiz, parse_instiz),
    "ruliweb": (list_ruliweb, parse_ruliweb),
}


# --------------------------------------------------------------- saving
def download(url, dest_noext, referer=None):
    r = get(url, referer=referer, stream=True, tries=2)
    ctype = (r.headers.get("Content-Type") or "").lower()
    ext = None
    for k, e in (("jpeg", ".jpg"), ("jpg", ".jpg"), ("png", ".png"), ("gif", ".gif"), ("webp", ".webp"),
                 ("mp4", ".mp4"), ("webm", ".webm"), ("quicktime", ".mov")):
        if k in ctype:
            ext = e
            break
    if not ext:
        m = re.search(r"\.(jpe?g|png|gif|webp|mp4|webm|mov)(\?|$)", url.lower())
        ext = "." + (m.group(1).replace("jpeg", "jpg") if m else "jpg")
    if "text/html" in ctype:
        raise RuntimeError("이미지 대신 HTML 응답")
    max_mb = CFG.get("max_file_mb", 80)
    path = dest_noext + ext
    size = 0
    with open(path, "wb") as f:
        for chunk in r.iter_content(65536):
            size += len(chunk)
            if size > max_mb * 1024 * 1024:
                break
            f.write(chunk)
    if size < CFG.get("min_image_bytes", 4000) and ext not in (".mp4", ".webm", ".mov"):
        os.remove(path)
        return None
    return path


def save_post(source, post):
    day_dir = os.path.join(DATA, today(), source)
    folder = os.path.join(day_dir, f"{now().strftime('%H%M')}_{post['id']}_{safe_name(post['title'])}")
    os.makedirs(folder, exist_ok=True)
    files = []
    media = [(u, "img") for u in post.get("images", [])]
    if CFG.get("download_videos", True):
        media += [(u, "vid") for u in post.get("videos", [])]
    for i, (u, kind) in enumerate(media[: CFG.get("max_media_per_post", 60)], 1):
        try:
            p = download(u, os.path.join(folder, f"{i:02d}"), referer=post.get("url"))
            if p:
                files.append(os.path.relpath(p, os.path.join(DATA, today())).replace("\\", "/"))
        except Exception as e:
            log.warning("  미디어 실패 %s: %s", u[:120], e)
    post["files"] = files
    post["folder"] = os.path.relpath(folder, os.path.join(DATA, today())).replace("\\", "/")
    post["collected_at"] = now().strftime("%Y-%m-%d %H:%M")
    post["source"] = source
    with open(os.path.join(folder, "post.json"), "w", encoding="utf-8") as f:
        json.dump(post, f, ensure_ascii=False, indent=1)
    return post


def collect_community(key, seen, dry=False):
    lister, parser = COMMUNITIES[key]
    s_seen = seen.setdefault(key, {})
    max_pages = CFG.get("max_pages", {}).get(key, CFG.get("default_max_pages", 8))
    new_posts = []
    for page in range(1, max_pages + 1):
        try:
            items = lister(page)
        except Exception as e:
            if page == 1:
                raise
            log.warning("%s %d페이지 실패: %s", key, page, e)
            break
        if not items:
            break
        any_today = False
        for it in items:
            if it["today"] is False:
                continue
            if it["id"] in s_seen:
                any_today = True
                continue
            if dry:
                any_today = True
                log.info("  [dry] %s %s", it["id"], it["title"][:60])
                continue
            try:
                detail = parser(it)
            except Exception as e:
                log.warning("  글 실패 %s: %s", it["url"], e)
                continue
            if it["today"] is None:  # 본문 날짜로 판단 (펨코)
                if detail.get("date") and detail["date"] != today():
                    s_seen[it["id"]] = today()
                    continue
            any_today = True
            post = {"id": it["id"], "url": it["url"], "title": detail.get("title") or it["title"],
                    "text": detail.get("text", ""), "images": detail.get("images", []),
                    "videos": detail.get("videos", []), "meta": it.get("meta", {})}
            save_post(key, post)
            s_seen[it["id"]] = today()
            new_posts.append(post)
            log.info("  + %s | 사진 %d | %s", key, len(post["files"]), post["title"][:50])
        if not any_today:
            break
    return new_posts


# --------------------------------------------------------------- foreign memes
def collect_reddit(seen, dry=False):
    s_seen = seen.setdefault("reddit", {})
    out = []
    cutoff = time.time() - 26 * 3600
    for sub in CFG.get("reddit_subs", []):
        data = None
        for host in ("https://www.reddit.com", "https://old.reddit.com"):
            try:
                r = get(f"{host}/r/{sub}/top.json?t=day&limit={CFG.get('reddit_limit', 25)}",
                        headers={"User-Agent": "meme-collector/1.0 (personal use)"}, tries=2)
                data = r.json()
                break
            except Exception as e:
                log.warning("reddit %s 실패(%s): %s", sub, host, e)
        if not data:
            continue
        for ch in data.get("data", {}).get("children", []):
            p = ch.get("data", {})
            pid = p.get("id")
            if not pid or pid in s_seen or p.get("over_18") or p.get("created_utc", 0) < cutoff:
                continue
            imgs, vids = [], []
            if p.get("is_gallery") and p.get("media_metadata"):
                order = [g["media_id"] for g in (p.get("gallery_data") or {}).get("items", [])]
                for mid in order or p["media_metadata"].keys():
                    mm = p["media_metadata"].get(mid, {})
                    u = (mm.get("s") or {}).get("u") or (mm.get("s") or {}).get("gif")
                    if u:
                        imgs.append(html.unescape(u))
            elif p.get("post_hint") == "image" or re.search(r"\.(jpe?g|png|gif|webp)$", p.get("url", "")):
                imgs.append(p.get("url"))
            if p.get("is_video") and (p.get("secure_media") or {}).get("reddit_video"):
                vids.append(p["secure_media"]["reddit_video"].get("fallback_url"))
            if not imgs and not vids:
                continue
            post = {"id": pid, "url": "https://www.reddit.com" + p.get("permalink", ""),
                    "title": f"[r/{sub}] {p.get('title', '')}", "text": p.get("selftext", "")[:1000],
                    "images": imgs, "videos": vids, "meta": {"upvotes": p.get("ups", 0)}}
            if dry:
                log.info("  [dry] reddit %s", post["title"][:60])
                continue
            save_post("reddit", post)
            s_seen[pid] = today()
            out.append(post)
    return out


def collect_9gag(seen, dry=False):
    s_seen = seen.setdefault("9gag", {})
    out = []
    r = get("https://9gag.com/v1/group-posts/group/default/type/hot",
            headers={"Accept": "application/json"}, tries=2)
    posts = r.json().get("data", {}).get("posts", [])
    cutoff = time.time() - 26 * 3600
    for p in posts[: CFG.get("ninegag_limit", 30)]:
        pid = p.get("id")
        if not pid or pid in s_seen or p.get("nsfw") or p.get("creationTs", time.time()) < cutoff:
            continue
        im = p.get("images", {})
        imgs, vids = [], []
        if p.get("type") == "Animated" and im.get("image460sv", {}).get("url"):
            vids.append(im["image460sv"]["url"])
        elif im.get("image700", {}).get("url"):
            imgs.append(im["image700"]["url"])
        if not imgs and not vids:
            continue
        post = {"id": pid, "url": p.get("url", ""), "title": f"[9GAG] {html.unescape(p.get('title', ''))}",
                "text": "", "images": imgs, "videos": vids, "meta": {"upvotes": p.get("upVoteCount", 0)}}
        if dry:
            log.info("  [dry] 9gag %s", post["title"][:60])
            continue
        save_post("9gag", post)
        s_seen[pid] = today()
        out.append(post)
    return out


# --------------------------------------------------------------- instagram (공식 API)
def collect_instagram(seen, dry=False):
    ig = CFG.get("instagram", {})
    token, my_id = ig.get("access_token"), ig.get("ig_user_id")
    if not token or not my_id:
        log.info("인스타: access_token/ig_user_id 미설정 → 건너뜀")
        return []
    s_seen = seen.setdefault("instagram", {})
    ver = ig.get("graph_version", "v21.0")
    out = []
    for username in ig.get("accounts", []):
        fields = ("business_discovery.username(%s){username,media.limit(25)"
                  "{id,caption,media_type,media_url,permalink,timestamp,"
                  "children{media_type,media_url}}}" % username)
        try:
            r = SESSION.get(f"https://graph.facebook.com/{ver}/{my_id}",
                            params={"fields": fields, "access_token": token}, timeout=30)
            j = r.json()
            if "error" in j:
                log.warning("인스타 %s: %s", username, j["error"].get("message"))
                continue
        except Exception as e:
            log.warning("인스타 %s 실패: %s", username, e)
            continue
        for m in j.get("business_discovery", {}).get("media", {}).get("data", []):
            ts = m.get("timestamp", "")
            try:
                t = dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").astimezone(KST)
            except ValueError:
                continue
            if t.strftime("%Y-%m-%d") != today() or m["id"] in s_seen:
                continue
            items = m.get("children", {}).get("data") or [m]
            imgs = [c["media_url"] for c in items if c.get("media_type") == "IMAGE" and c.get("media_url")]
            vids = [c["media_url"] for c in items if c.get("media_type") == "VIDEO" and c.get("media_url")]
            post = {"id": m["id"], "url": m.get("permalink", ""),
                    "title": f"[@{username}] " + (m.get("caption") or "").split("\n")[0][:60],
                    "text": m.get("caption") or "", "images": imgs, "videos": vids,
                    "meta": {"posted": t.strftime("%H:%M")}}
            if dry:
                log.info("  [dry] insta %s", post["title"][:60])
                continue
            save_post("instagram", post)
            s_seen[m["id"]] = today()
            out.append(post)
    return out


# --------------------------------------------------------------- trends
def trends_google():
    r = get("https://trends.google.com/trending/rss?geo=KR")
    root = ET.fromstring(r.content)
    ns = {"ht": "https://trends.google.com/trending/rss"}
    out = []
    for it in root.iter("item"):
        title = it.findtext("title")
        traffic = it.findtext("ht:approx_traffic", default="", namespaces=ns)
        news = [{"title": n.findtext("ht:news_item_title", default="", namespaces=ns),
                 "url": n.findtext("ht:news_item_url", default="", namespaces=ns)}
                for n in it.findall("ht:news_item", ns)][:2]
        out.append({"keyword": title, "traffic": traffic, "news": news})
    return out


def trends_signal():
    j = get("https://api.signal.bz/news/realtime", headers={"Accept": "application/json"}).json()
    kw = [{"rank": x.get("rank"), "keyword": x.get("keyword")} for x in j.get("top10", [])]
    naver = []
    seen_links = set()
    for x in j.get("naver", []):
        if x.get("link") in seen_links:
            continue
        seen_links.add(x.get("link"))
        naver.append({"title": x.get("title"), "url": x.get("link"),
                      "press": (x.get("press") or {}).get("name", "")})
    return kw, naver


def trends_x():
    s = soup_of("https://trends24.in/korea/")
    card = s.select_one(".trend-card") or s.select_one("ol.trend-card__list")
    if not card:
        return []
    return [a.get_text(strip=True) for a in card.select("li a")][:20]


def trends_youtube():
    key = CFG.get("youtube_api_key")
    if not key:
        return []
    j = SESSION.get("https://www.googleapis.com/youtube/v3/videos", params={
        "part": "snippet,statistics", "chart": "mostPopular", "regionCode": "KR",
        "maxResults": 30, "key": key}, timeout=25).json()
    return [{"title": v["snippet"]["title"], "channel": v["snippet"]["channelTitle"],
             "views": v.get("statistics", {}).get("viewCount"),
             "url": "https://youtu.be/" + v["id"]} for v in j.get("items", [])]


def collect_trends():
    t = {"time": now().strftime("%Y-%m-%d %H:%M")}
    for name, fn in (("google", trends_google), ("x", trends_x), ("youtube", trends_youtube)):
        if not CFG.get("trends", {}).get(name, True):
            continue
        try:
            t[name] = fn()
        except Exception as e:
            log.warning("트렌드 %s 실패: %s", name, e)
            t[name + "_error"] = str(e)[:200]
    if CFG.get("trends", {}).get("signal", True):
        try:
            t["signal"], t["naver_news"] = trends_signal()
        except Exception as e:
            log.warning("트렌드 signal 실패: %s", e)
            t["signal_error"] = str(e)[:200]
    d = os.path.join(DATA, today(), "_trends")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, now().strftime("%H%M") + ".json"), "w", encoding="utf-8") as f:
        json.dump(t, f, ensure_ascii=False, indent=1)
    return t


# --------------------------------------------------------------- index.html
def build_index(day=None):
    day = day or today()
    root = os.path.join(DATA, day)
    if not os.path.isdir(root):
        return None
    posts = []
    for dirpath, _, files in os.walk(root):
        if "post.json" in files:
            try:
                with open(os.path.join(dirpath, "post.json"), encoding="utf-8") as f:
                    posts.append(json.load(f))
            except Exception:
                pass
    posts.sort(key=lambda p: p.get("collected_at", ""), reverse=True)
    trend_html = ""
    tdir = os.path.join(root, "_trends")
    if os.path.isdir(tdir) and os.listdir(tdir):
        latest = sorted(os.listdir(tdir))[-1]
        with open(os.path.join(tdir, latest), encoding="utf-8") as f:
            t = json.load(f)
        parts = []
        if t.get("signal"):
            parts.append("<h3>실시간 검색어 (시그널)</h3><ol>" + "".join(
                f"<li>{html.escape(x['keyword'])}</li>" for x in t["signal"]) + "</ol>")
        if t.get("google"):
            parts.append("<h3>구글 트렌드 KR</h3><ol>" + "".join(
                f"<li>{html.escape(x['keyword'] or '')} <small>{html.escape(x.get('traffic') or '')}</small></li>"
                for x in t["google"][:20]) + "</ol>")
        if t.get("x"):
            parts.append("<h3>X 트렌드 (한국)</h3><ol>" + "".join(
                f"<li>{html.escape(x)}</li>" for x in t["x"]) + "</ol>")
        if t.get("naver_news"):
            parts.append("<h3>네이버 뉴스 랭킹</h3><ol>" + "".join(
                f"<li><a href='{html.escape(x['url'])}' target='_blank'>{html.escape(x['title'])}</a></li>"
                for x in t["naver_news"][:30]) + "</ol>")
        if t.get("youtube"):
            parts.append("<h3>유튜브 인기</h3><ol>" + "".join(
                f"<li><a href='{html.escape(x['url'])}' target='_blank'>{html.escape(x['title'])}</a> "
                f"<small>{html.escape(x['channel'])}</small></li>" for x in t["youtube"]) + "</ol>")
        trend_html = f"<section class='trends'><h2>트렌드 <small>{html.escape(t.get('time', ''))}</small></h2>" \
                     f"<div class='tg'>{''.join('<div>' + p + '</div>' for p in parts)}</div></section>"
    by_src = {}
    for p in posts:
        by_src.setdefault(p.get("source", "?"), []).append(p)
    nav = " · ".join(f"<a href='#{k}'>{html.escape(SOURCE_NAMES.get(k, k))} {len(v)}</a>" for k, v in by_src.items())
    body = []
    for k, ps in by_src.items():
        cards = []
        for p in ps:
            thumbs = ""
            for fpath in p.get("files", [])[:8]:
                if fpath.lower().endswith((".mp4", ".webm", ".mov")):
                    thumbs += f"<video src='{html.escape(fpath)}' controls preload='none'></video>"
                else:
                    thumbs += f"<a href='{html.escape(fpath)}' target='_blank'><img loading='lazy' src='{html.escape(fpath)}'></a>"
            more = len(p.get("files", [])) - 8
            cards.append(
                f"<div class='card'><div class='t'><a href='{html.escape(p.get('url', ''))}' target='_blank'>"
                f"{html.escape(p.get('title', ''))}</a></div>"
                f"<div class='m'>{html.escape(p.get('collected_at', ''))} · 파일 {len(p.get('files', []))}개"
                f" · <a href='{html.escape(p.get('folder', ''))}'>폴더</a></div>"
                f"<div class='th'>{thumbs}{f'<span>+{more}</span>' if more > 0 else ''}</div>"
                f"<details><summary>본문</summary><pre>{html.escape(p.get('text', '')[:1500])}</pre></details></div>")
        body.append(f"<h2 id='{k}'>{html.escape(SOURCE_NAMES.get(k, k))} ({len(ps)})</h2>" + "".join(cards))
    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>수집 {day}</title>
<style>body{{font-family:'Malgun Gothic',sans-serif;margin:16px;background:#f6f6f6;color:#222}}
h2{{border-bottom:2px solid #333;padding-bottom:4px;margin-top:32px}} nav{{position:sticky;top:0;background:#fff;padding:8px;border-radius:8px}}
.card{{background:#fff;border-radius:8px;padding:10px;margin:8px 0}} .t{{font-weight:bold}} .m{{color:#777;font-size:12px}}
.th img,.th video{{height:140px;margin:4px 4px 0 0;border-radius:4px;object-fit:cover;max-width:240px}}
pre{{white-space:pre-wrap;font-size:13px}} .tg{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}}
.tg>div{{background:#fff;border-radius:8px;padding:8px}} a{{color:#1a56db}}</style></head><body>
<h1>{day} 이슈·밈 수집 ({len(posts)}개)</h1><nav>{nav}</nav>{trend_html}{''.join(body)}</body></html>"""
    path = os.path.join(root, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path


# --------------------------------------------------------------- telegram
def tg(method, **data):
    tcfg = CFG.get("telegram", {})
    token = tcfg.get("bot_token")
    if not token:
        return None
    files = data.pop("_files", None)
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=data, files=files, timeout=60)
    j = r.json()
    if not j.get("ok"):
        log.warning("텔레그램 %s 실패: %s", method, j.get("description"))
    if j.get("error_code") == 429:
        time.sleep(j.get("parameters", {}).get("retry_after", 5) + 1)
    return j


def tg_text(text):
    chat = CFG.get("telegram", {}).get("chat_id")
    if not chat:
        return
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur)
    for c in chunks:
        tg("sendMessage", chat_id=chat, text=c, parse_mode="HTML", disable_web_page_preview="true")
        time.sleep(1.1)


def notify(new_by_src, trends, errors):
    tcfg = CFG.get("telegram", {})
    if not tcfg.get("bot_token") or not tcfg.get("chat_id"):
        log.info("텔레그램 미설정 → 알림 생략")
        return
    total = sum(len(v) for v in new_by_src.values())
    lines = []
    if total or tcfg.get("notify_when_empty", False):
        lines.append(f"📥 <b>{now().strftime('%m/%d %H:%M')} 새 글 {total}개</b>")
        lines.append(" · ".join(f"{SOURCE_NAMES.get(k, k)} {len(v)}" for k, v in new_by_src.items() if v))
        per = tcfg.get("titles_per_source", 15)
        for k, ps in new_by_src.items():
            if not ps:
                continue
            lines.append(f"\n<b>■ {html.escape(SOURCE_NAMES.get(k, k))}</b>")
            for p in ps[:per]:
                n = len(p.get("files", []))
                lines.append(f"• <a href=\"{html.escape(p['url'])}\">{html.escape(p['title'][:70])}</a>"
                             + (f" 🖼{n}" if n else ""))
            if len(ps) > per:
                lines.append(f"  …외 {len(ps) - per}개")
    hour = now().hour
    if trends and hour in tcfg.get("trend_hours", [9, 13, 18, 22]) and not _trend_sent_this_hour():
        lines.append("\n📈 <b>지금 트렌드</b>")
        if trends.get("signal"):
            lines.append("실검: " + ", ".join(html.escape(x["keyword"]) for x in trends["signal"][:10]))
        if trends.get("google"):
            lines.append("구글: " + ", ".join(html.escape(x["keyword"] or "") for x in trends["google"][:10]))
        if trends.get("x"):
            lines.append("X: " + ", ".join(html.escape(x) for x in trends["x"][:10]))
        if trends.get("naver_news"):
            lines.append("네이버 랭킹: " + " / ".join(html.escape(x["title"][:30]) for x in trends["naver_news"][:5]))
    if errors and tcfg.get("report_errors", True):
        lines.append("\n⚠️ 실패: " + ", ".join(html.escape(f"{SOURCE_NAMES.get(k, k)}") for k in errors))
    if lines:
        if not tcfg.get("send_all_photos"):
            lines.append(f"\n📂 data\\{today()}\\index.html 에서 전체 보기")
        tg_text("\n".join(lines))
    if tcfg.get("send_all_photos"):
        send_all_media(new_by_src)
        return
    # 사진 미리보기
    limit = tcfg.get("photo_previews_per_run", 10)
    sent = 0
    chat = tcfg.get("chat_id")
    for k, ps in new_by_src.items():
        for p in ps:
            if sent >= limit:
                return
            img = next((f for f in p.get("files", []) if f.lower().endswith((".jpg", ".png", ".webp"))), None)
            if not img:
                continue
            fp = os.path.join(DATA, today(), img)
            if os.path.getsize(fp) > 9.5 * 1024 * 1024:
                continue
            cap = f"[{SOURCE_NAMES.get(k, k)}] {p['title'][:150]}\n{p['url']}"
            with open(fp, "rb") as fh:
                tg("sendPhoto", chat_id=chat, caption=cap, _files={"photo": fh})
            sent += 1
            time.sleep(1.2)


def send_all_media(new_by_src):
    """글마다 사진/영상을 전부 앨범(최대 10장씩)으로 보냄 — 서버(GitHub) 실행 시 텔레그램이 보관함 역할"""
    chat = CFG["telegram"]["chat_id"]
    root = os.path.join(DATA, today())
    for k, ps in new_by_src.items():
        for p in ps:
            media = []
            for f in p.get("files", []):
                fp = os.path.join(root, f)
                size = os.path.getsize(fp)
                is_vid = f.lower().endswith((".mp4", ".webm", ".mov"))
                if is_vid and size <= 49 * 1024 * 1024 and f.lower().endswith(".mp4"):
                    media.append(("video", fp))
                elif not is_vid and size <= 9.5 * 1024 * 1024 and not f.lower().endswith(".gif"):
                    media.append(("photo", fp))
                elif f.lower().endswith(".gif") and size <= 49 * 1024 * 1024:
                    media.append(("animation", fp))
            if not media:
                continue
            cap = f"[{SOURCE_NAMES.get(k, k)}] {p['title'][:200]}\n{p['url']}"
            groupable = [m for m in media if m[0] != "animation"]
            for i in range(0, len(groupable), 10):
                chunk = groupable[i:i + 10]
                files, arr = {}, []
                for j, (typ, fp) in enumerate(chunk):
                    name = f"f{j}"
                    files[name] = open(fp, "rb")
                    item = {"type": typ, "media": f"attach://{name}"}
                    if i == 0 and j == 0:
                        item["caption"] = cap
                    arr.append(item)
                try:
                    if len(arr) == 1:
                        typ = chunk[0][0]
                        tg("send" + typ.capitalize(), chat_id=chat, caption=cap if i == 0 else "",
                           _files={typ: files["f0"]})
                    else:
                        tg("sendMediaGroup", chat_id=chat, media=json.dumps(arr), _files=files)
                finally:
                    for fh in files.values():
                        fh.close()
                time.sleep(3)
            for typ, fp in media:
                if typ == "animation":
                    with open(fp, "rb") as fh:
                        tg("sendAnimation", chat_id=chat, caption=cap[:200], _files={"animation": fh})
                    time.sleep(2)


_TREND_FLAG = os.path.join(STATE, "trend_sent.txt")


def _trend_sent_this_hour():
    key = now().strftime("%Y-%m-%d %H")
    try:
        with open(_TREND_FLAG, encoding="utf-8") as f:
            if f.read().strip() == key:
                return True
    except FileNotFoundError:
        pass
    with open(_TREND_FLAG, "w", encoding="utf-8") as f:
        f.write(key)
    return False


# --------------------------------------------------------------- main
def load_config():
    path = os.path.join(BASE, "config.json")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    # GitHub Actions 등에서는 비밀값을 환경변수로 받음
    env = os.environ.get
    t = cfg.setdefault("telegram", {})
    t["bot_token"] = env("TELEGRAM_BOT_TOKEN") or t.get("bot_token", "")
    t["chat_id"] = env("TELEGRAM_CHAT_ID") or t.get("chat_id", "")
    ig = cfg.setdefault("instagram", {})
    ig["ig_user_id"] = env("IG_USER_ID") or ig.get("ig_user_id", "")
    ig["access_token"] = env("IG_ACCESS_TOKEN") or ig.get("access_token", "")
    cfg["youtube_api_key"] = env("YOUTUBE_API_KEY") or cfg.get("youtube_api_key", "")
    if env("SEND_ALL_PHOTOS"):
        t["send_all_photos"] = env("SEND_ALL_PHOTOS").lower() in ("1", "true", "yes")
    return cfg


def run(only=None, dry=False):
    lock = os.path.join(DATA, "run.lock")
    if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 3 * 3600:
        log.info("이전 실행이 아직 진행 중 → 종료")
        return
    open(lock, "w").close()
    try:
        _run(only, dry)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def _run(only, dry):
    log.info("===== 수집 시작 %s =====", now().strftime("%Y-%m-%d %H:%M"))
    seen = load_seen()
    enabled = CFG.get("sources", {})
    new_by_src, errors = {}, []

    def want(k):
        return (only is None or k in only) and enabled.get(k, True)

    for key in COMMUNITIES:
        if not want(key):
            continue
        log.info("[%s]", key)
        try:
            new_by_src[key] = collect_community(key, seen, dry)
        except Exception as e:
            log.error("%s 실패: %s", key, e)
            log.debug(traceback.format_exc())
            errors.append(key)
        if not dry:
            save_seen(seen)
    for key, fn in (("reddit", collect_reddit), ("9gag", collect_9gag), ("instagram", collect_instagram)):
        if not want(key):
            continue
        log.info("[%s]", key)
        try:
            new_by_src[key] = fn(seen, dry)
        except Exception as e:
            log.error("%s 실패: %s", key, e)
            errors.append(key)
        if not dry:
            save_seen(seen)
    trends = None
    if want("trends") and not dry:
        trends = collect_trends()
    if dry:
        return
    idx = build_index()
    log.info("정리 페이지: %s", idx)
    notify(new_by_src, trends, errors)
    log.info("===== 끝: 새 글 %d개, 실패 %s =====", sum(len(v) for v in new_by_src.values()), errors or "없음")


def main():
    global CFG
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="쉼표로 구분한 소스 키 (예: theqoo,ruliweb,trends)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--get-chat-id", action="store_true")
    ap.add_argument("--telegram-test", action="store_true")
    ap.add_argument("--rebuild-index", action="store_true")
    ap.add_argument("--max-pages", type=int, help="테스트용: 모든 소스 페이지 수 제한")
    a = ap.parse_args()
    CFG = load_config()
    if a.max_pages:
        CFG["max_pages"] = {k: a.max_pages for k in COMMUNITIES}
    if a.get_chat_id:
        j = tg("getUpdates")
        if not j:
            print("config.json 의 telegram.bot_token 을 먼저 넣어주세요.")
            return
        ids = {}
        for u in j.get("result", []):
            m = u.get("message") or u.get("channel_post") or {}
            c = m.get("chat", {})
            if c:
                ids[c["id"]] = c.get("title") or c.get("username") or c.get("first_name")
        if not ids:
            print("봇에게 아무 메시지나 먼저 보낸 뒤 다시 실행하세요.")
        for cid, name in ids.items():
            print(f"chat_id = {cid}   ({name})")
        return
    if a.telegram_test:
        tg_text("✅ 이슈·밈 수집기 텔레그램 연결 완료!")
        print("보냈습니다. 텔레그램을 확인하세요.")
        return
    if a.rebuild_index:
        print(build_index())
        return
    only = set(a.only.split(",")) if a.only else None
    run(only, a.dry_run)


if __name__ == "__main__":
    main()

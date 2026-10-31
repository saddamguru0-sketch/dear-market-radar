"""Dear Market Viral Radar v2 - whole-niche YouTube outlier finder.

Runs daily on GitHub Actions with the free YouTube Data API v3 (env YOUTUBE_API_KEY).
Standard library only.

How it works:
  1. Runs ~50 niche searches (Hindi + English finance / economy / business / trading),
     videos from the last N days, sorted by views.
  2. Keeps long videos (no Shorts, no live streams, no tips/prediction junk).
  3. Outlier score = video views / that channel's average views per video.
     A small channel with a huge video = strong topic signal.
  4. Also pulls YouTube India's trending list (News + Education) filtered for finance words.

Outputs: data/latest.json, data/history/DATE.json, reports/latest.md
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://www.googleapis.com/youtube/v3/"
KEY = os.environ.get("YOUTUBE_API_KEY", "")
ROOT = os.path.dirname(os.path.abspath(__file__))
UNITS = 0
FIN_WORDS = ["market", "share", "stock", "economy", "economic", "rupee", "dollar", "gold", "bank",
             "tax", "budget", "gdp", "inflation", "recession", "invest", "trading", "crash",
             "billion", "crore", "company", "business", "startup", "ipo", "fii", "rbi", "sebi",
             "tariff", "bazaar", "paisa", "paise", "loan", "emi", "salary", "price", "scam",
             "शेयर", "बाजार", "अर्थव्यवस्था", "पैसा", "निवेश",
             "money", "wealth", "rich", "profit", "earning", "kamai", "kamata", "kamate", "economics",
             "करोड़", "कमाई", "कमाता", "कमाते", "सोना", "बिजनेस", "व्यापार", "बैंक", "मंदी", "महंगाई", "टैक्स"]


def get(endpoint, cost=1, **params):
    global UNITS
    params["key"] = KEY
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        UNITS += cost
        return json.loads(r.read().decode("utf-8"))


def iso_seconds(d):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    days, h, mi, s = (int(x or 0) for x in m.groups())
    return days * 86400 + h * 3600 + mi * 60 + s


def chunks(xs, n=50):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def fetch_videos(ids):
    out = []
    for c in chunks(list(ids)):
        out += get("videos", part="snippet,statistics,contentDetails,liveStreamingDetails",
                   id=",".join(c)).get("items", [])
    return out


def fetch_channels(ids):
    out = {}
    for c in chunks(list(ids)):
        for ch in get("channels", part="snippet,statistics", id=",".join(c)).get("items", []):
            st = ch.get("statistics", {})
            vc = int(st.get("videoCount", 0) or 0)
            tv = int(st.get("viewCount", 0) or 0)
            out[ch["id"]] = {
                "name": ch["snippet"]["title"],
                "subs": int(st.get("subscriberCount", 0) or 0),
                "avgViews": tv / vc if vc else 0,
                "videoCount": vc,
            }
    return out


def shape(v, now):
    sn, st = v["snippet"], v.get("statistics", {})
    pub = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
    age = max((now - pub).total_seconds() / 86400, 0.5)
    views = int(st.get("viewCount", 0) or 0)
    return {
        "id": v["id"],
        "url": "https://www.youtube.com/watch?v=" + v["id"],
        "title": sn.get("title", ""),
        "channel": sn.get("channelTitle", ""),
        "channelId": sn.get("channelId", ""),
        "lang": (sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "")[:2],
        "published": sn["publishedAt"][:10],
        "ageDays": round(age, 1),
        "durationMin": round(iso_seconds(v["contentDetails"].get("duration")) / 60, 1),
        "views": views,
        "viewsPerDay": int(views / age),
        "likes": int(st.get("likeCount", 0) or 0),
        "comments": int(st.get("commentCount", 0) or 0),
        "isLive": bool(v.get("liveStreamingDetails")) or sn.get("liveBroadcastContent") != "none",
        "thumbnail": sn.get("thumbnails", {}).get("high", {}).get("url", ""),
    }


def bad_title(t, cfg):
    t = t.lower()
    return any(w in t for w in cfg["excludeTitleWords"])


def niche_search(cfg, now, errors):
    after = (now - timedelta(days=cfg["searchDays"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = {}  # videoId -> list of queries
    jobs = [(q, "hi") for q in cfg["queries_hi"]] + [(q, "en") for q in cfg["queries_en"]]
    for q, lang in jobs:
        params = dict(part="snippet", q=q, type="video", order="viewCount", publishedAfter=after,
                      maxResults=cfg["resultsPerQuery"], relevanceLanguage=lang)
        if lang == "hi":
            params["regionCode"] = "IN"
        try:
            res = get("search", cost=100, **params)
        except urllib.error.HTTPError as e:
            errors.append(f"search '{q}': HTTP {e.code}")
            if e.code == 403:  # quota finished: stop searching, keep what we have
                break
            continue
        for it in res.get("items", []):
            vid = it["id"].get("videoId")
            if vid:
                found.setdefault(vid, []).append(q)
    return found


def trending_india(now, errors):
    ids = []
    for cat in ("25",):  # News & Politics (Education chart is not available for IN)
        try:
            res = get("videos", part="id", chart="mostPopular", regionCode="IN",
                      videoCategoryId=cat, maxResults=50)
            ids += [v["id"] for v in res.get("items", [])]
        except urllib.error.HTTPError as e:
            errors.append(f"trending cat {cat}: HTTP {e.code}")
    return ids


def fmt(n):
    if n >= 10_000_000:
        return f"{n / 10_000_000:.1f} Cr"
    if n >= 100_000:
        return f"{n / 100_000:.1f} L"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(int(n))


def own_channel(handle, now, errors):
    try:
        res = get("channels", part="snippet,statistics,contentDetails", forHandle=handle.lstrip("@"))
        ch = res["items"][0]
        up = ch["contentDetails"]["relatedPlaylists"]["uploads"]
        pl = get("playlistItems", part="contentDetails", playlistId=up, maxResults=10)
        vids = [shape(v, now) for v in fetch_videos([i["contentDetails"]["videoId"] for i in pl.get("items", [])])]
        return {"name": ch["snippet"]["title"], "subs": int(ch["statistics"].get("subscriberCount", 0)),
                "videos": sorted(vids, key=lambda v: v["published"], reverse=True)}
    except Exception as e:
        errors.append(f"own channel: {e}")
        return None


def report(d, cfg):
    L = [f"# Dear Market Viral Radar: {d['date']}", "",
         f"Scanned {d['scanned']} videos from {d['queriesRun']} niche searches (last {cfg['searchDays']} days). "
         f"Quota used: {d['quotaUnits']} units.", "",
         "Outlier = video views ÷ channel's average views per video. 10x = video ne channel ke normal se 10 guna kiya.", ""]

    def table(rows):
        out = ["| # | Outlier | Views | Views/day | Age | Lang | Channel (subs) | Title | Found via |",
               "|---|---|---|---|---|---|---|---|---|"]
        for i, v in enumerate(rows, 1):
            out.append(f"| {i} | {v['outlier']}x | {fmt(v['views'])} | {fmt(v['viewsPerDay'])} | {int(v['ageDays'])}d | "
                       f"{v['lang'] or '-'} | {v['channel']} ({fmt(v['channelSubs'])}) | [{v['title']}]({v['url']}) | "
                       f"{', '.join(v['queries'][:2])} |")
        return out

    hi = [v for v in d["outliers"] if v["bucket"] == "hi"][: cfg["topN"]]
    en = [v for v in d["outliers"] if v["bucket"] == "en"][: cfg["topN"] // 2]
    L += ["## Hindi / India niche outliers", ""] + (table(hi) if hi else ["Kuch nahi mila."]) + [""]
    L += ["## Global English niche outliers (format + topic ideas)", ""] + (table(en) if en else ["Kuch nahi mila."]) + [""]

    if d["trending"]:
        L += ["## YouTube India trending (finance-related)", ""]
        for v in d["trending"]:
            L.append(f"- {fmt(v['views'])} views, {v['channel']}: [{v['title']}]({v['url']})")
        L.append("")

    own = d.get("own")
    if own:
        L += ["## Dear Market", "", f"Subscribers: {fmt(own['subs'])}. Latest uploads:"]
        for v in own["videos"][:3]:
            L.append(f"- {v['published']}: {v['title']} ({fmt(v['views'])} views)")
        L.append("")
    if d["errors"]:
        L += ["## Errors", ""] + [f"- {e}" for e in d["errors"]]
    return "\n".join(L) + "\n"


def main():
    if not KEY:
        sys.exit("YOUTUBE_API_KEY missing. GitHub repo Settings > Secrets mein add karein.")
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    now = datetime.now(timezone.utc)
    errors = []

    found = niche_search(cfg, now, errors)
    hi_queries = set(cfg["queries_hi"])
    vids = [shape(v, now) for v in fetch_videos(found.keys())]
    keep = [v for v in vids
            if not v["isLive"] and v["durationMin"] >= cfg["minDurationMin"]
            and v["views"] >= cfg["minViews"] and not bad_title(v["title"], cfg)
            and any(w in v["title"].lower() for w in FIN_WORDS)]
    chans = fetch_channels({v["channelId"] for v in keep})
    for v in keep:
        c = chans.get(v["channelId"], {})
        v["channelSubs"] = c.get("subs", 0)
        v["channelAvgViews"] = int(c.get("avgViews", 0))
        v["outlier"] = round(v["views"] / c["avgViews"], 1) if c.get("avgViews") else 0
        v["queries"] = found[v["id"]]
        v["bucket"] = "hi" if any(q in hi_queries for q in v["queries"]) or v["lang"] == "hi" else "en"
    keep.sort(key=lambda v: v["outlier"], reverse=True)

    trend = []
    tids = [i for i in trending_india(now, errors) if i not in found]
    for v in (shape(x, now) for x in fetch_videos(tids)):
        t = v["title"].lower()
        if any(w in t for w in FIN_WORDS) and not bad_title(v["title"], cfg) and not v["isLive"]:
            trend.append(v)

    d = {"date": datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d"),
         "generatedAt": now.isoformat(), "queriesRun": len(cfg["queries_hi"]) + len(cfg["queries_en"]),
         "scanned": len(vids), "outliers": keep, "trending": trend[:10],
         "own": own_channel(cfg["ownChannel"], now, errors), "errors": errors}
    d["quotaUnits"] = UNITS

    os.makedirs(os.path.join(ROOT, "data", "history"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    blob = json.dumps(d, ensure_ascii=False, indent=1)
    for p in ("data/latest.json", f"data/history/{d['date']}.json"):
        open(os.path.join(ROOT, p), "w", encoding="utf-8").write(blob)
    open(os.path.join(ROOT, "reports", "latest.md"), "w", encoding="utf-8").write(report(d, cfg))
    print(f"Done. Scanned {len(vids)}, kept {len(keep)}. Quota: {UNITS}. Errors: {len(errors)}")


if __name__ == "__main__":
    main()

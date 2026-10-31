"""Dear Market Viral Radar - runs daily on GitHub Actions.

Uses the free YouTube Data API v3 (key in env YOUTUBE_API_KEY).
Only Python standard library, no installs needed.

Outputs:
  data/latest.json            full data for Claude to read
  data/history/YYYY-MM-DD.json
  reports/latest.md           human-readable summary
"""
import json
import os
import re
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://www.googleapis.com/youtube/v3/"
KEY = os.environ.get("YOUTUBE_API_KEY", "")
ROOT = os.path.dirname(os.path.abspath(__file__))
UNITS = 0  # quota counter


def get(endpoint, cost=1, **params):
    global UNITS
    params["key"] = KEY
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        UNITS += cost
        return json.loads(r.read().decode("utf-8"))


def iso_seconds(d):
    """PT1H2M3S -> seconds"""
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    days, h, mi, s = (int(x or 0) for x in m.groups())
    return days * 86400 + h * 3600 + mi * 60 + s


def video_details(ids):
    out = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        if not chunk:
            continue
        res = get("videos", part="snippet,statistics,contentDetails", id=",".join(chunk))
        out.extend(res.get("items", []))
    return out


def shape(v, now):
    sn, st = v["snippet"], v.get("statistics", {})
    pub = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
    secs = iso_seconds(v["contentDetails"].get("duration"))
    return {
        "id": v["id"],
        "url": "https://www.youtube.com/watch?v=" + v["id"],
        "title": sn.get("title", ""),
        "channel": sn.get("channelTitle", ""),
        "channelId": sn.get("channelId", ""),
        "published": sn["publishedAt"][:10],
        "ageDays": max((now - pub).days, 0),
        "durationMin": round(secs / 60, 1),
        "isShort": secs <= 180,
        "views": int(st.get("viewCount", 0)),
        "likes": int(st.get("likeCount", 0)),
        "comments": int(st.get("commentCount", 0)),
        "thumbnail": sn.get("thumbnails", {}).get("high", {}).get("url", ""),
    }


def scan_channel(handle, now, cfg):
    res = get("channels", part="snippet,statistics,contentDetails", forHandle=handle.lstrip("@"))
    if not res.get("items"):
        return {"handle": handle, "error": "channel not found"}
    ch = res["items"][0]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = get("playlistItems", part="contentDetails", playlistId=uploads, maxResults=50)
    ids = [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
    vids = [shape(v, now) for v in video_details(ids)]

    info = {
        "handle": handle,
        "channelId": ch["id"],
        "name": ch["snippet"]["title"],
        "subscribers": int(ch["statistics"].get("subscriberCount", 0)),
        "videosScanned": len(vids),
    }
    for kind, flag in (("long", False), ("shorts", True)):
        group = [v for v in vids if v["isShort"] == flag]
        # baseline: median views of videos older than 7 days (fresh ones are still growing)
        settled = [v["views"] for v in group if v["ageDays"] >= 7] or [v["views"] for v in group]
        base = statistics.median(settled) if settled else 0
        info["baseline_" + kind] = int(base)
        for v in group:
            v["outlierScore"] = round(v["views"] / base, 2) if base else 0
    info["videos"] = vids
    return info


def keyword_search(q, now, cfg):
    after = (now - timedelta(days=cfg["searchDays"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = get("search", cost=100, part="snippet", q=q, type="video", order="viewCount",
              publishedAfter=after, regionCode="IN", relevanceLanguage="hi",
              maxResults=cfg["searchResults"])
    ids = [i["id"]["videoId"] for i in res.get("items", []) if i["id"].get("videoId")]
    vids = [shape(v, now) for v in video_details(ids)]
    ch_ids = sorted({v["channelId"] for v in vids})
    subs = {}
    for i in range(0, len(ch_ids), 50):
        r = get("channels", part="statistics", id=",".join(ch_ids[i:i + 50]))
        for c in r.get("items", []):
            subs[c["id"]] = int(c["statistics"].get("subscriberCount", 0) or 0)
    for v in vids:
        s = subs.get(v["channelId"], 0)
        v["channelSubs"] = s
        # views vs subscribers: >1 means it reached far beyond the channel's audience
        v["viewsPerSub"] = round(v["views"] / s, 2) if s else None
    return {"query": q, "videos": vids}


def fmt(n):
    if n >= 10_000_000:
        return f"{n / 10_000_000:.1f} Cr"
    if n >= 100_000:
        return f"{n / 100_000:.1f} L"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


def report(data, cfg):
    L = [f"# Dear Market Viral Radar: {data['date']}", "",
         f"Quota used: {data['quotaUnits']} / 10,000 units", ""]
    recent = []
    for ch in data["channels"]:
        for v in ch.get("videos", []):
            if v["ageDays"] <= cfg["recentDays"] and v.get("outlierScore", 0) >= cfg["minOutlier"]:
                recent.append(v)
    recent.sort(key=lambda v: v["outlierScore"], reverse=True)
    L += [f"## Reference channel outliers (last {cfg['recentDays']} days, {cfg['minOutlier']}x+ baseline)", ""]
    if recent:
        L += ["| Score | Views | Age | Type | Channel | Title |", "|---|---|---|---|---|---|"]
        for v in recent[:15]:
            t = "Short" if v["isShort"] else "Long"
            L.append(f"| {v['outlierScore']}x | {fmt(v['views'])} | {v['ageDays']}d | {t} | {v['channel']} | [{v['title']}]({v['url']}) |")
    else:
        L.append("Koi naya outlier nahi.")
    L.append("")

    L += [f"## Keyword trends (last {cfg['searchDays']} days, India, Hindi)", ""]
    for s in data["searches"]:
        top = sorted(s["videos"], key=lambda v: (v["viewsPerSub"] or 0), reverse=True)[:5]
        L += [f"**{s['query']}**", "", "| Views | Views/Sub | Channel subs | Title |", "|---|---|---|---|"]
        for v in top:
            L.append(f"| {fmt(v['views'])} | {v['viewsPerSub']} | {fmt(v['channelSubs'])} | [{v['title']}]({v['url']}) |")
        L.append("")

    own = data.get("own")
    if own and not own.get("error"):
        L += ["## Dear Market (own channel)", "",
              f"Subscribers: {fmt(own['subscribers'])}. Long baseline: {fmt(own['baseline_long'])} views.", ""]
        for v in sorted(own["videos"], key=lambda v: v["published"], reverse=True)[:5]:
            L.append(f"- {v['published']}: {v['title']}: {fmt(v['views'])} views ({v.get('outlierScore', 0)}x)")
        L.append("")
    if data["errors"]:
        L += ["## Errors", ""] + [f"- {e}" for e in data["errors"]]
    return "\n".join(L) + "\n"


def main():
    if not KEY:
        sys.exit("YOUTUBE_API_KEY missing. GitHub repo Settings > Secrets mein add karein.")
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    now = datetime.now(timezone.utc)
    data = {"date": now.strftime("%Y-%m-%d"), "generatedAt": now.isoformat(),
            "channels": [], "searches": [], "own": None, "errors": []}

    for h in cfg["referenceChannels"]:
        try:
            data["channels"].append(scan_channel(h, now, cfg))
        except Exception as e:  # keep going if one channel fails
            data["errors"].append(f"{h}: {e}")
    try:
        data["own"] = scan_channel(cfg["ownChannel"], now, cfg)
    except Exception as e:
        data["errors"].append(f"own channel: {e}")
    for q in cfg["keywords"][: cfg["maxSearches"]]:
        try:
            data["searches"].append(keyword_search(q, now, cfg))
        except Exception as e:
            data["errors"].append(f"search '{q}': {e}")

    data["quotaUnits"] = UNITS
    os.makedirs(os.path.join(ROOT, "data", "history"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    blob = json.dumps(data, ensure_ascii=False, indent=1)
    for p in ("data/latest.json", f"data/history/{data['date']}.json"):
        open(os.path.join(ROOT, p), "w", encoding="utf-8").write(blob)
    open(os.path.join(ROOT, "reports", "latest.md"), "w", encoding="utf-8").write(report(data, cfg))
    print(f"Done. Quota used: {UNITS} units. Errors: {len(data['errors'])}")


if __name__ == "__main__":
    main()

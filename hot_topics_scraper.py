#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多平台热点抓取工具
支持：微博热搜、知乎热榜、百度热搜、B站热搜、抖音热搜、头条热榜
作者：艾嘉（为栋哥定制）
"""

import json
import os
import sys
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("缺少 requests 库，请先安装：pip install requests")
    sys.exit(1)

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ============================================================
# 配置区
# ============================================================

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

TIMEOUT = 15  # 请求超时秒数
TOP_N = 20  # 每个平台取前N条
MAX_RETRIES = 3  # 最大重试次数


# ============================================================
# 带重试的 Session
# ============================================================

def create_session():
    """创建带重试机制的 requests session"""
    session = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "HEAD"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=1, pool_maxsize=1)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# 全局 session（每个线程独立创建）
_session = None

def get_session():
    global _session
    if _session is None:
        _session = create_session()
    return _session


def safe_get(url, **kwargs):
    """带重试的安全 GET 请求"""
    session = get_session()
    kwargs.setdefault("timeout", TIMEOUT)
    kwargs.setdefault("verify", False)
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, **kwargs)
            return resp
        except Exception as e:
            last_error = e
            time.sleep(1 * (attempt + 1))
    raise last_error


# ============================================================
# 各平台抓取函数
# ============================================================

def fetch_weibo():
    """微博热搜 - 多重备选方案"""
    # 方案1：s.weibo.com HTML页面解析
    url1 = "https://s.weibo.com/top/summary"
    headers1 = {**HEADERS, "Referer": "https://s.weibo.com/"}
    try:
        resp = safe_get(url1, headers=headers1)
        if resp.status_code == 200 and "html" in resp.headers.get("Content-Type", "").lower():
            import re
            html = resp.text
            items = []
            # 匹配热搜列表
            matches = re.findall(r'<td class="td-02"[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>(?:.*?<span>(\d+)</span>)?', html, re.DOTALL)
            for i, (href, title, hot) in enumerate(matches[:TOP_N], 1):
                title = title.strip()
                if not title or title == "微博热搜":
                    continue
                items.append({
                    "rank": i,
                    "title": title,
                    "hot": hot if hot else "",
                    "url": f"https://s.weibo.com{href}" if href.startswith("/") else href,
                })
            if items:
                return {"platform": "微博热搜", "items": items, "ok": True}
    except:
        pass

    # 方案2：移动端API
    url2 = "https://m.weibo.cn/api/container/getIndex"
    params2 = {"containerid": "106003type%3D25%26filter_type%3Drealtimehot"}
    headers2 = {
        **HEADERS,
        "Referer": "https://m.weibo.cn/",
        "MWeibo-Pwa": "1",
    }
    try:
        resp = safe_get(url2, headers=headers2, params=params2)
        if resp.status_code == 200:
            content_type = resp.headers.get("Content-Type", "")
            if "json" in content_type or resp.text.strip().startswith("{"):
                data = resp.json()
                items = []
                cards = data.get("data", {}).get("cards", [])
                for card in cards:
                    if card.get("card_group"):
                        for item in card["card_group"]:
                            title = item.get("desc", "")
                            if not title:
                                continue
                            items.append({
                                "rank": len(items) + 1,
                                "title": title,
                                "hot": item.get("desc_extr", ""),
                                "url": item.get("scheme", f"https://s.weibo.com/weibo?q={title}"),
                            })
                    if len(items) >= TOP_N:
                        break
                if items:
                    return {"platform": "微博热搜", "items": items[:TOP_N], "ok": True}
    except:
        pass

    return {"platform": "微博热搜", "items": [], "ok": False, "error": "所有方案均失败（微博反爬限制）"}


def fetch_zhihu():
    """知乎热榜 - 使用API + HTML双重备选"""
    # 方案1：API接口
    url = "https://api.zhihu.com/topstory/hot-lists/total"
    params = {"limit": TOP_N, "reverse_order": "0"}
    headers = {
        **HEADERS,
        "Referer": "https://www.zhihu.com/hot",
        "User-Agent": "com.zhihu.android/10.25.0 (Android 13)",
    }
    try:
        resp = safe_get(url, headers=headers, params=params, timeout=TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            items = []
            for i, item in enumerate(data.get("data", [])[:TOP_N], 1):
                target = item.get("target", {})
                items.append({
                    "rank": i,
                    "title": target.get("title", ""),
                    "hot": item.get("detail_text", ""),
                    "excerpt": target.get("excerpt", "")[:80] if target.get("excerpt") else "",
                    "url": f"https://www.zhihu.com/question/{target.get('id', '')}",
                })
            if items:
                return {"platform": "知乎热榜", "items": items, "ok": True}

        # 方案2：HTML页面解析
        url2 = "https://www.zhihu.com/hot"
        headers2 = {**HEADERS, "Referer": "https://www.zhihu.com/", "Cookie": ""}
        resp2 = safe_get(url2, headers=headers2, timeout=TIMEOUT)
        if resp2.status_code == 200:
            import re
            html = resp2.text
            match = re.search(r'<script id="js-initialData"[^>]*>(.*?)</script>', html, re.DOTALL)
            if match:
                init_data = json.loads(match.group(1))
                hot_list = init_data.get("initialState", {}).get("topstory", {}).get("hotList", [])
                items = []
                for i, item in enumerate(hot_list[:TOP_N], 1):
                    target = item.get("target", {})
                    items.append({
                        "rank": i,
                        "title": target.get("titleArea", {}).get("text", ""),
                        "hot": target.get("metricsArea", {}).get("text", ""),
                        "url": target.get("link", {}).get("url", ""),
                    })
                if items:
                    return {"platform": "知乎热榜", "items": items, "ok": True}

        raise ValueError(f"所有方案均失败 (HTTP {resp.status_code})")
    except Exception as e:
        return {"platform": "知乎热榜", "items": [], "ok": False, "error": str(e)}


def fetch_baidu():
    """百度热搜"""
    # 尝试多个接口
    urls = [
        ("https://top.baidu.com/api/board?platform=wise&tab=realtime", "wise"),
        ("https://top.baidu.com/api/board?pc=1&tab=realtime", "pc"),
        ("https://top.baidu.com/api/board?tab=realtime", "default"),
    ]
    try:
        items = []
        for url, _ in urls:
            try:
                resp = safe_get(url, headers=HEADERS, timeout=TIMEOUT)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                cards = data.get("data", {}).get("cards", [])
                for card in cards:
                    content = card.get("content", [])
                    for item in content:
                        title = item.get("word", "")
                        if not title:
                            continue
                        items.append({
                            "rank": len(items) + 1,
                            "title": title,
                            "hot": item.get("hotScore", ""),
                            "desc": item.get("desc", "")[:80] if item.get("desc") else "",
                            "url": item.get("rawUrl", item.get("url", "")),
                        })
                        if len(items) >= TOP_N:
                            break
                    if len(items) >= TOP_N:
                        break
                if items:
                    break
            except:
                continue

        return {"platform": "百度热搜", "items": items[:TOP_N], "ok": True}
    except Exception as e:
        return {"platform": "百度热搜", "items": [], "ok": False, "error": str(e)}


def fetch_bilibili():
    """B站热搜"""
    url = "https://app.bilibili.com/x/v2/search/trending/ranking"
    params = {"limit": TOP_N, "type": "mobile"}
    try:
        resp = safe_get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        items = []
        code = data.get("code", -1)
        if code == 0:
            for i, item in enumerate(data.get("data", {}).get("list", [])[:TOP_N], 1):
                show_name = item.get("show_name", "")
                # show_name通常和keyword一样，不作为热度
                items.append({
                    "rank": i,
                    "title": item.get("keyword", ""),
                    "hot": "",
                    "url": f"https://search.bilibili.com/all?keyword={item.get('keyword', '')}",
                })
        else:
            # 备用接口
            url2 = "https://api.bilibili.com/x/web-interface/search/square?limit=10"
            resp2 = safe_get(url2, headers=HEADERS, timeout=TIMEOUT)
            data2 = resp2.json()
            for i, item in enumerate(data2.get("data", {}).get("trending", {}).get("list", [])[:TOP_N], 1):
                items.append({
                    "rank": i,
                    "title": item.get("keyword", ""),
                    "hot": "",
                    "url": f"https://search.bilibili.com/all?keyword={item.get('keyword', '')}",
                })
        return {"platform": "B站热搜", "items": items, "ok": True}
    except Exception as e:
        return {"platform": "B站热搜", "items": [], "ok": False, "error": str(e)}


def fetch_douyin():
    """抖音热搜 - 使用SNSSDK移动端接口"""
    url = "https://aweme.snssdk.com/aweme/v1/hot/search/list/"
    headers = {
        **HEADERS,
        "Referer": "https://www.douyin.com/",
        "User-Agent": "com.ss.android.ugc.aweme/250501 (Linux; U; Android 13; zh_CN; Pixel 7; Build/TQ3A.230805.001; Cronet/TTNetVersion:b3070043e8 2024-01-29 QuicVersion:0144d358 2024-01-10)",
    }
    try:
        resp = safe_get(url, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        items = []
        word_list = data.get("data", {}).get("word_list", [])
        if not word_list:
            # 尝试备用接口
            url2 = "https://www.iesdouyin.com/aweme/v1/web/hot/search/list/"
            headers2 = {**HEADERS, "Referer": "https://www.douyin.com/"}
            resp2 = safe_get(url2, headers=headers2, timeout=TIMEOUT)
            data2 = resp2.json()
            word_list = data2.get("data", {}).get("word_list", [])

        for i, item in enumerate(word_list[:TOP_N], 1):
            items.append({
                "rank": i,
                "title": item.get("word", ""),
                "hot": item.get("hot_value", 0),
                "url": f"https://www.douyin.com/search/{item.get('word', '')}",
            })
        return {"platform": "抖音热搜", "items": items, "ok": True}
    except Exception as e:
        return {"platform": "抖音热搜", "items": [], "ok": False, "error": str(e)}


def fetch_toutiao():
    """头条热榜"""
    url = "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc"
    try:
        resp = safe_get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        items = []
        for i, item in enumerate(data.get("data", [])[:TOP_N], 1):
            items.append({
                "rank": i,
                "title": item.get("Title", ""),
                "hot": item.get("HotValue", ""),
                "url": item.get("Url", ""),
            })
        return {"platform": "头条热榜", "items": items, "ok": True}
    except Exception as e:
        return {"platform": "头条热榜", "items": [], "ok": False, "error": str(e)}


def fetch_wallstreet():
    """华尔街见闻快讯（财经类）"""
    url = "https://api-one-wscn.awtmt.com/apiv1/content/lives?channel=global-channel&limit=30"
    try:
        resp = safe_get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        items = []
        for item in data.get("data", {}).get("items", []):
            title = item.get("title", "")
            content = item.get("content_text", "")
            display_title = title if title else content[:60]
            if not display_title.strip():
                continue
            items.append({
                "rank": len(items) + 1,
                "title": display_title,
                "hot": "",
                "desc": content[:80] if content else "",
                "url": f"https://wallstreetcn.com/lives/{item.get('id', '')}",
            })
            if len(items) >= TOP_N:
                break
        return {"platform": "华尔街见闻", "items": items, "ok": True}
    except Exception as e:
        return {"platform": "华尔街见闻", "items": [], "ok": False, "error": str(e)}


# ============================================================
# 所有平台列表
# ============================================================

PLATFORMS = [
    ("weibo", fetch_weibo),
    ("zhihu", fetch_zhihu),
    ("baidu", fetch_baidu),
    ("douyin", fetch_douyin),
    ("toutiao", fetch_toutiao),
    ("bilibili", fetch_bilibili),
    ("wallstreet", fetch_wallstreet),
]


# ============================================================
# 输出格式化
# ============================================================

def format_hot_value(hot):
    """格式化热度值"""
    if not hot:
        return ""
    if isinstance(hot, (int, float)):
        if hot >= 10000:
            return f"{hot / 10000:.1f}万"
        return str(int(hot))
    return str(hot)


def print_console(results):
    """控制台打印"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"  网络热点汇总 - {now}")
    print(f"{'='*60}")

    for result in results:
        platform = result["platform"]
        items = result["items"]
        ok = result["ok"]

        print(f"\n{'─'*50}")
        print(f"  【{platform}】", end="")

        if not ok:
            print(f"  ❌ 抓取失败: {result.get('error', '未知错误')}")
            continue

        if not items:
            print("  ⚠️ 无数据")
            continue

        print(f"  共 {len(items)} 条")
        print(f"{'─'*50}")

        for item in items:
            rank = item.get("rank", "")
            title = item.get("title", "")
            hot = format_hot_value(item.get("hot", ""))
            hot_str = f"  [{hot}]" if hot else ""
            print(f"  {rank:>2}. {title}{hot_str}")

    print(f"\n{'='*60}")
    print(f"  抓取完成 - 共 {sum(1 for r in results if r['ok'])}/{len(results)} 个平台成功")
    print(f"{'='*60}\n")


def save_markdown(results, output_dir):
    """保存为 Markdown 文件"""
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    filename = f"热点日报_{date_str}_{now.strftime('%H%M')}.md"
    filepath = os.path.join(output_dir, filename)

    lines = [
        f"# 网络热点汇总 - {date_str} {time_str}\n",
        f"> 自动抓取于 {now.strftime('%Y-%m-%d %H:%M:%S')}\n",
    ]

    for result in results:
        platform = result["platform"]
        items = result["items"]
        ok = result["ok"]

        lines.append(f"\n## {platform}\n")

        if not ok:
            lines.append(f"> ❌ 抓取失败: {result.get('error', '未知错误')}\n")
            continue

        if not items:
            lines.append("> ⚠️ 无数据\n")
            continue

        lines.append("| 排名 | 标题 | 热度 | 链接 |")
        lines.append("|------|------|------|------|")

        for item in items:
            rank = item.get("rank", "")
            title = item.get("title", "").replace("|", "/")
            hot = format_hot_value(item.get("hot", ""))
            url = item.get("url", "")
            if url:
                lines.append(f"| {rank} | {title} | {hot} | [查看]({url}) |")
            else:
                lines.append(f"| {rank} | {title} | {hot} | - |")

    lines.append(f"\n---\n> 由热点抓取工具自动生成\n")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return filepath


def save_json(results, output_dir):
    """保存为 JSON 文件"""
    now = datetime.now()
    filename = f"热点数据_{now.strftime('%Y%m%d_%H%M')}.json"
    filepath = os.path.join(output_dir, filename)

    output = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "platforms": results,
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return filepath


# ============================================================
# 主函数
# ============================================================

def main():
    # 输出目录
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(output_dir, exist_ok=True)

    print("正在抓取各平台热点数据...")
    print(f"支持平台: {', '.join(p[0] for p in PLATFORMS)}\n")

    # 并发抓取所有平台
    results = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {
            executor.submit(func): name
            for name, func in PLATFORMS
        }
        for future in as_completed(future_map):
            name = future_map[future]
            try:
                result = future.result()
                results.append(result)
                status = "✓" if result["ok"] else "✗"
                count = len(result["items"])
                print(f"  {status} {name}: {count} 条")
            except Exception as e:
                print(f"  ✗ {name}: 异常 - {e}")
                results.append({
                    "platform": name,
                    "items": [],
                    "ok": False,
                    "error": str(e),
                })

    # 按定义顺序排序
    PLATFORM_ORDER = ["微博热搜", "知乎热榜", "百度热搜", "抖音热搜", "头条热榜", "B站热搜", "华尔街见闻"]
    results.sort(key=lambda x: PLATFORM_ORDER.index(x["platform"]) if x["platform"] in PLATFORM_ORDER else 99)

    # 控制台输出
    print_console(results)

    # 保存文件
    md_path = save_markdown(results, output_dir)
    json_path = save_json(results, output_dir)

    print(f"📄 Markdown 报告: {md_path}")
    print(f"📊 JSON 数据: {json_path}")
    print(f"📁 输出目录: {output_dir}")


if __name__ == "__main__":
    main()

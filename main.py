#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SWING Portfolio 자동 업데이트
GitHub Actions에서 실행
"""
import os, sys, time, datetime, warnings, io, base64, pickle, requests, json
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.ticker as mticker
import matplotlib.dates as mdates
import yfinance as yf
from pykrx import stock as krx
from notion_client import Client
from collections import defaultdict
warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════
# 설정값 (GitHub Secrets에서 로드)
# ══════════════════════════════════════════════════════
NOTION_API_KEY    = os.environ["NOTION_API_KEY"]
HOLDINGS_DB_ID    = os.environ.get("HOLDINGS_DB_ID",   "80eb71748f034149b717a809ce9f8f17")
TRADES_DB_ID      = os.environ.get("TRADES_DB_ID",     "73a20d0923fc4a82a21c6f382db97719")
WATCHLIST_DB_ID   = os.environ.get("WATCHLIST_DB_ID",  "6d094ee04d8f40fb9afbdfa8892221fa")
PORTFOLIO_PAGE_ID = os.environ.get("PORTFOLIO_PAGE_ID","37295a5b-2a23-81c4-a48f-c6e7ec4b6eeb")
GITHUB_TOKEN      = os.environ["GITHUB_TOKEN"]
GITHUB_REPO       = os.environ.get("GITHUB_REPO",      "swing32/op")
GITHUB_BRANCH     = os.environ.get("GITHUB_BRANCH",    "main")

today     = datetime.date.today()
today_str = today.strftime("%Y-%m-%d")
now_str   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

notion = Client(auth=NOTION_API_KEY)

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# ══════════════════════════════════════════════════════
# 공통 유틸
# ══════════════════════════════════════════════════════
def query_all(database_id):
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    results, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor: body["start_cursor"] = cursor
        resp = requests.post(url, headers=HEADERS, json=body)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data["results"])
        if not data.get("has_more"): break
        cursor = data["next_cursor"]
    return results

def update_page(page_id, props):
    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=HEADERS,
        json={"properties": props}
    )
    resp.raise_for_status()
    return resp.json()

def create_page(database_id, props):
    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=HEADERS,
        json={"parent": {"database_id": database_id}, "properties": props}
    )
    resp.raise_for_status()
    return resp.json()

def upload_to_github(img_data, filename):
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/images/{filename}"
    gh_h = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    sha = None
    try:
        r = requests.get(api_url, headers=gh_h)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except: pass
    body = {
        "message": f"Update {filename}",
        "content": base64.b64encode(img_data).decode(),
        "branch": GITHUB_BRANCH
    }
    if sha: body["sha"] = sha
    r = requests.put(api_url, headers=gh_h, json=body)
    if r.status_code in (200, 201):
        # GitHub raw URL에 캐시 무력화 파라미터 추가
        url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/images/{filename}?v={today_str}"
        print(f"  ✅ GitHub 업로드: {url}")
        return url
    print(f"  ❌ GitHub 업로드 실패: {r.status_code} {r.text[:200]}")
    return ""

# ══════════════════════════════════════════════════════
# 한글 폰트 설정
# ══════════════════════════════════════════════════════
def setup_font():
    import subprocess
    subprocess.run(["apt-get", "-qq", "install", "-y", "fonts-nanum"], capture_output=True)
    font_path = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
    if not os.path.exists(font_path):
        font_path = "/usr/share/fonts/nanum/NanumGothic.ttf"
    try:
        fm.fontManager.addfont(font_path)
        plt.rcParams["font.family"] = "NanumGothic"
    except:
        pass
    plt.rcParams["axes.unicode_minus"] = False
    print("✅ 한글 폰트 설정 완료")

# ══════════════════════════════════════════════════════
# 환율 조회
# ══════════════════════════════════════════════════════
def get_usd_krw():
    try:
        rate = yf.Ticker("USDKRW=X").fast_info["lastPrice"]
        print(f"💱 USD/KRW: {rate:,.2f}")
        return rate
    except:
        print("⚠️ 환율 조회 실패, 기본값 1380 사용")
        return 1380.0

# ══════════════════════════════════════════════════════
# 현재가 조회
# ══════════════════════════════════════════════════════
def get_krx_price(ticker):
    try:
        for days_ago in range(0, 6):
            date = (today - datetime.timedelta(days=days_ago)).strftime("%Y%m%d")
            df = krx.get_market_ohlcv_by_date(date, date, ticker)
            if not df.empty:
                price = df["종가"].iloc[-1]
                print(f"  ✅ {ticker} → {price:,} KRW ({date})")
                return float(price)
    except Exception as e:
        print(f"  ⚠️ KRX {ticker} 실패: {e}")
    return None

def get_current_price(ticker, category):
    usd_krw = get_usd_krw()
    if category in ("국내종목", "국내ETF", "국내ETF-해외"):
        return get_krx_price(ticker)
    else:
        try:
            yf_ticker = ticker if "." in ticker else ticker
            data = yf.Ticker(yf_ticker).history(period="5d")
            if not data.empty:
                price_usd = data["Close"].iloc[-1]
                price_krw = price_usd * usd_krw
                print(f"  ✅ {ticker} → ${price_usd:.2f} = {price_krw:,.0f} KRW")
                return float(price_krw)
        except Exception as e:
            print(f"  ⚠️ YF {ticker} 실패: {e}")
    return None

# ══════════════════════════════════════════════════════
# STEP 1: 매매내역 → 보유주식 DB 업데이트
# ══════════════════════════════════════════════════════
def step1_update_holdings():
    print("\n" + "="*60)
    print("STEP 1. 매매내역 → 보유주식 업데이트")
    print("="*60)

    trades = query_all(TRADES_DB_ID)
    print(f"  → {len(trades)}건 로드됨")

    portfolio = defaultdict(lambda: {"name":"","ticker":"","category":"","qty":0,"total_cost":0})
    trades_sorted = sorted(trades, key=lambda t: (
        t["properties"]["날짜"]["date"]["start"]
        if t["properties"]["날짜"]["date"] else ""
    ))

    for t in trades_sorted:
        p    = t["properties"]
        name = p["종목명"]["title"][0]["plain_text"] if p["종목명"]["title"] else ""
        tkr  = p["티커"]["rich_text"][0]["plain_text"] if p["티커"]["rich_text"] else ""
        side = p["매수매도"]["select"]["name"] if p["매수매도"]["select"] else ""
        qty  = p["수량"]["number"] or 0
        price= p["단가"]["number"] or 0
        cat  = p["분류"]["select"]["name"] if p["분류"]["select"] else ""
        if not tkr: continue

        d = portfolio[tkr]
        d["name"] = name; d["ticker"] = tkr; d["category"] = cat
        if side == "매수":
            d["total_cost"] += qty * price
            d["qty"]        += qty
        elif side == "매도":
            if d["qty"] > 0:
                avg = d["total_cost"] / d["qty"]
                d["total_cost"] -= avg * min(qty, d["qty"])
            d["qty"] = max(0, d["qty"] - qty)

    print(f"\n{'티커':<10} {'종목명':<16} {'보유수량':>8} {'평균매입가':>12}")
    print("-"*50)
    for tkr, d in portfolio.items():
        if d["qty"] > 0:
            avg = d["total_cost"] / d["qty"] if d["qty"] > 0 else 0
            print(f"{tkr:<10} {d['name']:<16} {d['qty']:>8,.1f} {avg:>12,.0f}")

    # DB upsert
    existing = query_all(HOLDINGS_DB_ID)
    existing_map = {}
    for page in existing:
        p   = page["properties"]
        tkr = p["티커"]["rich_text"][0]["plain_text"] if p["티커"]["rich_text"] else ""
        if tkr: existing_map[tkr] = page["id"]

    for tkr, d in portfolio.items():
        if d["qty"] <= 0: continue
        avg = round(d["total_cost"] / d["qty"]) if d["qty"] > 0 else 0
        props = {
            "종목명":    {"title": [{"text": {"content": d["name"]}}]},
            "티커":      {"rich_text": [{"text": {"content": tkr}}]},
            "분류":      {"select": {"name": d["category"]}},
            "보유수량":  {"number": d["qty"]},
            "평균매입가":{"number": avg},
        }
        if tkr in existing_map:
            update_page(existing_map[tkr], props)
            print(f"  ✏️  {d['name']} 업데이트")
        else:
            create_page(HOLDINGS_DB_ID, props)
            print(f"  ➕ {d['name']} 신규 추가")
        time.sleep(0.3)

    print("\n✅ 보유주식 DB 업데이트 완료!")

# ══════════════════════════════════════════════════════
# STEP 2: 현재가 → 수익/수익률 업데이트
# ══════════════════════════════════════════════════════
def step2_update_prices():
    print("\n" + "="*60)
    print("STEP 2. 현재가 조회 및 수익/수익률 업데이트")
    print("="*60)

    holdings = query_all(HOLDINGS_DB_ID)
    stocks = []
    for page in holdings:
        p   = page["properties"]
        name= p["종목명"]["title"][0]["plain_text"] if p["종목명"]["title"] else ""
        tkr = p["티커"]["rich_text"][0]["plain_text"] if p["티커"]["rich_text"] else ""
        cat = p["분류"]["select"]["name"] if p["분류"]["select"] else ""
        qty = p["보유수량"]["number"] or 0
        avg = p["평균매입가"]["number"] or 0
        if tkr and qty > 0:
            stocks.append({"page_id": page["id"], "name": name,
                           "ticker": tkr, "category": cat, "qty": qty, "avg": avg})

    ok = fail = 0
    print(f"\n🚀 현재가 업데이트 ({len(stocks)}개 종목)\n")
    for s in stocks:
        print(f"📌 {s['name']} ({s['ticker']})")
        price = get_current_price(s["ticker"], s["category"])
        if price is None:
            fail += 1; continue
        ev   = round(price * s["qty"])
        pnl  = ev - round(s["avg"] * s["qty"])
        rate = round(pnl / (s["avg"] * s["qty"]) * 100, 2) if s["avg"] else 0
        ps   = "+" if pnl  >= 0 else ""
        rs   = "+" if rate >= 0 else ""
        print(f"  평가금액: {ev:,}원 | 수익: {ps}{pnl:,}원 ({rs}{rate}%)")
        try:
            update_page(s["page_id"], {
                "현재가":    {"number": price},
                "평가금액":  {"number": ev},
                "수익":      {"number": pnl},
                "수익률":    {"number": rate},
                "업데이트일":{"date": {"start": today_str}},
            })
            ok += 1
        except Exception as e:
            print(f"  ❌ 업데이트 실패: {e}")
            fail += 1
        time.sleep(0.4)

    print(f"\n✅ 현재가 업데이트 완료 | 성공 {ok}개 / 실패 {fail}개")

# ══════════════════════════════════════════════════════
# STEP 3: 파이차트 생성 → GitHub → 노션
# ══════════════════════════════════════════════════════
def step3_pie_chart():
    print("\n" + "="*60)
    print("STEP 3. 분류별 파이차트 생성")
    print("="*60)

    holdings = query_all(HOLDINGS_DB_ID)
    cat_totals = {}
    for page in holdings:
        p   = page["properties"]
        cat = p["분류"]["select"]["name"] if p["분류"]["select"] else "기타"
        qty = p["보유수량"]["number"] or 0
        cp  = p["현재가"]["number"] or 0
        ev  = qty * cp
        if ev > 0:
            cat_totals[cat] = cat_totals.get(cat, 0) + ev

    if not cat_totals:
        print("⚠️ 평가금액 없음"); return ""

    total = sum(cat_totals.values())
    colors = {"국내종목":"#4CAF50","국내ETF":"#2196F3","국내ETF-해외":"#9C27B0",
              "해외종목":"#F44336","해외ETF":"#FF9800","기타":"#9E9E9E"}

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    fig.patch.set_facecolor("white")
    labels = list(cat_totals.keys())
    values = list(cat_totals.values())
    clrs   = [colors.get(l, "#9E9E9E") for l in labels]

    wedges, texts, autotexts = ax1.pie(
        values, labels=labels, colors=clrs,
        autopct="%1.1f%%", startangle=90,
        wedgeprops={"edgecolor":"white","linewidth":2},
        textprops={"fontsize":11}
    )
    for at in autotexts:
        at.set_fontsize(10); at.set_color("white"); at.set_fontweight("bold")
    ax1.set_title("보유주식 분류별 비율", fontsize=14, fontweight="bold", pad=15)

    ax2.axis("off")
    tbl_data = [[c, f"{v:,.0f}원", f"{v/total*100:.1f}%"]
                for c, v in sorted(cat_totals.items(), key=lambda x: -x[1])]
    tbl_data.append(["합계", f"{total:,.0f}원", "100%"])
    tbl = ax2.table(cellText=tbl_data, colLabels=["분류","평가금액","비율"],
                    loc="center", cellLoc="center")
    tbl.auto_set_font_size(False); tbl.set_fontsize(11); tbl.scale(1.2, 1.8)
    for j in range(3):
        tbl[0,j].set_facecolor("#37474F"); tbl[0,j].set_text_props(color="white",fontweight="bold")
    last = len(tbl_data)
    for j in range(3):
        tbl[last,j].set_facecolor("#ECEFF1"); tbl[last,j].set_text_props(fontweight="bold")
    ax2.set_title("분류별 평가금액", fontsize=14, fontweight="bold", pad=15)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    buf.seek(0); img_data = buf.read(); plt.close()
    print(f"✅ 파이차트 생성 완료 ({len(img_data)/1024:.1f} KB)")

    return upload_to_github(img_data, f"pie_chart_{today_str}.png")

# ══════════════════════════════════════════════════════
# STEP 4: 노션 메인 페이지 업데이트
# ══════════════════════════════════════════════════════
def step4_update_notion_page(pie_url=""):
    print("\n" + "="*60)
    print("STEP 4. 노션 메인 페이지 업데이트")
    print("="*60)

    holdings_final = query_all(HOLDINGS_DB_ID)
    total_eval = total_buy = 0
    rows_data = []

    for page in holdings_final:
        p   = page["properties"]
        nm  = p["종목명"]["title"][0]["plain_text"] if p["종목명"]["title"] else "-"
        tkr = p["티커"]["rich_text"][0]["plain_text"] if p["티커"]["rich_text"] else "-"
        cat = p["분류"]["select"]["name"] if p["분류"]["select"] else "-"
        qty = p["보유수량"]["number"] or 0
        bp  = p["평균매입가"]["number"] or 0
        cp  = p["현재가"]["number"] or 0
        if qty <= 0: continue
        ev  = qty * cp; bv = qty * bp; pnl = ev - bv
        rt  = (pnl / bv * 100) if bv > 0 else 0
        total_eval += ev; total_buy += bv
        rows_data.append((nm, tkr, cat, qty, bp, cp, ev, pnl, rt))

    total_pnl  = total_eval - total_buy
    total_rate = round(total_pnl / total_buy * 100, 2) if total_buy > 0 else 0
    ps = "+" if total_pnl  >= 0 else ""
    rs = "+" if total_rate >= 0 else ""

    # 매매내역
    all_trades    = query_all(TRADES_DB_ID)
    latest_date   = max((t["properties"]["날짜"]["date"]["start"]
                         for t in all_trades if t["properties"]["날짜"]["date"]), default="")
    recent_trades = [t for t in all_trades
                     if t["properties"]["날짜"]["date"] and
                        t["properties"]["날짜"]["date"]["start"] == latest_date]

    blocks = requests.get(
        f"https://api.notion.com/v1/blocks/{PORTFOLIO_PAGE_ID}/children?page_size=100",
        headers=HEADERS
    ).json().get("results", [])

    SAFE = {"heading_1","heading_2","child_database","child_page","divider"}

    def make_row(nm,tkr,cat,qty,bp,cp,ev,pnl,rt):
        sp="+" if pnl>=0 else ""; sr="+" if rt>=0 else ""
        return {"object":"block","type":"table_row","table_row":{"cells":[
            [{"text":{"content":nm}}],[{"text":{"content":tkr}}],
            [{"text":{"content":f"{ev:,.0f}원"}}],[{"text":{"content":f"{sp}{pnl:,.0f}원"}}],
            [{"text":{"content":f"{sr}{rt:.2f}%"}}],[{"text":{"content":f"{qty:,.0f}주"}}],
            [{"text":{"content":f"{bp:,.0f}원"}}],[{"text":{"content":cat}}],
        ]}}

    # [1] 작성일자 heading_2 업데이트
    for b in blocks:
        if b["type"] == "heading_2":
            txt = "".join(t["plain_text"] for t in b["heading_2"]["rich_text"])
            if "작성일자" in txt or "새로고침" in txt:
                requests.patch(f"https://api.notion.com/v1/blocks/{b['id']}", headers=HEADERS,
                    json={"heading_2":{"rich_text":[
                        {"type":"text","text":{"content":f"📅 작성일자: {today_str}  "}},
                        {"type":"text","text":{"content":"🔄 새로고침 (GitHub Actions 실행)",
                            "link":{"url":f"https://github.com/{GITHUB_REPO}/actions"}}},
                    ]}})
                print("  ✅ 작성일자 업데이트")
                break

    # [2] 총자산 heading_2 업데이트
    for b in blocks:
        if b["type"] == "heading_2":
            txt = "".join(t["plain_text"] for t in b["heading_2"]["rich_text"])
            if "총평가금액" in txt:
                requests.patch(f"https://api.notion.com/v1/blocks/{b['id']}", headers=HEADERS,
                    json={"heading_2":{"rich_text":[{"type":"text","text":{"content":
                        f"💵 총평가금액 {total_eval:,.0f}원   "
                        f"📈 총수익 {ps}{total_pnl:,.0f}원   "
                        f"📊 총수익률 {rs}{total_rate:.2f}%"
                    }}]}})
                print("  ✅ 총자산 업데이트")
                break

    # [3] 보유주식 테이블 업데이트
    holding_h_idx = None
    for i, b in enumerate(blocks):
        if b["type"] in ("heading_1","heading_2"):
            txt = "".join(t["plain_text"] for t in b[b["type"]]["rich_text"])
            if "보유주식" in txt:
                holding_h_idx = i; break

    if holding_h_idx is not None:
        for b in blocks[holding_h_idx + 1:]:
            if b["type"] in SAFE: break
            if b["type"] == "table":
                rows = requests.get(f"https://api.notion.com/v1/blocks/{b['id']}/children",
                    headers=HEADERS).json().get("results",[])
                for row in rows[1:]:
                    requests.delete(f"https://api.notion.com/v1/blocks/{row['id']}", headers=HEADERS)
                    time.sleep(0.05)
                requests.patch(f"https://api.notion.com/v1/blocks/{b['id']}/children",
                    headers=HEADERS,
                    json={"children":[make_row(*r) for r in rows_data]})
                print(f"  ✅ 보유주식 업데이트 ({len(rows_data)}개)")
                break

    # [4] 파이차트 이미지 업데이트
    if pie_url:
        pie_h_idx = None
        for i, b in enumerate(blocks):
            if b["type"] in ("heading_1","heading_2"):
                txt = "".join(t["plain_text"] for t in b[b["type"]]["rich_text"])
                if "분류별 비율" in txt:
                    pie_h_idx = i; break
        if pie_h_idx is not None:
            for b in blocks[pie_h_idx+1:]:
                if b["type"] in SAFE: break
                if b["type"] == "image":
                    requests.patch(f"https://api.notion.com/v1/blocks/{b['id']}", headers=HEADERS,
                        json={"image":{"type":"external","external":{"url":pie_url}}})
                    print("  ✅ 파이차트 이미지 업데이트")
                    break
            else:
                requests.patch(f"https://api.notion.com/v1/blocks/{PORTFOLIO_PAGE_ID}/children",
                    headers=HEADERS,
                    json={"after": blocks[pie_h_idx]["id"], "children":[
                        {"object":"block","type":"image",
                         "image":{"type":"external","external":{"url":pie_url}}}
                    ]})
                print("  ✅ 파이차트 이미지 삽입")

    # [5] 최근 매매일지 업데이트
    recent_h_idx = None
    for i, b in enumerate(blocks):
        if b["type"] in ("heading_1","heading_2"):
            txt = "".join(t["plain_text"] for t in b[b["type"]]["rich_text"])
            if "최근" in txt and "매매" in txt:
                recent_h_idx = i; break

    if recent_h_idx is not None:
        # 타이틀 날짜 업데이트
        b = blocks[recent_h_idx]
        requests.patch(f"https://api.notion.com/v1/blocks/{b['id']}", headers=HEADERS,
            json={"heading_1":{"rich_text":[
                {"type":"text","text":{"content":f"📝 최근 매매일지 ({latest_date})  "}},
                {"type":"text","text":{"content":"📋 전체보기",
                    "link":{"url":"https://app.notion.com/p/37a95a5b2a238197aeabcfa5a4eafe2f"}}},
            ]}})
        for b in blocks[recent_h_idx+1:]:
            if b["type"] in SAFE: break
            if b["type"] == "table":
                rows = requests.get(f"https://api.notion.com/v1/blocks/{b['id']}/children",
                    headers=HEADERS).json().get("results",[])
                for row in rows[1:]:
                    requests.delete(f"https://api.notion.com/v1/blocks/{row['id']}", headers=HEADERS)
                    time.sleep(0.05)
                new_rows = []
                for t in recent_trades:
                    tp    = t["properties"]
                    nm    = tp["종목명"]["title"][0]["plain_text"] if tp["종목명"]["title"] else "-"
                    tkr   = tp["티커"]["rich_text"][0]["plain_text"] if tp["티커"]["rich_text"] else "-"
                    side  = tp["매수매도"]["select"]["name"] if tp["매수매도"]["select"] else "-"
                    qty   = tp["수량"]["number"] or 0
                    price = tp["단가"]["number"] or 0
                    cat   = tp["분류"]["select"]["name"] if tp["분류"]["select"] else "-"
                    reason= tp["사유"]["rich_text"][0]["plain_text"] if tp["사유"]["rich_text"] else "-"
                    new_rows.append({"object":"block","type":"table_row","table_row":{"cells":[
                        [{"text":{"content":latest_date}}],[{"text":{"content":nm}}],
                        [{"text":{"content":tkr}}],[{"text":{"content":side}}],
                        [{"text":{"content":f"{qty:,.0f}주"}}],[{"text":{"content":f"{price:,.0f}원"}}],
                        [{"text":{"content":cat}}],[{"text":{"content":reason}}],
                    ]}})
                if new_rows:
                    requests.patch(f"https://api.notion.com/v1/blocks/{b['id']}/children",
                        headers=HEADERS, json={"children":new_rows})
                print(f"  ✅ 최근 매매일지 업데이트 ({latest_date}, {len(recent_trades)}건)")
                break

    # [6] 전체 매매일지 페이지 업데이트
    FULL_LOG_PAGE_ID = "37a95a5b-2a23-8197-aeab-cfa5a4eafe2f"
    all_trades_sorted = sorted(all_trades,
        key=lambda t: t["properties"]["날짜"]["date"]["start"]
        if t["properties"]["날짜"]["date"] else "", reverse=True)
    fl_blocks = requests.get(
        f"https://api.notion.com/v1/blocks/{FULL_LOG_PAGE_ID}/children?page_size=100",
        headers=HEADERS).json().get("results",[])
    for b in fl_blocks:
        if b["type"] != "heading_1":
            requests.delete(f"https://api.notion.com/v1/blocks/{b['id']}", headers=HEADERS)
            time.sleep(0.05)
    all_rows = [{"object":"block","type":"table_row","table_row":{"cells":[
        [{"text":{"content":h}}] for h in ["날짜","종목이름","티커","매수/매도","수량","단가","분류","사유"]
    ]}}]
    for t in all_trades_sorted:
        tp    = t["properties"]
        nm    = tp["종목명"]["title"][0]["plain_text"] if tp["종목명"]["title"] else "-"
        tkr   = tp["티커"]["rich_text"][0]["plain_text"] if tp["티커"]["rich_text"] else "-"
        side  = tp["매수매도"]["select"]["name"] if tp["매수매도"]["select"] else "-"
        qty   = tp["수량"]["number"] or 0
        price = tp["단가"]["number"] or 0
        cat   = tp["분류"]["select"]["name"] if tp["분류"]["select"] else "-"
        reason= tp["사유"]["rich_text"][0]["plain_text"] if tp["사유"]["rich_text"] else "-"
        date  = tp["날짜"]["date"]["start"] if tp["날짜"]["date"] else "-"
        all_rows.append({"object":"block","type":"table_row","table_row":{"cells":[
            [{"text":{"content":date}}],[{"text":{"content":nm}}],
            [{"text":{"content":tkr}}],[{"text":{"content":side}}],
            [{"text":{"content":f"{qty:,.0f}주"}}],[{"text":{"content":f"{price:,.0f}원"}}],
            [{"text":{"content":cat}}],[{"text":{"content":reason}}],
        ]}})
    requests.patch(f"https://api.notion.com/v1/blocks/{FULL_LOG_PAGE_ID}/children",
        headers=HEADERS,
        json={"children":[{"object":"block","type":"table","table":{
            "table_width":8,"has_column_header":True,"has_row_header":False,
            "children":all_rows
        }}]})
    print(f"  ✅ 전체 매매일지 업데이트 ({len(all_trades_sorted)}건)")
    print(f"\n✅ 노션 업데이트 완료! ({now_str})")

# ══════════════════════════════════════════════════════
# 메인 실행
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    print("🚀 SWING Portfolio 자동 업데이트 시작")
    print(f"   실행일시: {now_str}")
    print("="*60)

    setup_font()
    step1_update_holdings()
    step2_update_prices()
    pie_url = step3_pie_chart()
    step4_update_notion_page(pie_url)

    print("\n🎉 모든 작업 완료!")

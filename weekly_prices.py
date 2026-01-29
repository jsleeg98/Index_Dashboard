#!/usr/bin/env python3
"""
자산별 최근 일주일 종가 데이터를 가져와 Markdown 표로 출력하는 스크립트
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from tabulate import tabulate
import sys
import importlib
import math
import itertools
import argparse
import json
import html as html_lib
import os
import sqlite3
import time
import threading

ASSETS = {
    "아이렌": "IREN",
    "로켓랩": "RKLB",
    "비트코인": "BTC-USD",
    "이더리움": "ETH-USD",
    "원달러환율": "KRW=X",
    "금": "GC=F",
    "은": "SI=F",
    "구리": "HG=F",
    "나스닥100": "^IXIC",
    "S&P500": "^GSPC"
}

USD_ASSETS = {"IREN", "RKLB", "BTC-USD", "ETH-USD", "GC=F", "SI=F", "HG=F"}
INDEX_ASSETS = {"^IXIC", "^GSPC"}
KRW_ASSETS = {"KRW=X"}


def load_env_file(path=".env"):
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        return


def ensure_db_dir(db_path):
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)


def log_message(message, payload=None):
    if payload is not None:
        print(f"{message} {payload}", flush=True)
    else:
        print(message, flush=True)


def init_db(db_path):
    ensure_db_dir(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asset_prices (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                close REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (ticker, date)
            )
            """
        )


def get_db_stats(db_path):
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT ticker,
                   COUNT(*) AS rows,
                   MIN(date) AS min_date,
                   MAX(date) AS max_date
            FROM asset_prices
            GROUP BY ticker
            ORDER BY ticker
            """
        )
        rows = cursor.fetchall()
    return rows


def fetch_cached_prices(ticker, start_date, end_date, db_path):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT date, close
            FROM asset_prices
            WHERE ticker = ? AND date BETWEEN ? AND ?
            ORDER BY date ASC
            """,
            (ticker, start_date, end_date)
        )
        rows = cursor.fetchall()
    return rows


def fetch_cached_last_n(ticker, limit_count, db_path):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT date, close
            FROM asset_prices
            WHERE ticker = ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (ticker, limit_count)
        )
        rows = cursor.fetchall()
    return list(reversed(rows))


def upsert_prices(ticker, hist_df, db_path):
    if hist_df.empty:
        return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for idx, row in hist_df.iterrows():
        date_str = idx.strftime("%Y-%m-%d")
        rows.append((ticker, date_str, float(row["Close"]), now_str))
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO asset_prices (ticker, date, close, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            rows
        )


def parse_date(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def resolve_date_range(period=None, start=None, end=None):
    if start and end:
        return parse_date(start), parse_date(end)
    period_map = {
        "7d": 7,
        "1mo": 30,
        "3mo": 90,
        "6mo": 180,
        "1y": 365
    }
    days = period_map.get(period or "7d", 7)
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=days)
    return start_date, end_date


def fetch_asset_history(
    assets,
    period=None,
    start=None,
    end=None,
    db_path="data/prices.db",
    force_refresh=False,
    trading_days=None,
    stale_only=False
):
    results = []
    chart_data = {}
    chart_dates = {}
    chart_changes = {}

    init_db(db_path)
    if trading_days:
        for korean_name, ticker in assets.items():
            try:
                ticker_start = time.time()
                if stale_only:
                    log_message(f"🧊 {korean_name}({ticker}) 캐시 조회")
                    cached_rows = fetch_cached_last_n(ticker, trading_days, db_path)
                else:
                    log_message(f"🔄 {korean_name}({ticker}) 조회 시작")
                    stock = yf.Ticker(ticker)
                    cached_rows = fetch_cached_last_n(ticker, trading_days, db_path)
                    if force_refresh or len(cached_rows) < trading_days:
                        hist = stock.history(period="1mo")
                        if not hist.empty:
                            hist = hist.sort_index().tail(trading_days)
                            upsert_prices(ticker, hist, db_path)

                    cached_rows = fetch_cached_last_n(ticker, trading_days, db_path)

                if not cached_rows:
                    continue

                cached_dates = [row[0] for row in cached_rows]
                close_values = [row[1] for row in cached_rows]
                dates = [datetime.strptime(date_str, "%Y-%m-%d").strftime('%m-%d') for date_str in cached_dates]
                closing_prices = [f"{value:.2f}" for value in close_values]
                current_price = close_values[-1]

                if len(close_values) >= 2:
                    start_price = close_values[0]
                    end_price = close_values[-1]
                    change_pct = ((end_price - start_price) / start_price * 100)
                    change_str = f"▲{change_pct:+.2f}%" if change_pct >= 0 else f"▼{change_pct:+.2f}%"
                else:
                    change_pct = None
                    change_str = "N/A"

                result_row = {
                    "자산": korean_name,
                    "티커": ticker,
                    "현재가": f"{current_price:.2f}",
                    "기간 등락": change_str
                }

                for i, (date, price) in enumerate(zip(dates, closing_prices)):
                    result_row[f"{i+1}일전"] = price

                results.append(result_row)
                chart_data[korean_name] = close_values
                chart_dates[korean_name] = dates
                chart_changes[korean_name] = change_pct
                elapsed = time.time() - ticker_start
                if stale_only:
                    log_message(f"✅ {korean_name}({ticker}) 캐시 완료 ({elapsed:.2f}s)")
                else:
                    log_message(f"✅ {korean_name}({ticker}) 조회 완료 ({elapsed:.2f}s)")
            except Exception:
                log_message(f"⚠️ {korean_name}({ticker}) 조회 실패")
                continue
        return results, chart_data, chart_dates, chart_changes

    start_date, end_date = resolve_date_range(period=period, start=start, end=end)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    refresh_start = max(start_date, end_date - timedelta(days=1))

    for korean_name, ticker in assets.items():
        try:
            ticker_start = time.time()
            stock = None
            if stale_only:
                log_message(f"🧊 {korean_name}({ticker}) 캐시 조회")
                cached_rows = fetch_cached_prices(ticker, start_str, end_str, db_path)
            else:
                log_message(f"🔄 {korean_name}({ticker}) 조회 시작")
                stock = yf.Ticker(ticker)
                cached_rows = fetch_cached_prices(ticker, start_str, end_str, db_path)
            cached_dates = [parse_date(row[0]) for row in cached_rows]
            cached_min = min(cached_dates) if cached_dates else None
            cached_max = max(cached_dates) if cached_dates else None

            fetch_ranges = []

            def add_range(range_start, range_end):
                if range_start > range_end:
                    return
                fetch_ranges.append((range_start, range_end))

            if not stale_only:
                if force_refresh:
                    add_range(start_date, end_date)
                else:
                    add_range(refresh_start, end_date)

                if not cached_rows:
                    add_range(start_date, end_date)
                else:
                    if cached_min and cached_min > start_date:
                        add_range(start_date, cached_min - timedelta(days=1))
                    if cached_max and cached_max < end_date:
                        add_range(cached_max + timedelta(days=1), end_date)

            if not stale_only:
                if stock is None:
                    continue
                for range_start, range_end in fetch_ranges:
                    fetch_start = range_start.strftime("%Y-%m-%d")
                    fetch_end = (range_end + timedelta(days=1)).strftime("%Y-%m-%d")
                    hist = stock.history(start=fetch_start, end=fetch_end)
                    if not hist.empty:
                        hist = hist.sort_index()
                        upsert_prices(ticker, hist, db_path)

                cached_rows = fetch_cached_prices(ticker, start_str, end_str, db_path)
                if not cached_rows:
                    continue

            cached_dates = [row[0] for row in cached_rows]
            close_values = [row[1] for row in cached_rows]
            dates = [datetime.strptime(date_str, "%Y-%m-%d").strftime('%m-%d') for date_str in cached_dates]
            closing_prices = [f"{value:.2f}" for value in close_values]
            current_price = close_values[-1]

            if len(close_values) >= 2:
                start_price = close_values[0]
                end_price = close_values[-1]
                change_pct = ((end_price - start_price) / start_price * 100)
                change_str = f"▲{change_pct:+.2f}%" if change_pct >= 0 else f"▼{change_pct:+.2f}%"
            else:
                change_pct = None
                change_str = "N/A"

            result_row = {
                "자산": korean_name,
                "티커": ticker,
                "현재가": f"{current_price:.2f}",
                "기간 등락": change_str
            }

            for i, (date, price) in enumerate(zip(dates, closing_prices)):
                result_row[f"{i+1}일전"] = price

            results.append(result_row)
            chart_data[korean_name] = close_values
            chart_dates[korean_name] = dates
            chart_changes[korean_name] = change_pct
            elapsed = time.time() - ticker_start
            if stale_only:
                log_message(f"✅ {korean_name}({ticker}) 캐시 완료 ({elapsed:.2f}s)")
            else:
                log_message(f"✅ {korean_name}({ticker}) 조회 완료 ({elapsed:.2f}s)")
        except Exception:
            log_message(f"⚠️ {korean_name}({ticker}) 조회 실패")
            continue

    return results, chart_data, chart_dates, chart_changes


def build_assets_payload(assets_map, chart_data, chart_dates, chart_changes):
    assets_payload = []
    for name, ticker in assets_map.items():
        if name not in chart_data:
            continue
        dates = chart_dates.get(name, [])
        closes = chart_data.get(name, [])
        if not dates or not closes:
            continue
        current = closes[-1]
        assets_payload.append({
            "name": name,
            "ticker": ticker,
            "dates": dates,
            "close": closes,
            "current": current,
            "change_pct": chart_changes.get(name)
        })
    return assets_payload


def get_weekly_closing_prices():
    """
    요청하신 자산들의 최근 일주일 종가를 가져와 Markdown 표로 출력
    """
    
    # 그래프 라벨 모드: "korean", "ticker", "both"
    chart_label_mode = "korean"

    results, chart_data, chart_dates, chart_changes = fetch_asset_history(ASSETS, period="7d")
    
    print("🔍 자산 데이터 조회 중...")
    
    for korean_name in ASSETS.keys():
        for row in results:
            if row["자산"] == korean_name:
                print(f"✅ {korean_name}: {row['현재가']} ({row['기간 등락']})")
                break
    
    if not results:
        print("❌ 데이터를 가져오지 못했습니다.")
        return
    
    # DataFrame 생성
    df = pd.DataFrame(results)
    
    # 컬럼 순서 재정렬
    cols = ["자산", "티커", "현재가", "기간 등락"]
    price_cols = [col for col in df.columns if col.endswith("일전")]
    cols.extend(price_cols)
    df = df[cols]
    
    # Markdown 표로 출력
    print("\n" + "="*80)
    print("📊 최근 일주일 자산별 종가 현황")
    print("="*80)
    
    # tabulate로 Markdown 표 생성
    table = tabulate(df, headers='keys', tablefmt='pipe', showindex=False)
    print(table)
    
    # 요약 정보
    print(f"\n📅 조회 시각: {datetime.now().strftime('%Y년 %m월 %d일 %H:%M:%S')}")
    print(f"📈 조회 자산 수: {len(results)}개")
    
    # CSV 파일로 저장 (선택적)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    try:
        filename = f"weekly_prices_{timestamp}.csv"
        df.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"💾 데이터 저장: {filename}")
    except Exception as e:
        print(f"⚠️ 파일 저장 실패: {str(e)}")

    # 그래프 저장 (선택적)
    try:
        if chart_data:
            matplotlib = importlib.import_module("matplotlib")
            matplotlib.use("Agg")
            plt = importlib.import_module("matplotlib.pyplot")
            font_manager = importlib.import_module("matplotlib.font_manager")
            for font_path in font_manager.findSystemFonts(fontpaths=None, fontext="ttf"):
                font_manager.fontManager.addfont(font_path)
            available_fonts = {f.name for f in font_manager.fontManager.ttflist}
            for font_name in [
                "NanumGothic",
                "Malgun Gothic",
                "AppleGothic",
                "Noto Sans CJK KR",
                "Noto Sans KR"
            ]:
                if font_name in available_fonts:
                    matplotlib.rcParams["font.family"] = font_name
                    break

            items = list(chart_data.items())
            color_cycle = itertools.cycle([
                "#1b6ca8", "#c0392b", "#2ecc71", "#f39c12", "#8e44ad",
                "#16a085", "#d35400", "#2c3e50", "#7f8c8d", "#27ae60"
            ])
            columns = 2
            rows = math.ceil(len(items) / columns)
            fig, axes = plt.subplots(rows, columns, figsize=(12, 3.2 * rows), squeeze=False)

            plotted = 0
            for idx, (name, prices) in enumerate(items):
                row_idx = idx // columns
                col_idx = idx % columns
                ax = axes[row_idx][col_idx]
                dates = chart_dates.get(name, [])
                if not dates or len(dates) != len(prices):
                    ax.set_visible(False)
                    continue
                color = next(color_cycle)
                ax.plot(dates, prices, marker='o', linewidth=1.8, color=color)
                ticker = ASSETS.get(name, "")
                if chart_label_mode == "ticker":
                    title_base = ticker or name
                elif chart_label_mode == "both":
                    title_base = f"{name} ({ticker})" if ticker else name
                else:
                    title_base = name
                change_pct = chart_changes.get(name)
                ax.set_title(title_base, color="#000000", pad=18)
                if change_pct is not None:
                    arrow = "▲" if change_pct >= 0 else "▼"
                    change_text = f"[{arrow}{change_pct:+.2f}%]"
                    change_color = "#c0392b" if change_pct >= 0 else "#1f5aa6"
                    ax.text(
                        0.5,
                        1.02,
                        change_text,
                        transform=ax.transAxes,
                        ha="center",
                        va="bottom",
                        color=change_color
                    )
                if ticker in KRW_ASSETS:
                    ax.yaxis.set_major_formatter(
                        matplotlib.ticker.FuncFormatter(lambda v, _: f"KRW {v:,.0f}")
                    )
                elif ticker in INDEX_ASSETS:
                    ax.yaxis.set_major_formatter(
                        matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}")
                    )
                else:
                    ax.yaxis.set_major_formatter(
                        matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:,.2f}")
                    )
                ax.set_xlabel("날짜 (월-일)")
                ax.set_ylabel("종가")
                ax.grid(True, alpha=0.3)
                plotted += 1

            for idx in range(len(items), rows * columns):
                row_idx = idx // columns
                col_idx = idx % columns
                axes[row_idx][col_idx].set_visible(False)

            if plotted > 0:
                fig.suptitle("최근 7일 자산별 종가 추이", y=1.02)
                fig.tight_layout()

                chart_filename = f"weekly_prices_{timestamp}.png"
                fig.savefig(chart_filename, dpi=150, bbox_inches="tight")
                plt.close(fig)
                print(f"🖼️ 그래프 저장: {chart_filename}")
    except Exception as e:
        print(f"⚠️ 그래프 저장 실패: {str(e)}")

def build_app():
    from flask import Flask, request, jsonify, Response

    app = Flask(__name__)
    assets_json = json.dumps(ASSETS, ensure_ascii=False)
    asset_filters_html = "".join(
        [
            f"<div class=\"asset-toggle\"><input type=\"checkbox\" id=\"asset-{html_lib.escape(ticker)}\" data-ticker=\"{html_lib.escape(ticker)}\" checked><label for=\"asset-{html_lib.escape(ticker)}\"><span>{html_lib.escape(name)}</span><span class=\"asset-ticker\">{html_lib.escape(ticker)}</span></label></div>"
            for name, ticker in ASSETS.items()
        ]
    )

    html = """
<!doctype html>
<html lang="ko">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>자산 종가 대시보드</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>
      :root {
        --bg: #0c0f14;
        --bg-elev: #141a24;
        --bg-card: #171f2b;
        --border: #263244;
        --text: #e6edf3;
        --muted: #96a1b1;
        --accent: #4dd4ff;
        --accent-2: #ff8aa3;
        --good: #4ade80;
        --bad: #f87171;
      }

      * { box-sizing: border-box; }

      body {
        margin: 0;
        font-family: "IBM Plex Sans", "Pretendard", "Noto Sans KR", sans-serif;
        color: var(--text);
        background: radial-gradient(circle at top right, #1b2230, #0c0f14 60%);
      }

      .page {
        min-height: 100vh;
        padding: 32px 20px 56px;
      }

      header {
        max-width: 1180px;
        margin: 0 auto 24px;
      }

      h1 {
        margin: 0 0 8px;
        font-weight: 600;
        letter-spacing: 0.2px;
      }

      .subtitle {
        color: var(--muted);
        margin: 0 0 18px;
      }

      .controls {
        display: grid;
        gap: 12px;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        background: var(--bg-elev);
        border: 1px solid var(--border);
        padding: 16px;
        border-radius: 14px;
      }

      .button-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }

      button {
        background: #1f2a3a;
        color: var(--text);
        border: 1px solid var(--border);
        border-radius: 999px;
        padding: 8px 14px;
        cursor: pointer;
        transition: background 0.2s ease, border 0.2s ease;
      }

      button.active {
        background: var(--accent);
        color: #081019;
        border-color: transparent;
      }

      button:hover {
        background: #263244;
      }

      label {
        display: block;
        font-size: 13px;
        margin-bottom: 6px;
        color: var(--muted);
      }

      input[type="date"] {
        width: 100%;
        background: #111825;
        color: var(--text);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 8px 10px;
      }

      .asset-grid {
        display: grid;
        gap: 8px;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        max-height: 190px;
        overflow: auto;
      }

      .option-row {
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
        align-items: center;
      }

      .option-row label {
        margin: 0;
      }

      .option-row input[type="checkbox"] {
        accent-color: var(--accent);
      }

      .option-row input[type="number"] {
        width: 80px;
        background: #111825;
        color: var(--text);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 6px 8px;
      }

      .action-btn {
        background: #223041;
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 8px 12px;
        color: var(--text);
      }

      .asset-toggle input {
        display: none;
      }

      .asset-toggle label {
        display: flex;
        flex-direction: column;
        gap: 4px;
        padding: 8px 10px;
        background: #111825;
        border: 1px solid var(--border);
        border-radius: 10px;
        cursor: pointer;
        font-size: 13px;
        color: var(--text);
      }

      .asset-toggle input:checked + label {
        background: #233146;
        border-color: #3b4a61;
      }

      .asset-ticker {
        font-size: 11px;
        color: var(--muted);
      }

      .content {
        max-width: 1180px;
        margin: 24px auto 0;
        display: grid;
        gap: 18px;
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }

      .card {
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: 16px;
        padding: 16px 16px 12px;
        display: grid;
        gap: 8px;
      }

      .card header {
        margin: 0;
      }

      .asset-title {
        font-size: 18px;
        margin: 0;
      }

      .meta {
        display: flex;
        gap: 12px;
        font-size: 13px;
        color: var(--muted);
      }

      .price {
        font-size: 20px;
        font-weight: 600;
      }

      .change.good { color: var(--good); }
      .change.bad { color: var(--bad); }

      .hover-readout {
        font-size: 13px;
        color: var(--muted);
        min-height: 26px;
        line-height: 1.8;
        padding: 4px 0;
        letter-spacing: 0.3px;
      }

      .status {
        max-width: 1180px;
        margin: 16px auto 0;
        color: var(--muted);
        display: inline-flex;
        align-items: center;
        gap: 8px;
        justify-content: center;
        width: 100%;
      }

      .spinner {
        width: 14px;
        height: 14px;
        border-radius: 50%;
        border: 2px solid rgba(255, 255, 255, 0.15);
        border-top-color: var(--accent);
        animation: spin 1s linear infinite;
        display: none;
      }

      .status.loading .spinner {
        display: inline-block;
      }

      @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
      }

      .skeleton-line,
      .skeleton-chart {
        position: relative;
        overflow: hidden;
        background: #1c2634;
        border-radius: 10px;
      }

      .skeleton-line::after,
      .skeleton-chart::after {
        content: "";
        position: absolute;
        top: 0;
        left: -150px;
        width: 150px;
        height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.08), transparent);
        animation: shimmer 1.4s infinite;
      }

      .skeleton-line {
        height: 14px;
        width: 60%;
      }

      .skeleton-line.sm {
        width: 40%;
      }

      .skeleton-line.lg {
        height: 20px;
        width: 70%;
      }

      .skeleton-chart {
        height: 240px;
      }

      @keyframes shimmer {
        0% { transform: translateX(0); }
        100% { transform: translateX(300%); }
      }

      .chart-wrap {
        height: 240px;
      }

      canvas {
        display: block;
        width: 100% !important;
        height: 100% !important;
      }

      @media (max-width: 640px) {
        h1 { font-size: 22px; }
        .page { padding: 20px 14px 40px; }
        .content { grid-template-columns: 1fr; }
      }
    </style>
  </head>
  <body>
    <div class="page">
      <header>
        <h1>자산 종가 대시보드</h1>
        <p class="subtitle">기간을 선택하면 최근 종가 추이를 확인할 수 있습니다.</p>
        <div class="controls">
          <div>
            <label>빠른 선택</label>
            <div class="button-row" id="period-buttons">
              <button data-period="7d" class="active" onclick="applyQuickSelect('7d')">7거래일</button>
              <button data-period="1mo" onclick="applyQuickSelect('1mo')">1개월</button>
              <button data-period="3mo" onclick="applyQuickSelect('3mo')">3개월</button>
              <button data-period="6mo" onclick="applyQuickSelect('6mo')">6개월</button>
              <button data-period="1y" onclick="applyQuickSelect('1y')">1년</button>
            </div>
          </div>
          <div>
            <label>시작일</label>
            <input type="date" id="start-date" />
          </div>
          <div>
            <label>종료일</label>
            <input type="date" id="end-date" />
          </div>
          <div>
            <label>&nbsp;</label>
            <button id="apply-range">기간 적용</button>
          </div>
          <div>
            <label>자산 선택</label>
            <div class="asset-grid" id="asset-filters">__ASSET_FILTERS__</div>
          </div>
          <div>
            <label>옵션</label>
            <div class="option-row">
              <label><input type="checkbox" id="toggle-ma" /> 이동평균</label>
              <input type="number" id="ma-window" min="2" max="60" value="5" />
              <button class="action-btn" id="export-csv">CSV 내보내기</button>
              <button class="action-btn" id="refresh-data">갱신</button>
            </div>
          </div>
        </div>
      </header>

      <div class="status" id="status"><span class="status-text">캐시에서 불러오는 중</span><span class="spinner" aria-hidden="true"></span></div>
      <section class="content" id="cards"></section>
    </div>

    <script>
      const cardsEl = document.getElementById("cards");
      const statusEl = document.getElementById("status");
      const periodButtons = document.getElementById("period-buttons");
      const startInput = document.getElementById("start-date");
      const endInput = document.getElementById("end-date");
        const applyRange = document.getElementById("apply-range");
      const assetFiltersEl = document.getElementById("asset-filters");
      const toggleMa = document.getElementById("toggle-ma");
      const maWindowInput = document.getElementById("ma-window");
      const exportCsvBtn = document.getElementById("export-csv");
      const refreshBtn = document.getElementById("refresh-data");

      const ASSETS = __ASSETS__;

      const charts = new Map();
      let currentMode = "period";
      let currentPeriod = "7d";
      let currentStart = "";
      let currentEnd = "";
      let currentTradingDays = 7;
      let lastPayload = null;

      function setStatus(message) {
        const textEl = statusEl.querySelector(".status-text");
        if (textEl) {
          textEl.textContent = message;
        } else {
          statusEl.textContent = message;
        }
      }

      function clearActiveButtons() {
        periodButtons.querySelectorAll("button").forEach(btn => btn.classList.remove("active"));
      }

      function setActiveButton(period) {
        const match = periodButtons.querySelector(`button[data-period="${period}"]`);
        if (match) {
          match.classList.add("active");
        }
      }

      function formatDateInput(dateObj) {
        const year = dateObj.getFullYear();
        const month = String(dateObj.getMonth() + 1).padStart(2, "0");
        const day = String(dateObj.getDate()).padStart(2, "0");
        return `${year}-${month}-${day}`;
      }

      function addMonths(dateObj, months) {
        const next = new Date(dateObj);
        const day = next.getDate();
        next.setDate(1);
        next.setMonth(next.getMonth() + months);
        const daysInMonth = new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate();
        next.setDate(Math.min(day, daysInMonth));
        return next;
      }

      function setQuickRangeInputs(period) {
        const end = new Date();
        let start;
        if (period === "7d") {
          start = new Date(end);
          start.setDate(start.getDate() - 7);
        } else if (period === "1mo") {
          start = addMonths(end, -1);
        } else if (period === "3mo") {
          start = addMonths(end, -3);
        } else if (period === "6mo") {
          start = addMonths(end, -6);
        } else if (period === "1y") {
          start = addMonths(end, -12);
        } else {
          return;
        }
        const startValue = formatDateInput(start);
        const endValue = formatDateInput(end);
        startInput.value = startValue;
        endInput.value = endValue;
        startInput.valueAsDate = start;
        endInput.valueAsDate = end;
        console.info("[dashboard] quick range set", {
          period,
          start: startValue,
          end: endValue
        });
      }

      function applyQuickSelect(selectedPeriod) {
        clearActiveButtons();
        setActiveButton(selectedPeriod);
        setQuickRangeInputs(selectedPeriod);
        if (selectedPeriod === "7d") {
          currentMode = "trading";
          currentTradingDays = 7;
          currentPeriod = "7d";
        } else {
          currentMode = "period";
          currentPeriod = selectedPeriod;
          currentTradingDays = 0;
        }
        currentStart = "";
        currentEnd = "";
        applyFilters();
      }

      window.applyQuickSelect = applyQuickSelect;

      function formatNumber(value, ticker) {
        if (ticker === "KRW=X") {
          return `KRW ${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
        }
        if (ticker === "^IXIC" || ticker === "^GSPC") {
          return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
        }
        return `$${value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      }

      function destroyCharts() {
        charts.forEach(chart => chart.destroy());
        charts.clear();
      }

      function safeStorageGet(key) {
        try {
          return localStorage.getItem(key);
        } catch (error) {
          return null;
        }
      }

      function safeStorageSet(key, value) {
        try {
          localStorage.setItem(key, value);
        } catch (error) {
          return;
        }
      }

      function saveSelectedTickers(tickers) {
        safeStorageSet("selectedAssets", JSON.stringify(tickers));
      }

      function loadSelectedTickers() {
        const raw = safeStorageGet("selectedAssets");
        if (!raw) return null;
        try {
          const parsed = JSON.parse(raw);
          return Array.isArray(parsed) ? parsed : null;
        } catch (error) {
          return null;
        }
      }

      function saveOptions() {
        safeStorageSet("showMA", toggleMa.checked ? "1" : "0");
        safeStorageSet("maWindow", maWindowInput.value);
      }

      function loadOptions() {
        toggleMa.checked = safeStorageGet("showMA") === "1";
        const savedWindow = parseInt(safeStorageGet("maWindow"), 10);
        if (!Number.isNaN(savedWindow)) {
          maWindowInput.value = Math.min(60, Math.max(2, savedWindow));
        }
      }

      function computeMovingAverage(values, windowSize) {
        const result = [];
        for (let i = 0; i < values.length; i += 1) {
          if (i + 1 < windowSize) {
            result.push(null);
            continue;
          }
          const slice = values.slice(i + 1 - windowSize, i + 1);
          const sum = slice.reduce((acc, value) => acc + value, 0);
          result.push(sum / windowSize);
        }
        return result;
      }

      function renderSkeleton(count) {
        destroyCharts();
        cardsEl.innerHTML = "";
        const total = Math.max(1, Math.min(count, 6));
        for (let i = 0; i < total; i += 1) {
          const card = document.createElement("article");
          card.className = "card";
          card.innerHTML = `
            <div class="skeleton-line lg"></div>
            <div class="skeleton-line sm"></div>
            <div class="skeleton-line"></div>
            <div class="skeleton-chart"></div>
          `;
          cardsEl.appendChild(card);
        }
      }

      function buildCard(asset) {
        const card = document.createElement("article");
        card.className = "card";
        const changeClass = asset.change_pct === null ? "" : (asset.change_pct >= 0 ? "good" : "bad");
        const changeSymbol = asset.change_pct === null ? "N/A" : `${asset.change_pct >= 0 ? "▲" : "▼"}${asset.change_pct.toFixed(2)}%`;

        card.innerHTML = `
          <header>
            <h2 class="asset-title">${asset.name}</h2>
            <div class="meta"><span>${asset.ticker}</span><span>${asset.dates[0]} ~ ${asset.dates[asset.dates.length - 1]}</span></div>
          </header>
          <div class="price">${formatNumber(asset.current, asset.ticker)}</div>
          <div class="change ${changeClass}">${changeSymbol}</div>
          <div class="hover-readout">포인트에 마우스를 올리면 값 표시</div>
          <div class="chart-wrap"><canvas></canvas></div>
        `;

        return card;
      }

      function renderCharts(assets) {
        destroyCharts();
        cardsEl.innerHTML = "";

        const showMA = toggleMa.checked;
        const maWindow = Math.min(60, Math.max(2, parseInt(maWindowInput.value, 10) || 5));

        assets.forEach((asset, index) => {
          const card = buildCard(asset);
          const canvas = card.querySelector("canvas");
          cardsEl.appendChild(card);

          const palette = [
            "#4dd4ff", "#ff8aa3", "#ffd166", "#6ee7b7", "#a78bfa",
            "#fca5a5", "#93c5fd", "#fbbf24", "#34d399", "#f472b6"
          ];
          const color = palette[index % palette.length];
          const datasets = [{
            label: asset.name,
            data: asset.close,
            borderColor: color,
            backgroundColor: "rgba(0,0,0,0)",
            pointRadius: 3,
            pointHoverRadius: 5,
            pointBackgroundColor: color,
            tension: 0.3
          }];

          if (showMA) {
            const maColor = "rgba(255, 255, 255, 0.65)";
            datasets.push({
              label: `${maWindow}일 이동평균`,
              data: computeMovingAverage(asset.close, maWindow),
              borderColor: maColor,
              backgroundColor: "rgba(0,0,0,0)",
              pointRadius: 0,
              borderDash: [6, 6],
              tension: 0.25
            });
          }

          const chart = new Chart(canvas, {
            type: "line",
            data: {
              labels: asset.dates,
              datasets
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              interaction: { mode: "nearest", intersect: false },
              plugins: {
                legend: { display: showMA },
                tooltip: {
                  callbacks: {
                    label: (ctx) => formatNumber(ctx.parsed.y, asset.ticker)
                  }
                }
              },
              scales: {
                x: {
                  ticks: { color: "#96a1b1" },
                  grid: { color: "rgba(255,255,255,0.05)" }
                },
                y: {
                  ticks: {
                    color: "#96a1b1",
                    callback: (value) => formatNumber(value, asset.ticker)
                  },
                  grid: { color: "rgba(255,255,255,0.05)" }
                }
              }
            }
          });
          const hoverReadout = card.querySelector(".hover-readout");
          const defaultHoverText = "포인트에 마우스를 올리면 값 표시";
          const updateHover = (event) => {
            const points = chart.getElementsAtEventForMode(event, "nearest", { intersect: false }, true);
            if (!points.length) {
              hoverReadout.textContent = defaultHoverText;
              return;
            }
            const primaryPoint = points.find(point => point.datasetIndex === 0) || points[0];
            const idx = primaryPoint.index;
            const dateLabel = asset.dates[idx];
            const priceValue = asset.close[idx];
            hoverReadout.textContent = `${dateLabel} · ${formatNumber(priceValue, asset.ticker)}`;
          };
          canvas.addEventListener("mousemove", updateHover);
          canvas.addEventListener("mouseleave", () => {
            hoverReadout.textContent = defaultHoverText;
          });
          charts.set(asset.ticker, chart);
        });
      }

      async function loadData(params) {
        const selectedTickers = getSelectedTickers();
        const fallbackCount = selectedTickers.length || Object.keys(ASSETS).length;
        const isAutoLive = params.auto_live === "true";

        console.info("[dashboard] fetch start", params);
        const isStale = params.stale === "true";
        if (isStale) {
          setStatus("캐시에서 불러오는 중");
          statusEl.classList.remove("loading");
        } else {
          setStatus("데이터를 불러오는 중");
          statusEl.classList.add("loading");
        }
        renderSkeleton(fallbackCount);
        try {
          const query = new URLSearchParams(params);
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 20000);
          const response = await fetch(`/api/prices?${query.toString()}`, {
            signal: controller.signal
          });
          clearTimeout(timeoutId);
          if (!response.ok) {
            throw new Error("데이터 요청 실패");
          }
          const payload = await response.json();
          if (!payload.assets || payload.assets.length === 0) {
            if (isStale && !isAutoLive) {
              setStatus("캐시가 없습니다. 실시간 데이터를 불러오는 중입니다...");
              const nextParams = { ...params, auto_live: "true" };
              delete nextParams.stale;
              loadData(nextParams);
              return;
            }
            if (isStale) {
              setStatus("캐시가 없습니다. 갱신 버튼을 눌러주세요.");
            } else {
              setStatus("표시할 데이터가 없습니다.");
            }
            cardsEl.innerHTML = "";
            destroyCharts();
            return;
          }
          lastPayload = payload;
          renderCharts(payload.assets);
          statusEl.classList.remove("loading");
          setStatus(`자산 ${payload.assets.length}개 표시 중 (${payload.meta.range_label})`);
          console.info("[dashboard] fetch success", {
            assets: payload.assets.length,
            range: payload.meta.range_label,
            stale: payload.meta.stale,
            source: payload.meta.source
          });
        } catch (error) {
          statusEl.classList.remove("loading");
          if (error.name === "AbortError") {
            setStatus("응답이 지연되고 있습니다. 네트워크 상태 또는 서버 로그를 확인하세요.");
            console.warn("[dashboard] fetch timeout", params);
          } else {
            setStatus("데이터를 불러오지 못했습니다.");
            console.error("[dashboard] fetch error", error);
          }
          cardsEl.innerHTML = "";
          destroyCharts();
        }
      }

      function renderAssetFilters() {
        const saved = loadSelectedTickers();
        if (assetFiltersEl.children.length === 0) {
          Object.entries(ASSETS).forEach(([name, ticker]) => {
            const wrapper = document.createElement("div");
            wrapper.className = "asset-toggle";
            const id = `asset-${ticker.replace(/[^a-zA-Z0-9]/g, "-")}`;
            const isChecked = !saved || saved.includes(ticker);
            wrapper.innerHTML = `
              <input type="checkbox" id="${id}" data-ticker="${ticker}" ${isChecked ? "checked" : ""}>
              <label for="${id}">
                <span>${name}</span>
                <span class="asset-ticker">${ticker}</span>
              </label>
            `;
            assetFiltersEl.appendChild(wrapper);
          });
        } else if (saved) {
          Array.from(assetFiltersEl.querySelectorAll("input[type=checkbox]")).forEach(input => {
            input.checked = saved.includes(input.dataset.ticker);
          });
        }
      }

      function getSelectedTickers() {
        return Array.from(assetFiltersEl.querySelectorAll("input:checked")).map(input => input.dataset.ticker);
      }

      function exportToCsv() {
        if (!lastPayload || !lastPayload.assets || lastPayload.assets.length === 0) {
          setStatus("내보낼 데이터가 없습니다.");
          return;
        }
        const rows = [];
        rows.push(["자산", "티커", "날짜", "종가"]);
        lastPayload.assets.forEach(asset => {
          asset.dates.forEach((date, idx) => {
            rows.push([asset.name, asset.ticker, date, asset.close[idx]]);
          });
        });
        const csv = rows.map(row => row.map(value => `"${String(value).replace(/"/g, '""')}"`).join(",")).join("\\n");
        const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        const stamp = new Date().toISOString().slice(0, 10);
        link.href = url;
        link.download = `asset_prices_${stamp}.csv`;
        link.click();
        URL.revokeObjectURL(url);
      }

      function buildParams() {
        const params = {};
        if (currentMode === "range") {
          params.start = currentStart;
          params.end = currentEnd;
        } else if (currentMode === "trading") {
          params.trading_days = currentTradingDays;
        } else {
          params.period = currentPeriod;
        }
        const selected = getSelectedTickers();
        if (selected.length) {
          params.assets = selected.join(",");
        }
        return params;
      }

      function applyFilters() {
        const selected = getSelectedTickers();
        if (!selected.length) {
          setStatus("자산을 선택하세요.");
          cardsEl.innerHTML = "";
          destroyCharts();
          return;
        }
        const params = buildParams();
        params.stale = "true";
        loadData(params);
      }

      periodButtons.addEventListener("click", (event) => {
        const button = event.target.closest("button");
        if (!button) return;
        applyQuickSelect(button.dataset.period);
      });

      applyRange.addEventListener("click", () => {
        const start = startInput.value;
        const end = endInput.value;
        if (!start || !end) {
          setStatus("시작일과 종료일을 모두 입력하세요.");
          return;
        }
        clearActiveButtons();
        currentMode = "range";
        currentStart = start;
        currentEnd = end;
        currentTradingDays = 0;
        applyFilters();
      });

      assetFiltersEl.addEventListener("change", (event) => {
        if (event.target && event.target.matches("input[type=checkbox]")) {
          saveSelectedTickers(getSelectedTickers());
          applyFilters();
        }
      });

      toggleMa.addEventListener("change", () => {
        saveOptions();
        if (lastPayload) {
          renderCharts(lastPayload.assets);
        }
      });

      maWindowInput.addEventListener("change", () => {
        saveOptions();
        if (lastPayload) {
          renderCharts(lastPayload.assets);
        }
      });

      exportCsvBtn.addEventListener("click", exportToCsv);
      refreshBtn.addEventListener("click", () => {
        const params = buildParams();
        params.refresh = "true";
        delete params.stale;
        loadData(params);
      });

      renderAssetFilters();
      loadOptions();
      applyQuickSelect("7d");
    </script>
  </body>
</html>
    """

    html = html.replace("__ASSETS__", assets_json)
    html = html.replace("__ASSET_FILTERS__", asset_filters_html)

    @app.get("/")
    def index():
        return Response(html, mimetype="text/html")

    @app.get("/api/prices")
    def api_prices():
        period = request.args.get("period")
        start = request.args.get("start")
        end = request.args.get("end")
        assets_filter = request.args.get("assets")
        refresh = request.args.get("refresh")
        trading_days = request.args.get("trading_days")

        stale = request.args.get("stale")

        log_message("📥 API 요청 수신", {
            "period": period,
            "start": start,
            "end": end,
            "assets": assets_filter,
            "refresh": refresh,
            "trading_days": trading_days,
            "stale": stale
        })

        if assets_filter:
            requested = {item.strip() for item in assets_filter.split(",") if item.strip()}
            assets_map = {name: ticker for name, ticker in ASSETS.items() if ticker in requested}
        else:
            assets_map = ASSETS

        use_start_end = bool(start and end)
        use_trading_days = False
        trading_days_value = None
        if trading_days:
            try:
                trading_days_value = max(1, int(trading_days))
                use_trading_days = True
            except ValueError:
                use_trading_days = False

        if stale == "true":
            _, cached_data, cached_dates, cached_changes = fetch_asset_history(
                assets_map,
                period=None if (use_start_end or use_trading_days) else (period or "7d"),
                start=start if use_start_end else None,
                end=end if use_start_end else None,
                force_refresh=False,
                trading_days=trading_days_value if use_trading_days else None,
                stale_only=True
            )
            cached_payload = build_assets_payload(assets_map, cached_data, cached_dates, cached_changes)
            if cached_payload:
                if use_trading_days:
                    range_label = f"최근 {trading_days_value} 거래일"
                else:
                    range_label = "기간 지정" if use_start_end else (period or "7d")
                return jsonify({
                    "assets": cached_payload,
                    "meta": {
                        "start": start,
                        "end": end,
                        "period": None if (use_start_end or use_trading_days) else (period or "7d"),
                        "trading_days": trading_days_value if use_trading_days else None,
                        "range_label": range_label,
                        "stale": True,
                        "source": "cache"
                    }
                })
            return jsonify({
                "assets": [],
                "meta": {
                    "start": start,
                    "end": end,
                    "period": None if (use_start_end or use_trading_days) else (period or "7d"),
                    "trading_days": trading_days_value if use_trading_days else None,
                    "range_label": "캐시 없음",
                    "stale": True,
                    "source": "cache"
                }
            })

        _, chart_data, chart_dates, chart_changes = fetch_asset_history(
            assets_map,
            period=None if (use_start_end or use_trading_days) else (period or "7d"),
            start=start if use_start_end else None,
            end=end if use_start_end else None,
            force_refresh=refresh == "true",
            trading_days=trading_days_value if use_trading_days else None
        )
        assets_payload = build_assets_payload(assets_map, chart_data, chart_dates, chart_changes)

        if use_trading_days:
            range_label = f"최근 {trading_days_value} 거래일"
        else:
            range_label = "기간 지정" if use_start_end else (period or "7d")
        return jsonify({
            "assets": assets_payload,
            "meta": {
                "start": start,
                "end": end,
                "period": None if (use_start_end or use_trading_days) else (period or "7d"),
                "trading_days": trading_days_value if use_trading_days else None,
                "range_label": range_label,
                "stale": False,
                "source": "live"
            }
        })

    @app.get("/api/health")
    def api_health():
        return jsonify({"status": "ok"})

    @app.get("/favicon.ico")
    def favicon():
        return ("", 204)

    return app


def main():
    """메인 함수"""
    print("🚀 자산별 일주일 종가 조회 시작")
    print("="*50)

    load_env_file()
    default_host = os.getenv("WEB_HOST", "0.0.0.0")
    try:
        default_port = int(os.getenv("WEB_PORT", "5000"))
    except ValueError:
        default_port = 5000

    parser = argparse.ArgumentParser(description="자산별 종가 조회")
    parser.add_argument("--web", action="store_true", help="웹 대시보드 실행")
    parser.add_argument("--host", default=default_host, help="웹 호스트 (기본 .env 또는 0.0.0.0)")
    parser.add_argument("--port", type=int, default=default_port, help="웹 포트 (기본 .env 또는 5000)")
    parser.add_argument("--db-stats", action="store_true", help="캐시 DB 요약 출력")
    args = parser.parse_args()

    if args.db_stats:
        stats = get_db_stats("data/prices.db")
        if not stats:
            print("ℹ️ DB에 저장된 데이터가 없습니다.")
            return
        print("📦 캐시 DB 현황")
        for ticker, rows, min_date, max_date in stats:
            print(f"- {ticker}: {rows}개 ({min_date} ~ {max_date})")
        return

    if args.web:
        app = build_app()
        display_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
        print(f"🌐 웹 서버 실행: http://{display_host}:{args.port}")
        app.run(host=args.host, port=args.port, debug=False)
        return

    try:
        get_weekly_closing_prices()
    except KeyboardInterrupt:
        print("\n⏹️  사용자에 의해 중단되었습니다.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 심각한 오류 발생: {str(e)}")
        sys.exit(1)
    
    print("\n✅ 조회 완료!")

if __name__ == "__main__":
    main()

import sys
import traceback

# 強制主控台輸出使用 UTF-8，避免 Windows 預設 cp950 編碼遇到 emoji（例如 🎉）
# 或其他非中文標準字元時直接崩潰
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def show_error_and_wait(exc_type, exc_value, exc_traceback):
    print("\n" + "="*60)
    print("程式發生錯誤，即將結束：")
    traceback.print_exception(exc_type, exc_value, exc_traceback)
    print("="*60)
    input("\n按 Enter 鍵關閉視窗...")
    sys.exit(1)

sys.excepthook = show_error_and_wait

"""
inline_checker.py  -  Inline 訂位監控程式
===========================================================
執行此程式後，會先讓你選擇：
  1. 要監控的訂位網址（可輸入新網址，或從曾經用過的網址紀錄中選擇）
  2. 用餐人數（大人），直接按 Enter 則使用預設 2 位
  3. 掃描模式：
     [日期模式] 掃描月曆，找出哪些日期還有位置可訂
     [時段模式] 鎖定某一天，監看當天還有哪些時段可訂
接著會自動彈出一個 Chrome 視窗並開啟該訂位頁面。
如果有遇到「人機驗證 (CAPTCHA)」，請在該視窗中手動點擊通過。
通過後，程式就會在背景（或該視窗中）定期自動幫你檢查是否有位置！

在每次檢查之間的等待時間內，只要按下 M 鍵，就能隨時跳回「選擇掃描模式」，
不需要整個關閉重開程式（網址、瀏覽器分頁都會保留）。

【本版變更】
新增：啟動時可以自行輸入用餐人數（大人），不選則預設為 2 位。
日期模式改回用 Inline 頁面上 data-cy="date-picker" / data-cy="bt-cal-day"
這組測試屬性去讀月曆格子（data-date 屬性 + disabled 屬性判斷可訂與否），
比用 class 名稱猜「時段按鈕」穩定，改版時比較不容易失效。
新增：等待期間按 M 鍵可隨時切換掃描模式（日期模式 / 時段模式），不必重開程式。
===========================================================
"""
import time
import random
import re
import json
import winsound
import os
import threading
import msvcrt
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

try:
    from win11toast import toast
    HAS_TOAST = True
except ImportError:
    HAS_TOAST = False
    print("⚠️ 尚未安裝 win11toast，將不會跳出系統通知。")
    print("   請執行: pip install win11toast")

# ─── 設定 ──────────────────────────────────────────────────────────────────────
SCAN_INTERVAL_MIN = 300   # 每次檢查間隔的下限秒數 (5分鐘)
SCAN_INTERVAL_MAX = 600   # 每次檢查間隔的上限秒數 (10分鐘)，實際間隔會在區間內隨機
PAGE_WAIT_MS    = 4000    # 每次刷新後等待 JS 的毫秒數
USER_DATA_DIR   = r"C:\chrome_debug_inline_v2"  # 改用新資料夾避免鎖定問題
DEFAULT_PARTY_SIZE = 2     # 用餐人數（大人）的預設值，啟動時可另外輸入覆蓋
MIN_PARTY_SIZE  = 1
MAX_PARTY_SIZE  = 20       # 依 Inline 頁面選單實際上限調整即可
NAV_TIMEOUT_MS  = 60000   # 導覽逾時（毫秒），太短容易在網路較慢時失敗
DEBUG_DUMP_ON_EMPTY = True  # 找不到任何日期/時段時，自動存一份頁面 HTML 方便排查
SWITCH_MODE_KEY = "m"     # 等待期間按下此鍵可跳回選擇模式

# 網址歷史紀錄檔（跟本程式放在同一個資料夾）
def get_base_dir():
    """取得程式所在資料夾，無論是直接執行 .py 還是打包成 .exe。"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包後執行：用 exe 檔案本身所在的資料夾
        # （而不是 --onefile 展開用的暫存資料夾 sys._MEIPASS）
        return os.path.dirname(sys.executable)
    else:
        # 直接執行 .py 檔案
        return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()

# 網址歷史紀錄檔（跟 exe / py 放在同一個資料夾）
HISTORY_FILE = os.path.join(BASE_DIR, "url_history.json")
DEBUG_DIR = os.path.join(BASE_DIR, "debug_dumps")
# ──────────────────────────────────────────────────────────────────────────────


def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def alert():
    for _ in range(5):
        winsound.Beep(1000, 400)
        time.sleep(0.15)


def notify(title, message):
    """跳出 Windows 系統通知（右下角提示氣泡）。"""
    if not HAS_TOAST:
        return

    def _send():
        try:
            toast(
                title,
                message,
                duration="short",
                tag="inline_checker_status",
                group="inline_checker",
            )
        except Exception as e:
            print(f"[{ts()}]   ⚠️ 系統通知失敗: {e}")

    threading.Thread(target=_send, daemon=True).start()


def format_dates_for_notify(dates, max_shown=4):
    """把日期清單（YYYY-MM-DD）濃縮成適合通知顯示的簡短文字，轉成 MM/DD。"""
    count = len(dates)
    shown = dates[:max_shown]
    formatted = []
    for d in shown:
        try:
            parts = d.split("-")
            if len(parts) == 3:
                formatted.append(f"{int(parts[1]):02d}/{int(parts[2]):02d}")
            else:
                formatted.append(d)
        except Exception:
            formatted.append(d)
    text = "、".join(formatted)
    if count > max_shown:
        text += f" 等共{count}天"
    else:
        text += f"（共{count}天）"
    return text


def format_slots_for_notify(slots, max_shown=6):
    """把時段清單濃縮成適合通知顯示的簡短文字。"""
    count = len(slots)
    shown = slots[:max_shown]
    text = "、".join(shown)
    if count > max_shown:
        text += f" 等共{count}個時段"
    else:
        text += f"（共{count}個時段）"
    return text


def next_check_time_str(wait_seconds):
    next_time = datetime.now() + timedelta(seconds=wait_seconds)
    return next_time.strftime("%H:%M:%S")


def dump_debug_snapshot(page, tag):
    """把目前頁面 HTML 存檔，方便在「抓不到資料」時直接檢查實際 DOM 結構。"""
    if not DEBUG_DUMP_ON_EMPTY:
        return
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        fname = os.path.join(
            DEBUG_DIR, f"{tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        )
        with open(fname, "w", encoding="utf-8") as f:
            f.write(page.content())
        print(f"[{ts()}]   🛠️ 已將目前頁面 HTML 存到: {fname}（可用瀏覽器打開檢查結構）")
    except Exception as e:
        print(f"[{ts()}]   ⚠️ 存 debug 頁面失敗: {e}")


def interruptible_sleep(wait_seconds, allow_switch=True):
    """
    等待 wait_seconds 秒，但每 0.2 秒檢查一次鍵盤緩衝區。
    如果 allow_switch 為 True 且使用者在等待期間按下 SWITCH_MODE_KEY（預設 M），
    會立刻結束等待並回傳 True，代表使用者要求跳回選擇模式畫面。
    正常等到時間到，回傳 False。
    """
    end_time = time.time() + wait_seconds
    while time.time() < end_time:
        if allow_switch:
            try:
                while msvcrt.kbhit():
                    key = msvcrt.getch()
                    try:
                        key_char = key.decode("utf-8", errors="ignore").lower()
                    except Exception:
                        key_char = ""
                    if key_char == SWITCH_MODE_KEY:
                        return True
            except Exception:
                # 非互動式主控台（例如某些打包環境）可能不支援 msvcrt，
                # 這種情況就直接退化成一般的 time.sleep。
                pass
        time.sleep(0.2)
    return False


# ─── 網址歷史紀錄（簡易 JSON 資料庫） ──────────────────────────────────────────
def load_url_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as e:
        print(f"[{ts()}] ⚠️ 讀取網址紀錄失敗，將視為沒有紀錄: {e}")
    return []


def save_url_history(history):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[{ts()}] ⚠️ 儲存網址紀錄失敗: {e}")


def add_url_to_history(history, url, label=None):
    now_str = ts()
    for item in history:
        if item.get("url") == url:
            item["last_used"] = now_str
            item["count"] = item.get("count", 1) + 1
            if label:
                item["label"] = label
            history.sort(key=lambda x: x.get("last_used", ""), reverse=True)
            save_url_history(history)
            return history

    history.insert(0, {
        "url": url,
        "label": label or "",
        "last_used": now_str,
        "count": 1,
    })
    save_url_history(history)
    return history


def choose_url():
    history = load_url_history()

    print("=" * 65)
    print("   選擇要監控的 Inline 訂位網址")
    print("=" * 65)

    if history:
        print("曾經使用過的網址：")
        for idx, item in enumerate(history, start=1):
            url = item.get("url", "")
            last_used = item.get("last_used", "")
            count = item.get("count", 1)
            label = item.get("label", "")
            label_str = f"「{label}」 " if label else ""
            print(f"  [{idx}] {label_str}{url}")
            print(f"        （上次使用: {last_used}，使用次數: {count}）")
        print(f"  [0] 輸入新的網址")
        print()

        while True:
            choice = input(
                "請輸入編號選擇網址（直接按 Enter 使用最近一次的網址）: "
            ).strip()

            if choice == "":
                chosen_url = history[0]["url"]
                break

            if choice == "0":
                chosen_url = input("請貼上新的訂位網址: ").strip()
                if not chosen_url:
                    print("⚠️ 網址不可為空，請重新輸入。\n")
                    continue
                break

            if choice.isdigit() and 1 <= int(choice) <= len(history):
                chosen_url = history[int(choice) - 1]["url"]
                break

            print("⚠️ 輸入無效，請重新輸入。\n")
    else:
        print("目前沒有使用紀錄，請輸入要監控的訂位網址。")
        while True:
            chosen_url = input("訂位網址: ").strip()
            if chosen_url:
                break
            print("⚠️ 網址不可為空，請重新輸入。\n")

    label = None
    is_new = not any(item.get("url") == chosen_url for item in history)
    if is_new:
        label = input("（選填）幫這個網址取個名稱，方便下次辨識，直接 Enter 略過: ").strip()

    history = add_url_to_history(history, chosen_url, label=label)
    print(f"\n[{ts()}] 已選擇網址: {chosen_url}\n")
    return chosen_url


def choose_party_size():
    """讓使用者輸入用餐人數（大人），直接按 Enter 則使用預設值。"""
    print("=" * 65)
    print("   設定用餐人數")
    print("=" * 65)
    while True:
        raw = input(
            f"請輸入用餐人數（大人，直接按 Enter 使用預設 {DEFAULT_PARTY_SIZE} 位）: "
        ).strip()
        if raw == "":
            print(f"\n[{ts()}] 已選擇人數: {DEFAULT_PARTY_SIZE} 位大人\n")
            return DEFAULT_PARTY_SIZE
        if raw.isdigit() and MIN_PARTY_SIZE <= int(raw) <= MAX_PARTY_SIZE:
            party_size = int(raw)
            print(f"\n[{ts()}] 已選擇人數: {party_size} 位大人\n")
            return party_size
        print(f"⚠️ 請輸入 {MIN_PARTY_SIZE}~{MAX_PARTY_SIZE} 之間的整數，或直接按 Enter 使用預設值。\n")


def choose_target_date():
    print()
    print("請輸入要監看時段的日期，格式為 YYYY-MM-DD（例如 2025-09-07）")
    while True:
        date_str = input("目標日期（直接按 Enter 使用今天）: ").strip()
        if date_str == "":
            return datetime.now().strftime("%Y-%m-%d")
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return date_str
        except ValueError:
            print("⚠️ 日期格式錯誤，請用 YYYY-MM-DD 格式重新輸入。\n")


def choose_mode():
    print("=" * 65)
    print("   選擇掃描模式")
    print("=" * 65)
    print("  [1] 日期模式：掃描月曆，找出哪些日期還有位置可訂")
    print("  [2] 時段模式：鎖定某一天，監看當天還有哪些時段可訂")
    print()
    while True:
        choice = input("請輸入編號選擇模式（直接按 Enter 預設為日期模式）: ").strip()
        if choice in ("", "1"):
            print(f"\n[{ts()}] 已選擇：日期模式\n")
            return "DATE", None
        if choice == "2":
            target_date = choose_target_date()
            print(f"\n[{ts()}] 已選擇：時段模式（鎖定日期 {target_date}）\n")
            return "TIME_SLOT", target_date
        print("⚠️ 輸入無效，請重新輸入。\n")


def handle_switch_request(page, current_url, party_size):
    """
    使用者在等待期間按下切換鍵後呼叫。
    先問要「只切換掃描模式」還是「重新選擇店家網址」，
    如果選擇重新選店家，會直接在同一個瀏覽器分頁導覽到新網址，
    並接著再選一次掃描模式（因為換了店家，原本鎖定的日期/時段不一定適用）。
    回傳 (新的 url, 新的 mode, 新的 target_date)。
    """
    print("=" * 65)
    print("   切換設定")
    print("=" * 65)
    print("  [1] 只切換掃描模式（日期模式 / 時段模式）")
    print("  [2] 重新選擇店家網址（會回到選擇店家畫面，接著再選一次模式）")
    print()
    choice = input("請輸入編號（直接按 Enter 預設為 1）: ").strip()

    if choice == "2":
        new_url = choose_url()
        print(f"[{ts()}] 前往新網址: {new_url}")
        try:
            page.goto(new_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            print(f"[{ts()}] ⚠️ 載入新網址失敗（{e}），嘗試重新整理一次...")
            page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(5000)
        select_party_size(page, party_size)
        page.wait_for_timeout(800)
        new_mode, new_target_date = choose_mode()
        return new_url, new_mode, new_target_date

    new_mode, new_target_date = choose_mode()
    return current_url, new_mode, new_target_date
# ──────────────────────────────────────────────────────────────────────────────


def select_party_size(page, party_size):
    """在頁面上找到「請選擇用餐人數」的選單，選擇對應人數（大人）。"""
    label = f"{party_size}位大人"

    try:
        selects = page.locator("select")
        count = selects.count()
        for i in range(count):
            sel = selects.nth(i)
            options_text = sel.evaluate(
                "el => Array.from(el.options).map(o => o.textContent.trim())"
            )
            if any(label in opt for opt in options_text):
                sel.select_option(label=[opt for opt in options_text if label in opt][0])
                print(f"[{ts()}]   已透過 <select> 選取人數: {label}")
                return True
    except Exception as e:
        print(f"[{ts()}]   嘗試 <select> 選人數時發生例外: {e}")

    try:
        trigger = page.get_by_text("請選擇用餐人數", exact=False).first
        if trigger.count() > 0:
            trigger.click()
            page.wait_for_timeout(500)
            option = page.get_by_text(label, exact=True).first
            if option.count() > 0:
                option.click()
                print(f"[{ts()}]   已透過自訂選單選取人數: {label}")
                return True
    except Exception as e:
        print(f"[{ts()}]   嘗試自訂選單選人數時發生例外: {e}")

    print(f"[{ts()}]   ⚠️ 找不到人數選單或選項「{label}」，請確認頁面結構，可能需要調整選擇器。")
    return False


# ─── 日期模式：沿用舊版已驗證可行的 data-cy 月曆選擇器邏輯 ────────────────────
def open_date_picker(page):
    """展開「用餐日期」的月曆選擇器。"""
    try:
        picker = page.locator('[data-cy="date-picker"]').first
        if picker.count() == 0:
            return False
        expanded = picker.get_attribute("aria-expanded")
        if expanded != "true":
            picker.click(timeout=3000)
            page.wait_for_timeout(500)
        return True
    except Exception as e:
        print(f"[{ts()}]   ⚠️ 展開日期選擇器失敗: {e}")
        return False


def get_clickable_calendar_days(page):
    """找出月曆上所有日期格。"""
    return page.locator('[data-cy="bt-cal-day"]')


def check_availability(page, party_size):
    """刷新頁面並檢查可訂日期（日期模式）。"""
    try:
        page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(PAGE_WAIT_MS)

        title = page.title()
        if any(x in title.lower() for x in ["denied", "captcha", "challenge", "access"]):
            print(f"[{ts()}] WARNING: 人機驗證頁 (標題={title})")
            return "CAPTCHA", []

        select_party_size(page, party_size)

        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        open_date_picker(page)
        page.wait_for_timeout(300)

        day_buttons = get_clickable_calendar_days(page)
        day_count = day_buttons.count()

        if day_count == 0:
            print(f"[{ts()}]   ⚠️ 沒找到月曆上的日期格，可能選擇器需要調整。")
            dump_debug_snapshot(page, "date_mode_no_cal_days")
            return "NOT_FOUND", []

        avail_dates = []
        for i in range(day_count):
            day_div = day_buttons.nth(i)
            date_str = day_div.get_attribute("data-date")
            if not date_str:
                continue
            if day_div.get_attribute("disabled") is None:
                avail_dates.append(date_str)

        # 補上目前預設選中的日期（月曆上顯示的當前選擇文字，通常沒有 disabled 標記可讀）
        try:
            picker_text = page.locator('[data-cy="date-picker"]').first.inner_text()
            m = re.search(r"(\d{1,2})月(\d{1,2})日", picker_text)
            if m:
                mm, dd = int(m.group(1)), int(m.group(2))
                today = datetime.now()
                year = today.year
                candidate = datetime(year, mm, dd)
                if candidate.date() < today.date():
                    year += 1
                selected_date_str = f"{year}-{mm:02d}-{dd:02d}"
                if selected_date_str not in avail_dates:
                    avail_dates.append(selected_date_str)
                    print(f"[{ts()}]   （補上目前預設選中日期: {selected_date_str}）")
        except Exception as e:
            print(f"[{ts()}]   ⚠️ 讀取目前選中日期失敗: {e}")

        avail_dates = sorted(set(avail_dates))
        print(f"[{ts()}]   共 {day_count} 個日期格 | 可訂日期={len(avail_dates)} 天")

        if avail_dates:
            return "FOUND", avail_dates

        dump_debug_snapshot(page, "date_mode_zero_available")
        return "NOT_FOUND", []

    except Exception as e:
        print(f"[{ts()}] ERROR: {e}")
        return "NOT_FOUND", []


# ─── 時段模式：掃描指定日期底下的時段按鈕 ──────────────────────────────────
def scan_time_slot_buttons(page):
    """直接掃描頁面上所有時段按鈕，取得每個按鈕的日期、時間、是否已滿。"""
    return page.evaluate("""
    () => {
        const results = [];
        const btns = Array.from(
            document.querySelectorAll('button[class*="time-slot"]')
        );

        btns.forEach(b => {
            const spans = Array.from(b.querySelectorAll('span'));
            let timeStr = '';
            let dateStr = '';

            spans.forEach(s => {
                const txt = (s.textContent || '').trim();
                if (s.hasAttribute('hidden')) {
                    if (txt && !dateStr) dateStr = txt;
                } else {
                    if (/^\\d{1,2}:\\d{2}$/.test(txt)) timeStr = txt;
                }
            });

            if (!timeStr || !dateStr) return;

            const isFull = b.className.toLowerCase().includes('full');
            results.push({ date: dateStr, time: timeStr, full: isFull });
        });

        return results;
    }
    """)


def ymd_to_zh_date_label(date_str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{dt.month}月{dt.day}日"


def check_time_slots(page, target_date_str, party_size):
    """刷新頁面，掃描所有時段按鈕，篩出指定日期（YYYY-MM-DD）目前可訂的時間點。"""
    try:
        page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        page.wait_for_timeout(PAGE_WAIT_MS)

        title = page.title()
        if any(x in title.lower() for x in ["denied", "captcha", "challenge", "access"]):
            print(f"[{ts()}] WARNING: 人機驗證頁 (標題={title})")
            return "CAPTCHA", []

        select_party_size(page, party_size)

        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        slots = scan_time_slot_buttons(page)

        if not slots:
            print(f"[{ts()}]   ⚠️ 沒找到任何時段按鈕，可能選擇器需要調整，或頁面尚未載入完成。")
            dump_debug_snapshot(page, "time_slot_mode_no_buttons")
            return "NOT_FOUND", []

        target_label = ymd_to_zh_date_label(target_date_str)
        day_slots = [s for s in slots if s["date"] == target_label]

        if not day_slots:
            available_labels = sorted({s["date"] for s in slots})
            shown = "、".join(available_labels) if available_labels else "（無）"
            print(f"[{ts()}]   ⚠️ 頁面上目前沒有 {target_label} 的時段（可能超出可訂範圍）。"
                  f" 目前頁面上有時段的日期：{shown}")
            return "NOT_FOUND", []

        avail_times = sorted(s["time"] for s in day_slots if not s["full"])
        print(f"[{ts()}]   {target_label} 共 {len(day_slots)} 個時段 | 可訂時段={len(avail_times)} 個")

        if avail_times:
            return "FOUND", avail_times

        return "NOT_FOUND", []

    except Exception as e:
        print(f"[{ts()}] ERROR: {e}")
        return "NOT_FOUND", []


def next_interval():
    return random.uniform(SCAN_INTERVAL_MIN, SCAN_INTERVAL_MAX)


def clear_stale_singleton_locks(user_data_dir):
    """
    清除 Chrome profile 資料夾裡殘留的 Singleton 鎖定檔。

    常見情況：上一次程式被強制關閉（或當機）、或曾經手動開著同一個
    --user-data-dir 的 Chrome 視窗，都會讓這個資料夾被視為「使用中」，
    導致下次啟動時出現：
      "Target page, context or browser has been closed"
    這類看似跟程式邏輯無關、但其實是 Chrome 啟動失敗的錯誤。
    """
    lock_files = ["SingletonLock", "SingletonCookie", "SingletonSocket"]
    removed_any = False
    for name in lock_files:
        path = os.path.join(user_data_dir, name)
        try:
            if os.path.exists(path) or os.path.islink(path):
                os.remove(path)
                removed_any = True
        except Exception as e:
            print(f"[{ts()}]   ⚠️ 清除殘留鎖定檔 {name} 失敗（可忽略，若稍後啟動失敗請手動處理）: {e}")
    if removed_any:
        print(f"[{ts()}] 已清除瀏覽器設定檔殘留的鎖定檔，避免「啟動失敗」問題。")


def main():
    print("=" * 65)
    print("   Inline 訂位監控程式")
    print("=" * 65)
    print()

    url = choose_url()
    party_size = choose_party_size()
    mode, target_date = choose_mode()

    print("瀏覽器只會開啟一次，之後在同一個分頁定期重新整理檢查，")
    print("不會每次都整個關閉重開。如果遇到人機驗證，視窗會保持開啟等你點擊通過！")
    print(f"提示：在等待檢查的期間，隨時按下「{SWITCH_MODE_KEY.upper()}」鍵可以切換掃描模式，或重新選擇店家網址，不必重開程式。")
    print()

    os.makedirs(USER_DATA_DIR, exist_ok=True)
    clear_stale_singleton_locks(USER_DATA_DIR)

    with sync_playwright() as pw:
        context = None
        try:
            print(f"[{ts()}] 正在啟動瀏覽器...")
            try:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=USER_DATA_DIR,
                    headless=False,
                    channel="chrome",
                    viewport={"width": 1280, "height": 900},
                    args=[
                        "--lang=zh-TW",
                        "--disable-blink-features=AutomationControlled",
                    ],
                    ignore_default_args=["--enable-automation"]
                )
            except Exception as e:
                print(f"[{ts()}] ❌ 啟動瀏覽器失敗: {e}")
                print()
                print("可能原因：已經有另一個 Chrome 視窗（或殘留的背景行程）")
                print(f"正在使用同一個設定檔資料夾：{USER_DATA_DIR}")
                print("請開啟工作管理員，結束所有 chrome.exe 行程後，再重新執行本程式。")
                raise

            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            page.set_default_timeout(NAV_TIMEOUT_MS)

            print(f"[{ts()}] 前往網頁: {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except Exception as e:
                print(f"[{ts()}] ⚠️ 第一次載入逾時/失敗（{e}），嘗試重新整理一次...")
                page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            page.wait_for_timeout(5000)

            select_party_size(page, party_size)
            page.wait_for_timeout(800)

            mode_desc = "日期模式" if mode == "DATE" else f"時段模式（鎖定 {target_date}）"
            print(f"[{ts()}] 目前模式：{mode_desc} | 人數：{party_size} 位大人")
            print()

            scan = 0
            while True:
                scan += 1
                print(f"[{ts()}] ── 第 {scan} 次檢查 ──")

                if mode == "TIME_SLOT":
                    status, results = check_time_slots(page, target_date, party_size)
                else:
                    status, results = check_availability(page, party_size)

                if status == "FOUND":
                    if mode == "TIME_SLOT":
                        summary = f"{target_date} 可訂時段：{format_slots_for_notify(results)}"
                    else:
                        summary = "可訂日期：" + format_dates_for_notify(results)

                    print(f"[{ts()}] 🎉 {summary}")
                    wait_s = next_interval()
                    alert()
                    notify(
                        "Inline 訂位提醒 🎉",
                        summary + f"\n下次檢查：{next_check_time_str(wait_s)}"
                    )

                elif status == "CAPTCHA":
                    print(f"[{ts()}] ⚠️ 偵測到驗證頁！請在瀏覽器視窗中手動完成驗證。")
                    print(f"[{ts()}] 30 秒後會自動重新檢查...（等待期間按「{SWITCH_MODE_KEY.upper()}」鍵可切換模式或店家）")
                    alert()
                    notify(
                        "Inline 監控提醒 ⚠️",
                        "偵測到人機驗證，請手動點擊通過"
                        + f"\n下次檢查：{next_check_time_str(30)}"
                    )
                    want_switch = interruptible_sleep(30)
                    if want_switch:
                        print(f"\n[{ts()}] 偵測到切換要求...\n")
                        url, mode, target_date = handle_switch_request(page, url, party_size)
                        mode_desc = "日期模式" if mode == "DATE" else f"時段模式（鎖定 {target_date}）"
                        print(f"[{ts()}] 目前設定：{mode_desc} | 店家: {url}\n")
                    continue

                else:
                    if mode == "TIME_SLOT":
                        print(f"[{ts()}] {target_date} 目前無可用時段。")
                    else:
                        print(f"[{ts()}] 目前無可用位置。")
                    wait_s = next_interval()

                print(f"[{ts()}] 下次檢查：{next_check_time_str(wait_s)}（等待期間按「{SWITCH_MODE_KEY.upper()}」鍵可切換模式或店家）")
                print()
                want_switch = interruptible_sleep(wait_s)
                if want_switch:
                    print(f"\n[{ts()}] 偵測到切換要求...\n")
                    url, mode, target_date = handle_switch_request(page, url, party_size)
                    mode_desc = "日期模式" if mode == "DATE" else f"時段模式（鎖定 {target_date}）"
                    print(f"[{ts()}] 目前設定：{mode_desc} | 店家: {url}\n")

        except KeyboardInterrupt:
            print()
            print(f"[{ts()}] 監控已停止")
        except Exception as e:
            print(f"[{ts()}] 發生錯誤: {e}")
        finally:
            if context:
                context.close()


if __name__ == "__main__":
    main()
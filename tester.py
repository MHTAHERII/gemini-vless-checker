import concurrent.futures
import json
import os
import subprocess
import time
from urllib.parse import parse_qs, unquote, urlsplit
import requests

# تنظیمات اصلی
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models?key=AIzaSyFakeKeyForTestingLocation123"
INPUT_FILE = "config.txt"      # فایل ورودی کانفیگ‌ها
OUTPUT_FILE = "gemini-configs.txt"    # فایل خروجی کانفیگ‌های سالم
TARGET_URL = "https://gemini.google.com/app"
MAX_WORKERS = 20                      # تعداد تست همزمان
TIMEOUT = 5                           # مهلت پاسخ (ثانیه)
BLOCKED_KEYWORDS = [
    "isn't supported in this country",
    "not supported in your country",
    "isn't available in your country",
    "not available in your country",
    "geo_unavailable",
    "location_unsupported",
    "country_unavailable",
    "location not supported",
]


# مسیر Xray (در ویندوز xray.exe و در لینوکس xray)
XRAY_BIN = "xray" if os.name != "nt" else r"C:\xray\xray.exe"


def vless_to_xray_config(vless_link: str, socks_port: int) -> dict:
    """تبدیل لینک VLESS به ساختار دیکشنری Xray"""
    parsed = urlsplit(vless_link.strip())
    if parsed.scheme != "vless":
        raise ValueError("Invalid VLESS link")

    uuid = parsed.username
    address = parsed.hostname
    port = parsed.port or 443
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    network = params.get("type", "tcp")
    security = params.get("security", "none")
    sni = params.get("sni", address)
    host_header = params.get("host", sni)
    path = unquote(params.get("path", "/"))
    fingerprint = params.get("fp", "chrome")

    stream_settings = {"network": network, "security": security}
    if security == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": sni,
            "fingerprint": fingerprint
        }
    if network == "ws":
        stream_settings["wsSettings"] = {
            "path": path,
            "headers": {"Host": host_header}
        }

    return {
        "log": {"loglevel": "none"},
        "inbounds": [{
            "listen": "127.0.0.1",
            "port": socks_port,
            "protocol": "socks",
            "settings": {"udp": True}
        }],
        "outbounds": [{
            "protocol": "vless",
            "settings": {
                "vnext": [{
                    "address": address,
                    "port": port,
                    "users": [{"id": uuid, "encryption": "none"}]
                }]
            },
            "streamSettings": stream_settings
        }]
    }

def test_single_config(item: tuple) -> str | None:
    index, link = item
    socks_port = 20000 + (index % 1000)
    config_file = f"temp_cfg_{socks_port}.json"
    try:
        cfg = vless_to_xray_config(link, socks_port)
    except Exception:
        return None
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    proc = None
    try:
        proc = subprocess.Popen(
            [XRAY_BIN, "run", "-config", config_file],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.4)
        proxies = {
            "http": f"socks5h://127.0.0.1:{socks_port}",
            "https": f"socks5h://127.0.0.1:{socks_port}",
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        # ارسال درخواست به API جمینای
        resp = requests.get(
            GEMINI_API_URL,
            proxies=proxies,
            headers=headers,
            timeout=TIMEOUT,
        )
        content = resp.text.lower()
        # ۱. اگر خطای عدم پشتیبانی لوکیشن داد -> رد
        if "location is not supported" in content or "user location" in content:
            print(f"🚫 [لوکیشن نامعتبر برای Gemini] کانفیگ {index}")
            return None
        # ۲. اگر کد 403 داد -> یعنی آی‌پی مسدود است
        # if resp.status_code == 403:
        #     print(f"❌ [403 Forbidden] کانفیگ {index}")
        #     return None
        # ۳. اگر کد 200 یا 400 (نیاز به کلید) داد و خطای لوکیشن نداد -> کاملاً سالم است!
        if resp.status_code == 400 and "api key not valid" in content:
            print(f"✅ [سالم و تأیید شده برای Gemini] کانفیگ شماره {index}")
            return link
            # ۳. اگر کد 200 داد
        if resp.status_code == 200:
            print(f"✅ [سالم] کانفیگ شماره {index}")
            return link
        print(f"❌ [عدم تأیید - کد {resp.status_code}] کانفیگ {index}")
        return None
    except Exception:
        print(f"⏳ [قطع / تایم‌اوت] کانفیگ شماره {index}")
        return None
    finally:
        if proc:
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass
        if os.path.exists(config_file):
            try:
                os.remove(config_file)
            except Exception:
                pass


def main():
    if not os.path.exists(INPUT_FILE):
        print(f"فایل {INPUT_FILE} پیدا نشد!")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        links = [line.strip() for line in f if line.strip().startswith("vless://")]

    print(f"🚀 شروع تست {len(links)} کانفیگ با {MAX_WORKERS} ترد همزمان...")
    start_time = time.time()

    valid_configs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        items = list(enumerate(links, start=1))
        results = executor.map(test_single_config, items)

        for res in results:
            if res:
                valid_configs.append(res)

    # ذخیره کانفیگ‌های سالم
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for cfg in valid_configs:
            f.write(cfg + "\n")

    elapsed = round(time.time() - start_time, 1)
    print("=" * 40)
    print(f"✨ پایان عملیات در {elapsed} ثانیه!")
    print(f"🎯 کانفیگ‌های مناسب Gemini: {len(valid_configs)} از {len(links)}")
    print(f"💾 ذخیره شد در: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
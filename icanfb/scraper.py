"""
sporekrani_scraper.py
Spor Ekranı'ndan Fenerbahçe maçlarını çekip Firebase'e kaydeder.
Yerel serviceAccountKey.json dosyasını kullanır.
"""

import os
import re
import json
import time
import random
import logging
import datetime

import requests
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, db

# ──────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# SABİT AYARLAR
# ──────────────────────────────────────────────
TARGET_URL   = "https://www.sporekrani.com/home/team/fenerbahce"
TEAM_KEYWORD = "fenerbahçe"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# ──────────────────────────────────────────────
# FIREBASE BAŞLATMA
# ──────────────────────────────────────────────
def init_firebase() -> None:
    try:
        # 1. Önce ortam değişkenini kontrol et (GitHub Actions için)
        sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT")
        db_url = "https://icanfb-default-rtdb.europe-west1.firebasedatabase.app/"

        if not firebase_admin._apps:
            if sa_json:
                # JSON metnini sözlüğe çevir
                cred_dict = json.loads(sa_json)
                cred = credentials.Certificate(cred_dict)
                log.info("Firebase bağlantısı ortam değişkeni üzerinden kuruldu.")
            else:
                # Yerel dosya kontrolü
                json_path = "serviceAccountKey.json"
                if not os.path.exists(json_path):
                    log.error(f"HATA: Ne ortam değişkeni ne de '{json_path}' bulundu!")
                    raise SystemExit(1)
                cred = credentials.Certificate(json_path)
                log.info("Firebase bağlantısı yerel dosya üzerinden kuruldu.")
            
            firebase_admin.initialize_app(cred, {"databaseURL": db_url})
    except Exception as e:
        log.error(f"Firebase başlatma hatası: {e}")
        raise SystemExit(1)

# ──────────────────────────────────────────────
# YARDIMCI FONKSİYONLAR
# ──────────────────────────────────────────────
def parse_date_to_utc(raw_time_str: str, day_header: str) -> str:
    istanbul_tz = datetime.timezone(datetime.timedelta(hours=3))
    now = datetime.datetime.now(istanbul_tz)
    
    TR_MONTHS = {
        "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
        "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12,
    }

    target_year = now.year
    target_month = now.month
    target_day = now.day

    header = day_header.lower()
    
    if "yarın" in header:
        tomorrow = now + datetime.timedelta(days=1)
        target_day, target_month, target_year = tomorrow.day, tomorrow.month, tomorrow.year
    elif "bugün" in header:
        pass # Zaten bugüne ayarlı
    else:
        # Format: 'CUMARTESİ · 2 Mayıs' veya '04.05.2026 Pazartesi'
        # Önce DD.MM.YYYY kontrolü
        m_dot = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", header)
        if m_dot:
            target_day, target_month, target_year = map(int, m_dot.groups())
        else:
            # '2 Mayıs' formatı kontrolü
            m_text = re.search(r"(\d{1,2})\s+([a-zçğıöşü]+)", header)
            if m_text:
                target_day = int(m_text.group(1))
                month_name = m_text.group(2)
                target_month = TR_MONTHS.get(month_name, now.month)
                # Eğer çekilen ay bugünden küçükse yıl atlamış olabiliriz (Örn: Aralık'tayken Ocak maçı)
                if target_month < now.month:
                    target_year += 1

    # Saati ayarla
    m_time = re.search(r"(\d{2}):(\d{2})", raw_time_str)
    hour, minute = (0, 0)
    if m_time:
        hour, minute = map(int, m_time.groups())
    
    try:
        dt = datetime.datetime(target_year, target_month, target_day, hour, minute, tzinfo=istanbul_tz)
        return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def fetch_page(url: str) -> BeautifulSoup | None:
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        log.error(f"Sayfa alınamadı: {exc}")
        return None

# ──────────────────────────────────────────────
# SCRAPING VE VERİ YAZMA
# ──────────────────────────────────────────────
def parse_and_save():
    soup = fetch_page(TARGET_URL)
    if not soup: return

    matches = []
    container = soup.select_one(".event-list")
    if not container:
        log.warning("Maç listesi konteynırı bulunamadı.")
        return

    current_day = "Bugün"
    
    for child in container.find_all(recursive=False):
        if "event-list__day-badge" in child.get("class", []):
            current_day = child.get_text(strip=True)
            continue
            
        if "event-list__row" in child.get("class", []):
            try:
                # Saat ve Tarih
                time_el = child.select_one(".event-list__time")
                raw_time = time_el.get_text(strip=True) if time_el else "00:00"
                date_iso = parse_date_to_utc(raw_time, current_day)
                
                # Başlık
                name_el = child.select_one(".event-list__name")
                match_name = name_el.get_text(strip=True) if name_el else "Fenerbahçe Maçı"
                
                # Rakip ve Ev Sahibi Kontrolü
                is_home = False
                opponent = match_name
                if " - " in match_name:
                    parts = match_name.split(" - ")
                    if "fenerbahçe" in parts[0].lower():
                        is_home = True
                        opponent = parts[1]
                    else:
                        is_home = False
                        opponent = parts[0]

                # Branş
                sport_img = child.select_one(".event-list__sport-icon")
                sport_raw = sport_img.get("alt", "").lower() if sport_img else "futbol"
                branch = "Football"
                if "basketbol" in sport_raw: branch = "Basketball"
                elif "voleybol" in sport_raw: branch = "Volleyball"

                # Maç Yeri (Venue) Tahmini
                venue = "Deplasman"
                if is_home:
                    if branch == "Football": venue = "Ülker Stadyumu"
                    elif branch == "Basketball": venue = "Ülker Spor ve Etkinlik Salonu"
                    elif branch == "Volleyball": venue = "Burhan Felek Voleybol Salonu"
                    else: venue = "Fenerbahçe Tesisleri"

                # Lig
                league_el = child.select_one(".event-list__league")
                league = league_el.get_text(strip=True) if league_el else "Lig Bilgisi Yok"
                
                # Kanal
                channel_img = child.select_one(".event-list__channel img")
                channel = channel_img.get("alt", "Kanal Belirsiz") if channel_img else "Yayın Yok"
                
                matches.append({
                    "match_id": f"fb_{date_iso}_{match_name.replace(' ', '_')}",
                    "branch": branch,
                    "opponent": opponent.strip(),
                    "date_utc": date_iso,
                    "league_name": league,
                    "venue": venue,
                    "tv_channel": channel
                })
            except Exception as e:
                log.error(f"Satır parse hatası: {e}")


    if matches:
        ref = db.reference("fenerbahce_fikstur")
        ref.set({
            "last_update": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "matches": matches
        })
        log.info(f"{len(matches)} maç başarıyla kaydedildi.")
    else:
        log.warning("Sitede aktif Fenerbahçe maçı bulunamadı.")

def main():
    init_firebase()
    parse_and_save()

if __name__ == "__main__":
    main()

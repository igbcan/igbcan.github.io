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

# Monkeypatch requests to disable SSL verification globally (fixes cert issues in local environments)
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    _orig_request = requests.Session.request
    def _patched_request(self, method, url, *args, **kwargs):
        kwargs['verify'] = False
        return _orig_request(self, method, url, *args, **kwargs)
    requests.Session.request = _patched_request
except Exception:
    pass

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
FB_TARGET_URL = "https://www.sporekrani.com/home/team/fenerbahce"

NATIONAL_TARGET_URLS = [
    "https://www.sporekrani.com/home/team/turkiye",
    "https://www.sporekrani.com/home/team/turkiye-futbol",
    "https://www.sporekrani.com/home/team/turkiye-basketbol",
    "https://www.sporekrani.com/home/team/turkiye-voleybol",
]

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
def parse_date_to_utc(raw_time_str: str, day_header: str) -> tuple[str, datetime.datetime]:
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
        m_dot = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", header)
        if m_dot:
            target_day, target_month, target_year = map(int, m_dot.groups())
        else:
            m_text = re.search(r"(\d{1,2})\s+([a-zçğıöşü]+)", header)
            if m_text:
                target_day = int(m_text.group(1))
                month_name = m_text.group(2)
                target_month = TR_MONTHS.get(month_name, now.month)
                # Yıl geçişi (Örnek: Kasım/Aralık ayında Ocak/Şubat maçı)
                if now.month >= 10 and target_month <= 3:
                    target_year += 1

    # Saati ayarla
    m_time = re.search(r"(\d{2}):(\d{2})", raw_time_str)
    hour, minute = (0, 0)
    if m_time:
        hour, minute = map(int, m_time.groups())
    
    try:
        dt = datetime.datetime(target_year, target_month, target_day, hour, minute, tzinfo=istanbul_tz)
        return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), dt
    except Exception:
        return now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), now

def fetch_page(url: str) -> BeautifulSoup | None:
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return BeautifulSoup(resp.text, "html.parser")
    except requests.exceptions.SSLError as ssl_err:
        log.warning(f"SSL sertifika doğrulama hatası ({url}), doğrulama devre dışı bırakılarak tekrar deneniyor: {ssl_err}")
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = requests.get(url, headers=headers, timeout=15, verify=False)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            log.error(f"Sayfa alınamadı ({url}) SSL devre dışı iken de: {exc}")
            return None
    except Exception as exc:
        log.error(f"Sayfa alınamadı ({url}): {exc}")
        return None

def parse_matches_from_url(url: str, team_keyword: str = "fenerbahçe", id_prefix: str = "fb") -> list[dict]:
    soup = fetch_page(url)
    if not soup:
        return []

    matches = []
    container = soup.select_one(".event-list")
    if not container:
        log.warning(f"Maç listesi konteynırı bulunamadı ({url}).")
        return []

    current_day = "Bugün"
    istanbul_tz = datetime.timezone(datetime.timedelta(hours=3))
    now = datetime.datetime.now(istanbul_tz)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    for child in container.find_all(recursive=False):
        if "event-list__day-badge" in child.get("class", []):
            current_day = child.get_text(strip=True)
            continue
            
        if "event-list__row" in child.get("class", []):
            try:
                # Saat ve Tarih
                time_el = child.select_one(".event-list__time")
                raw_time = time_el.get_text(strip=True) if time_el else "00:00"
                date_iso, dt_local = parse_date_to_utc(raw_time, current_day)
                
                # Bugünden (00:00) önceki geçmiş maçları filtrele
                if dt_local < today_start:
                    continue

                # Başlık
                name_el = child.select_one(".event-list__name")
                match_name = name_el.get_text(strip=True) if name_el else "Maç"
                
                # Rakip ve Ev Sahibi Kontrolü
                is_home = False
                opponent = match_name
                if " - " in match_name:
                    parts = match_name.split(" - ")
                    if team_keyword in parts[0].lower():
                        is_home = True
                        opponent = parts[1]
                    else:
                        is_home = False
                        opponent = parts[0]

                # Branş
                sport_img = child.select_one(".event-list__sport-icon")
                sport_raw = sport_img.get("alt", "").lower() if sport_img else ""
                branch = "Football"
                url_lower = url.lower()
                if "basketbol" in sport_raw or "turkiye-basketbol" in url_lower:
                    branch = "Basketball"
                elif "voleybol" in sport_raw or "turkiye-voleybol" in url_lower:
                    branch = "Volleyball"
                elif "futbol" in sport_raw or "turkiye-futbol" in url_lower:
                    branch = "Football"

                # Maç Yeri (Venue) Tahmini
                venue = "Deplasman"
                if is_home:
                    if id_prefix == "fb":
                        if branch == "Football": venue = "Ülker Stadyumu"
                        elif branch == "Basketball": venue = "Ülker Spor ve Etkinlik Salonu"
                        elif branch == "Volleyball": venue = "Burhan Felek Voleybol Salonu"
                        else: venue = "Fenerbahçe Tesisleri"
                    else:
                        if branch == "Football": venue = "Türkiye (İç Saha)"
                        elif branch == "Basketball": venue = "Türkiye (İç Saha)"
                        elif branch == "Volleyball": venue = "İstanbul / Türkiye"
                        else: venue = "İç Saha"

                # Lig
                league_el = child.select_one(".event-list__league")
                league = league_el.get_text(strip=True) if league_el else "Lig Bilgisi Yok"
                
                # Kanal Tespiti (Gelişmiş & Çoklu Kanal Desteği)
                channel_imgs = child.select(".event-list__channels .event-list__channel img")
                if not channel_imgs:
                    channel_imgs = child.select(".event-list__channels-mobile img")
                if not channel_imgs:
                    channel_imgs = child.select(".event-list__channel img")

                channels = []
                for img in channel_imgs:
                    c_name = (img.get("alt") or img.get("title") or "").strip()
                    if c_name and c_name not in channels:
                        channels.append(c_name)

                if channels:
                    channel = ", ".join(channels)
                else:
                    text_ch = child.select_one(".event-list__channels") or child.select_one(".event-list__channel")
                    channel = text_ch.get_text(strip=True) if text_ch else "Yayın Yok"
                
                # Kararlı Match ID: Tarih (YYYY-MM-DD), branş, lig ve maç adına göre üretilir.
                # Saat/Kanal/Stadyum sonradan belli olduğunda veya değiştiğinde ID sabit kalır,
                # böylece Firebase ve Room veritabanında aynı maçın bilgileri doğrudan güncellenir.
                date_ymd = dt_local.strftime("%Y-%m-%d")
                clean_league = re.sub(r"[^\w\s-]", "", league).strip().lower().replace(" ", "_")
                clean_name = re.sub(r"[^\w\s-]", "", match_name).strip().lower().replace(" ", "_")
                stable_match_id = f"{id_prefix}_{branch.lower()}_{date_ymd}_{clean_league}_{clean_name}"
                
                matches.append({
                    "match_id": stable_match_id,
                    "branch": branch,
                    "opponent": opponent.strip(),
                    "date_utc": date_iso,
                    "league_name": league,
                    "venue": venue,
                    "tv_channel": channel
                })
            except Exception as e:
                log.error(f"Satır parse hatası ({url}): {e}")

    return matches

# ──────────────────────────────────────────────
# SCRAPING VE VERİ YAZMA
# ──────────────────────────────────────────────
def parse_and_save():
    # 1. Fenerbahçe Fikstürü
    fb_matches = parse_matches_from_url(FB_TARGET_URL, team_keyword="fenerbahçe", id_prefix="fb")
    if fb_matches:
        ref_fb = db.reference("fenerbahce_fikstur")
        ref_fb.update({
            "last_update": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "matches": fb_matches
        })
        log.info(f"Fenerbahçe: {len(fb_matches)} maç başarıyla kaydedildi.")
    else:
        log.warning("Fenerbahçe için aktif maç bulunamadı.")

    # 2. Milli Takımlar Fikstürü
    national_matches = []
    seen_ids = set()
    
    for url in NATIONAL_TARGET_URLS:
        scraped = parse_matches_from_url(url, team_keyword="türkiye", id_prefix="milli")
        for m in scraped:
            if m["match_id"] not in seen_ids:
                seen_ids.add(m["match_id"])
                national_matches.append(m)

    if national_matches:
        ref_national = db.reference("milli_takimlar_fikstur")
        ref_national.update({
            "last_update": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "match_count": len(national_matches),
            "matches": national_matches
        })
        log.info(f"Milli Takımlar: {len(national_matches)} maç başarıyla kaydedildi.")
    else:
        log.warning("Milli Takımlar için aktif maç bulunamadı.")

def main():
    init_firebase()
    parse_and_save()

if __name__ == "__main__":
    main()

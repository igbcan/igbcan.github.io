import os
import re
import json
import logging
import datetime

log = logging.getLogger(__name__)

# ── Sabitler ──────────────────────────────────────────────────────────────────
WC_START = datetime.date(2026, 6, 11)
WC_END   = datetime.date(2026, 7, 19)
FIREBASE_NODE = "fenerbahce_fikstur/worldcup2026_fixtures"
SPOREKRANI_WC_URL = "https://www.sporekrani.com/home/league/fifa-2026-dunya-kupasi"


# ── Ana Scraper ile Uyumlu Tarih Ayrıştırıcı ──────────────────────────────────
def _parse_date_to_utc(raw_time_str: str, day_header: str) -> str:
    """sporekrani_scraper.py dosyasındaki tarih mantığının birebir aynısıdır."""
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
        pass
    else:
        m_dot = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", header)
        if m_dot:
            target_day, target_month, target_year = map(int, m_dot.groups())
        else:
            m_text = re.search(r"(\d{1,2})\s+([a-zçğıöşü]+)", header)
            if m_text:
                target_day = int(m_text.group(1))
                month_name = m_text.group(2)
                target_month = TR_MONTHS.get(month_name, now.month)
                if target_month < now.month:
                    target_year += 1

    m_time = re.search(r"(\d{2}):(\d{2})", raw_time_str)
    hour, minute = (0, 0)
    if m_time:
        hour, minute = map(int, m_time.groups())
    
    try:
        dt = datetime.datetime(target_year, target_month, target_day, hour, minute, tzinfo=istanbul_tz)
        return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _in_tournament_range(date_utc: str) -> bool:
    try:
        dt = datetime.datetime.fromisoformat(date_utc.replace("Z", "+00:00"))
        match_date = dt.astimezone(datetime.timezone.utc).date()
        return WC_START <= match_date <= WC_END
    except Exception:
        return False


# ── Spor Ekranı Dünya Kupası Sayfası Kazıma ───────────────────────────────────
def _fetch_from_sporekrani() -> list[dict] | None:
    try:
        import requests
        from bs4 import BeautifulSoup
        import random

        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ]
        headers = {"User-Agent": random.choice(user_agents)}
        
        log.info(f"Dünya Kupası fikstürü kazınıyor: {SPOREKRANI_WC_URL}")
        resp = requests.get(SPOREKRANI_WC_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        
        soup = BeautifulSoup(resp.text, "html.parser")
        container = soup.select_one(".event-list")
        if not container:
            log.warning("Dünya Kupası maç listesi konteynırı bulunamadı.")
            return None

        matches = []
        current_day = "Bugün"
        count = 1

        for child in container.find_all(recursive=False):
            if "event-list__day-badge" in child.get("class", []):
                current_day = child.get_text(strip=True)
                continue
                
            if "event-list__row" in child.get("class", []):
                try:
                    time_el = child.select_one(".event-list__time")
                    raw_time = time_el.get_text(strip=True) if time_el else "00:00"
                    date_iso = _parse_date_to_utc(raw_time, current_day)
                    
                    if not _in_tournament_range(date_iso):
                        continue

                    name_el = child.select_one(".event-list__name")
                    match_name = name_el.get_text(strip=True) if name_el else "Dünya Kupası Maçı"
                    
                    home_team, away_team = "TBD", "TBD"
                    if " - " in match_name:
                        parts = match_name.split(" - ")
                        home_team = parts[0].strip()
                        away_team = parts[1].strip()

                    league_el = child.select_one(".event-list__league")
                    league = league_el.get_text(strip=True) if league_el else "FIFA World Cup 2026"
                    
                    channel_img = child.select_one(".event-list__channel img")
                    channel = channel_img.get("alt", "Kanal Belirsiz") if channel_img else "Yayın Yok"

                    matches.append({
                        "match_id": f"wc2026_scraped_{count:03d}",
                        "home_team": home_team,
                        "away_team": away_team,
                        "date_utc": date_iso,
                        "venue": "North America Stadiums",
                        "group": "-",
                        "round": "Group Stage" if "Grup" in league else league,
                        "status": "SCHEDULED",
                        "tv_channel": channel,
                        "home_score": None,
                        "away_score": None
                    })
                    count += 1
                except Exception as row_err:
                    log.error(f"Dünya kupası satır parse hatası: {row_err}")

        log.info(f"Spor Ekranı'ndan {len(matches)} Dünya Kupası maçı başarıyla ayıklandı.")
        return matches if len(matches) > 0 else None

    except Exception as exc:
        log.error(f"Spor Ekranı kazıma işleminde hata: {exc}")
        return None


# ── Firebase Realtime Database Entegrasyonu ───────────────────────────────────
def _write_to_firebase(matches: list[dict]) -> None:
    try:
        from firebase_admin import db
        ref = db.reference(FIREBASE_NODE)
        ref.set({
            "last_update": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "sporekrani.com (World Cup Scraper)",
            "tournament": "FIFA World Cup 2026",
            "match_count": len(matches),
            "matches": matches,
        })
        log.info(f"Firebase'e {len(matches)} WC2026 maçı başarıyla yazıldı → /{FIREBASE_NODE}")
    except Exception as exc:
        log.error(f"Firebase yazma hatası: {exc}")
        raise


def sync_worldcup2026() -> None:
    log.info("=== WC2026: Dünya Kupası fikstür senkronizasyonu başlıyor ===")
    today = datetime.date.today()
    if today > WC_END:
        log.info("Dünya Kupası 2026 sona erdi, senkronizasyon atlandı.")
        return

    matches = _fetch_from_sporekrani()
    if not matches:
        log.warning("WC2026: Yeni maç bulunamadı veya kazınamadı.")
        return

    _write_to_firebase(matches)
    log.info("=== WC2026: Senkronizasyon başarıyla tamamlandı ===")


# ── Çalıştırma ve Test Bloğu (GitHub Actions / Local Uyumlu) ──────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        import firebase_admin
        from firebase_admin import credentials
        
        if not firebase_admin._apps:
            sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
            db_url  = "https://icanfb-default-rtdb.europe-west1.firebasedatabase.app/"

            if sa_json:
                # GitHub Actions ortamındayız, secret değişkeni kullanılıyor
                cred = credentials.Certificate(json.loads(sa_json))
                log.info("Firebase: GitHub Actions ortam değişkeni üzerinden bağlanıldı.")
            else:
                # Lokal bilgisayardayız, serviceAccountKey.json aranıyor
                key_path = os.path.join(os.path.dirname(__file__), "serviceAccountKey.json")
                if not os.path.exists(key_path):
                    log.error("FIREBASE_SERVICE_ACCOUNT ortam değişkeni veya serviceAccountKey.json bulunamadı!")
                    sys.exit(1)
                cred = credentials.Certificate(key_path)
                log.info("Firebase: Yerel serviceAccountKey.json üzerinden bağlanıldı.")

            firebase_admin.initialize_app(cred, {"databaseURL": db_url})
            
        sync_worldcup2026()
        
    except ImportError:
        log.warning("firebase_admin yok, sadece terminal testi yapılıyor...")
        res = _fetch_from_sporekrani()
        if res: 
            print(json.dumps(res[:1], indent=2, ensure_ascii=False))

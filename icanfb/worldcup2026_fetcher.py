"""
worldcup2026_fetcher.py
=== TEMPORARY: FIFA World Cup 2026 (June 11 – July 19 2026) - remove after tournament ===

Bu modül FIFA Dünya Kupası 2026 fikstürünü çekip Firebase'deki
`worldcup2026_fixtures` koleksiyonuna yazar.

Veri kaynağı (öncelik sırası):
  1. football-data.org ücretsiz API (FOOTBALL_DATA_API_KEY ortam değişkeni varsa)
  2. worldcup2026_fixtures.json statik fallback dosyası

Geri alma: Bu dosyayı sil + scraper.py'deki import bloğunu kaldır.
"""

import os
import json
import logging
import datetime

log = logging.getLogger(__name__)

# ── Sabitler ──────────────────────────────────────────────────────────────────
WC_START = datetime.date(2026, 6, 11)
WC_END   = datetime.date(2026, 7, 19)
FIREBASE_NODE = "worldcup2026_fixtures"
STATIC_JSON   = os.path.join(os.path.dirname(__file__), "worldcup2026_fixtures.json")

# football-data.org ücretsiz tier — Competition: WC, Season: 2026
FOOTBALL_DATA_URL = "https://api.football-data.org/v4/competitions/WC/matches?season=2026"


# ── Yardımcı: İstanbul tarihini UTC ISO 8601'e çevir ──────────────────────────
def _to_utc_iso(utc_str: str) -> str:
    """Zaten UTC ISO 8601 formatındaki stringi normalize eder."""
    try:
        # Gelen format: '2026-06-11T20:00:00Z' veya '2026-06-11T20:00:00+00:00'
        dt = datetime.datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return utc_str


def _in_tournament_range(date_utc: str) -> bool:
    """Maç tarihi turnuva aralığında mı kontrol eder."""
    try:
        dt = datetime.datetime.fromisoformat(date_utc.replace("Z", "+00:00"))
        match_date = dt.astimezone(datetime.timezone.utc).date()
        return WC_START <= match_date <= WC_END
    except Exception:
        return False


# ── API'den veri çekme ────────────────────────────────────────────────────────
def _fetch_from_api(api_key: str) -> list[dict] | None:
    """
    football-data.org API'den FIFA WC 2026 maçlarını çeker.
    Başarısız olursa None döner.
    """
    try:
        import requests
        headers = {"X-Auth-Token": api_key}
        log.info("football-data.org API'ye istek gönderiliyor...")
        resp = requests.get(FOOTBALL_DATA_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        matches = []
        for m in data.get("matches", []):
            utc_date = m.get("utcDate", "")
            if not _in_tournament_range(utc_date):
                continue

            home = m.get("homeTeam", {}).get("name", "TBD")
            away = m.get("awayTeam", {}).get("name", "TBD")
            stage = m.get("stage", "GROUP_STAGE")
            group = m.get("group") or "-"
            score_home = (m.get("score", {}).get("fullTime", {}) or {}).get("home")
            score_away = (m.get("score", {}).get("fullTime", {}) or {}).get("away")

            round_name = {
                "GROUP_STAGE": "Group Stage",
                "LAST_32": "Round of 32",
                "LAST_16": "Round of 16",
                "QUARTER_FINALS": "Quarter-Final",
                "SEMI_FINALS": "Semi-Final",
                "THIRD_PLACE": "Third Place",
                "FINAL": "Final",
            }.get(stage, stage)

            matches.append({
                "match_id": f"wc2026_{m.get('id', '')}",
                "home_team": home,
                "away_team": away,
                "date_utc": _to_utc_iso(utc_date),
                "venue": m.get("venue") or "TBD",
                "group": group,
                "round": round_name,
                "status": m.get("status", "SCHEDULED"),
                "home_score": score_home,
                "away_score": score_away,
            })

        log.info(f"API'den {len(matches)} WC2026 maçı alındı.")
        return matches

    except Exception as exc:
        log.warning(f"football-data.org API hatası, statik JSON'a fallback yapılıyor: {exc}")
        return None


# ── Statik JSON'dan veri okuma ────────────────────────────────────────────────
def _load_static_fixtures() -> list[dict]:
    """worldcup2026_fixtures.json dosyasından fikstür yükler."""
    try:
        with open(STATIC_JSON, encoding="utf-8") as f:
            data = json.load(f)
        matches = [
            m for m in data.get("matches", [])
            if _in_tournament_range(m.get("date_utc", ""))
        ]
        log.info(f"Statik JSON'dan {len(matches)} WC2026 maçı yüklendi.")
        return matches
    except Exception as exc:
        log.error(f"Statik JSON okunamadı: {exc}")
        return []


# ── Firebase'e yazma ──────────────────────────────────────────────────────────
def _write_to_firebase(matches: list[dict]) -> None:
    """Maçları Firebase `worldcup2026_fixtures` node'una yazar."""
    try:
        from firebase_admin import db
        ref = db.reference(FIREBASE_NODE)
        ref.set({
            "last_update": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "worldcup2026_fetcher.py",
            "tournament": "FIFA World Cup 2026",
            "match_count": len(matches),
            "matches": matches,
        })
        log.info(f"Firebase'e {len(matches)} WC2026 maçı yazıldı → /{FIREBASE_NODE}")
    except Exception as exc:
        log.error(f"Firebase yazma hatası: {exc}")
        raise


# ── Ana fonksiyon (scraper.py'den çağrılır) ──────────────────────────────────
def sync_worldcup2026() -> None:
    """
    === TEMPORARY: FIFA World Cup 2026 - remove after July 19 2026 ===
    
    Fikstürü API veya statik JSON'dan çekip Firebase'e yazar.
    Firebase bağlantısının daha önce init_firebase() ile kurulmuş olması gerekir.
    """
    log.info("=== WC2026: Dünya Kupası fikstür senkronizasyonu başlıyor ===")

    # Turnuva bitti mi kontrol et
    today = datetime.date.today()
    if today > WC_END:
        log.info("Dünya Kupası 2026 sona erdi, senkronizasyon atlandı.")
        return

    # Veri kaynağı seç
    api_key = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
    matches = _fetch_from_api(api_key) if api_key else None
    if not matches:
        matches = _load_static_fixtures()

    if not matches:
        log.warning("WC2026: Hiç maç bulunamadı, Firebase güncellenmedi.")
        return

    _write_to_firebase(matches)
    log.info("=== WC2026: Senkronizasyon tamamlandı ===")


# ── Standalone / GitHub Actions çalıştırma ───────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # Firebase başlatma (scraper.py'den bağımsız çalışabilmek için)
    try:
        import firebase_admin
        from firebase_admin import credentials, db as firebase_db

        if not firebase_admin._apps:
            sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
            db_url  = "https://icanfb-default-rtdb.europe-west1.firebasedatabase.app/"

            if sa_json:
                import json as _json
                cred = credentials.Certificate(_json.loads(sa_json))
                log.info("Firebase: ortam değişkeni üzerinden bağlanıldı.")
            else:
                key_path = os.path.join(os.path.dirname(__file__), "serviceAccountKey.json")
                if not os.path.exists(key_path):
                    log.error("FIREBASE_SERVICE_ACCOUNT ortam değişkeni veya serviceAccountKey.json bulunamadı!")
                    sys.exit(1)
                cred = credentials.Certificate(key_path)
                log.info("Firebase: yerel serviceAccountKey.json üzerinden bağlanıldı.")

            firebase_admin.initialize_app(cred, {"databaseURL": db_url})

        log.info("=== Standalone mod: Firebase'e gerçek yazma yapılacak ===")
        sync_worldcup2026()

    except ImportError:
        # firebase_admin yoksa sadece lokal test yap
        log.warning("firebase_admin yüklü değil — sadece lokal test yapılıyor (Firebase'e yazılmıyor)")
        api_key = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
        matches = _fetch_from_api(api_key) if api_key else None
        if not matches:
            matches = _load_static_fixtures()
        log.info(f"Test: {len(matches)} maç bulundu.")
        if matches:
            import json as _json
            print(_json.dumps(matches[0], indent=2, ensure_ascii=False))


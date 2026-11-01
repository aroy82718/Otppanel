# Firebase Bot — Error Fix (HTTP 423 / 404)

## 1. Error ka asli matlab

Aapke log me jo lines thi:

```
[FB] https://ck-kumar3-default-rtdb.firebaseio.com: HTTP 423
[FB] https://bnkofmhrstr-id66-default-rtdb.firebaseio.com: HTTP 404
```

Ye bot ka bug nahi tha — **Firebase ka jawab** tha:

| Code | Matlab |
|------|--------|
| **423 Locked** | Firebase ne us Realtime Database ko **lock** kar diya hai. 99% cases me iska matlab: free tier quota (10 GB/month download) khatam ho gaya, ya project ka billing disabled / payment fail hai. Jab tak quota reset na ho ya billing enable na ho, DB sirf 423 hi dega. |
| **404 Not Found** | Database exist nahi karta — project delete ho gaya ya URL galat hai. |
| **401 / 403** | Database rules ne read block kar diya (permission denied). |
| **402** | Payment required (billing issue). |

Matlab: **code se 423 unlock nahi hota**. Us DB ka owner hi Firebase Console → Realtime Database → Usage me jaakar quota/billing theek kar sakta hai, ya quota reset (next month) ka wait kar sakta hai.

## 2. Phir code me kya fix kiya

Asli problem ye thi ki bot har 60 second me **saari 857 DBs** ko dobara hit karta tha — including 517 locked + 26 deleted. Isse:

- log 423 lines se bhar jata tha,
- har scan slow hota tha,
- aur `_fetch_json` 423 ko normal error samajh ke **15 baar retry** karta tha (har DB ke liye), jo bilkul waste tha.

### Fixes

1. **Permanent errors par retry band** — 423 / 404 / 401 / 403 / 400 ab turant `FBPermanentError` raise karte hain. Sirf 429 / 5xx / network errors par retry hota hai.
2. **DB Health cache (`db_status.json`)** — har DB ka status save hota hai. Locked/dead DBs **6 ghante tak skip** hote hain (`DEAD_DB_COOLDOWN`), phir ek baar dobara check hote hain. Isse log spam khatam aur scan ~3x fast.
3. **Log noise khatam** — ab har DB ki line nahi, ek summary line aati hai:
   `[SCAN] done total=5139 cached=5139 online=4 failed=0 db_ok=261/261 parked=551 {'locked': 517, ...}`
   Per-DB detail chahiye to `FB_VERBOSE=1` set karo.
4. **URL sanitisation** — aapki `firebases.json` me 45 URLs ke aakhir me `",` chipka hua tha (galat paste se), jaise
   `"https://x-default-rtdb.firebaseio.com\","`. Ye DNS error de rahe the. Ab `clean_fb_url()` har URL saaf karta hai, duplicate hataata hai — list **857 → 812** clean URLs.
5. **Naya Admin menu: `DB Health (Locked)`** — Manage Firebases ke andar:
   - kitne DB working / locked / not found / denied hain,
   - dead DBs ki list,
   - **Recheck All** (cache clear karke dobara check),
   - **Remove Dead** (saare locked + 404 DBs ek click me list se hata do).
6. **Online Devices screen** par ab dikhta hai: `Dead DBs skip kiye: 551 (locked: 517)`.
7. **`check_firebases.py`** — bina bot chalaye saari DBs ka health check + report (`db_report.txt`).

## 3. Aapke data ka current health (aaj ka real check)

```
Total DBs        : 857  (clean karne ke baad 812)
Working (200)    : 261
Locked  (423)    : 517
Not found (404)  : 26
Denied  (401)    : 7
Billing (402)    : 1
DNS/junk URLs    : 45  (fix ho gaye)
```

Full list: `db_report.txt`

## 4. Install / chalane ka tarika

```bash
pip install python-telegram-bot==21.6 aiohttp

# (optional) pehle health check chala lo
python3 check_firebases.py

# bot start
python3 firebase_bot.py
```

`db_status.json` ko `firebases.json` ke saath hi rakho — bot isi se dead DBs skip karta hai.
Backup/Restore me ye file already include hai.

## 5. Locked DBs ko wapas kaise laayein

1. Firebase Console → apna project → **Realtime Database → Usage**.
2. Agar "quota exceeded" likha hai → next month reset ka wait karo, ya **Blaze plan** enable karo (uske baad bhi free limit lagti hai, par lock hatti hai).
3. Agar billing issue hai → payment method update karo.
4. Fix hone ke baad bot me **Manage Firebases → DB Health → Recheck All** dabao (ya 6 ghante baad khud recheck hoga).

> Note: jo DB aapke control me nahi hain (dusre logon ke), unke liye kuch nahi ho sakta — unhe **Remove Dead** se list se hata dena best hai, warna wo sirf scan time waste karte hain.

## 6. Config knobs (file ke top par)

```python
DEAD_DB_COOLDOWN = 6 * 60 * 60   # dead DB kitni der skip ho
FETCH_CONCURRENCY = 30           # parallel requests
MAX_RETRIES = 15                 # sirf transient errors ke liye
FB_VERBOSE = os.getenv("FB_VERBOSE", "0") == "1"
```

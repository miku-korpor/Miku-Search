import os
import json
import time
import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor

# Переменные окружения (на Render ты пропишешь их в настройках)
BOT_TOKEN = os.getenv("BOT_TOKEN", "8994282826:AAENOVRS9R-Q-wHj6kXZpJUrYyix0CrCW5k")
ADMIN_ID = os.getenv("ADMIN_ID", "8620880150")
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "mikukorpor")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://miku-search.onrender.com")

app = FastAPI()

def get_db():
    """Подключение к облачной базе PostgreSQL (Supabase)"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# Автоматическое создание таблиц при старте сервера
@app.on_event("startup")
def startup_event():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            attempts INTEGER DEFAULT 5,
            premium_attempts INTEGER DEFAULT 0,
            last_reset BIGINT DEFAULT 0,
            favorites TEXT DEFAULT '[]',
            last_advent_claim BIGINT DEFAULT 0,
            advent_opened INTEGER DEFAULT 0,
            advent_rewards TEXT DEFAULT '[]',
            last_wheel_spin BIGINT DEFAULT 0,
            wheel_spins_left INTEGER DEFAULT 1,
            invited_by TEXT DEFAULT NULL,
            referral_count INTEGER DEFAULT 0,
            last_ref_claim BIGINT DEFAULT 0,
            ref_reward_available INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS available_usernames (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE,
            style TEXT,
            status TEXT DEFAULT 'free'
        );
        CREATE TABLE IF NOT EXISTS feedback (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            message TEXT,
            timestamp BIGINT
        );
    """)
    conn.commit()
    cursor.close()
    conn.close()
    print("[SERVER] База данных Supabase успешно инициализирована!")

def get_or_create_user(user_id: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT attempts, premium_attempts, last_reset, favorites, last_advent_claim, 
               advent_opened, advent_rewards, last_wheel_spin, wheel_spins_left, 
               invited_by, referral_count, last_ref_claim, ref_reward_available 
        FROM users WHERE user_id = %s
    """, (user_id,))
    row = cursor.fetchone()
    
    now_ms = int(time.time() * 1000)
    twenty_four_hours = 86400000

    if not row:
        rewards = [random.randint(1, 5) if 'random' in globals() else 3 for _ in range(25)]
        rewards_json = json.dumps(rewards)
        cursor.execute("""
            INSERT INTO users (user_id, attempts, premium_attempts, last_reset, favorites, last_advent_claim, advent_opened, advent_rewards, last_wheel_spin, wheel_spins_left)
            VALUES (%s, 5, 0, %s, '[]', 0, 0, %s, 0, 1)
            ON CONFLICT (user_id) DO NOTHING
        """, (user_id, now_ms, rewards_json))
        conn.commit()
        cursor.close()
        conn.close()
        return {
            "attempts": 5, "premium_attempts": 0, "last_reset": now_ms, "favorites": [],
            "last_advent_claim": 0, "advent_opened": 0, "advent_rewards": rewards,
            "last_wheel_spin": 0, "wheel_spins_left": 1, "invited_by": None,
            "referral_count": 0, "last_ref_claim": 0, "ref_reward_available": 0
        }
    else:
        attempts = row['attempts']
        prem_att = row['premium_attempts']
        last_reset = row['last_reset']
        favs = json.loads(row['favorites']) if row['favorites'] else []
        last_adv = row['last_advent_claim']
        adv_opened = row['advent_opened']
        adv_rew = json.loads(row['advent_rewards']) if row['advent_rewards'] else []
        last_wheel = row['last_wheel_spin']
        wheel_spins = row['wheel_spins_left']
        inv_by = row['invited_by']
        ref_cnt = row['referral_count']
        last_ref = row['last_ref_claim']
        ref_avail = row['ref_reward_available']

        if now_ms - last_reset >= twenty_four_hours:
            attempts = 5
            last_reset = now_ms
            cursor.execute("UPDATE users SET attempts = 5, last_reset = %s WHERE user_id = %s", (last_reset, user_id))
            conn.commit()

        if now_ms - last_wheel >= twenty_four_hours and wheel_spins < 1:
            wheel_spins = 1
            cursor.execute("UPDATE users SET wheel_spins_left = 1 WHERE user_id = %s", (user_id,))
            conn.commit()

        cursor.close()
        conn.close()
        return {
            "attempts": attempts, "premium_attempts": prem_att, "last_reset": last_reset,
            "favorites": favs, "last_advent_claim": last_adv, "advent_opened": adv_opened,
            "advent_rewards": adv_rew, "last_wheel_spin": last_wheel, "wheel_spins_left": wheel_spins,
            "invited_by": inv_by, "referral_count": ref_cnt, "last_ref_claim": last_ref, "ref_reward_available": ref_avail
        }

def update_user(user_id: str, **kwargs):
    conn = get_db()
    cursor = conn.cursor()
    for key, val in kwargs.items():
        if isinstance(val, (dict, list)):
            val = json.dumps(val)
        cursor.execute(f"UPDATE users SET {key} = %s WHERE user_id = %s", (val, user_id))
    conn.commit()
    cursor.close()
    conn.close()

def parse_user_id(x_telegram_init_data: str) -> str:
    user_id = "default_user"
    if x_telegram_init_data and "user=" in x_telegram_init_data:
        try:
            import urllib.parse
            parsed = urllib.parse.parse_qs(x_telegram_init_data)
            user_json = json.loads(parsed.get("user", ["{}"])[0])
            user_id = str(user_json.get("id", "default_user"))
        except:
            pass
    return user_id

async def check_telegram_sub(user_id: str) -> bool:
    if str(user_id) == str(ADMIN_ID) or user_id == "default_user":
        return True
    try:
        async with httpx.AsyncClient() as http_client:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember"
            res = await http_client.get(url, params={"chat_id": f"@{REQUIRED_CHANNEL}", "user_id": user_id})
            data = res.json()
            if data.get("ok"):
                status = data["result"].get("status", "")
                return status in ["creator", "administrator", "member"]
    except Exception as e:
        print(f"[SUB ERROR] {e}")
    return False

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "Miku Search WebApp is running!"

@app.get("/api/check-subscription")
async def check_subscription(x_telegram_init_data: str = Header(None)):
    user_id = parse_user_id(x_telegram_init_data)
    is_subscribed = await check_telegram_sub(user_id)
    return {"subscribed": is_subscribed, "channel": REQUIRED_CHANNEL}

@app.get("/api/user-data")
async def get_user_data(x_telegram_init_data: str = Header(None)):
    user_id = parse_user_id(x_telegram_init_data)
    is_admin = (str(user_id) == str(ADMIN_ID))
    u = get_or_create_user(user_id)
    now_ms = int(time.time() * 1000)
    
    reset_time_left = max(0, 86400000 - (now_ms - u["last_reset"])) if u["attempts"] <= 0 else 0
    advent_time_left = max(0, 86400000 - (now_ms - u["last_advent_claim"]))
    wheel_time_left = max(0, 86400000 - (now_ms - u["last_wheel_spin"])) if u["wheel_spins_left"] <= 0 else 0
    ref_time_left = max(0, 86400000 - (now_ms - u["last_ref_claim"]))

    return {
        "userId": user_id,
        "isAdmin": is_admin,
        "attempts": "∞" if is_admin else u["attempts"],
        "premium_attempts": "∞" if is_admin else u["premium_attempts"],
        "resetTimeLeft": reset_time_left,
        "favorites": u["favorites"],
        "adventOpened": u["advent_opened"],
        "adventRewards": u["advent_rewards"],
        "adventTimeLeft": advent_time_left,
        "wheelSpinsLeft": u["wheel_spins_left"],
        "wheelTimeLeft": wheel_time_left,
        "referralCount": u["referral_count"],
        "refRewardAvailable": u["ref_reward_available"],
        "refTimeLeft": ref_time_left,
        "botUsername": "searchbymiku_bot"
    }

class RefRequest(BaseModel):
    inviterId: str

@app.post("/api/register-ref")
async def register_ref(req: RefRequest, x_telegram_init_data: str = Header(None)):
    friend_id = parse_user_id(x_telegram_init_data)
    inviter_id = req.inviterId
    if not inviter_id or not friend_id or inviter_id == friend_id:
        return {"success": False}
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT invited_by FROM users WHERE user_id = %s", (friend_id,))
    row = cursor.fetchone()
    
    if not row or row['invited_by'] is None:
        cursor.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (friend_id,))
        cursor.execute("UPDATE users SET invited_by = %s WHERE user_id = %s AND invited_by IS NULL", (inviter_id, friend_id))
        cursor.execute("UPDATE users SET referral_count = referral_count + 1, ref_reward_available = ref_reward_available + 1 WHERE user_id = %s", (inviter_id,))
        conn.commit()
        success = True
    else:
        success = False
    cursor.close()
    conn.close()
    return {"success": success}

@app.post("/api/claim-ref")
async def claim_ref(x_telegram_init_data: str = Header(None)):
    user_id = parse_user_id(x_telegram_init_data)
    u = get_or_create_user(user_id)
    now_ms = int(time.time() * 1000)
    
    if u["ref_reward_available"] <= 0:
        return {"success": False, "error": "Нет доступных наград"}
        
    if u["last_ref_claim"] > 0 and (now_ms - u["last_ref_claim"] < 86400000):
        return {"success": False, "error": "Таймер 24 часа еще не истек"}
        
    new_attempts = u["attempts"] + 2
    update_user(user_id, attempts=new_attempts, ref_reward_available=u["ref_reward_available"] - 1, last_ref_claim=now_ms)
    return {"success": True, "attempts": new_attempts}

class ShopBuyRequest(BaseModel):
    packageId: int
    attempts: int
    price: int

@app.post("/api/create-invoice")
async def create_invoice(req: ShopBuyRequest, x_telegram_init_data: str = Header(None)):
    user_id = parse_user_id(x_telegram_init_data)
    async with httpx.AsyncClient() as client:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink"
        payload = {
            "title": f"Пакет: {req.attempts} попыток",
            "description": f"Покупка {req.attempts} поисковых попыток в Miku Search Bot",
            "payload": json.dumps({"user_id": user_id, "attempts": req.attempts}),
            "currency": "XTR",
            "prices": [{"label": f"{req.attempts} попыток", "amount": req.price}]
        }
        res = await client.post(url, json=payload)
        data = res.json()
        if data.get("ok"):
            return {"success": True, "invoiceLink": data["result"]}
    return {"success": False, "error": "Не удалось создать инвойс"}

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    update = await request.json()
    
    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        text = msg["text"]
        chat_id = msg["chat"]["id"]
        user_id = str(msg["from"]["id"])
        
        if text.startswith("/start"):
            if "ref_" in text:
                inviter_id = text.replace("/start ref_", "").strip()
                if inviter_id and inviter_id != user_id:
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("SELECT invited_by FROM users WHERE user_id = %s", (user_id,))
                    row = cursor.fetchone()
                    if not row or row['invited_by'] is None:
                        cursor.execute("INSERT INTO users (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
                        cursor.execute("UPDATE users SET invited_by = %s WHERE user_id = %s AND invited_by IS NULL", (inviter_id, user_id))
                        cursor.execute("UPDATE users SET referral_count = referral_count + 1, ref_reward_available = ref_reward_available + 1 WHERE user_id = %s", (inviter_id,))
                        conn.commit()
                    cursor.close()
                    conn.close()
            
            async with httpx.AsyncClient() as client:
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
                payload = {
                    "chat_id": chat_id,
                    "photo": "https://i.ibb.co/JRSykhsh/image.png",
                    "caption": "Чтобы начать, нажми кнопочку ниже 👇",
                    "reply_markup": {
                        "inline_keyboard": [
                            [{
                                "text": "🚀 Запустить Miku",
                                "web_app": {"url": WEBAPP_URL}
                            }]
                        ]
                    }
                }
                await client.post(url, json=payload)

    if "pre_checkout_query" in update:
        query_id = update["pre_checkout_query"]["id"]
        async with httpx.AsyncClient() as client:
            await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerPreCheckoutQuery", json={"pre_checkout_query_id": query_id, "ok": True})
    elif "message" in update and "successful_payment" in update["message"]:
        payment = update["message"]["successful_payment"]
        payload = json.loads(payment["invoice_payload"])
        user_id = payload["user_id"]
        add_attempts = payload["attempts"]
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET attempts = attempts + %s WHERE user_id = %s", (add_attempts, user_id))
        conn.commit()
        cursor.close()
        conn.close()
        
    return {"ok": True}

@app.get("/api/generate")
async def api_generate(style: str = "classic", x_telegram_init_data: str = Header(None)):
    user_id = parse_user_id(x_telegram_init_data)
    is_admin = (str(user_id) == str(ADMIN_ID))
    u = get_or_create_user(user_id)
    target_style = style if style in ["classic", "cyber", "premium"] else "classic"

    if not is_admin:
        if target_style == "premium":
            if u["premium_attempts"] <= 0: return {"error": "Нет премиум попыток"}
            update_user(user_id, premium_attempts=u["premium_attempts"] - 1)
        else:
            if u["attempts"] <= 0: return {"error": "Нет попыток"}
            update_user(user_id, attempts=u["attempts"] - 1)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username FROM available_usernames WHERE style = %s AND status = 'free' LIMIT 1", (target_style,))
    row = cursor.fetchone()
    
    if row:
        row_id = row['id']
        uname = row['username']
        cursor.execute("DELETE FROM available_usernames WHERE id = %s", (row_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return {"username": uname, "style": target_style}
    
    cursor.close()
    conn.close()
    return {"error": "База фармит ники, попробуйте через пару секунд снова!"}

class FeedbackRequest(BaseModel):
    message: str

@app.post("/api/feedback")
async def send_feedback(req: FeedbackRequest, x_telegram_init_data: str = Header(None)):
    user_id = parse_user_id(x_telegram_init_data)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO feedback (user_id, message, timestamp) VALUES (%s, %s, %s)", (user_id, req.message.strip(), int(time.time() * 1000)))
    conn.commit()
    cursor.close()
    conn.close()
    
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            admin_text = f"📩 **Новый отзыв!**\n\n👤 `{user_id}`\n💬 {req.message.strip()}"
            await client.post(url, json={"chat_id": ADMIN_ID, "text": admin_text, "parse_mode": "Markdown"})
    except Exception as e:
        print(f"[FEEDBACK ERROR] {e}")
        
    return {"success": True}

class FavRequest(BaseModel):
    username: str

@app.post("/api/favorites/add")
async def add_favorite(req: FavRequest, x_telegram_init_data: str = Header(None)):
    u = get_or_create_user(parse_user_id(x_telegram_init_data))
    favs = u["favorites"]
    if req.username not in favs and len(favs) < 25:
        favs.append(req.username)
        update_user(parse_user_id(x_telegram_init_data), favorites=favs)
    return {"success": True, "favorites": favs}

@app.post("/api/favorites/remove")
async def remove_favorite(req: FavRequest, x_telegram_init_data: str = Header(None)):
    u = get_or_create_user(parse_user_id(x_telegram_init_data))
    favs = u["favorites"]
    if req.username in favs:
        favs.remove(req.username)
        update_user(parse_user_id(x_telegram_init_data), favorites=favs)
    return {"success": True, "favorites": favs}
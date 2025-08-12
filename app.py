import os
import threading
import sqlite3
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

from flask import Flask, render_template, request, redirect, session, send_file
from dotenv import load_dotenv

import discord
from discord.ext import commands

# ── Load .env ──────────────────────────────────────────────────────────────────
load_dotenv()
app = Flask(__name__, static_folder="static")
app.secret_key = os.getenv("SECRET_KEY")

# ── ENV ────────────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.getenv("BOT_TOKEN")
GUILD_ID       = int(os.getenv("GUILD_ID"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))

CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI  = os.getenv("DISCORD_REDIRECT_URI")

EXAM_LOG_CHANNEL_ID    = int(os.getenv("EXAM_LOG_CHANNEL_ID", LOG_CHANNEL_ID))
SAI_LOG_CHANNEL_ID     = int(os.getenv("SAI_LOG_CHANNEL_ID", LOG_CHANNEL_ID))
VEHICLE_LOG_CHANNEL_ID = int(os.getenv("VEHICLE_LOG_CHANNEL_ID", LOG_CHANNEL_ID))

ALLOWED_ROLES     = [r.strip() for r in os.getenv("ALLOWED_ROLES", "").split(",") if r.strip()]
SAI_ALLOWED_ROLES = [r.strip() for r in os.getenv("SAI_ALLOWED_ROLES", "BCSD").split(",") if r.strip()]
SA_LOG_CHANNEL_ID = int(os.getenv("SA_LOG_CHANNEL_ID", SAI_LOG_CHANNEL_ID))


# ── Транспорт: ID = plate (щоб 1:1) ───────────────────────────────────────────
VEHICLES = [
    {"id": "BCSD-07", "name": "Vapid f150",     "plate": "BCSD-07", "img": "/static/vehicles/car1.jpg"},
    {"id": "BCSD-16", "name": "Vapid f150",     "plate": "BCSD-16", "img": "/static/vehicles/car1.jpg"},
    {"id": "BCSD-08", "name": "Vapid f150",     "plate": "BCSD-08", "img": "/static/vehicles/car1.jpg"},
    {"id": "BCSD-05", "name": "Vapid f150",     "plate": "BCSD-05", "img": "/static/vehicles/car1.jpg"},
    {"id": "BCSD-19", "name": "Vapid f150",     "plate": "BCSD-19", "img": "/static/vehicles/car1.jpg"},
    {"id": "BCSD-17", "name": "Vapid f150",     "plate": "BCSD-17", "img": "/static/vehicles/car1.jpg"},
    {"id": "BCSD-18", "name": "Vapid f150",     "plate": "BCSD-18", "img": "/static/vehicles/car1.jpg"},
    {"id": "BCSD-06", "name": "Vapid f150",     "plate": "BCSD-06", "img": "/static/vehicles/car1.jpg"},

    {"id": "BCSD-03", "name": "Vapid explorer", "plate": "BCSD-03", "img": "/static/vehicles/car2.jpg"},
    {"id": "BCSD-14", "name": "Vapid explorer", "plate": "BCSD-14", "img": "/static/vehicles/car2.jpg"},
    {"id": "BCSD-01", "name": "Vapid explorer", "plate": "BCSD-01", "img": "/static/vehicles/car2.jpg"},
    {"id": "BCSD-02", "name": "Vapid explorer", "plate": "BCSD-02", "img": "/static/vehicles/car2.jpg"},
    {"id": "BCSD-04", "name": "Vapid explorer", "plate": "BCSD-04", "img": "/static/vehicles/car2.jpg"},
    {"id": "BCSD-13", "name": "Vapid explorer", "plate": "BCSD-13", "img": "/static/vehicles/car2.jpg"},
    {"id": "BCSD-15", "name": "Vapid explorer", "plate": "BCSD-15", "img": "/static/vehicles/car2.jpg"},
    {"id": "BCSD-12", "name": "Vapid explorer", "plate": "BCSD-12", "img": "/static/vehicles/car2.jpg"},
]

VEHICLES_BY_ID = {v["id"]: v for v in VEHICLES}
if len(VEHICLES_BY_ID) != len(VEHICLES):
    print("WARNING: Duplicate vehicle IDs in VEHICLES!", flush=True)

# ── Discord bot ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── DB init ────────────────────────────────────────────────────────────────────
def init_db():
    with sqlite3.connect("audit.db") as conn:
        c = conn.cursor()
        # кадровий аудит
        c.execute("""
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            executor TEXT,
            target TEXT,
            action TEXT,
            role TEXT,
            reason TEXT,
            date TEXT
        )""")
        # бронювання транспорту
        c.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_rentals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id TEXT NOT NULL,
            plate TEXT NOT NULL,
            model TEXT NOT NULL,
            taken_by_id TEXT NOT NULL,
            taken_by_name TEXT NOT NULL,
            duration TEXT NOT NULL,
            reason TEXT NOT NULL,
            taken_at TEXT NOT NULL,
            returned_at TEXT
        )""")
        # запити (іспит/присяга/лекція)
        c.execute("""
        CREATE TABLE IF NOT EXISTS exam_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_name TEXT NOT NULL,
            author_id   TEXT NOT NULL,
            action_type TEXT NOT NULL,  -- Присяга / Іспит / Лекція
            submitted_at TEXT NOT NULL  -- YYYY-MM-DD HH:MM:SS (Europe/Kyiv)
        )""")
        conn.commit()

init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────
def user_has_any_role(member, allowed_names):
    if not member or not allowed_names:
        return False
    names = {r.name for r in member.roles if r and r.name}
    return any(n in names for n in allowed_names)

def is_vehicle_taken(vehicle_id: str) -> bool:
    with sqlite3.connect("audit.db") as conn:
        c = conn.cursor()
        c.execute("SELECT 1 FROM vehicle_rentals WHERE vehicle_id=? AND returned_at IS NULL LIMIT 1", (vehicle_id,))
        return c.fetchone() is not None

def my_active_rentals(discord_user_id: str):
    with sqlite3.connect("audit.db") as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, vehicle_id, plate, model, duration, reason, taken_at
            FROM vehicle_rentals
            WHERE taken_by_id=? AND returned_at IS NULL
            ORDER BY taken_at DESC
        """, (discord_user_id,))
        return c.fetchall()

# ── Routes: базові ────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("login.html")

@app.route("/login")
def login():
    next_page = request.args.get("next", "/dashboard")
    if not str(next_page).startswith("/"):
        next_page = "/dashboard"
    url = (
        "https://discord.com/api/oauth2/authorize?"
        f"client_id={CLIENT_ID}"
        f"&redirect_uri={quote_plus(REDIRECT_URI)}"
        f"&response_type=code"
        f"&scope=identify%20guilds.members.read"
        f"&state={quote_plus(next_page)}"
    )
    return redirect(url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    next_page = request.args.get("state", "/dashboard")
    if not code:
        return "❌ Помилка авторизації."
    if not str(next_page).startswith("/"):
        next_page = "/dashboard"

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    if not r.ok:
        return f"❌ Помилка токену: {r.status_code} {r.text}"

    access_token = r.json()["access_token"]

    user_info = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    gm = requests.get(
        f"https://discord.com/api/users/@me/guilds/{GUILD_ID}/member",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if gm.status_code != 200:
        return "❌ Ви не є учасником сервера."

    roles = gm.json().get("roles", [])
    guild = discord.utils.get(bot.guilds, id=GUILD_ID)
    if not guild:
        return "❌ Бот не підключений до сервера або не має доступу."

    for r_id in roles:
        role = discord.utils.get(guild.roles, id=int(r_id))
        if role and role.name in ALLOWED_ROLES:
            session["user"] = user_info
            return redirect(next_page)

    return "❌ У вас немає доступу до кадрового аудиту."

# ── Кадровий аудит ────────────────────────────────────────────────────────────
@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "user" not in session:
        return redirect("/")
    guild = discord.utils.get(bot.guilds, id=GUILD_ID)
    if not guild:
        return "❌ Бот не бачить сервер."
    members = [(m.display_name, m.id) for m in guild.members if not m.bot]

    if request.method == "POST":
        executor    = session["user"]["username"]
        executor_id = session["user"]["id"]
        target_id   = request.form["user_id"]
        full_name   = request.form.get("full_name_id", "Невідомо")
        action      = request.form["action"]
        new_role    = request.form.get("role_name", "").strip()
        reason      = request.form.get("reason", "Без причини")

        member = discord.utils.get(guild.members, id=int(target_id)) if target_id.isdigit() else None
        mention = member.mention if member else f"`{target_id}`"
        target_name = member.display_name if member else target_id

        embed = discord.Embed(
            title="📋 Кадровий аудит | BCSD",
            description=(
                "━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **Кого:** {mention} | `{full_name}`\n"
                f"📌 **Дія:** `{action}`\n"
                f"🎖️ **Роль:** `{new_role or '-'}`\n"
                f"📝 **Підстава:** {reason}\n"
                f"🕒 **Дата:** `{datetime.now(ZoneInfo('Europe/Kyiv')):%d.%m.%Y}`\n"
                f"✍️ **Хто заповнив:** <@{executor_id}>\n"
                "━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="BCSD • Кадровий аудит")

        ch = bot.get_channel(LOG_CHANNEL_ID)
        if ch:
            bot.loop.create_task(ch.send(embed=embed))

        with sqlite3.connect("audit.db") as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO actions (executor, target, action, role, reason, date)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                executor,
                target_name,
                action,
                new_role or "-",
                reason,
                datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%Y-%m-%d %H:%M:%S")
            ))
            conn.commit()

        return redirect("/dashboard")

    return render_template("dashboard.html", members=members)

@app.route("/history")
def history():
    with sqlite3.connect("audit.db") as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM actions ORDER BY date DESC")
        rows = c.fetchall()

    actions = []
    for r in rows:
        try:
            d = datetime.strptime(r[6], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")
        except Exception:
            d = r[6]
        actions.append((r[0], r[1], r[2], r[3], r[4], r[5], d))

    return render_template("history.html", actions=actions)

@app.route("/download_db")
def download_db():
    return send_file("audit.db", as_attachment=True)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# ── SAI: звіт на підвищення ───────────────────────────────────────────────────
@app.route("/sai", methods=["GET", "POST"])
def sai_report():
    if "user" not in session:
        return redirect("/login?next=/sai")

    guild = discord.utils.get(bot.guilds, id=GUILD_ID)
    if not guild:
        return "❌ Бот не бачить сервер."

    member = discord.utils.get(guild.members, id=int(session["user"]["id"]))
    # За потреби — поверни перевірку на ролі:
    # if not user_has_any_role(member, SAI_ALLOWED_ROLES):
    #     need = ", ".join(SAI_ALLOWED_ROLES)
    #     return f"❌ У вас немає доступу до SAI (потрібна роль: {need})."

    if request.method == "POST":
        rank_from   = request.form.get("rank_from", "").strip()
        rank_to     = request.form.get("rank_to", "").strip()
        work_report = request.form.get("work_report", "").strip()

        if not rank_from or not rank_to or not work_report:
            return "❌ Заповніть усі обов'язкові поля.", 400

        author_id   = session["user"]["id"]
        author_name = session["user"].get("username", "Unknown")

        embed = discord.Embed(
            title="🆙 Звіт на підвищення | SAI",
            description=(
                "━━━━━━━━━━━━━━━━━━━\n"
                f"🧑‍✈️ **Хто подав:** <@{author_id}> (`{author_name}`)\n"
                f"🎖️ **Ранг:** {rank_from} → {rank_to}\n"
                f"📝 **Звіт:** {work_report}\n"
                f"🕒 **Дата:** `{datetime.now(ZoneInfo('Europe/Kyiv')):%d.%m.%Y}`\n"
                "━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text="BCSD • SAI")

        ch = bot.get_channel(SAI_LOG_CHANNEL_ID)
        if ch:
            bot.loop.create_task(ch.send(embed=embed))

        return redirect("/sai?ok=1")

    return render_template("sai_report.html")

# ── VEHICLES: вільні картки + взяти/повернути ────────────────────────────────
@app.route("/vehicles")
def vehicles():
    if "user" not in session:
        return redirect("/login?next=/vehicles")
    available = [v for v in VEHICLES if not is_vehicle_taken(v["id"])]
    mine = my_active_rentals(session["user"]["id"])
    return render_template("vehicles.html", vehicles=available, my_rentals=mine)

@app.route("/vehicles/take", methods=["POST"])
def vehicles_take():
    if "user" not in session:
        return redirect("/login?next=/vehicles")

    vehicle_id = request.form.get("vehicle_id", "").strip()
    duration   = request.form.get("duration", "").strip()
    reason     = request.form.get("reason", "").strip()

    v = VEHICLES_BY_ID.get(vehicle_id)   # надійний пошук
    if not v:
        return "❌ Невідомий транспорт.", 400
    if not duration or not reason:
        return "❌ Вкажіть тривалість і причину.", 400
    if is_vehicle_taken(vehicle_id):
        return "❌ Цей транспорт уже взяли.", 400

    user = session["user"]
    now_str = datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect("audit.db") as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO vehicle_rentals
            (vehicle_id, plate, model, taken_by_id, taken_by_name, duration, reason, taken_at, returned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """, (v["id"], v["plate"], v["name"], user["id"], user.get("username","Unknown"), duration, reason, now_str))
        conn.commit()

    # Embed у лог-канал
    embed = discord.Embed(
        title="🚓 Видача транспорту",
        description=(
            "━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Хто взяв:** <@{user['id']}> (`{user.get('username','Unknown')}`)\n"
            f"🪪 **Номера транспорту:** `{v['plate']}`\n"
            f"🚘 **Модель:** {v['name']}\n"
            f"⏳ **На час:** {duration}\n"
            f"📝 **Причина:** {reason}\n"
            f"🕒 **Дата:** `{datetime.now(ZoneInfo('Europe/Kyiv')):%d.%m.%Y %H:%M}`\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
        color=discord.Color.gold()
    )
    embed.set_footer(text="BCSD • Vehicle Request")
    ch = bot.get_channel(VEHICLE_LOG_CHANNEL_ID)
    if ch:
        bot.loop.create_task(ch.send(embed=embed))

    return redirect("/vehicles?ok=1")

@app.route("/vehicles/return", methods=["POST"])
def vehicles_return():
    if "user" not in session:
        return redirect("/login?next=/vehicles")

    rental_id = request.form.get("rental_id")
    if not rental_id:
        return redirect("/vehicles?err=no_id")

    with sqlite3.connect("audit.db") as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, vehicle_id, plate, model
            FROM vehicle_rentals
            WHERE id=? AND taken_by_id=? AND returned_at IS NULL
        """, (rental_id, session["user"]["id"]))
        row = c.fetchone()

        if not row:
            return redirect("/vehicles?err=not_found")

        now_str = datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("UPDATE vehicle_rentals SET returned_at=? WHERE id=?", (now_str, rental_id))
        conn.commit()

    embed = discord.Embed(
        title="✅ Повернення транспорту",
        description=(
            "━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Хто повернув:** <@{session['user']['id']}>\n"
            f"🪪 **Номера:** `{row[2]}`\n"
            f"🚘 **Модель:** {row[3]}\n"
            f"🕒 **Час:** `{datetime.now(ZoneInfo('Europe/Kyiv')):%d.%m.%Y %H:%M}`\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
        color=discord.Color.green()
    )
    embed.set_footer(text="BCSD • Vehicle Return")
    ch = bot.get_channel(VEHICLE_LOG_CHANNEL_ID)
    if ch:
        bot.loop.create_task(ch.send(embed=embed))

    return redirect("/vehicles?returned=1")

# ── Запит: іспит / присяга / лекція ──────────────────────────────────────────
@app.route("/exam_request", methods=["GET", "POST"])
def exam_request():
    if "user" not in session:
        return redirect("/login?next=/exam_request")

    if request.method == "POST":
        author_id   = session["user"]["id"]
        author_name = session["user"].get("username", "Unknown")

        action_type = (request.form.get("action_type") or "").strip()
        allowed = {"Присяга", "Іспит", "Лекція"}
        if action_type not in allowed:
            return "❌ Оберіть дію: Присяга / Іспит / Лекція.", 400

        now = datetime.now(ZoneInfo("Europe/Kyiv"))
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        with sqlite3.connect("audit.db") as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO exam_requests (author_name, author_id, action_type, submitted_at)
                VALUES (?, ?, ?, ?)
            """, (author_name, author_id, action_type, now_str))
            conn.commit()

        embed = discord.Embed(
            title="📨 Запит на іспит / присягу / лекцію",
            description=(
                "━━━━━━━━━━━━━━━━━━━\n"
                f"🧑‍✈️ **Хто подав:** <@{author_id}> (`{author_name}`)\n"
                f"🏷️ **Дія:** {action_type}\n"
                f"🕒 **Подано:** `{now:%d.%m.%Y %H:%M}`\n"
                "━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.purple()
        )
        embed.set_footer(text="BCSD • Exam/Oath/Lecture Request")

        ch = bot.get_channel(EXAM_LOG_CHANNEL_ID)
        if ch:
            bot.loop.create_task(ch.send(embed=embed))

        return redirect("/exam_request?ok=1")

    return render_template("exam_request.html")
@app.route("/sa", methods=["GET", "POST"])
def sa_report():
    if "user" not in session:
        return redirect("/login?next=/sa")

    guild = discord.utils.get(bot.guilds, id=GUILD_ID)
    if not guild:
        return "❌ Бот не бачить сервер."

    if request.method == "POST":
        rank_from   = request.form.get("rank_from", "").strip()
        rank_to     = request.form.get("rank_to", "").strip()
        work_report = request.form.get("work_report", "").strip()

        if not rank_from or not rank_to or not work_report:
            return "❌ Заповніть усі обов'язкові поля.", 400

        author_id   = session["user"]["id"]
        author_name = session["user"].get("username", "Unknown")

        embed = discord.Embed(
            title="🆙 Звіт на підвищення | SA",
            description=(
                "━━━━━━━━━━━━━━━━━━━\n"
                f"🧑‍✈️ **Хто подав:** <@{author_id}> (`{author_name}`)\n"
                f"🎖️ **Ранг:** {rank_from} → {rank_to}\n"
                f"📝 **Звіт:** {work_report}\n"
                f"🕒 **Дата:** `{datetime.now(ZoneInfo('Europe/Kyiv')):%d.%m.%Y}`\n"
                "━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text="BCSD • SA")

        ch = bot.get_channel(SA_LOG_CHANNEL_ID)
        if ch:
            bot.loop.create_task(ch.send(embed=embed))

        return redirect("/sa?ok=1")

    return render_template("sa_report.html")
# ENV:
CRAFT_LOG_CHANNEL_ID = int(os.getenv("CRAFT_LOG_CHANNEL_ID", LOG_CHANNEL_ID))
SENIOR_ROLE_NAME = os.getenv("SENIOR_ROLE_NAME", "Senior Staff")

# --- DB: додай таблицю craft_reports у init_db() ---
def init_db():
    with sqlite3.connect("audit.db") as conn:
        c = conn.cursor()
        # ... твої інші таблиці ...

        c.execute("""
        CREATE TABLE IF NOT EXISTS craft_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id TEXT NOT NULL,
            author_name TEXT NOT NULL,
            level INTEGER NOT NULL,
            discount_pct INTEGER NOT NULL,
            role_cap INTEGER NOT NULL,
            total_cost INTEGER NOT NULL,
            items_json TEXT NOT NULL,
            purpose TEXT NOT NULL,
            submitted_at TEXT NOT NULL
        )""")
        conn.commit()

# --- Константи крафту ---
# Знижка застосовується ТІЛЬКИ до зброї (is_weapon=True)
GUNSMITH_LEVELS = {
    1: {"discount_pct": 0},
    2: {"discount_pct": 10},
    3: {"discount_pct": 20},
    4: {"discount_pct": 30},
    5: {"discount_pct": 50},
}

# Ліміт матеріалів визначається РОЛЛЮ:
# є Senior Staff -> 900, інакше -> 500
def craft_role_cap(member):
    if not member:
        return 500
    names = {r.name for r in member.roles if r and r.name}
    return 900 if SENIOR_ROLE_NAME in names else 500

# Каталог предметів: base_cost = матеріали за 1 одиницю (або 1 пак для набоїв), is_weapon для знижки
CRAFT_ITEMS = {
    "handcuffs":               {"label": "Кайданки (1 шт)",                       "base_cost": 25,   "is_weapon": False},
    "armor":                   {"label": "Бронежилет (1 шт)",                     "base_cost": 20,   "is_weapon": False},
    "heavy_rifle_556":         {"label": "Важка гвинтівка [5.56x45] (1 шт)",      "base_cost": 56,   "is_weapon": True},
    "mre":                     {"label": "Сухпайок (1 шт)",                       "base_cost": 10,   "is_weapon": False},
    "drone":                   {"label": "Дрон (1 шт)",                           "base_cost": 4000, "is_weapon": False},
    "baton":                   {"label": "Поліцейська дубінка (1 шт)",            "base_cost": 10,   "is_weapon": False},
    "taser":                   {"label": "Тайзер (1 шт)",                         "base_cost": 20,   "is_weapon": False},
    "micro_smg_9x19":          {"label": "Мікро-ПП [9x19] (1 шт)",                "base_cost": 40,   "is_weapon": True},
    "smg":                     {"label": "Пістолет-пулемет (1 шт)",               "base_cost": 20,   "is_weapon": True},
    "pump_12_70":              {"label": "Помповий дробовик [12/70] (1 шт)",      "base_cost": 60,   "is_weapon": True},
    "carbine_mk2_556":         {"label": "Карабін Mk2 [5.56x45] (1 шт)",          "base_cost": 80,   "is_weapon": True},
    "carbine_556":             {"label": "Карабін [5.56x45] (1 шт)",              "base_cost": 40,   "is_weapon": True},
    "heavy_pistol_9x19":       {"label": "Важкий пістолет [9x19] (1 шт)",         "base_cost": 30,   "is_weapon": True},
    "pistol_mk2_9mm":          {"label": "Пістолет Mk2 [9mm] (1 шт)",             "base_cost": 30,   "is_weapon": True},

    # Набої — ціна за ПАК 10 шт
    "ammo_556_pack":           {"label": "Патрони [5.56x45] (пак 10 шт)",         "base_cost": 1,    "is_weapon": False},
    "ammo_9x19_pack":          {"label": "Патрони [9x19] (пак 10 шт)",            "base_cost": 1,    "is_weapon": False},
    "ammo_762x39_pack":        {"label": "Патрони [7.62x39] (пак 10 шт)",         "base_cost": 1,    "is_weapon": False},
    "ammo_338lm_pack":         {"label": "Патрони [.338 Lapua Magnum] (пак 10)",  "base_cost": 1,    "is_weapon": False},
    "ammo_12_70_pack":         {"label": "Патрони [12/70 Magnum Buckshot] (пак)", "base_cost": 1,    "is_weapon": False},
    "ammo_45acp_pack":         {"label": "Патрони [.45 ACP] (пак 10 шт)",         "base_cost": 1,    "is_weapon": False},
}

# Обчислення вартості з урахуванням знижки на зброю
def compute_craft_cost(items_qty: dict, level: int):
    level_info = GUNSMITH_LEVELS.get(level, {"discount_pct": 0})
    disc = int(level_info["discount_pct"])

    total = 0
    breakdown = []  # для ембеду та БД
    for key, qty in items_qty.items():
        if key not in CRAFT_ITEMS:
            continue
        if qty <= 0:
            continue
        base = CRAFT_ITEMS[key]["base_cost"]
        is_weapon = CRAFT_ITEMS[key]["is_weapon"]
        unit_cost = base
        if is_weapon and disc > 0:
            unit_cost = round(base * (100 - disc) / 100)
        cost = unit_cost * qty
        total += cost
        breakdown.append({
            "key": key,
            "label": CRAFT_ITEMS[key]["label"],
            "qty": qty,
            "unit_cost": unit_cost,
            "cost": cost,
            "is_weapon": is_weapon
        })
    return total, disc, breakdown

# --- Роут: форма/звіт крафту ---
# ── Конфіг для крафту ─────────────────────────────────────────────
CRAFT_ITEMS = {
    "handcuffs": {"label": "Кайданки", "base_cost": 25, "is_weapon": False},
    "armor": {"label": "Бронежилет", "base_cost": 20, "is_weapon": False},
    "heavy_rifle_556": {"label": "Важка гвинтівка [5.56x45]", "base_cost": 56, "is_weapon": True},
    "ration": {"label": "Сухпайок", "base_cost": 10, "is_weapon": False},
    "drone": {"label": "Дрон", "base_cost": 4000, "is_weapon": False},
    "baton": {"label": "Поліцейська дубінка", "base_cost": 10, "is_weapon": False},
    "taser": {"label": "Тайзер", "base_cost": 20, "is_weapon": False},
    "micro_smg": {"label": "Мікро-ПП [9x19]", "base_cost": 40, "is_weapon": True},
    "smg": {"label": "Пістолет-кулемет", "base_cost": 20, "is_weapon": True},
    "pump_shotgun": {"label": "Помповий дробовик [12/70]", "base_cost": 60, "is_weapon": True},
    "carbine_mk2": {"label": "Карабін Mk II [5.56x45]", "base_cost": 80, "is_weapon": True},
    "carbine": {"label": "Карабін [5.56x45]", "base_cost": 40, "is_weapon": True},
    "heavy_pistol": {"label": "Важкий пістолет [9x19]", "base_cost": 30, "is_weapon": True},
    "pistol_mk2": {"label": "Пістолет Mk II [9mm]", "base_cost": 30, "is_weapon": True},

    # Боєприпаси
    "ammo_556": {"label": "Патрони [5.56x45] (10 шт.)", "base_cost": 1, "is_weapon": False},
    "ammo_9mm": {"label": "Патрони [9x19] (10 шт.)", "base_cost": 1, "is_weapon": False},
    "ammo_762": {"label": "Патрони [7.62x39] (10 шт.)", "base_cost": 1, "is_weapon": False},
    "ammo_338": {"label": "Патрони [.338 LAPUA MAGNUM] (10 шт.)", "base_cost": 1, "is_weapon": False},
    "ammo_12g": {"label": "Патрони [12/70 MAGNUM BUCKSHOT] (10 шт.)", "base_cost": 1, "is_weapon": False},
    "ammo_45": {"label": "Патрони [.45 ACP] (10 шт.)", "base_cost": 1, "is_weapon": False}
}

GUNSMITH_LEVELS = {
    1: {"discount_pct": 0, "cap": 500},
    2: {"discount_pct": 10, "cap": 750},
    3: {"discount_pct": 20, "cap": 1000},
    4: {"discount_pct": 30, "cap": 1250},
    5: {"discount_pct": 50, "cap": 1500},
}

CRAFT_LOG_CHANNEL_ID = int(os.getenv("CRAFT_LOG_CHANNEL_ID", 0))

# ── Функції ───────────────────────────────────────────────────────
def craft_role_cap(member):
    """Визначає ліміт за роллю (900 для Senior Staff, інакше стандарт по рівню)."""
    if not member:
        return 500
    if any(r.name == "Senior Staff" for r in member.roles):
        return 900
    return 500

def compute_craft_cost(items_qty, level):
    """Підраховує загальну вартість, знижку і детальний список."""
    discount_pct = GUNSMITH_LEVELS.get(level, {}).get("discount_pct", 0)
    breakdown = []
    total_cost = 0

    for key, qty in items_qty.items():
        if key not in CRAFT_ITEMS or qty <= 0:
            continue
        unit_cost = CRAFT_ITEMS[key]["base_cost"]
        if CRAFT_ITEMS[key]["is_weapon"] and discount_pct > 0:
            unit_cost = round(unit_cost * (100 - discount_pct) / 100)
        cost = unit_cost * qty
        total_cost += cost
        breakdown.append({
            "label": CRAFT_ITEMS[key]["label"],
            "qty": qty,
            "unit_cost": unit_cost,
            "cost": cost
        })

    return total_cost, discount_pct, breakdown

# ── Роут ──────────────────────────────────────────────────────────
@app.route("/craft", methods=["GET", "POST"])
def craft_report():
    if "user" not in session:
        return redirect("/login?next=/craft")

    guild = discord.utils.get(bot.guilds, id=GUILD_ID)
    member = discord.utils.get(guild.members, id=int(session["user"]["id"])) if guild else None
    role_cap = craft_role_cap(member)

    if request.method == "POST":
        author_id = session["user"]["id"]
        author_name = session["user"].get("username", "Unknown")

        try:
            level = int(request.form.get("level", "1"))
            if level not in GUNSMITH_LEVELS:
                raise ValueError()
        except Exception:
            return "❌ Невірно вказаний рівень.", 400

        purpose = (request.form.get("purpose") or "").strip()
        if not purpose:
            return "❌ Вкажіть мету.", 400

        items_qty = {}
        for key in CRAFT_ITEMS.keys():
            try:
                qty = int(request.form.get(f"q_{key}", "0"))
            except Exception:
                qty = 0
            items_qty[key] = max(0, qty)

        total_cost, discount_pct, breakdown = compute_craft_cost(items_qty, level)

        if total_cost > role_cap:
            return f"❌ Перевищено ліміт матеріалів: {total_cost} > {role_cap}.", 400

        now = datetime.now(ZoneInfo("Europe/Kyiv"))
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        import json
        with sqlite3.connect("audit.db") as conn:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS craft_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    author_id TEXT,
                    author_name TEXT,
                    level INTEGER,
                    discount_pct INTEGER,
                    role_cap INTEGER,
                    total_cost INTEGER,
                    items_json TEXT,
                    purpose TEXT,
                    submitted_at TEXT
                )
            """)
            c.execute("""
                INSERT INTO craft_reports
                    (author_id, author_name, level, discount_pct, role_cap, total_cost, items_json, purpose, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                author_id, author_name, level, discount_pct, role_cap, total_cost,
                json.dumps(breakdown, ensure_ascii=False), purpose, now_str
            ))
            conn.commit()

        lines = [f"- {item['label']}: x{item['qty']} × {item['unit_cost']} = {item['cost']}" for item in breakdown]

        desc = (
            f"🧑‍🏭 **Хто крафтить:** <@{author_id}> (`{author_name}`)\n"
            f"🛠️ **Рівень зброяра:** {level} (знижка: {discount_pct}%)\n"
            f"📦 **Ліміт:** {role_cap} матеріалів\n"
            f"🎯 **Мета:** {purpose}\n"
            f"🧾 **Сума:** {total_cost} матеріалів\n"
            f"📄 **Номенклатура:**\n" + ("\n".join(lines) if lines else "—") + "\n"
            f"🕒 **Дата:** `{now:%d.%m.%Y %H:%M}`\n"
        )

        embed = discord.Embed(title="🧰 Звіт крафту", description=desc, color=discord.Color.teal())
        embed.set_footer(text="BCSD • Craft Report")

        ch = bot.get_channel(CRAFT_LOG_CHANNEL_ID)
        if ch:
            bot.loop.create_task(ch.send(embed=embed))

        return redirect("/craft?ok=1")

    return render_template(
        "craft_report.html",
        catalog=CRAFT_ITEMS,
        role_cap=role_cap,
        levels=GUNSMITH_LEVELS
    )



# ── Run ───────────────────────────────────────────────────────────────────────
def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run(BOT_TOKEN)

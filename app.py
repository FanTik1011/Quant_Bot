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

import uuid
from werkzeug.utils import secure_filename


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
# ── Craft: імпорти ────────────────────────────────────────────────────────────
import uuid
from werkzeug.utils import secure_filename
from flask import send_from_directory, url_for

# ── CRAFT ENV / CONFIG ────────────────────────────────────────────────────────
CRAFT_LOG_CHANNEL_ID = int(os.getenv("CRAFT_LOG_CHANNEL_ID", LOG_CHANNEL_ID))
SENIOR_ROLE_NAME     = os.getenv("SENIOR_ROLE_NAME", "Senior Staff")

# Обмеження на розмір запиту з файлами (наприклад, 25MB)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

# Каталог для завантажень
UPLOAD_DIR = os.path.join(app.static_folder, "craft_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTS = {"png", "jpg", "jpeg", "webp"}

def _allowed_file(filename: str) -> bool:
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_EXTS

# ── DB: init (єдина версія, без дублів!) ─────────────────────────────────────
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
            action_type TEXT NOT NULL,
            submitted_at TEXT NOT NULL
        )""")

        # крафт — звіти
        c.execute("""
        CREATE TABLE IF NOT EXISTS craft_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id    TEXT NOT NULL,
            author_name  TEXT NOT NULL,
            level        INTEGER NOT NULL,
            discount_pct INTEGER NOT NULL,
            role_cap     INTEGER NOT NULL,
            total_cost   INTEGER NOT NULL,
            items_json   TEXT NOT NULL,
            purpose      TEXT NOT NULL,
            submitted_at TEXT NOT NULL
        )""")

        # крафт — фото (багато-до-одного craft_report_id)
        c.execute("""
        CREATE TABLE IF NOT EXISTS craft_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            craft_report_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            FOREIGN KEY (craft_report_id) REFERENCES craft_reports(id)
        )""")

        conn.commit()

# ВАЖЛИВО: викликати лише ОДИН раз у всьому файлі
init_db()

# ── КОНСТАНТИ КРАФТУ ─────────────────────────────────────────────────────────
# знижка застосовується лише до зброї (is_weapon=True)
GUNSMITH_LEVELS = {
    1: {"discount_pct": 0},
    2: {"discount_pct": 10},
    3: {"discount_pct": 20},
    4: {"discount_pct": 30},
    5: {"discount_pct": 50},
}

# Ліміт за роллю: Senior Staff → 900, інакше → 500
def craft_role_cap(member):
    if not member:
        return 500
    names = {r.name for r in member.roles if r and r.name}
    return 900 if SENIOR_ROLE_NAME in names else 500

# Каталог предметів
CRAFT_ITEMS = {
    "handcuffs":         {"label": "Кайданки (1 шт)",                              "base_cost": 25,   "is_weapon": False},
    "armor":             {"label": "Бронежилет (1 шт)",                            "base_cost": 20,   "is_weapon": False},
    "heavy_rifle_556":   {"label": "Важка гвинтівка [5.56x45] (1 шт)",             "base_cost": 57,   "is_weapon": True},
    "mre":               {"label": "Сухпайок (1 шт)",                              "base_cost": 10,   "is_weapon": False},
    "drone":             {"label": "Дрон (1 шт)",                                  "base_cost": 4000, "is_weapon": False},
    "baton":             {"label": "Поліцейська дубінка (1 шт)",                   "base_cost": 10,   "is_weapon": False},
    "taser":             {"label": "Тайзер (1 шт)",                                "base_cost": 20,   "is_weapon": False},
    "micro_smg_9x19":    {"label": "Мікро-ПП [9x19] (1 шт)",                       "base_cost": 40,   "is_weapon": True},
    "smg":               {"label": "Міні-СМГ (1 шт)",                      "base_cost": 20,   "is_weapon": True},
    "pump_12_70":        {"label": "Помповий дробовик [12/70] (1 шт)",             "base_cost": 60,   "is_weapon": True},
    "carbine_mk2_556":   {"label": "Карабін Mk 2 [5.56x45] (1 шт)",                "base_cost": 80,   "is_weapon": True},
    "carbine_556":       {"label": "Карабін [5.56x45] (1 шт)",                     "base_cost": 40,   "is_weapon": True},
    "heavy_pistol_9x19": {"label": "Важкий пістолет [9x19] (1 шт)",                "base_cost": 30,   "is_weapon": True},
    "pistol_mk2_9mm":    {"label": "Пістолет Mk 2 [9mm] (1 шт)",                   "base_cost": 30,   "is_weapon": True},

    # Патрони — ціна за пак 10 шт
# Патрони — ціна за 1 шт
    "ammo_556_pack":    {"label": "Патрони [5.56x45] (1 шт)", "base_cost": 0.1, "is_weapon": False},
    "ammo_9x19_pack":   {"label": "Патрони [9x19] (1 шт)",    "base_cost": 0.1, "is_weapon": False},
    "ammo_762x39_pack": {"label": "Патрони [7.62x39] (1 шт)", "base_cost": 0.1, "is_weapon": False},
    "ammo_338lm_pack":  {"label": "Патрони [.338 LAPUA MAGNUM] (1 шт)", "base_cost": 0.1, "is_weapon": False},
    "ammo_12_70_pack":  {"label": "Патрони [12/70 MAGNUM BUCKSHOT] (1 шт)", "base_cost": 0.1, "is_weapon": False},
    "ammo_45acp_pack":  {"label": "Патрони [.45 ACP] (1 шт)", "base_cost": 0.1, "is_weapon": False},

}

def compute_craft_cost(items_qty: dict, level: int):
    level_info = GUNSMITH_LEVELS.get(level, {"discount_pct": 0})
    disc = int(level_info["discount_pct"])
    total = 0
    breakdown = []
    for key, qty in items_qty.items():
        if key not in CRAFT_ITEMS or qty <= 0:
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

# ── ROUTE: /craft ─────────────────────────────────────────────────────────────
@app.route("/craft", methods=["GET", "POST"])
def craft_report():
    if "user" not in session:
        return redirect("/login?next=/craft")

    guild  = discord.utils.get(bot.guilds, id=GUILD_ID)
    member = discord.utils.get(guild.members, id=int(session["user"]["id"])) if guild else None
    role_cap = craft_role_cap(member)  # 900 або 500

    if request.method == "POST":
        author_id   = session["user"]["id"]
        author_name = session["user"].get("username", "Unknown")

        # рівень
        try:
            level = int(request.form.get("level", "1"))
            if level not in GUNSMITH_LEVELS:
                raise ValueError()
        except Exception:
            return "❌ Невірно вказаний рівень.", 400

        # мета
        purpose = (request.form.get("purpose") or "").strip()
        if not purpose:
            return "❌ Вкажіть мету (добова норма / ВЗХ/ВЗГ/ВЗА / Постачання / інше).", 400

        # кількості
        items_qty = {}
        for key in CRAFT_ITEMS.keys():
            try:
                qty = int(request.form.get(f"q_{key}", "0"))
            except Exception:
                qty = 0
            items_qty[key] = max(0, qty)

        # підрахунок
        total_cost, discount_pct, breakdown = compute_craft_cost(items_qty, level)

        # ліміт
        if total_cost > role_cap:
            return f"❌ Перевищено ліміт матеріалів: {total_cost} > {role_cap}. Скоротіть кількість.", 400

        # час
        now = datetime.now(ZoneInfo("Europe/Kyiv"))
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # збереження в БД
        import json
        with sqlite3.connect("audit.db") as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO craft_reports
                    (author_id, author_name, level, discount_pct, role_cap, total_cost, items_json, purpose, submitted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                author_id, author_name, level, discount_pct, role_cap, total_cost,
                json.dumps(breakdown, ensure_ascii=False), purpose, now_str
            ))
            craft_id = c.lastrowid

            # ФОТО: збереження у файлову систему + відносні шляхи у БД
            files = request.files.getlist("photos")
            saved_paths = []
            for f in files:
                if not f or not f.filename:
                    continue
                if not _allowed_file(f.filename):
                    continue
                safe_name = secure_filename(f.filename)
                # унікальна назва
                unique = f"{uuid.uuid4().hex}_{safe_name}"
                abs_path = os.path.join(UPLOAD_DIR, unique)
                rel_path = f"/static/craft_uploads/{unique}"
                f.save(abs_path)
                saved_paths.append(rel_path)
                c.execute("""
                    INSERT INTO craft_photos (craft_report_id, file_path)
                    VALUES (?, ?)
                """, (craft_id, rel_path))

            conn.commit()

        # ембед у Discord (що і скільки штук)
        lines = []
        for item in breakdown:
            lines.append(f"- {item['label']}: **{item['qty']} шт** × {item['unit_cost']} = {item['cost']}")

        desc = (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"🧑‍🏭 **Хто крафтить:** <@{author_id}> (`{author_name}`)\n"
            f"🛠️ **Рівень зброяра:** {level} (знижка на зброю: {discount_pct}%)\n"
            f"📦 **Ліміт за роллю:** {role_cap} матеріалів\n"
            f"🎯 **Мета:** {purpose}\n"
            f"🧾 **Сума:** {total_cost} матеріалів\n"
            f"📄 **Номенклатура:**\n" + ("\n".join(lines) if lines else "—") + "\n"
            f"🕒 **Дата:** `{now:%d.%m.%Y %H:%M}`\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "_Нагадування: прошу прикріпити фото докази до повідомлення._"
        )

        embed = discord.Embed(
            title="🧰 Звіт крафту",
            description=desc,
            color=discord.Color.teal()
        )
        embed.set_footer(text="BCSD • Craft Report")

        # якщо є фото — додамо перше як прев'ю (Discord дозволяє одне зображення в Embed)
        # решту можна прикріпити як окремі повідомлення, якщо дуже треба
        if 'saved_paths' in locals() and saved_paths:
            embed.set_image(url=saved_paths[0])

        ch = bot.get_channel(CRAFT_LOG_CHANNEL_ID)
        if ch:
            bot.loop.create_task(ch.send(embed=embed))
            # якщо хочеш докинути решту фото окремими повідомленнями:
            # for p in saved_paths[1:]:
            #     bot.loop.create_task(ch.send(p))

        return redirect("/craft?ok=1")

    # GET
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

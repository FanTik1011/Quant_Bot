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

ALLOWED_ROLES      = [r.strip() for r in os.getenv("ALLOWED_ROLES", "").split(",") if r.strip()]
SAI_ALLOWED_ROLES  = [r.strip() for r in os.getenv("SAI_ALLOWED_ROLES", "BCSD").split(",") if r.strip()]
SAI_LOG_CHANNEL_ID = int(os.getenv("SAI_LOG_CHANNEL_ID", LOG_CHANNEL_ID))
VEHICLE_LOG_CHANNEL_ID = int(os.getenv("VEHICLE_LOG_CHANNEL_ID", LOG_CHANNEL_ID))

# Список транспорту для карток (приклад; допиши свої)
VEHICLES = [
    {"id": "car_01", "name": "Dodge Charger Sheriff", "plate": "BCSD-001", "img": "/static/vehicles/car2.jpg"},
    {"id": "car_02", "name": "Ford Explorer Sheriff", "plate": "BCSD-002", "img": "/static/vehicles/car1.jpg"},
    {"id": "car_03", "name": "Motorcycle Sheriff",    "plate": "BCSD-003", "img": "/static/vehicles/car1.jpg"},
]


# ── Discord bot ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── DB init (без змін у структурах) ───────────────────────────────────────────
def init_db():
    with sqlite3.connect("audit.db") as conn:
        c = conn.cursor()
        c.execute('''
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            executor TEXT,
            target TEXT,
            action TEXT,
            role TEXT,
            reason TEXT,
            date TEXT
        )''')
        c.execute('''
        CREATE TABLE IF NOT EXISTS military_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            static_id TEXT,
            days INTEGER,
            amount REAL,
            issued_by TEXT,
            date TEXT
        )''')
        conn.commit()

init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────
def user_has_any_role(member: discord.Member, allowed_names: list[str]) -> bool:
    """Перевіряє, чи має користувач хоча б одну з дозволених ролей (за назвою)."""
    if not member or not allowed_names:
        return False
    member_role_names = {r.name for r in member.roles if r and r.name}
    return any(name in member_role_names for name in allowed_names)

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("login.html")

# /login з підтримкою next → після OAuth редіректимо куди треба (наприклад, /sai)
@app.route("/login")
def login():
    next_page = request.args.get("next", "/dashboard")
    if not next_page.startswith("/"):
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
    if not next_page.startswith("/"):
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

    guild_member_resp = requests.get(
        f"https://discord.com/api/users/@me/guilds/{GUILD_ID}/member",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if guild_member_resp.status_code != 200:
        return "❌ Ви не є учасником сервера."

    roles = guild_member_resp.json().get("roles", [])
    guild = discord.utils.get(bot.guilds, id=GUILD_ID)
    if not guild:
        return "❌ Бот не підключений до сервера або не має доступу."

    # доступ у "кадровий аудит" за ALLOWED_ROLES
    for r_id in roles:
        role = discord.utils.get(guild.roles, id=int(r_id))
        if role and role.name in ALLOWED_ROLES:
            session["user"] = user_info
            return redirect(next_page)

    return "❌ У вас немає доступу до кадрового аудиту."

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

# ── SAI: звіт на підвищення (доступ ТІЛЬКИ для SAI_ALLOWED_ROLES) ────────────
@app.route("/sai", methods=["GET", "POST"])
def sai_report():
    # якщо не авторизований — відправляємо на OAuth і повернемось сюди
    if "user" not in session:
        return redirect("/login?next=/sai")

    guild = discord.utils.get(bot.guilds, id=GUILD_ID)
    if not guild:
        return "❌ Бот не бачить сервер."
    member = discord.utils.get(guild.members, id=int(session["user"]["id"]))
    if not user_has_any_role(member, SAI_ALLOWED_ROLES):
        need = ", ".join(SAI_ALLOWED_ROLES)
        return f"❌ У вас немає доступу до SAI (потрібна роль: {need})."

    if request.method == "POST":
        author_tag  = request.form.get("author_tag", "").strip()
        rank_from   = request.form.get("rank_from", "").strip()
        rank_to     = request.form.get("rank_to", "").strip()
        work_report = request.form.get("work_report", "").strip()

        if not author_tag or not rank_from or not rank_to or not work_report:
            return "❌ Заповніть усі обов'язкові поля.", 400

        author_id = session["user"]["id"]

        embed = discord.Embed(
            title="🆙 Звіт на підвищення | SAI",
            description=(
                "━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **Тег:** {author_tag}\n"
                f"🎖️ **Ранг:** {rank_from} → {rank_to}\n"
                f"📝 **Звіт:** {work_report}\n"
                f"🕒 **Дата:** `{datetime.now(ZoneInfo('Europe/Kyiv')):%d.%m.%Y}`\n"
                f"✍️ **Хто подав:** <@{author_id}>\n"
                "━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text="BCSD • SAI")

        ch = bot.get_channel(SAI_LOG_CHANNEL_ID)
        if ch:
            bot.loop.create_task(ch.send(embed=embed))

        return redirect("/sai")

    return render_template("sai_report.html")
@app.route("/vehicles")
def vehicles():
    if "user" not in session:
        return redirect("/login?next=/vehicles")
    return render_template("vehicles.html", vehicles=VEHICLES)
@app.route("/vehicles/take", methods=["POST"])
def vehicles_take():
    if "user" not in session:
        return redirect("/login?next=/vehicles")

    vehicle_id = request.form.get("vehicle_id", "").strip()
    duration   = request.form.get("duration", "").strip()   # "2 години", "до 18:00", тощо
    reason     = request.form.get("reason", "").strip()

    v = next((x for x in VEHICLES if x["id"] == vehicle_id), None)
    if not v:
        return "❌ Невідомий транспорт.", 400
    if not duration or not reason:
        return "❌ Вкажіть тривалість і причину.", 400

    user = session["user"]
    executor_name = user.get("username", "Невідомо")
    executor_id   = user.get("id")

    embed = discord.Embed(
        title="🚓 Видача транспорту",
        description=(
            "━━━━━━━━━━━━━━━━━━━\n"
            f"👤 **Хто взяв:** <@{executor_id}> (`{executor_name}`)\n"
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

    # Повертаємося на список з коротким підтвердженням у query (можеш показати алерт на фронті)
    return redirect("/vehicles?ok=1")


# ── Run ───────────────────────────────────────────────────────────────────────
def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run(BOT_TOKEN)

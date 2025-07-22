import os
import threading
import sqlite3
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, redirect, session, send_file
from dotenv import load_dotenv
import discord
from discord.ext import commands

load_dotenv()
app = Flask(__name__, static_folder="static")
app.secret_key = os.getenv("SECRET_KEY")

BOT_TOKEN            = os.getenv("BOT_TOKEN")
GUILD_ID             = int(os.getenv("GUILD_ID"))
LOG_CHANNEL_ID       = int(os.getenv("LOG_CHANNEL_ID"))
TICKETS_CHANNEL_ID   = int(os.getenv("TICKETS_CHANNEL_ID"))
CLIENT_ID            = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET        = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI         = os.getenv("DISCORD_REDIRECT_URI")
TICKETS_REDIRECT_URI = os.getenv("DISCORD_TICKETS_REDIRECT_URI")
ALLOWED_ROLES        = os.getenv("ALLOWED_ROLES").split(",")
ALLOWED_TICKET_ROLES = ["Командування National Guard"]

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

def init_db():
    with sqlite3.connect("audit.db") as conn:
        c = conn.cursor()
        # таблиця кадрового аудиту
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
        # таблиця військових квитків
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

# ——— Кадровий аудит ———

@app.route("/")
def index():
    return render_template("login.html")

@app.route("/login")
def login():
    url = (
        f"https://discord.com/api/oauth2/authorize?"
        f"client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20guilds.members.read"
    )
    return redirect(url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "❌ Помилка авторизації."

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

    guild_member = requests.get(
        f"https://discord.com/api/users/@me/guilds/{GUILD_ID}/member",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if guild_member.status_code != 200:
        return "❌ Ви не є учасником сервера."

    roles = guild_member.json().get("roles", [])
    guild = discord.utils.get(bot.guilds, id=GUILD_ID)
    for r_id in roles:
        role = discord.utils.get(guild.roles, id=int(r_id))
        if role and role.name in ALLOWED_ROLES:
            session["user"] = user_info
            return redirect("/dashboard")

    return "❌ У вас немає доступу до кадрового аудиту."

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "user" not in session:
        return redirect("/")

    guild   = discord.utils.get(bot.guilds, id=GUILD_ID)
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
            title="📋 Кадровий аудит | National Guard",
            description=(
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **Кого:** {mention} | `{full_name}`\n"
                f"📌 **Дія:** `{action}`\n"
                f"🎖️ **Роль:** `{new_role or '-'}`\n"
                f"📝 **Підстава:** {reason}\n"
                f"🕒 **Дата:** `{datetime.now(ZoneInfo('Europe/Kyiv')):%d.%m.%Y}`\n"
                f"✍️ **Хто заповнив:** <@{executor_id}>\n"
                f"━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="National Guard • Кадровий аудит")

        ch = bot.get_channel(LOG_CHANNEL_ID)
        if ch:
            bot.loop.create_task(ch.send(embed=embed))

        with sqlite3.connect("audit.db") as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO actions
                (executor, target, action, role, reason, date)
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
        except:
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


# ——— Облік військових квитків ———

@app.route("/login_tickets")
def login_tickets():
    url = (
        f"https://discord.com/api/oauth2/authorize?"
        f"client_id={CLIENT_ID}"
        f"&redirect_uri={TICKETS_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20guilds.members.read"
    )
    return redirect(url)

@app.route("/tickets_callback")
def tickets_callback():
    code = request.args.get("code")
    if not code:
        return "❌ Помилка авторизації."

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": TICKETS_REDIRECT_URI,
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

    guild_member = requests.get(
        f"https://discord.com/api/users/@me/guilds/{GUILD_ID}/member",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if guild_member.status_code != 200:
        return "❌ Ви не є учасником сервера."

    roles = guild_member.json().get("roles", [])
    guild = discord.utils.get(bot.guilds, id=GUILD_ID)
    for r_id in roles:
        role = discord.utils.get(guild.roles, id=int(r_id))
        if role and role.name in ALLOWED_TICKET_ROLES:
            session["user"] = user_info
            return redirect("/tickets")

    return "❌ У вас немає доступу до обліку квитків."

@app.route("/tickets", methods=["GET", "POST"])
def tickets():
    if "user" not in session:
        return redirect("/")

    if request.method == "POST":
        issuer    = session["user"]["username"]
        issued_id = session["user"]["id"]
        name      = request.form["name"]
        static_id = request.form["static_id"]
        days      = int(request.form["days"])
        amount    = float(request.form["amount"])
        now_kyiv  = datetime.now(ZoneInfo("Europe/Kyiv"))

        # запис у БД
        with sqlite3.connect("audit.db") as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO military_tickets
                (name, static_id, days, amount, issued_by, date)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                name,
                static_id,
                days,
                amount,
                issuer,
                now_kyiv.strftime("%Y-%m-%d %H:%M:%S")
            ))
            conn.commit()

        # embed
        embed = discord.Embed(
            title="🎫 Облік військових квитків",
            description=(
                f"👤 **Кому:** {name} | `{static_id}`\n"
                f"📆 **Днів:** {days}\n"
                f"💰 **Сума:** `{amount:.2f}$`\n"
                f"🗓 **Дата:** `{now_kyiv.strftime('%d.%m.%Y')}`\n"
                f"✍️ **Видав:** <@{issued_id}>"
            ),
            color=discord.Color.green()
        )
        ch = bot.get_channel(TICKETS_CHANNEL_ID)
        if ch:
            bot.loop.create_task(ch.send(embed=embed))

        return redirect("/tickets")

    return render_template("tickets.html")

# ——— Запуск ———

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run(BOT_TOKEN)

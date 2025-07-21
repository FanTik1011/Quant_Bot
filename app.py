import os
import threading
import sqlite3
import requests
from datetime import datetime
from flask import Flask, render_template, request, redirect, session, Response, send_file
from dotenv import load_dotenv
import discord
from discord.ext import commands

load_dotenv()
app = Flask(__name__, static_folder="static")
app.secret_key = os.getenv("SECRET_KEY")

BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
ALLOWED_ROLES = os.getenv("ALLOWED_ROLES").split(",")

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Створення бази даних
def init_db():
    with sqlite3.connect("audit.db") as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            executor TEXT,
            target TEXT,
            action TEXT,
            role TEXT,
            reason TEXT,
            date TEXT
        )''')
        conn.commit()

init_db()

@app.route("/")
def index():
    return render_template("login.html")

@app.route("/login")
def login():
    url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds.members.read"
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
        return f"❌ Помилка при отриманні токену. {r.status_code}: {r.text}"

    access_token = r.json().get("access_token")
    user_info = requests.get("https://discord.com/api/users/@me", headers={
        "Authorization": f"Bearer {access_token}"
    }).json()

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

    return "❌ У вас немає доступу."

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "user" not in session:
        return redirect("/")

    guild = discord.utils.get(bot.guilds, id=GUILD_ID)
    members = [(m.display_name, m.id) for m in guild.members if not m.bot]

    if request.method == "POST":
        executor = session["user"]["username"]
        executor_id = session["user"]["id"]
        target_id = request.form.get("user_id")
        action = request.form.get("action")
        new_role = request.form.get("role_name", "").strip()
        reason = request.form.get("reason", "Без причини")
        full_name_id = request.form.get("full_name_id", "Невідомо")

        member = discord.utils.get(guild.members, id=int(target_id)) if target_id.isdigit() else None
        member_mention = member.mention if member else full_name_id
        member_display = member.display_name if member else full_name_id


        embed = discord.Embed(
            title="📋 Кадровий аудит | National Guard",
            description=(
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **Кого:** {member_mention} | `{full_name_id}`\n"
                f"📌 **Дія:** `{action}`\n"
                f"🎖️ **Роль:** `{new_role if new_role else '-'}`\n"
                f"📝 **Підстава:** {reason}\n"
                f"🕒 **Дата:** `{datetime.now().strftime('%d.%m.%Y')}`\n"
                f"✍️ **Хто заповнив:** <@{executor_id}>\n"
                f"━━━━━━━━━━━━━━━━━━━"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Форма кадрового аудиту • National Guard")

        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            bot.loop.create_task(log_channel.send(embed=embed))

        with sqlite3.connect("audit.db") as conn:
            c = conn.cursor()
            c.execute("INSERT INTO actions (executor, target, action, role, reason, date) VALUES (?, ?, ?, ?, ?, ?)",
          (executor, member_display, action, new_role if new_role else "-", reason,
           datetime.now().strftime("%Y-%m-%d %H:%M:%S")))

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
        for row in rows:
            try:
                formatted_date = datetime.strptime(row[6], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")
            except:
                formatted_date = row[6]  # Якщо формат неочікуваний — залишаємо як є

            # Додаємо новий кортеж із форматованою датою
            actions.append((row[0], row[1], row[2], row[3], row[4], row[5], formatted_date))

    return render_template("history.html", actions=actions)

@app.route("/download_db")
def download_db():
    return send_file("audit.db", as_attachment=True)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run(BOT_TOKEN)

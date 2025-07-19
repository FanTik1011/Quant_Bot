import os
import threading
import asyncio
from flask import Flask, render_template, request, redirect, session, url_for
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))
SECRET_KEY = os.getenv("SECRET_KEY")
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "").split(",")

RANK_ROLE_NAMES = [
    "Guest", "Курсант", "Сержант", "Лейтенант",
    "Капітан", "Майор", "Полковник", "Генерал"
]

intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
app = Flask(__name__)
app.secret_key = SECRET_KEY
bot_ready = asyncio.Event()

authenticated_users = {}  # session_id: discord_user_id

@bot.event
async def on_ready():
    print(f"✅ Бот запущено як {bot.user}")
    bot_ready.set()

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        discord_id = request.form.get("discord_id", "").strip()
        pin = request.form.get("pin", "").strip()

        if discord_id in ALLOWED_USERS and pin == os.getenv("ACCESS_PIN"):
            session["user_id"] = discord_id
            return redirect("/dashboard")
        return "❌ Доступ заборонено. Невірний ID або PIN."

    return render_template("login.html")

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "user_id" not in session or session["user_id"] not in ALLOWED_USERS:
        return redirect("/")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot_ready.wait())

    guild = discord.utils.get(bot.guilds, id=GUILD_ID)
    if not guild:
        return "❌ Бот не бачить сервер."

    if request.method == "POST":
        action = request.form.get("action")
        user_id_raw = request.form.get("user_id", "").strip()
        reason = request.form.get("reason", "Без причини")
        author = f"Discord ID: {session['user_id']}"

        if not user_id_raw.isdigit():
            return "❌ Некоректний ID користувача."
        user_id = int(user_id_raw)

        role_id_raw = request.form.get("role_id", "").strip()
        if action in ["promote", "demote"]:
            if not role_id_raw.isdigit():
                return "❌ Некоректний ID ролі."
            role_id = int(role_id_raw)
        else:
            role_id = None

        member = guild.get_member(user_id)
        role = guild.get_role(role_id) if role_id else None

        if not member:
            return "❌ Користувач не знайдений."

        if action == "kick":
            loop.create_task(member.kick(reason=reason))
            loop.create_task(send_log("❌ Виганяється", member, None, reason, author))
            return "✅ Користувача вигнано."

        elif action in ["promote", "demote"]:
            loop.create_task(handle_action("📈 Підвищення" if action == "promote" else "📉 Пониження", member, role, reason, author))
            return "✅ Дію виконано."

        elif action == "accepted":
            loop.create_task(send_log("✅ Прийнято до фракції", member, None, reason, author))
            return "✅ Прийнято."

        return "❌ Невідома дія."

    members = [(m.name, m.id) for m in guild.members if not m.bot]
    roles = [(r.name, r.id) for r in guild.roles if not r.managed and r.name != "@everyone"]
    return render_template("dashboard.html", members=members, roles=roles)

async def handle_action(title, member, role, reason, author):
    old_roles = [r for r in member.roles if r.name in RANK_ROLE_NAMES and r != role]
    if old_roles:
        await member.remove_roles(*old_roles)
    if role:
        await member.add_roles(role)
    await send_log(title, member, role, reason, author)

async def send_log(title, member, role, reason, author):
    embed = discord.Embed(title=title, color=discord.Color.blue())
    embed.add_field(name="👤 Користувач", value=member.mention, inline=False)
    if role:
        embed.add_field(name="🎖️ Нова роль", value=role.name, inline=False)
    embed.add_field(name="📌 Причина", value=reason, inline=False)
    embed.add_field(name="✍️ Автор", value=author, inline=False)

    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        await channel.send(embed=embed)

@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect("/")

def run_flask():
    app.run(port=5000)

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run(TOKEN)

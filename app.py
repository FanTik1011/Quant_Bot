import os
import threading
import asyncio
from flask import Flask, render_template, request, redirect, session
import discord
from discord.ext import commands
from dotenv import load_dotenv
from concurrent.futures import TimeoutError as AsyncTimeoutError

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))
SECRET_KEY = os.getenv("SECRET_KEY")
ALLOWED_USERS = os.getenv("ALLOWED_USERS", "").split(",")
PIN_CODE = os.getenv("ACCESS_PIN")

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
discord_loop = None

@bot.event
async def on_ready():
    global discord_loop
    discord_loop = asyncio.get_event_loop()
    print(f"✅ Бот запущено як {bot.user}")
    bot_ready.set()

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        discord_id = request.form.get("discord_id", "").strip()
        pin = request.form.get("pin", "").strip()

        if discord_id in ALLOWED_USERS and pin == PIN_CODE:
            session["user_id"] = discord_id
            return redirect("/dashboard")
        return render_template("login.html", error="❌ Невірний Discord ID або PIN-код.")

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
        return "❌ Сервер не знайдено."

    members = [(m.display_name, m.id) for m in guild.members if not m.bot]
    roles = [(r.name, r.id) for r in guild.roles if not r.managed and r.name != "@everyone"]

    if request.method == "POST":
        action = request.form.get("action")
        display_name = request.form.get("user_display", "").strip().lower()
        reason = request.form.get("reason", "Без причини")
        author = f"Discord ID: {session['user_id']}"
        role_id_raw = request.form.get("role_id", "").strip()

        member = next((m for m in guild.members if m.display_name.lower() == display_name), None)
        if not member:
            return "❌ Користувача не знайдено по ніку."

        role = discord.utils.get(guild.roles, id=int(role_id_raw)) if role_id_raw.isdigit() else None

        if action == "kick":
            asyncio.run_coroutine_threadsafe(member.kick(reason=reason), discord_loop)
            asyncio.run_coroutine_threadsafe(send_log("❌ Виганяється", member, None, reason, author), discord_loop)
            return "✅ Користувача вигнано."

        elif action in ["promote", "demote"]:
            asyncio.run_coroutine_threadsafe(handle_action("📈 Підвищення" if action == "promote" else "📉 Пониження", member, role, reason, author), discord_loop)
            return "✅ Дію виконано."

        elif action == "accepted":
            asyncio.run_coroutine_threadsafe(send_log("✅ Прийнято до фракції", member, None, reason, author), discord_loop)
            return "✅ Прийнято."

        return "❌ Невідома дія."

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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run(TOKEN)

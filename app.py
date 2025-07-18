import os
from flask import Flask, redirect, request, session, render_template, url_for
from dotenv import load_dotenv
import requests
import discord
from discord.ext import commands
import asyncio
import threading

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
ALLOWED_USERS = ["340574840878530560", "1133495444345983116", "793110164730937365"]
PIN_CODE = os.getenv("ACCESS_PIN")

BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))

intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot_ready = asyncio.Event()

@bot.event
async def on_ready():
    print(f"✅ Бот запущено як {bot.user}")
    bot_ready.set()

def start_bot():
    asyncio.run(bot.start(BOT_TOKEN))

threading.Thread(target=start_bot).start()

@app.route("/", methods=["GET", "POST"])
def index():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(bot_ready.wait())

    if "user" not in session or not session.get("pin_verified"):
        return redirect(url_for("login"))

    guild = discord.utils.get(bot.guilds, id=GUILD_ID)
    if not guild:
        return "❌ Бот не бачить сервер."

    if request.method == "POST":
        try:
            user_id = int(request.form['user_id'])
            action = request.form['action']
            reason = request.form['reason']
            author = request.form['author']
            role_id = int(request.form['role_id']) if 'role_id' in request.form else None

            member = guild.get_member(user_id)
            role = guild.get_role(role_id) if role_id else None

            if not member:
                return "❌ Користувача не знайдено."

            if action in ["promote", "demote"] and not role:
                return "❌ Роль не обрано."

            if action == "promote":
                coro = promote(member, role, reason, author)
            elif action == "demote":
                coro = demote(member, role, reason, author)
            elif action == "kick":
                coro = kick(member, reason, author)
            elif action == "join":
                coro = join(member, reason, author)
            else:
                return "❌ Невідома дія."

            asyncio.run_coroutine_threadsafe(coro, bot.loop)
            return "✅ Успішно виконано!"

        except Exception as e:
            return f"❌ Помилка: {str(e)}"

    members = [(m.name, m.id) for m in guild.members if not m.bot]
    roles = [(r.name, r.id) for r in guild.roles if not r.managed and r.name != "@everyone"]
    return render_template("index.html", username=session['user']['username'], members=members, roles=roles)

async def promote(member, role, reason, author):
    current_roles = [r for r in member.roles if r.name != "@everyone" and r != role]
    if current_roles:
        await member.remove_roles(*current_roles)
    await member.add_roles(role)
    await send_embed("📈 Підвищення", member, reason, author)

async def demote(member, role, reason, author):
    current_roles = [r for r in member.roles if r.name != "@everyone" and r != role]
    if current_roles:
        await member.remove_roles(*current_roles)
    await member.add_roles(role)
    await send_embed("📉 Пониження", member, reason, author)

async def kick(member, reason, author):
    await send_embed("❌ Вигнання", member, reason, author)
    await member.kick(reason=reason)

async def join(member, reason, author):
    await send_embed("✅ Новий учасник", member, reason, author)

async def send_embed(title, member, reason, author):
    embed = discord.Embed(title=title, color=discord.Color.blue())
    embed.add_field(name="👤 Учасник", value=member.mention, inline=False)
    embed.add_field(name="📝 Причина", value=reason, inline=False)
    embed.add_field(name="✍️ Автор", value=author, inline=False)
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        await channel.send(embed=embed)

@app.route("/login")
def login():
    return redirect(
        f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope=identify"
    )

@app.route("/callback")
def callback():
    code = request.args.get("code")
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "scope": "identify"
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    r.raise_for_status()
    access_token = r.json()["access_token"]
    user_info = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    if user_info["id"] not in ALLOWED_USERS:
        return "❌ Доступ заборонено."

    session["user"] = user_info
    return redirect(url_for("pin"))

@app.route("/pin", methods=["GET", "POST"])
def pin():
    if request.method == "POST":
        entered_pin = request.form.get("pin")
        if entered_pin == PIN_CODE:
            session["pin_verified"] = True
            return redirect(url_for("index"))
        return "❌ Невірний PIN-код."
    return '''
        <form method="post" style="text-align:center; padding:50px;">
            <h2>🔐 Введіть PIN-код</h2>
            <input type="password" name="pin" placeholder="PIN-код">
            <br><br>
            <button type="submit">Увійти</button>
        </form>
    '''

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

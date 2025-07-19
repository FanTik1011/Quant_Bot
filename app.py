import os
from flask import Flask, render_template, request, redirect, session
from dotenv import load_dotenv
import discord
from discord.ext import commands
import threading
import asyncio

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

ALLOWED_USERS = os.getenv("ALLOWED_USERS").split(",")
PIN_CODE = os.getenv("ACCESS_PIN")

TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
bot_ready = asyncio.Event()

@bot.event
async def on_ready():
    print(f"✅ Бот запущено як {bot.user}")
    bot_ready.set()

def run_bot():
    asyncio.run(bot.start(TOKEN))

threading.Thread(target=run_bot).start()

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        discord_id = request.form.get("discord_id")
        pin = request.form.get("pin")
        if discord_id in ALLOWED_USERS and pin == PIN_CODE:
            session["user_id"] = discord_id
            return redirect("/dashboard")
        else:
            return render_template("login.html", error="Невірний Discord ID або PIN.")
    return render_template("login.html")

@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if "user_id" not in session:
        return redirect("/")
    loop = asyncio.get_event_loop()
    loop.run_until_complete(bot_ready.wait())
    guild = discord.utils.get(bot.guilds, id=GUILD_ID)
    if request.method == "POST":
        action = request.form["action"]
        user_id = int(request.form["user_id"])
        reason = request.form["reason"]
        author = session["user_id"]
        role_id = request.form.get("role_id")
        member = guild.get_member(user_id)
        role = guild.get_role(int(role_id)) if role_id else None

        if action == "promote" and role:
            asyncio.run_coroutine_threadsafe(handle_action("📈 Підвищення", member, role, reason, author), bot.loop)
        elif action == "demote" and role:
            asyncio.run_coroutine_threadsafe(handle_action("📉 Пониження", member, role, reason, author), bot.loop)
        elif action == "kick":
            asyncio.run_coroutine_threadsafe(kick(member, reason, author), bot.loop)
        elif action == "accepted":
            asyncio.run_coroutine_threadsafe(handle_action("✅ Прийнято", member, None, reason, author), bot.loop)
        return "✅ Виконано!"
    members = [(m.name, m.id) for m in guild.members if not m.bot]
    roles = [(r.name, r.id) for r in guild.roles if not r.managed and r.name != "@everyone"]
    return render_template("dashboard.html", members=members, roles=roles)

async def handle_action(title, member, role, reason, author):
    if role:
        await member.add_roles(role)
    embed = discord.Embed(title=title, color=discord.Color.blue())
    embed.add_field(name="Учасник", value=member.mention, inline=False)
    embed.add_field(name="Причина", value=reason, inline=False)
    embed.add_field(name="Автор", value=author, inline=False)
    channel = bot.get_channel(LOG_CHANNEL_ID)
    await channel.send(embed=embed)

async def kick(member, reason, author):
    embed = discord.Embed(title="❌ Вигнання", color=discord.Color.red())
    embed.add_field(name="Учасник", value=member.mention, inline=False)
    embed.add_field(name="Причина", value=reason, inline=False)
    embed.add_field(name="Автор", value=author, inline=False)
    channel = bot.get_channel(LOG_CHANNEL_ID)
    await channel.send(embed=embed)
    await member.kick(reason=reason)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

if __name__ == "__main__":
    app.run(debug=True)

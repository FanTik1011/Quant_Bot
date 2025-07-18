import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("MTM5NTQ4NzIxMDA2NDU3NjY0Mw.GnnNT_.AsTEWG7aQi5D_yj2EtyegmcjOKrlZj7GC_kpto")
GUILD_ID = int(os.getenv("1147624476293480480"))
LOG_CHANNEL_ID = int(os.getenv("1395497313887195278"))


intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Бот запущено як {bot.user}")

async def promote(member, role, reason, author):
    current_rank_roles = [r for r in member.roles if r.name != "@everyone" and r != role]
    if current_rank_roles:
        await member.remove_roles(*current_rank_roles)
    await member.add_roles(role)

    embed = discord.Embed(title="📈 Підвищення", color=discord.Color.green())
    embed.add_field(name="1. Підвищений", value=member.mention, inline=False)
    embed.add_field(name="2. Причина", value=reason, inline=False)
    embed.add_field(name="3. Автор", value=author, inline=False)

    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        await channel.send(embed=embed)

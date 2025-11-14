import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import tasks
from mcstatus import JavaServer
import asyncpg
from dotenv import load_dotenv

load_dotenv()

# ---------------- Config ----------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
MINECRAFT_SERVER = os.getenv("MINECRAFT_SERVER", "192.168.0.155:25565")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "1279387365323833397"))
DATABASE_URL = os.getenv("DATABASE_URL")
BOT_VERSION = "1.0.2"
SERVER_DISPLAY_NAME = "ПЧ"

# ---------------- Intents & Bot ----------------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

class MinecraftBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.server_was_online: Optional[bool] = None
        self.maintenance_mode: bool = False
        self.check_count: int = 0
        self.db_pool: Optional[asyncpg.pool.Pool] = None

    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Slash команды синхронизированы")
        if DATABASE_URL:
            try:
                self.db_pool = await asyncpg.create_pool(DATABASE_URL)
                async with self.db_pool.acquire() as conn:
                    # Создание таблиц
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS alliances (
                            id SERIAL PRIMARY KEY,
                            name TEXT UNIQUE NOT NULL,
                            description TEXT,
                            owner_id BIGINT NOT NULL,
                            created_at TIMESTAMP DEFAULT now()
                        )
                    """)
                    await conn.execute("""
                        CREATE TABLE IF NOT EXISTS wars (
                            id SERIAL PRIMARY KEY,
                            attacker TEXT NOT NULL,
                            defender TEXT NOT NULL,
                            reason TEXT,
                            started_at TIMESTAMP DEFAULT now(),
                            attack_at TIMESTAMP
                        )
                    """)
                print("✅ DB ready")
            except Exception as e:
                print(f"❌ Ошибка при подключении к БД: {e}")
                self.db_pool = None
        else:
            print("⚠️ DATABASE_URL не задан — функции БД недоступны")

bot = MinecraftBot()

# ---------------- Helpers ----------------
def fmt_time(dt: Optional[datetime]):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "—"

def parse_server_address(addr: str):
    if ":" in addr:
        host, port = addr.split(":", 1)
        return host, int(port)
    return addr, 25565

def check_server() -> (bool, Optional[int], Optional[int]):
    host, port = parse_server_address(MINECRAFT_SERVER)
    try:
        server = JavaServer(host, port)
        status = server.status()
        players_online = getattr(status.players, "online", None) if status.players else None
        players_max = getattr(status.players, "max", None) if status.players else None
        return True, players_online, players_max
    except Exception as e:
        print(f"[DEBUG] check_server exception: {e}")
        return False, None, None

# ---------------- Monitoring task ----------------
@tasks.loop(seconds=60)
async def check_server_status():
    bot.check_count += 1
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] Проверка #{bot.check_count}")
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"❌ Канал с ID {CHANNEL_ID} не найден.")
        return

    is_online, players_online, players_max = check_server()

    if bot.maintenance_mode:
        bot.server_was_online = is_online
        return

    if bot.server_was_online is None:
        bot.server_was_online = is_online
        return

    if bot.server_was_online == is_online:
        return

    embed_color = discord.Color.green() if is_online else discord.Color.red()
    title = f"{'✅' if is_online else '⚠️'} {SERVER_DISPLAY_NAME} — сервер {'онлайн' if is_online else 'отключился'}"
    desc = f"Сервер `{SERVER_DISPLAY_NAME}` ({MINECRAFT_SERVER}) {'доступен' if is_online else 'недоступен'}."

    embed = discord.Embed(title=title, description=desc, color=embed_color, timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Проверка №", value=str(bot.check_count), inline=True)
    embed.add_field(name="Maintenance", value=str(bot.maintenance_mode), inline=True)
    if is_online and players_online is not None:
        embed.add_field(name="Игроки", value=f"{players_online}/{players_max}", inline=True)
    try:
        await channel.send(embed=embed)
    except Exception as e:
        print(f"[DEBUG] Ошибка отправки уведомления: {e}")

    bot.server_was_online = is_online

@check_server_status.before_loop
async def before_check_server_status():
    await bot.wait_until_ready()
    print("⏳ Мониторинг будет запущен после готовности бота...")

# ---------------- Alliance & War System ----------------
class CreateAllianceModal(discord.ui.Modal, title="Создание Альянса"):
    name = discord.ui.TextInput(label="Название альянса", max_length=50)
    description = discord.ui.TextInput(label="Описание (необязательно)", style=discord.TextStyle.paragraph, required=False, max_length=300)

    async def on_submit(self, interaction: discord.Interaction):
        if not bot.db_pool:
            await interaction.response.send_message("⚠️ База данных не настроена.", ephemeral=True)
            return
        async with bot.db_pool.acquire() as conn:
            exists = await conn.fetchrow("SELECT id FROM alliances WHERE name = $1", self.name.value)
            if exists:
                await interaction.response.send_message("❌ Альянс с таким именем уже существует.", ephemeral=True)
                return
            await conn.execute(
                "INSERT INTO alliances (name, description, owner_id) VALUES ($1, $2, $3)",
                self.name.value, self.description.value or None, interaction.user.id
            )
        embed = discord.Embed(
            title="✅ Альянс создан",
            description=f"🏰 **{self.name.value}**\n{self.description.value or '—'}",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"Создатель: {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

class DeclareWarModal(discord.ui.Modal, title="Объявление войны"):
    attacker: str
    defender: str

    reason_input = discord.ui.TextInput(label="Причина войны", placeholder="Введите причину", style=discord.TextStyle.paragraph, required=True, max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        attack_time = datetime.utcnow() + timedelta(hours=2, minutes=40)  # NAIVE datetime!
        if not bot.db_pool:
            await interaction.response.send_message("⚠️ База данных не настроена.", ephemeral=True)
            return
        async with bot.db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO wars (attacker, defender, reason, attack_at) VALUES ($1, $2, $3, $4)",
                self.attacker, self.defender, self.reason_input.value, attack_time
            )
        embed = discord.Embed(
            title="⚔ Война объявлена!",
            description=f"Атакующий: **{self.attacker}**\nЗащитник: **{self.defender}**",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()  # здесь тоже можно оставить utcnow
        )
        embed.add_field(name="Причина", value=self.reason_input.value, inline=False)
        embed.add_field(name="Нападение через", value="2 игровых дня (40 минут)", inline=True)
        embed.add_field(name="Атаковать можно с", value=attack_time.strftime("%H:%M:%S UTC"), inline=True)
        await interaction.response.send_message(embed=embed)

class WarSelect(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption]):
        super().__init__(placeholder="Выберите два альянса (атакующий → защитник)", min_values=2, max_values=2, options=options)

    async def callback(self, interaction: discord.Interaction):
        modal = DeclareWarModal()
        modal.attacker = self.values[0]
        modal.defender = self.values[1]
        await interaction.response.send_modal(modal)

class AllianceMenu(discord.ui.View):
    def __init__(self, timeout=None):
        super().__init__(timeout=timeout)

    @discord.ui.button(label="Создать альянс", style=discord.ButtonStyle.green)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CreateAllianceModal())

    @discord.ui.button(label="Посмотреть все", style=discord.ButtonStyle.blurple)
    async def view_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not bot.db_pool:
            await interaction.response.send_message("⚠️ DB не настроена.", ephemeral=True)
            return
        async with bot.db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM alliances ORDER BY created_at DESC")
        if not rows:
            await interaction.response.send_message("❌ Альянсов пока нет.", ephemeral=True)
            return
        embed = discord.Embed(title="📜 Все альянсы", color=discord.Color.gold(), timestamp=datetime.now(timezone.utc))
        for r in rows:
            embed.add_field(name=f"🏰 {r['name']}", value=f"👑 <@{r['owner_id']}>\n{r['description'] or '—'}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Объявить войну", style=discord.ButtonStyle.danger)
    async def declare_war(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not bot.db_pool:
            await interaction.response.send_message("⚠️ DB не настроена.", ephemeral=True)
            return
        async with bot.db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT name FROM alliances ORDER BY name")
        if not rows or len(rows) < 2:
            await interaction.response.send_message("❌ Недостаточно альянсов для войны (нужно >=2).", ephemeral=True)
            return
        options = [discord.SelectOption(label=r['name']) for r in rows]
        view = discord.ui.View()
        view.add_item(WarSelect(options=options))
        await interaction.response.send_message("Выберите два альянса: атакующий и защитник.", view=view, ephemeral=True)

# ---------------- Commands ----------------
@bot.tree.command(name="alliance", description="Меню управления альянсами")
async def alliance(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏰 Меню альянсов",
        description="Выберите действие ниже.",
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc)
    )
    embed.set_footer(text="Используй кнопки для взаимодействия.")
    await interaction.response.send_message(embed=embed, view=AllianceMenu(), ephemeral=True)

# ---------------- Ready ----------------
@bot.event
async def on_ready():
    print(f"✅ Бот {bot.user} запущен. Версия: {BOT_VERSION}")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        is_online, players_online, players_max = check_server()
        bot.server_was_online = is_online
        embed = discord.Embed(
            title=f"🚀 Бот мониторинга запущен — {SERVER_DISPLAY_NAME}",
            description=f"Привет! Я готов следить за сервером **{SERVER_DISPLAY_NAME}**.",
            color=discord.Color.green() if is_online else discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Статус", value="🟢 Онлайн" if is_online else "🔴 Оффлайн", inline=True)
        if is_online and players_online is not None:
            embed.add_field(name="Игроки", value=f"{players_online}/{players_max}", inline=True)
        embed.add_field(name="IP", value=MINECRAFT_SERVER, inline=True)
        embed.add_field(name="Канал проверок", value=str(CHANNEL_ID), inline=True)
        embed.add_field(name="Версия бота", value=BOT_VERSION, inline=True)
        try:
            await channel.send(embed=embed)
        except Exception as e:
            print(f"[DEBUG] Ошибка отправки стартового embed: {e}")
    if not check_server_status.is_running():
        check_server_status.start()
        print("✅ Мониторинг сервера запущен")

# ---------------- Run ----------------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN не задан в .env")
        exit(1)
    print("🚀 Запуск бота...")
    bot.run(DISCORD_TOKEN)

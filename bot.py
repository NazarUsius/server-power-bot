import discord
from discord.ext import commands, tasks
from discord import app_commands
from mcstatus import JavaServer
import asyncio
from datetime import datetime
import os
from dotenv import load_dotenv
load_dotenv()

# Настройки
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
MINECRAFT_SERVER = '192.168.0.155:25565'  # Например: 'play.example.com:25565'
CHANNEL_ID = 1279387365323833397  # ID канала для уведомлений

# Создание бота
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True


class MinecraftBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.server_was_online = None
        self.maintenance_mode = False
        self.check_count = 0

    async def setup_hook(self):
        await self.tree.sync()
        print('✅ Slash команды синхронизированы')


bot = MinecraftBot()


def check_server():
    """Проверяет доступность Minecraft сервера"""
    try:
        server = JavaServer.lookup(MINECRAFT_SERVER)
        status = server.status()
        return True, status.players.online, status.players.max
    except Exception as e:
        print(f"[DEBUG] Ошибка проверки сервера: {e}")
        return False, None, None


class AllianceMenu(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🏰 Создать альянс", style=discord.ButtonStyle.green)
    async def create_alliance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Введите `/alliance_create <название>` чтобы создать альянс.",
            ephemeral=True
        )

    @discord.ui.button(label="📜 Список альянсов", style=discord.ButtonStyle.blurple)
    async def list_alliances(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "📋 Здесь позже будет список всех альянсов (ты его добавишь сам 😉)",
            ephemeral=True
        )

    @discord.ui.button(label="✉️ Мои приглашения", style=discord.ButtonStyle.gray)
    async def show_invites(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "📨 Тут будут твои активные приглашения.", ephemeral=True
        )

    @discord.ui.button(label="⚔️ Объявить войну", style=discord.ButtonStyle.red)
    async def declare_war(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "⚔️ Здесь позже появится меню для объявления войны.", ephemeral=True
        )


@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} успешно запущен!')
    print(f'📡 Мониторинг сервера: {MINECRAFT_SERVER}')
    print(f'📢 Канал уведомлений: {CHANNEL_ID}')
    check_server_status.start()
    print('🔄 Мониторинг запущен!')
    print('📋 Доступные команды: /status, /maintenance, /ping')


@tasks.loop(seconds=60)  # Проверка каждые 60 секунд
async def check_server_status():
    bot.check_count += 1
    print(f'\n[Проверка #{bot.check_count}] {datetime.now().strftime("%H:%M:%S")}')

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print(f'❌ ОШИБКА: Канал {CHANNEL_ID} не найден!')
        return

    is_online, players_online, players_max = check_server()
    print(f'📊 Статус: {"🟢 ОНЛАЙН" if is_online else "🔴 ОФФЛАЙН"}')
    if is_online:
        print(f'👥 Игроки: {players_online}/{players_max}')

    # Первая проверка
    if bot.server_was_online is None:
        print('ℹ️ Первая проверка - инициализация состояния')
        bot.server_was_online = is_online
        if is_online:
            embed = discord.Embed(
                title="🟢 Сервер онлайн",
                description=f"Minecraft сервер `{MINECRAFT_SERVER}` работает",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Игроки", value=f"{players_online}/{players_max}")
            if bot.maintenance_mode:
                embed.set_footer(text="🔧 Режим обслуживания активен")
        else:
            embed = discord.Embed(
                title="🔴 Сервер оффлайн",
                description=f"Minecraft сервер `{MINECRAFT_SERVER}` не отвечает",
                color=discord.Color.red(),
                timestamp=datetime.utcnow()
            )
            if bot.maintenance_mode:
                embed.set_footer(text="🔧 Режим обслуживания активен")

        try:
            if bot.maintenance_mode:
                pass
            else:
                await channel.send(content="@everyone", embed=embed)
                print('✅ Уведомление об отключении отправлено')
        except Exception as e:
            print(f'❌ Ошибка отправки сообщения: {e}')
        return

    # Сервер упал
    if bot.server_was_online and not is_online:
        print('⚠️ ИЗМЕНЕНИЕ: Сервер отключился!')
        embed = discord.Embed(
            title="⚠️ Сервер отключился",
            description=f"Minecraft сервер `{MINECRAFT_SERVER}` вне сети!",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        if bot.maintenance_mode:
            embed.set_footer(text="🔧 Режим обслуживания активен")

        try:
            if bot.maintenance_mode:
                pass
            else:
                await channel.send(content="@everyone", embed=embed)
                print('✅ Уведомление об отключении отправлено')
        except Exception as e:
            print(f'❌ Ошибка отправки: {e}')

        bot.server_was_online = False

    # Сервер включился
    elif not bot.server_was_online and is_online:
        print('✅ ИЗМЕНЕНИЕ: Сервер включился!')
        embed = discord.Embed(
            title="✅ Сервер подключен",
            description=f"Minecraft сервер `{MINECRAFT_SERVER}` снова онлайн!",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Игроки", value=f"{players_online}/{players_max}")
        if bot.maintenance_mode:
            embed.set_footer(text="🔧 Режим обслуживания активен")

        try:
            if bot.maintenance_mode:
                pass
            else:
                await channel.send(content="@everyone", embed=embed)
                print('✅ Уведомление об отключении отправлено')
        except Exception as e:
            print(f'❌ Ошибка отправки: {e}')

        bot.server_was_online = True
    else:
        print('ℹ️ Статус не изменился')


@check_server_status.before_loop
async def before_check_server_status():
    await bot.wait_until_ready()
    print('⏳ Ожидание готовности бота...')


@bot.tree.command(name="status", description="Показать статус Minecraft сервера")
async def status(interaction: discord.Interaction):
    """Показывает текущий статус сервера"""
    print(f'ℹ️ Команда /status от {interaction.user}')

    is_online, players_online, players_max = check_server()

    if is_online:
        embed = discord.Embed(
            title="🟢 Сервер онлайн",
            description=f"Сервер: `{MINECRAFT_SERVER}`",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Игроки", value=f"{players_online}/{players_max}")
        embed.add_field(name="Проверок выполнено", value=str(bot.check_count))
    else:
        embed = discord.Embed(
            title="🔴 Сервер оффлайн",
            description=f"Сервер: `{MINECRAFT_SERVER}`",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Проверок выполнено", value=str(bot.check_count))

    if bot.maintenance_mode:
        embed.set_footer(text="🔧 Режим обслуживания активен")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="maintenance", description="Переключить режим обслуживания сервера")
@app_commands.default_permissions(administrator=True)
async def maintenance(interaction: discord.Interaction):
    """Переключает режим планового обслуживания"""
    bot.maintenance_mode = not bot.maintenance_mode

    if bot.maintenance_mode:
        embed = discord.Embed(
            title="🔧 Режим обслуживания",
            description=f"Плановое отключение сервера `{MINECRAFT_SERVER}`",
            color=discord.Color.orange(),
            timestamp=datetime.utcnow()
        )
        print(f'🔧 Режим обслуживания ВКЛЮЧЕН пользователем {interaction.user}')
    else:
        embed = discord.Embed(
            title="✅ Обслуживание завершено",
            description=f"Мониторинг сервера `{MINECRAFT_SERVER}` продолжается",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        print(f'✅ Режим обслуживания ВЫКЛЮЧЕН пользователем {interaction.user}')

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ping", description="Проверить работу бота")
async def ping(interaction: discord.Interaction):
    """Проверка работы бота"""
    latency = round(bot.latency * 1000)
    embed = discord.Embed(
        title="🏓 Понг!",
        description=f"Задержка: {latency}мс\nПроверок: {bot.check_count}",
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    print(f'🏓 Команда /ping от {interaction.user} - задержка {latency}мс')


@bot.tree.command(name="alliance", description="Меню управления альянсами")
async def alliance_menu(interaction: discord.Interaction):
    """Главное меню альянсов"""
    embed = discord.Embed(
        title="🏰 Меню альянсов",
        description="Выбери действие:",
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed, view=AllianceMenu(), ephemeral=True)


# Запуск бота
if __name__ == '__main__':
    print('🚀 Запуск бота...')
    print(f'📋 Команды: /status, /maintenance, /ping')
    print(f'🎯 Интервал проверки: 60 секунд')

    bot.run(DISCORD_TOKEN)


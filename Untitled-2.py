import json
import os
from datetime import datetime, timedelta, timezone

import discord
import gspread
from discord.ext import commands, tasks
from dotenv import load_dotenv
from gspread.exceptions import WorksheetNotFound
from oauth2client.service_account import ServiceAccountCredentials

load_dotenv()

KST = timezone(timedelta(hours=9))

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

GUILD_ID = os.getenv("GUILD_ID")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME")
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

if not TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN이 설정되지 않았습니다.")
if not SPREADSHEET_NAME:
    raise ValueError("SPREADSHEET_NAME이 설정되지 않았습니다.")
if not GOOGLE_CREDENTIALS_JSON:
    raise ValueError("GOOGLE_CREDENTIALS_JSON이 설정되지 않았습니다.")
if not GUILD_ID:
    raise ValueError("GUILD_ID가 설정되지 않았습니다.")

GUILD_ID = int(GUILD_ID)

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(GOOGLE_CREDENTIALS_JSON), scope
)
client = gspread.authorize(creds)
spreadsheet = client.open(SPREADSHEET_NAME)
sheet = spreadsheet.get_worksheet(0)


def now_kst() -> datetime:
    return datetime.now(KST)


def get_today_sheet_name() -> str:
    return now_kst().strftime("%Y. %m. %d.")


def create_sheet() -> str:
    global sheet
    title = get_today_sheet_name()

    try:
        try:
            sheet = spreadsheet.worksheet(title)
            return f"이미 존재하는 시트로 전환됨: {title}"
        except WorksheetNotFound:
            template_sheet = spreadsheet.get_worksheet(0)
            new_sheet = template_sheet.duplicate(new_sheet_name=title)
            new_sheet.batch_clear(["A2:H2"])

            if new_sheet.row_count > 2:
                new_sheet.delete_rows(3, new_sheet.row_count)

            sheet = new_sheet
            return f"새 시트 생성 완료: {title}"
    except Exception as e:
        return f"오류 발생: {e}"


def ensure_sheet_rows(target_row: int) -> None:
    rows_to_add = target_row - sheet.row_count
    if rows_to_add > 0:
        sheet.add_rows(rows_to_add)


async def is_kicked_or_banned(member: discord.Member) -> bool:
    now = datetime.now(timezone.utc)

    for action in (discord.AuditLogAction.kick, discord.AuditLogAction.ban):
        async for entry in member.guild.audit_logs(limit=10, action=action):
            if entry.target and entry.target.id == member.id:
                if (now - entry.created_at).total_seconds() < 10:
                    return True
    return False


@bot.event
async def on_ready():
    print(f"봇 실행됨: {bot.user}")

    create_sheet()

    if not create_new_sheet.is_running():
        create_new_sheet.start()

    try:
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 동기화 완료: {len(synced)}개")
    except Exception as e:
        print(f"명령어 동기화 오류: {e}")


@tasks.loop(minutes=1)
async def create_new_sheet():
    now = now_kst()
    if now.hour == 0 and now.minute == 0:
        print(create_sheet())


@bot.tree.command(name="워크시트추가", description="새 날짜 시트를 생성합니다")
async def add_sheet(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await interaction.followup.send(create_sheet(), ephemeral=True)


@bot.event
async def on_member_remove(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return

    try:
        if await is_kicked_or_banned(member):
            print(f"추방/차단 사용자라 기록 생략: {member} ({member.id})")
            return

        user_ids = sheet.col_values(4)
        if str(member.id) in user_ids:
            print(f"중복 사용자라 기록 생략: {member} ({member.id})")
            return

        next_row = len(sheet.col_values(3)) + 1
        ensure_sheet_rows(next_row)
        sheet.update(f"C{next_row}:D{next_row}", [[str(member), str(member.id)]])
        print(f"기록 완료: {member} ({member.id})")
    except Exception as e:
        print(f"시트 기록 오류: {e}")


bot.run(TOKEN)

import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os

# ─── Configuration ───────────────────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))  # Channel to post updates
SUBREDDITS = ["hardwareswap", "homelabsales"]
POLL_INTERVAL = 300  # seconds (5 minutes)
POST_LIMIT = 10      # number of posts to fetch per subreddit per poll
# ─────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Track seen post IDs to avoid duplicate posts
seen_post_ids: set[str] = set()
first_run = True  # Skip posting on first run (just seed the seen set)


async def fetch_reddit_posts(subreddit: str, limit: int = POST_LIMIT) -> list[dict]:
    """Fetch recent posts from a subreddit using the public JSON API."""
    url = f"https://www.reddit.com/r/{subreddit}/new.json?limit={limit}"
    headers = {"User-Agent": "DiscordBot/1.0 (multi-sub watcher)"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                print(f"[ERROR] Reddit returned status {resp.status} for r/{subreddit}")
                return []
            data = await resp.json()

    posts = []
    for child in data.get("data", {}).get("children", []):
        p = child["data"]
        posts.append({
            "id":        p["id"],
            "subreddit": subreddit,
            "title":     p["title"],
            "author":    p["author"],
            "url":       f"https://reddit.com{p['permalink']}",
            "flair":     p.get("link_flair_text") or "No Flair",
            "score":     p["score"],
        })
    return posts


def format_post(post: dict) -> str:
    """Format a single post into a Discord message string."""
    return (
        f"**[r/{post['subreddit']}]** [{post['flair']}] {post['title']}\n"
        f"> 👤 u/{post['author']}  •  ⬆️ {post['score']}\n"
        f"> 🔗 {post['url']}"
    )


@tasks.loop(seconds=POLL_INTERVAL)
async def poll_subreddit():
    """Periodically check for new posts across all subreddits and send them to the channel."""
    global first_run

    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"[WARN] Channel {CHANNEL_ID} not found. Check CHANNEL_ID.")
        return

    # Fetch from all subreddits concurrently
    results = await asyncio.gather(*[fetch_reddit_posts(sub) for sub in SUBREDDITS])
    all_posts = [post for sub_posts in results for post in sub_posts]

    if not all_posts:
        return

    new_posts = [p for p in all_posts if p["id"] not in seen_post_ids]

    # On first run just seed the seen set — don't flood the channel
    if first_run:
        for p in all_posts:
            seen_post_ids.add(p["id"])
        first_run = False
        print(f"[INFO] Seeded {len(seen_post_ids)} existing post IDs across {len(SUBREDDITS)} subreddits. Watching for new posts...")
        return

    for post in reversed(new_posts):  # Post oldest-first
        seen_post_ids.add(post["id"])
        await channel.send(format_post(post))

    if new_posts:
        print(f"[INFO] Posted {len(new_posts)} new listing(s).")


@bot.command(name="latest")
async def latest(ctx, count: int = 5):
    """
    !latest [count]
    Fetch and display the most recent listings from all subreddits (default: 5, max: 25).
    """
    count = max(1, min(count, 25))

    results = await asyncio.gather(*[fetch_reddit_posts(sub, limit=count) for sub in SUBREDDITS])
    all_posts = [post for sub_posts in results for post in sub_posts]

    if not all_posts:
        await ctx.send("⚠️ Couldn't fetch posts right now. Try again later.")
        return

    subs_label = " & ".join(f"r/{s}" for s in SUBREDDITS)
    await ctx.send(f"📦 **Latest {count} listings from {subs_label}:**")
    for post in all_posts[:count]:
        await ctx.send(format_post(post))


@bot.command(name="search")
async def search(ctx, *, keyword: str):
    """
    !search <keyword>
    Search the latest 25 posts across all subreddits for titles containing the keyword.
    """
    results = await asyncio.gather(*[fetch_reddit_posts(sub, limit=25) for sub in SUBREDDITS])
    all_posts = [post for sub_posts in results for post in sub_posts]
    matches = [p for p in all_posts if keyword.lower() in p["title"].lower()]

    if not matches:
        await ctx.send(f"🔍 No recent listings matching **{keyword}**.")
        return

    await ctx.send(f"🔍 Found **{len(matches)}** match(es) for `{keyword}`:")
    for post in matches:
        await ctx.send(format_post(post))


@bot.event
async def on_ready():
    print(f"[INFO] Logged in as {bot.user} (ID: {bot.user.id})")
    if CHANNEL_ID == 0:
        print("[WARN] CHANNEL_ID is not set. Auto-posting is disabled.")
    else:
        poll_subreddit.start()
        subs = ", ".join(f"r/{s}" for s in SUBREDDITS)
        print(f"[INFO] Polling {subs} every {POLL_INTERVAL}s → channel {CHANNEL_ID}")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

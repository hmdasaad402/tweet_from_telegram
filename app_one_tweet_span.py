import os
import asyncio
import logging
import tweepy
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from tempfile import NamedTemporaryFile
import time
import random

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
API_ID = 20572087
API_HASH = '044ac78962bfd63b5487896a2cf33151'
CHANNEL_USERNAME = '@weebiok'
SESSION_STRING = None

# Twitter Configuration
TWITTER_API_KEY = 'Rhur7Z8HxzEzfALVJ0KbOvQMS'
TWITTER_API_SECRET = 'nAltcZhA3kRGnc0juYSpkY5yKPkOSW6CnQAfIxGQuwPgVK4Dg1'
TWITTER_ACCESS_TOKEN = '1890260569581637632-zpjfFevmkEzRYhpgjr9oq7w9CkmOCD'
TWITTER_ACCESS_SECRET = 'vyvAMi2BISSFOtBT2lFM6xipCHhCFZSa5UqyofLeyNgO8'

# App Settings
POST_INTERVAL_MINUTES = 2
MAX_MESSAGE_HISTORY = 10
SOURCE_ATTRIBUTION = ' (منقول من مصدر فلسطيني)'
MAX_RETRIES = 5  # Increased from 3
BASE_DELAY = 10  # Base delay in seconds

class BotClient:
    def __init__(self):
        self.message_history = []
        self.last_post_time = None
        self.posting_lock = asyncio.Lock()
        self.initialize_clients()

    def initialize_clients(self):
        try:
            # Twitter Client with longer timeout
            self.twitter_api = tweepy.API(
                tweepy.OAuth1UserHandler(
                    consumer_key=TWITTER_API_KEY,
                    consumer_secret=TWITTER_API_SECRET,
                    access_token=TWITTER_ACCESS_TOKEN,
                    access_token_secret=TWITTER_ACCESS_SECRET
                ),
                wait_on_rate_limit=True,
                timeout=60,
                retry_count=3,
                retry_delay=5
            )
            
            self.twitter_client = tweepy.Client(
                consumer_key=TWITTER_API_KEY,
                consumer_secret=TWITTER_API_SECRET,
                access_token=TWITTER_ACCESS_TOKEN,
                access_token_secret=TWITTER_ACCESS_SECRET,
                wait_on_rate_limit=True
            )
            logger.info("Twitter clients initialized")

            # Telegram Client
            session = 'user_monitor_session.session'
            if SESSION_STRING:
                session = StringSession(SESSION_STRING)
                
            self.telegram_client = TelegramClient(
                session,
                API_ID,
                API_HASH,
                system_version="4.16.30-vxCONNECTED"
            )
            logger.info("Telegram client initialized")

        except Exception as e:
            logger.error(f"Client initialization failed: {e}")
            raise

    async def download_media(self, msg):
        try:
            if not msg.media:
                return None
                
            temp_file = NamedTemporaryFile(delete=False, suffix='.jpg')
            await msg.download_media(file=temp_file.name)
            return temp_file.name
        except Exception as e:
            logger.error(f"Media download failed: {e}")
            return None

    async def post_to_twitter(self, msg):
        if not self.twitter_client:
            logger.error("Twitter client not available")
            return False
        
        for attempt in range(MAX_RETRIES):
            try:
                # Exponential backoff with jitter
                delay = min(BASE_DELAY * (2 ** attempt) + random.uniform(0, 1), 300)
                if attempt > 0:
                    logger.info(f"Waiting {delay:.1f} seconds before retry...")
                    await asyncio.sleep(delay)

                # Prepare tweet content
                base_text = msg.text or ""
                tweet_text = f"{base_text[:280-len(SOURCE_ATTRIBUTION)]}{SOURCE_ATTRIBUTION}"
                
                # Handle media
                media_ids = []
                if msg.media:
                    media_path = await self.download_media(msg)
                    if media_path:
                        try:
                            media = self.twitter_api.media_upload(media_path)
                            media_ids.append(media.media_id)
                        finally:
                            if os.path.exists(media_path):
                                os.unlink(media_path)
                
                # Post tweet with longer timeout
                response = self.twitter_client.create_tweet(
                    text=tweet_text,
                    media_ids=media_ids if media_ids else None
                )
                logger.info(f"Posted to Twitter (ID: {response.data['id']})")
                return True
                
            except tweepy.TweepyException as e:
                error_msg = str(e)
                if "500" in error_msg:
                    logger.warning(f"Twitter server error (attempt {attempt + 1})")
                else:
                    logger.error(f"Twitter API error (attempt {attempt + 1}): {e}")
                
                if attempt == MAX_RETRIES - 1:
                    logger.error("Max retries reached, giving up on this message")
                    return False

            except Exception as e:
                logger.error(f"Unexpected error (attempt {attempt + 1}): {e}")
                if attempt == MAX_RETRIES - 1:
                    return False

    async def run(self):
        if not await self.connect_telegram():
            logger.error("Failed to connect to Telegram")
            return

        try:
            channel = await self.telegram_client.get_entity(CHANNEL_USERNAME)
            logger.info(f"Monitoring channel: {channel.title}")

            @self.telegram_client.on(events.NewMessage(chats=channel))
            async def handler(event):
                async with self.posting_lock:
                    self.message_history.append(event.message)
                    if len(self.message_history) > MAX_MESSAGE_HISTORY:
                        self.message_history.pop(0)
                    logger.info(f"New message: {event.message.id}")

            while True:
                await asyncio.sleep(60)
                
                async with self.posting_lock:
                    if (not self.last_post_time or 
                        (datetime.now() - self.last_post_time).total_seconds() >= POST_INTERVAL_MINUTES * 60):
                        
                        if self.message_history:
                            msg = self.message_history[-1]
                            if await self.post_to_twitter(msg):
                                self.message_history.remove(msg)
                                self.last_post_time = datetime.now()

        except Exception as e:
            logger.error(f"Bot error: {e}")
        finally:
            await self.telegram_client.disconnect()

    async def connect_telegram(self):
        max_retries = 5
        for attempt in range(max_retries):
            try:
                if not self.telegram_client.is_connected():
                    await self.telegram_client.connect()

                if not await self.telegram_client.is_user_authorized():
                    if SESSION_STRING:
                        raise ConnectionError("Invalid session string")
                    raise ConnectionError("Pre-authentication required")

                logger.info("Telegram connection established")
                return True

            except Exception as e:
                logger.error(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    return False
                await asyncio.sleep(10)

async def main():
    bot = BotClient()
    await bot.run()

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())

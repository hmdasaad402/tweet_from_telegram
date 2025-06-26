import os
import asyncio
import logging
import tweepy
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from tempfile import NamedTemporaryFile
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration - Use environment variables in production!
API_ID = 20572087
API_HASH = '044ac78962bfd63b5487896a2cf33151'
CHANNEL_USERNAME =  '@hamza20300'

# Twitter Configuration
TWITTER_API_KEY = 'bwyk8VCfY5IGtKLQdv3oHQ51a'
TWITTER_API_SECRET = 'XvKWkYqcSNs9sMS5vx1V4F9Ads93MHtVm4eagUo73EWBspI3l9'
TWITTER_ACCESS_TOKEN = 'XvKWkYqcSNs9sMS5vx1V4F9Ads93MHtVm4eagUo73EWBspI3l9'
TWITTER_ACCESS_SECRET = 'AGV19KVYsj9HgqwEPhv37GOgT4DT1uek97UyP3UF8INST'

# App Settings
POST_INTERVAL_MINUTES = 28
MAX_MESSAGE_HISTORY = 10
SOURCE_ATTRIBUTION = " (منقول من مصدر فلسطيني)"
SESSION_STRING = os.getenv('TELEGRAM_SESSION_STRING')  # For cloud deployment

class BotClient:
    def __init__(self):
        self.message_history = []
        self.last_post_time = None
        self.posting_lock = asyncio.Lock()
        self.initialize_clients()

    def initialize_clients(self):
        """Initialize Twitter and Telegram clients"""
        try:
            # Twitter Client
            auth_v1 = tweepy.OAuth1UserHandler(
                TWITTER_API_KEY,
                TWITTER_API_SECRET,
                TWITTER_ACCESS_TOKEN,
                TWITTER_ACCESS_SECRET
            )
            self.twitter_api = tweepy.API(auth_v1, wait_on_rate_limit=True)
            self.twitter_client = tweepy.Client(
                consumer_key=TWITTER_API_KEY,
                consumer_secret=TWITTER_API_SECRET,
                access_token=TWITTER_ACCESS_TOKEN,
                access_token_secret=TWITTER_ACCESS_SECRET,
                wait_on_rate_limit=True
            )
            logger.info("Twitter clients initialized")

            # Telegram Client
            if SESSION_STRING:
                self.telegram_client = TelegramClient(
                    StringSession(SESSION_STRING), API_ID, API_HASH
                )
            else:
                self.telegram_client = TelegramClient(
                    'user_monitor_session', API_ID, API_HASH
                )
            logger.info("Telegram client initialized")

        except Exception as e:
            logger.error(f"Client initialization failed: {e}")
            raise

    async def connect_telegram(self):
        """Handle Telegram connection with retries"""
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

    async def download_media(self, msg):
        """Download media to temporary file"""
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
        """Post message to Twitter with error handling"""
        if not self.twitter_client:
            logger.error("Twitter client not available")
            return False
        
        for attempt in range(3):
            try:
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
                
                # Post tweet
                response = self.twitter_client.create_tweet(
                    text=tweet_text,
                    media_ids=media_ids if media_ids else None
                )
                logger.info(f"Posted to Twitter (ID: {response.data['id']})")
                return True
                
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e}")
                if attempt == 2:
                    return False
                await asyncio.sleep(5)

    async def run(self):
        """Main bot execution loop"""
        if not await self.connect_telegram():
            logger.error("Failed to connect to Telegram")
            return

        try:
            channel = await self.telegram_client.get_entity(CHANNEL_USERNAME)
            logger.info(f"Monitoring channel: {channel.title}")

            @self.telegram_client.on(events.NewMessage(chats=channel))
            async def handler(event):
                with self.posting_lock:
                    self.message_history.append(event.message)
                    if len(self.message_history) > MAX_MESSAGE_HISTORY:
                        self.message_history.pop(0)
                    logger.info(f"New message: {event.message.id}")

            # Start periodic posting
            while True:
                await asyncio.sleep(60)  # Check every minute
                
                with self.posting_lock:
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

async def main():
    bot = BotClient()
    await bot.run()

if __name__ == "__main__":
    # For Windows compatibility
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())

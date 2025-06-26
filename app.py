from telethon import TelegramClient, events
import asyncio
import logging
import tweepy
from datetime import datetime, timedelta
import os
from tempfile import NamedTemporaryFile
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Telegram Configuration
api_id = 20572087
api_hash = '044ac78962bfd63b5487896a2cf33151'
channel_username = '@hamza20300'

# Twitter Configuration
twitter_api_key = 'bwyk8VCfY5IGtKLQdv3oHQ51a'
twitter_api_secret = 'XvKWkYqcSNs9sMS5vx1V4F9Ads93MHtVm4eagUo73EWBspI3l9'
twitter_access_token = '1692979869120929792-VbAE4cV0oJEBdfaEDur3FP17mK4ZN2'
twitter_access_secret = 'AGV19KVYsj9HgqwEPhv37GOgT4DT1uek97UyP3UF8INST'

# Posting Configuration
POST_INTERVAL_MINUTES = 2  # Post every 28 minutes
MAX_MESSAGE_HISTORY = 10    # Keep last 10 messages for fallback
SOURCE_ATTRIBUTION = " (منقول من مصدر فلسطيني)"  # Palestinian source attribution

# Initialize Twitter clients
try:
    auth_v1 = tweepy.OAuth1UserHandler(
        twitter_api_key,
        twitter_api_secret,
        twitter_access_token,
        twitter_access_secret
    )
    twitter_api = tweepy.API(auth_v1, wait_on_rate_limit=True)
    twitter_client = tweepy.Client(
        consumer_key=twitter_api_key,
        consumer_secret=twitter_api_secret,
        access_token=twitter_access_token,
        access_token_secret=twitter_access_secret,
        wait_on_rate_limit=True
    )
    logger.info("Twitter clients initialized successfully")
except Exception as e:
    logger.error(f"Twitter initialization failed: {e}")
    raise

class MessageProcessor:
    def __init__(self):
        self.message_history = []
        self.last_post_time = None
        self.posting_lock = asyncio.Lock()

    async def add_message(self, msg):
        """Add new message to history"""
        self.message_history.append(msg)
        if len(self.message_history) > MAX_MESSAGE_HISTORY:
            self.message_history.pop(0)

    async def get_next_message_to_post(self):
        """Get most recent message that hasn't been posted yet"""
        if not self.message_history:
            return None
        return self.message_history[-1]  # Always try latest first

    async def should_post_now(self):
        """Check if it's time to post based on interval"""
        if not self.last_post_time:
            return True
        elapsed = datetime.now() - self.last_post_time
        return elapsed.total_seconds() >= POST_INTERVAL_MINUTES * 60

    async def mark_posted(self, msg):
        """Remove successfully posted message from history"""
        if msg in self.message_history:
            self.message_history.remove(msg)
        self.last_post_time = datetime.now()

async def download_telegram_media(msg):
    """Download media from Telegram message to temp file"""
    try:
        if not msg.media:
            return None
            
        temp_file = NamedTemporaryFile(delete=False, suffix='.jpg')
        await msg.download_media(file=temp_file.name)
        return temp_file.name
    except Exception as e:
        logger.error(f"Media download failed: {e}")
        return None

async def post_to_twitter(msg):
    """Post message with media to Twitter with retry logic"""
    if not twitter_client or not twitter_api:
        logger.error("Twitter client not available")
        return False
    
    max_retries = 3
    retry_delay = 5
    
    for attempt in range(max_retries):
        try:
            # Prepare text with Palestinian source attribution
            tweet_text = (msg.text or "") + SOURCE_ATTRIBUTION
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            
            # Handle media
            media_ids = []
            if msg.media:
                media_path = await download_telegram_media(msg)
                if media_path:
                    try:
                        media = twitter_api.media_upload(media_path)
                        media_ids.append(media.media_id)
                        logger.info(f"📷 Media uploaded (ID: {media.media_id})")
                    except Exception as e:
                        logger.error(f"Media upload failed: {e}")
                    finally:
                        if os.path.exists(media_path):
                            os.unlink(media_path)
            
            # Post tweet (ensure total length <= 280 characters)
            max_text_length = 280 - len(SOURCE_ATTRIBUTION)
            response = twitter_client.create_tweet(
                text=tweet_text[:max_text_length] + SOURCE_ATTRIBUTION,
                media_ids=media_ids if media_ids else None
            )
            logger.info(f"✅ Posted to Twitter (ID: {response.data['id']})")
            return True
            
        except tweepy.TweepyException as e:
            logger.error(f"Twitter API error (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                return False
            time.sleep(retry_delay)
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return False
    
    return False

async def periodic_poster(processor):
    """Periodically check and post messages"""
    while True:
        try:
            async with processor.posting_lock:
                if await processor.should_post_now():
                    msg = await processor.get_next_message_to_post()
                    if msg:
                        success = await post_to_twitter(msg)
                        if success:
                            await processor.mark_posted(msg)
                        else:
                            logger.warning("Posting failed, will try again next cycle")
                    else:
                        logger.info("No messages to post")
                else:
                    next_post = processor.last_post_time + timedelta(minutes=POST_INTERVAL_MINUTES)
                    wait_seconds = (next_post - datetime.now()).total_seconds()
                    if wait_seconds > 0:
                        logger.info(f"Next post scheduled at {next_post}")
        
            await asyncio.sleep(60)  # Check every minute
            
        except Exception as e:
            logger.error(f"Error in periodic poster: {e}")
            await asyncio.sleep(60)

async def main():
    processor = MessageProcessor()
    
    # Telegram client setup
    client = TelegramClient('user_monitor_session', api_id, api_hash)
    
    try:
        await client.start()
        if not await client.is_user_authorized():
            logger.error("User not authorized.")
            return

        channel = await client.get_entity(channel_username)
        logger.info(f"✅ Channel found: {channel.title}")

        # Start periodic posting task
        asyncio.create_task(periodic_poster(processor))

        @client.on(events.NewMessage(chats=channel))
        async def handler(event):
            try:
                msg = event.message
                logger.info(f"\n📩 New message:\n🕒 {msg.date}\n📝 {msg.text or '[Media]'}")
                await processor.add_message(msg)
            except Exception as e:
                logger.error(f"Message processing error: {e}")

        logger.info(f"👂 Listening to {channel_username}...")
        await client.run_until_disconnected()

    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        await client.disconnect()
        logger.info("Disconnected")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")

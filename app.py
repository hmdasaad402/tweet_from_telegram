import os
import sys
import time
import asyncio
import logging
import tweepy
import locale
from datetime import datetime, timedelta
from tempfile import NamedTemporaryFile
from telethon import TelegramClient, events

# Set up proper encoding for Windows
if sys.platform == 'win32':
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Configure logging with UTF-8 support
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('twitter_poster.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class Config:
    # Telegram
    API_ID = 20572087
    API_HASH = '044ac78962bfd63b5487896a2cf33151'
    CHANNEL_USERNAME = '@hamza20300'
    
    # Twitter
    TWITTER_API_KEY = 'VHDiSLLL1VLR5U0QCKqSbi56R'
    TWITTER_API_SECRET = 'vJ6ZAXJD1Rd0MnzIPdj182deIcYZ1GbgxBW4J7xGqRRAbWavif'
    TWITTER_ACCESS_TOKEN = '1429500221604651019-Ha8fisnSLoT2CQdhLyp5049lWkdVhT'
    TWITTER_ACCESS_SECRET = 'UzHHzgsF1Z6gW2bZigHCzSAJ0HAZE2pKNbTV8zqVlZp4h'
    
    # Posting
    POST_INTERVAL = timedelta(minutes=3)
    MAX_MEDIA_SIZE_MB = 15
    MAX_TWEET_LENGTH = 280
    MAX_THREAD_LENGTH = 25  # Max tweets in a thread
    SOURCE_ATTRIBUTION = " (منقول من مصدر فلسطيني)"
    MAX_ATTEMPTS = 3
    RETRY_DELAY = 5

class MessageValidator:
    @staticmethod
    async def validate_message(msg):
        """Validate message for inclusion in report"""
        try:
            # Skip messages with large media
            if msg.media:
                media_path = await MediaHandler.download_media(msg)
                if media_path:
                    try:
                        size_mb = os.path.getsize(media_path) / (1024 * 1024)
                        if size_mb > Config.MAX_MEDIA_SIZE_MB:
                            logger.warning(f"Skipping message with large media ({size_mb:.2f} MB)")
                            return False
                    finally:
                        if os.path.exists(media_path):
                            os.unlink(media_path)
            return True
        except Exception as e:
            logger.error(f"Validation error: {e}")
            return False

class MediaHandler:
    @staticmethod
    async def download_media(msg):
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
        finally:
            if 'temp_file' in locals():
                temp_file.close()

class TwitterPoster:
    def __init__(self):
        self.client = self._initialize_client()
        
    def _initialize_client(self):
        """Initialize Twitter client with OAuth"""
        try:
            auth_v1 = tweepy.OAuth1UserHandler(
                Config.TWITTER_API_KEY,
                Config.TWITTER_API_SECRET,
                Config.TWITTER_ACCESS_TOKEN,
                Config.TWITTER_ACCESS_SECRET
            )
            
            client_v2 = tweepy.Client(
                consumer_key=Config.TWITTER_API_KEY,
                consumer_secret=Config.TWITTER_API_SECRET,
                access_token=Config.TWITTER_ACCESS_TOKEN,
                access_token_secret=Config.TWITTER_ACCESS_SECRET,
                wait_on_rate_limit=True
            )
            
            return {
                'v1': tweepy.API(auth_v1),
                'v2': client_v2
            }
        except Exception as e:
            logger.error(f"Twitter client initialization failed: {e}")
            raise

    async def post_thread(self, messages):
        """Post a thread of messages to Twitter"""
        if not messages:
            return False
            
        try:
            # Format the messages into a thread
            thread = self._format_thread(messages)
            if not thread:
                logger.warning("No valid content to post")
                return False
                
            # Post the thread
            previous_tweet_id = None
            for tweet_text in thread:
                response = self.client['v2'].create_tweet(
                    text=tweet_text,
                    in_reply_to_tweet_id=previous_tweet_id
                )
                previous_tweet_id = response.data['id']
                logger.info(f"Posted tweet (ID: {previous_tweet_id})")
                time.sleep(1)  # Small delay between tweets in thread
                
            return True
        except tweepy.TweepyException as e:
            logger.error(f"Twitter API error: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return False
    
    def _format_thread(self, messages):
        """Format messages into tweet thread chunks"""
        thread = []
        current_tweet = ""
        
        for msg in messages:
            message_content = f"{msg.text or ''}\n\n"  # Add two newlines between messages
            
            # If adding this message would exceed the limit, start a new tweet
            if len(current_tweet) + len(message_content) + len(Config.SOURCE_ATTRIBUTION) > Config.MAX_TWEET_LENGTH:
                if current_tweet:  # Don't add empty tweets
                    thread.append(current_tweet.strip() + Config.SOURCE_ATTRIBUTION)
                current_tweet = message_content
            else:
                current_tweet += message_content
        
        # Add the last tweet if it has content
        if current_tweet.strip():
            thread.append(current_tweet.strip() + Config.SOURCE_ATTRIBUTION)
            
        # Split into chunks if thread is too long
        if len(thread) > Config.MAX_THREAD_LENGTH:
            thread = thread[:Config.MAX_THREAD_LENGTH]
            thread[-1] += "\n[Thread continued in next interval]"
            
        return thread

class IntervalPoster:
    def __init__(self):
        self.current_interval_messages = []
        self.last_post_time = None
        self.lock = asyncio.Lock()
        self.twitter_poster = TwitterPoster()
        
    async def add_message(self, msg):
        """Add message to current interval buffer"""
        async with self.lock:
            self.current_interval_messages.append(msg)
            
    async def process_interval(self):
        """Process all valid messages in current interval into a thread"""
        async with self.lock:
            if not self.current_interval_messages:
                logger.info("No messages in current interval")
                return False
                
            # Filter valid messages
            valid_messages = []
            for msg in self.current_interval_messages:
                if await MessageValidator.validate_message(msg):
                    valid_messages.append(msg)
            
            if not valid_messages:
                logger.info("No valid messages in current interval")
                self.current_interval_messages = []
                self.last_post_time = datetime.now()
                return False
                
            # Post as a thread
            success = await self.twitter_poster.post_thread(valid_messages)
            
            # Log results
            logger.info(f"Processed interval: {len(valid_messages)} valid messages, {len(self.current_interval_messages)-len(valid_messages)} skipped")
            
            self.last_post_time = datetime.now()
            self.current_interval_messages = []
            return success
            
    def time_until_next_post(self):
        """Calculate time until next posting interval"""
        if not self.last_post_time:
            return timedelta(seconds=60)
            
        time_since_last = datetime.now() - self.last_post_time
        if time_since_last >= Config.POST_INTERVAL:
            return timedelta(seconds=0)
            
        return Config.POST_INTERVAL - time_since_last

async def main():
    poster = IntervalPoster()
    client = TelegramClient('user_monitor_session', Config.API_ID, Config.API_HASH)
    
    try:
        await client.start()
        if not await client.is_user_authorized():
            logger.error("User not authorized.")
            return

        channel = await client.get_entity(Config.CHANNEL_USERNAME)
        logger.info(f"Channel found: {channel.title}")

        @client.on(events.NewMessage(chats=channel))
        async def handler(event):
            try:
                msg = event.message
                logger.info(f"New message: {msg.date} - {msg.text or '[Media]'}")
                await poster.add_message(msg)
            except Exception as e:
                logger.error(f"Message processing error: {e}")

        logger.info(f"Listening to {Config.CHANNEL_USERNAME}")
        
        while True:
            wait_time = poster.time_until_next_post()
            if wait_time.total_seconds() > 0:
                logger.info(f"Next post in {wait_time}")
                await asyncio.sleep(wait_time.total_seconds())
                
            await poster.process_interval()
            
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
    except Exception as e:
        logger.error(f"Unexpected error: {e}")

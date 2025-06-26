from flask import Flask, render_template_string
from telethon import TelegramClient, events
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import tweepy
from datetime import datetime, timedelta
import logging
import os
from tempfile import NamedTemporaryFile
import asyncio
import threading

app = Flask(__name__)

# Configuration
class Config:
    # Telegram
    API_ID = 20572087
    API_HASH = '044ac78962bfd63b5487896a2cf33151'
    CHANNEL_USERNAME = '@hamza20300'
    
    # Twitter
    TWITTER_API_KEY = 'bwyk8VCfY5IGtKLQdv3oHQ51a'
    TWITTER_API_SECRET = 'XvKWkYqcSNs9sMS5vx1V4F9Ads93MHtVm4eagUo73EWBspI3l9'
    TWITTER_ACCESS_TOKEN = '1692979869120929792-VbAE4cV0oJEBdfaEDur3FP17mK4ZN2'
    TWITTER_ACCESS_SECRET = 'AGV19KVYsj9HgqwEPhv37GOgT4DT1uek97UyP3UF8INST'
    
    # App
    POST_INTERVAL_MINUTES = 28
    SOURCE_ATTRIBUTION = " (منقول من مصدر فلسطيني)"
    MAX_MESSAGE_HISTORY = 10

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables
message_history = []
last_post_time = None
telegram_client = None
twitter_api = None
twitter_client = None
processing_lock = threading.Lock()

def initialize_twitter():
    global twitter_api, twitter_client
    try:
        auth_v1 = tweepy.OAuth1UserHandler(
            Config.TWITTER_API_KEY,
            Config.TWITTER_API_SECRET,
            Config.TWITTER_ACCESS_TOKEN,
            Config.TWITTER_ACCESS_SECRET
        )
        twitter_api = tweepy.API(auth_v1, wait_on_rate_limit=True)
        twitter_client = tweepy.Client(
            consumer_key=Config.TWITTER_API_KEY,
            consumer_secret=Config.TWITTER_API_SECRET,
            access_token=Config.TWITTER_ACCESS_TOKEN,
            access_token_secret=Config.TWITTER_ACCESS_SECRET,
            wait_on_rate_limit=True
        )
        logger.info("Twitter clients initialized successfully")
    except Exception as e:
        logger.error(f"Twitter initialization failed: {e}")
        raise

async def initialize_telegram():
    global telegram_client
    telegram_client = TelegramClient('webapp_session', Config.API_ID, Config.API_HASH)
    await telegram_client.start()
    
    if not await telegram_client.is_user_authorized():
        logger.error("Telegram client not authorized")
        return False
    
    try:
        channel = await telegram_client.get_entity(Config.CHANNEL_USERNAME)
        logger.info(f"Connected to channel: {channel.title}")
        
        @telegram_client.on(events.NewMessage(chats=channel))
        async def handler(event):
            try:
                with processing_lock:
                    message_history.append(event.message)
                    if len(message_history) > Config.MAX_MESSAGE_HISTORY:
                        message_history.pop(0)
                    logger.info(f"New message received: {event.message.text[:50]}...")
            except Exception as e:
                logger.error(f"Error handling message: {e}")
                
        return True
    except Exception as e:
        logger.error(f"Telegram channel connection failed: {e}")
        return False

def post_to_twitter_sync():
    """Synchronous wrapper for the async post_to_twitter function"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(post_to_twitter())
    loop.close()
    return result

async def post_to_twitter():
    """Post the latest message to Twitter"""
    global last_post_time, message_history
    
    if not message_history:
        logger.info("No messages to post")
        return False
    
    if last_post_time and (datetime.now() - last_post_time).total_seconds() < Config.POST_INTERVAL_MINUTES * 60:
        logger.info("Not time to post yet")
        return False
    
    msg = message_history[-1]
    temp_file_path = None
    
    try:
        # Prepare text
        tweet_text = (msg.text or "") + Config.SOURCE_ATTRIBUTION
        max_text_length = 280 - len(Config.SOURCE_ATTRIBUTION)
        final_text = tweet_text[:max_text_length] + Config.SOURCE_ATTRIBUTION
        
        # Handle media
        media_ids = []
        if msg.media:
            temp_file_path = await download_telegram_media(msg)
            if temp_file_path:
                try:
                    media = twitter_api.media_upload(temp_file_path)
                    media_ids.append(media.media_id)
                    logger.info(f"Media uploaded (ID: {media.media_id})")
                except Exception as e:
                    logger.error(f"Media upload failed: {e}")
        
        # Post tweet
        response = twitter_client.create_tweet(
            text=final_text,
            media_ids=media_ids if media_ids else None
        )
        logger.info(f"Posted to Twitter (ID: {response.data['id']})")
        
        with processing_lock:
            message_history.remove(msg)
            last_post_time = datetime.now()
        
        return True
        
    except Exception as e:
        logger.error(f"Error posting to Twitter: {e}")
        return False
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.unlink(temp_file_path)

async def download_telegram_media(msg):
    """Download media from Telegram message"""
    try:
        if not msg.media:
            return None
            
        temp_file = NamedTemporaryFile(delete=False, suffix='.jpg')
        await msg.download_media(file=temp_file.name)
        return temp_file.name
    except Exception as e:
        logger.error(f"Media download failed: {e}")
        return None

def scheduled_post():
    """Scheduled task to post to Twitter"""
    with app.app_context():
        logger.info("Running scheduled post check")
        post_to_twitter_sync()

@app.route('/')
def index():
    status = {
        'last_post_time': last_post_time,
        'next_post_time': last_post_time + timedelta(minutes=Config.POST_INTERVAL_MINUTES) if last_post_time else None,
        'message_queue_size': len(message_history),
        'is_connected': telegram_client is not None and telegram_client.is_connected()
    }
    return render_template_string('''
        <h1>Telegram to Twitter Bot</h1>
        <p>Status: {% if status.is_connected %}✅ Connected{% else %}❌ Disconnected{% endif %}</p>
        <p>Messages in queue: {{ status.message_queue_size }}</p>
        <p>Last post: {{ status.last_post_time or 'Never' }}</p>
        <p>Next post: {{ status.next_post_time or 'Soon' }}</p>
    ''', status=status)

def run_telegram_client():
    """Run the Telegram client in a separate thread"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(initialize_telegram())
    loop.run_forever()

if __name__ == '__main__':
    # Initialize Twitter
    initialize_twitter()
    
    # Start Telegram client in background thread
    telegram_thread = threading.Thread(target=run_telegram_client, daemon=True)
    telegram_thread.start()
    
    # Configure scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        scheduled_post,
        trigger=IntervalTrigger(minutes=1),
        max_instances=1
    )
    scheduler.start()
    
    # Run Flask app
    app.run(host='0.0.0.0', port=5000, use_reloader=False)
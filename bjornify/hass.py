#!/usr/bin/env python
"""Load hass Discord.py bot"""

# pylint: disable=R0801

# Based on:
#  https://www.reddit.com/r/homeassistant/comments/1fcjypt/discord_assist_via_conversation_api/

import asyncio
import logging
import os
import signal
import sys
from typing import List

import discord
import requests
from discord.ext import commands

try:
    from version import __version__
except ImportError:
    __version__ = "dev"  # fallback for local dev without version.py

# Get and validate configured log output
LOG_OUTPUT_RAW = os.getenv("LOG_OUTPUT", "console")
if LOG_OUTPUT_RAW.strip() == "":
    LOG_OUTPUT = set()
else:
    LOG_OUTPUT = {
        entry.strip().lower() for entry in LOG_OUTPUT_RAW.split(",") if entry.strip()
    }

LOG_PATH = "logs/hass.log"

# Make sure log folder exists
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

# Common formatter
log_formatter = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
    "%Y-%m-%d - %H:%M:%S",
)

LOG_HANDLERS: List[logging.Handler] = []

# Create and configure file handler
if "file" in LOG_OUTPUT:
    file_handler = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    file_handler.setFormatter(log_formatter)
    LOG_HANDLERS.append(file_handler)

# Create and configure console (stdout) handler
if "console" in LOG_OUTPUT:
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(log_formatter)
    LOG_HANDLERS.append(console_handler)

# Define valid log levels
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Get and validate configured log levels
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
if LOG_LEVEL not in VALID_LOG_LEVELS:
    LOG_LEVEL = "INFO"  # fallback to default
LIB_LOG_LEVEL = os.getenv("LIB_LOG_LEVEL", "WARNING").upper()
if LIB_LOG_LEVEL not in VALID_LOG_LEVELS:
    LIB_LOG_LEVEL = "WARNING"  # fallback to default

# Apply to root logger
root_logger = logging.getLogger()
if LOG_HANDLERS:
    root_logger.setLevel(LOG_LEVEL)
    for handler in LOG_HANDLERS:
        root_logger.addHandler(handler)
else:
    root_logger.handlers.clear()
    logging.disable(logging.CRITICAL)

# Create app-specific logger
_LOGGER = logging.getLogger("hass")
_LOGGER.setLevel(LOG_LEVEL)
_LOGGER.propagate = True  # Let messages bubble up to root

# Apply lib log level to third-party modules
for lib in ("asyncio", "discord"):
    logging.getLogger(lib).setLevel(LIB_LOG_LEVEL)

# Load environment variables
DISCORD_BOT_TOKEN = os.getenv("HASS_DISCORD_BOT_TOKEN")
CHANNEL_ID = os.getenv("HASS_CHANNEL_ID")
HA_URL = os.getenv("HA_URL")
HA_ACCESS_TOKEN = os.getenv("HA_ACCESS_TOKEN")

# Validate that all required env vars are set
required_env_vars = [
    "HASS_DISCORD_BOT_TOKEN",
    "HASS_CHANNEL_ID",
    "HA_URL",
    "HA_ACCESS_TOKEN",
]

for var in required_env_vars:
    if not os.getenv(var):
        raise EnvironmentError(f"Missing required environment variable: {var}")

# Set up Bot
intents = discord.Intents.default()
intents.members = True
intents.messages = True
intents.message_content = True
bot = commands.Bot(
    command_prefix="!",
    description="Control Home Assistant in Björngrottan",
    intents=intents,
)


@bot.event
async def on_ready():
    """Bot logged in"""
    _LOGGER.info("Logged in as %s", bot.user)


@bot.event
async def on_message(message):
    """New message"""
    _LOGGER.debug("New message in %s", message.channel.id)
    # Ignore the bot's own messages
    if message.author == bot.user or message.author.bot:
        _LOGGER.debug("Message ignored from %s", message.author)
        return

    # Check if the message is in the specified channel
    _LOGGER.debug("Check if it matches %s", CHANNEL_ID)
    if int(message.channel.id) == int(CHANNEL_ID):
        _LOGGER.debug("Channel matches")
        query = message.content
        _LOGGER.debug("Query: %s", query)
        response = send_query_to_ha_assist(query)  # Send the query to HA Assist
        await message.channel.send(response)  # Respond in the same channel


def send_query_to_ha_assist(query):
    """Prepare the API request to Home Assistant Assist"""
    url = HA_URL
    headers = {
        "Authorization": f"Bearer {HA_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {"text": query, "language": "en"}

    # Send the request to HA Assist and handle the response
    response = requests.post(url, json=data, headers=headers, timeout=30)

    if response.status_code == 200:
        _LOGGER.debug("Response: %s", str(response.json()))
        return response.json()["response"]["speech"]["plain"][
            "speech"
        ]  # Extract the response text

    _LOGGER.info("Error communicating with Home Assistant")
    return "Error communicating with Home Assistant."


async def main():
    """Initialize and start the bot."""
    _LOGGER.info("HASS bot version: %s", __version__)
    await bot.start(DISCORD_BOT_TOKEN)


async def shutdown():
    """Handle graceful shutdown of the bot."""
    _LOGGER.info("Received stop signal. Shutting down gracefully...")
    await bot.close()
    _LOGGER.info("Shutdown complete")
    for log_handler in logging.getLogger().handlers:
        log_handler.flush()


def handle_signal(*_):
    """Signal handler that initiates bot shutdown."""
    asyncio.create_task(shutdown())


def run_bot():
    """Run the bot and set up signal handlers."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, handle_signal)
    try:
        loop.run_until_complete(main())
    finally:
        _LOGGER.debug("Cleaning up asyncio loop")
        loop.close()


if __name__ == "__main__":
    run_bot()

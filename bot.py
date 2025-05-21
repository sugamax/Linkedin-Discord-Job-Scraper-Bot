import logging
import os
import platform
import random
from typing import Dict, List
import json
from datetime import datetime, date, time, timedelta
from dotenv import load_dotenv
import pytz
import argparse
import time
from sqlalchemy.exc import OperationalError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String

import yaml
from jobspy import scrape_jobs
import discord
from discord.ext import commands, tasks
import asyncio
import sys

# Parse command line arguments
parser = argparse.ArgumentParser(description='Discord Job Hunter Bot')
parser.add_argument('--run-now', action='store_true', help='Run job search immediately on startup')
args = parser.parse_args()

# Load environment variables
load_dotenv()

# Load config at module level
try:
    with open('config.yaml', 'r') as file:
        CONFIG = yaml.safe_load(file)
    
    # Get Discord token from environment variable
    discord_token = os.getenv('DISCORD_TOKEN')
    if not discord_token:
        raise ValueError("DISCORD_TOKEN environment variable is not set")
    
    # Validate token format
    if len(discord_token) < 50:
        raise ValueError("Discord token appears to be invalid. Please check your .env file")
except Exception as e:
    print(f"Error loading configuration: {str(e)}")
    raise

intents = discord.Intents.default()
Base = declarative_base()

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True)
    description = Column(String)
    job_id = Column(String, unique=True)
    application_url = Column(String)
    job_title = Column(String)
    company_name = Column(String)
    company_url = Column(String)
    location = Column(String)
    job_type = Column(String)  # To store which job config this belongs to

class JobConfig:
    def __init__(self, config: Dict):
        self.name = config['name']
        self.icon = config.get('icon', '💼')  # Default to briefcase if no icon specified
        self.search_terms = config['search_terms']
        self.mandatory_terms = config['mandatory_terms']
        self.location = config.get('location', 'United States')  # Default to United States if not specified
        self.is_remote = config.get('is_remote', False)  # Default to False if not specified
        self.discord_channel_id = config['discord_channel_id']
        self.blacklist_companies = set(config['blacklist_companies'])
        self.current_search_index = 0

    def get_next_search_term(self) -> str:
        term = self.search_terms[self.current_search_index]
        self.current_search_index = (self.current_search_index + 1) % len(self.search_terms)
        return term

class LoggingFormatter(logging.Formatter):
    black = "\x1b[30m"
    red = "\x1b[31m"
    green = "\x1b[32m"
    yellow = "\x1b[33m"
    blue = "\x1b[34m"
    gray = "\x1b[38m"
    reset = "\x1b[0m"
    bold = "\x1b[1m"

    COLORS = {
        logging.DEBUG: gray + bold,
        logging.INFO: blue + bold,
        logging.WARNING: yellow + bold,
        logging.ERROR: red,
        logging.CRITICAL: red + bold,
    }

    def format(self, record):
        log_color = self.COLORS[record.levelno]
        format = "(black){asctime}(reset) (levelcolor){levelname:<8}(reset) (green){name}(reset) {message}"
        format = format.replace("(black)", self.black + self.bold)
        format = format.replace("(reset)", self.reset)
        format = format.replace("(levelcolor)", log_color)
        format = format.replace("(green)", self.green + self.bold)
        formatter = logging.Formatter(format, "%Y-%m-%d %H:%M:%S", style="{")
        return formatter.format(record)

logger = logging.getLogger("discord_bot")
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setFormatter(LoggingFormatter())
file_handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")
file_handler_formatter = logging.Formatter(
    "[{asctime}] [{levelname:<8}] {name}: {message}", "%Y-%m-%d %H:%M:%S", style="{"
)
file_handler.setFormatter(file_handler_formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Configure SQLite to handle concurrent access better
engine = create_engine("sqlite:///jobs.db", echo=False, connect_args={
    "timeout": 30,  # Increase timeout
    "check_same_thread": False  # Allow multi-threaded access
})

# Add event listener to handle database locks
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA busy_timeout=30000")  # Set busy timeout to 30 seconds
    cursor.close()

Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

def get_session():
    return Session()

class DiscordBot(commands.Bot):
    def __init__(self, run_now=False) -> None:
        super().__init__(
            command_prefix=None,
            intents=intents,
            help_command=None,
        )
        self.logger = logger
        self.session_factory = get_session
        self.job_configs = self.load_job_configs()
        self.run_now = run_now
        # Set Denver timezone
        self.denver_tz = pytz.timezone('America/Denver')

    def load_job_configs(self) -> List[JobConfig]:
        return [JobConfig(cfg) for cfg in CONFIG['job_hunting_configs']]

    async def setup_hook(self) -> None:
        self.logger.info(f"Logged in as {self.user.name}")
        self.logger.info(f"discord.py API version: {discord.__version__}")
        self.logger.info(f"Python version: {platform.python_version()}")
        self.logger.info(
            f"Running on: {platform.system()} {platform.release()} ({os.name})"
        )
        self.logger.info("-------------------")

    async def on_ready(self):
        print('Bot is ready!')
        # Run immediately if --run-now flag was specified
        if self.run_now:
            self.logger.info("--run-now flag specified, running job search immediately")
            await self.job_posting_task()
            # Exit after running the job posting task
            await self.close()
            sys.exit(0)

    async def post_jobs(self, jobs, job_config: JobConfig):
        channel_id = int(job_config.discord_channel_id)
        target_channel = self.get_channel(channel_id)
        
        if target_channel is None:
            self.logger.error(f"No channel with ID {channel_id} found for {job_config.name}")
            return

        session = self.session_factory()
        try:
            for index, row in jobs.iterrows():
                if row['company'] in job_config.blacklist_companies:
                    self.logger.info(
                        f"Skipping job from blacklisted company: {row['company']} for {job_config.name}")
                    continue

                # Check if job title contains any mandatory terms
                if not any(term.lower() in row['title'].lower() for term in job_config.mandatory_terms):
                    self.logger.info(
                        f"Skipping job {row['title']} - doesn't contain mandatory terms for {job_config.name}")
                    continue

                # Retry mechanism for database operations
                max_retries = 3
                retry_delay = 1
                
                for attempt in range(max_retries):
                    try:
                        # Check if job was already posted to this channel by checking the job URL
                        query = session.query(Job).filter(
                            Job.application_url == row['job_url'],
                            Job.job_type == job_config.name
                        ).first()
                        
                        if query is None:
                            # Check if job exists in any channel
                            existing_job = session.query(Job).filter(
                                Job.job_id == row['id']
                            ).first()
                            
                            if existing_job is None:
                                # Get the job title without tags
                                job_title = row.get('title', 'Position Not Listed')
                                
                                # Determine remote/local status
                                is_remote = row.get('is_remote', False)
                                is_local = job_config.location != "United States"
                                
                                # Format the status text
                                status = []
                                if is_remote:
                                    status.append("🏠 Remote")
                                if is_local:
                                    status.append("📍 Local")
                                if not status:
                                    status.append("🏢 On-site")

                                location = row.get('location', '').strip()
                                status_str = ' | '.join(status)
                                if location:
                                    info_line = f"{location} | {status_str}"
                                else:
                                    info_line = status_str

                                # Format the date if it exists and is not NaN
                                date_str = ''
                                if row.get('date_posted') and str(row['date_posted']).lower() != 'nan':
                                    try:
                                        if isinstance(row['date_posted'], (datetime, date)):
                                            date_str = f" • Posted: {row['date_posted'].strftime('%Y-%m-%d')}"
                                        else:
                                            date_str = f" • Posted: {str(row['date_posted'])}"
                                    except Exception as e:
                                        self.logger.warning(f"Error formatting date for job {row.get('title')}: {str(e)}")
                                        date_str = ''

                                job_info = f""">>> ## {job_config.icon} [{row.get('company', 'Company Not Listed')}](<{row.get('company_url', '#')}>)

[**{job_title}**](<{row.get('job_url', '#')}>) | {info_line}

{row.get('company_industry', 'Industry Not Specified')}{date_str}
---
                                """
                                self.logger.info(f"Posting job: {job_title} for {job_config.name}")
                                
                                # Create new job entry
                                new_job = Job(
                                    job_id=row['id'],
                                    application_url=row['job_url'],
                                    job_title=job_title,
                                    company_name=row['company'],
                                    company_url=row['company_url'],
                                    location=row['location'],
                                    job_type=job_config.name
                                )
                                
                                # Add to session and commit immediately
                                session.add(new_job)
                                session.commit()
                                
                                # Send message after successful database operation
                                await target_channel.send(job_info)
                                # Add 1 second delay between messages
                                await asyncio.sleep(1)
                            else:
                                # Job exists in another channel, update job_type to include this channel
                                if job_config.name not in existing_job.job_type:
                                    existing_job.job_type = f"{existing_job.job_type}, {job_config.name}"
                                    session.commit()
                                    self.logger.info(f"Updated job_type for existing job {row['title']} to include {job_config.name}")
                        else:
                            self.logger.info(f"Job already posted to channel {job_config.name}: {row['title']}")
                        
                        # If we get here, the operation was successful
                        break
                        
                    except OperationalError as e:
                        if attempt < max_retries - 1:
                            self.logger.warning(f"Database locked, retrying in {retry_delay} seconds... (Attempt {attempt + 1}/{max_retries})")
                            time.sleep(retry_delay)
                            retry_delay *= 2  # Exponential backoff
                            session.rollback()
                        else:
                            raise
                    
        except Exception as e:
            self.logger.error(f"Error in post_jobs: {str(e)}")
            session.rollback()
            raise
        finally:
            session.close()

    async def job_posting_task(self):
        for job_config in self.job_configs:
            search_term = job_config.get_next_search_term()
            self.logger.info(
                f"Searching for {job_config.name} with term: {search_term} in {job_config.location} "
                f"({'remote only' if job_config.is_remote else 'all locations'})"
            )
            
            try:
                jobs = await self.get_jobs(
                    search_term=search_term,
                    location=job_config.location,
                    is_remote=job_config.is_remote
                )
                            
                await self.post_jobs(jobs, job_config)
            except Exception as e:
                self.logger.error(f"Error processing {job_config.name}: {str(e)}")
                continue

    async def get_jobs(self, sites=None, search_term='', location='United States',
                       results_wanted=20, hours_old=72, is_remote=False):
        if sites is None:
            sites = ['linkedin']
        jobs = scrape_jobs(
            site_name=sites,
            search_term=search_term,
            location=location,
            results_wanted=results_wanted,
            hours_old=hours_old,
            is_remote=is_remote,
            linkedin_fetch_description=True,
        )
        return jobs

    def format_salary(self, row):
        try:
            job_function = row.get('job_function', {})
            if not job_function:
                return "Not Specified"
            
            min_amount = job_function.get('min_amount')
            max_amount = job_function.get('max_amount')
            interval = job_function.get('interval', '')
            
            if min_amount and max_amount:
                return f"${min_amount:,} - ${max_amount:,} {interval}"
            elif min_amount:
                return f"${min_amount:,}+ {interval}"
            elif max_amount:
                return f"Up to ${max_amount:,} {interval}"
            else:
                return "Not Specified"
        except Exception:
            return "Not Specified"

    async def close(self):
        """Clean up when the bot is shutting down"""
        await super().close()

try:
    bot = DiscordBot(run_now=args.run_now)
    print("Starting bot...")
    bot.run(discord_token)
except discord.errors.LoginFailure as e:
    print("Failed to login to Discord. Please check if your token is valid.")
    print("Error:", str(e))
except Exception as e:
    print(f"An unexpected error occurred: {str(e)}")
    raise
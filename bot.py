import logging
import os
import platform
import random
from typing import Dict, List
import json
from datetime import datetime, date, time
from dotenv import load_dotenv
import pytz
import argparse

import yaml
from jobspy import scrape_jobs
import discord
from discord.ext import commands, tasks
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import Column, Integer, String

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

engine = create_engine("sqlite:///jobs.db", echo=False)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

class DiscordBot(commands.Bot):
    def __init__(self, s=None, run_now=False) -> None:
        super().__init__(
            command_prefix=None,
            intents=intents,
            help_command=None,
        )
        self.logger = logger
        self.session = s
        self.job_configs = self.load_job_configs()
        self.run_now = run_now
        # Set Denver timezone
        self.denver_tz = pytz.timezone('America/Denver')
        self.check_time_task = self.check_time_task

    def load_job_configs(self) -> List[JobConfig]:
        return [JobConfig(cfg) for cfg in CONFIG['job_hunting_configs']]

    @tasks.loop(minutes=1.0)
    async def check_time_task(self) -> None:
        """Check if it's 9:30 AM Denver time"""
        denver_time = datetime.now(self.denver_tz)
        if denver_time.hour == 9 and denver_time.minute == 30:
            self.logger.info("It's 9:30 AM Denver time - running job search")
            await self.job_posting_task()

    @check_time_task.before_loop
    async def before_check_time_task(self) -> None:
        await self.wait_until_ready()

    async def setup_hook(self) -> None:
        self.logger.info(f"Logged in as {self.user.name}")
        self.logger.info(f"discord.py API version: {discord.__version__}")
        self.logger.info(f"Python version: {platform.python_version()}")
        self.logger.info(
            f"Running on: {platform.system()} {platform.release()} ({os.name})"
        )
        self.logger.info("-------------------")
        # Start the time check task
        if not self.check_time_task.is_running():
            self.check_time_task.start()

    async def post_jobs(self, jobs, job_config: JobConfig):
        channel_id = int(job_config.discord_channel_id)
        target_channel = self.get_channel(channel_id)
        
        if target_channel is None:
            self.logger.error(f"No channel with ID {channel_id} found for {job_config.name}")
            return

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

            query = self.session.query(Job).filter(Job.job_id == row['id']).first()
            if query is None:
                # Format job title with location and remote tags
                job_title = row.get('title', 'Position Not Listed')
                tags = []
                
                if job_config.location != "United States":
                    tags.append("Local")
                if job_config.is_remote:
                    tags.append("Remote")
                
                if tags:
                    job_title = f"[{'/'.join(tags)}] {job_title}"
                    self.logger.info(f"Adding tags to job: {job_title} for config: {job_config.name}")

                job_info = f""">>> ## {job_config.icon} [{row.get('company', 'Company Not Listed')}](<{row.get('company_url', '#')}>) just posted a new job! 

### **Role:** 
[**{job_title}**](<{row.get('job_url', '#')}>)

### **Industry:**
{row.get('company_industry', 'Industry Not Specified')}

### **Location:** 
{row.get('location', 'Location Not Specified')}

### **Remote Status:**
{'🏠 Remote' if row.get('is_remote') else '🏢 On-site/Not Specified'}

### **Posted:**
{row.get('date_posted', 'Date Not Specified')}
---
                """
                self.logger.info(f"Posting job: {job_title} for {job_config.name}")
                self.session.add(Job(
                    job_id=row['id'],
                    application_url=row['job_url'],
                    job_title=job_title,
                    company_name=row['company'],
                    company_url=row['company_url'],
                    location=row['location'],
                    job_type=job_config.name
                ))
                await target_channel.send(job_info)
            else:
                self.logger.info(f"Job already exists in the database: {row['title']}")

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

    async def on_ready(self):
        print('Bot is ready!')
        # Run immediately if --run-now flag was specified
        if self.run_now:
            self.logger.info("--run-now flag specified, running job search immediately")
            await self.job_posting_task()

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
        if self.check_time_task.is_running():
            self.check_time_task.cancel()
        await super().close()

try:
    bot = DiscordBot(s=session, run_now=args.run_now)
    print("Starting bot...")
    bot.run(discord_token)
except discord.errors.LoginFailure as e:
    print("Failed to login to Discord. Please check if your token is valid.")
    print("Error:", str(e))
except Exception as e:
    print(f"An unexpected error occurred: {str(e)}")
    raise
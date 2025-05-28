# LinkedIn Job Hunter Discord Bot

### Forked  from https://github.com/haydenthai/Linkedin-Discord-Job-Scraper-Bot


A Discord bot that automatically scrapes LinkedIn for job postings and posts them to specified Discord channels. The bot supports multiple job configurations, remote/local filtering, and runs on a schedule.

## Features

- 🔍 Multiple job search configurations
- 🌍 Location-based filtering (US-wide and specific locations)
- 🏠 Remote job filtering
- ⏰ Daily run at 9AM
- 🏷️ Automatic tagging of jobs ([Local], [Remote])
- 📊 Job deduplication
- 📝 Detailed job postings with company info
- 🔄 Automatic daily updates

## Setup

1. Clone the repository:
```bash
git clone https://github.com/sugamax/Linkedin-Discord-Job-Scraper-Bot.git
cd Linkedin-Discord-Job-Scraper-Bot
```

2. Create a `.env` file with your Discord token:
```
DISCORD_TOKEN=your_discord_token_here
```

3. Install dependencies:
```bash
yum install sqlite-devel -y
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

4. Configure your job searches in `config.yaml`:
```yaml
job_hunting_configs:
  - name: "Engineering Manager Jobs Remote US"
    icon: "👨‍💼" 
    search_terms:
      - "software engineering manager"
      - "director of software engineering"
    mandatory_terms:
      - "manager"
      - "director"
    location: "United States" 
    is_remote: True
    discord_channel_id: your_channel_id
    blacklist_companies: []
```

## Running the Bot

### Manual Run
```bash
# Run with scheduled timing
python bot.py

# Run immediately and then continue with schedule
python bot.py --run-now
```

### Systemd Service (Recommended)
1. Install the service and timer:
```bash
chmod +x install-service.sh
./install-service.sh
```

2. View logs:
```bash
# View real-time logs
sudo journalctl -u job-hunter -f

# View log files
tail -f bot.log
tail -f bot.error.log
```

3. Service management:
```bash
# Check status
sudo systemctl status job-hunter

# Stop service
sudo systemctl stop job-hunter

# Start service
sudo systemctl start job-hunter

# Restart service
sudo systemctl restart job-hunter
```

## Configuration

### Job Configurations
Each job configuration in `config.yaml` supports:
- `name`: Display name for the job type
- `icon`: Emoji icon for job posts
- `search_terms`: List of search terms to use
- `mandatory_terms`: Terms that must appear in job titles
- `location`: Job location (e.g., "United States", "Colorado")
- `is_remote`: Whether to search for remote jobs only
- `discord_channel_id`: Channel to post jobs to
- `blacklist_companies`: Companies to exclude

### Job Posting Format
Jobs are posted with:
- Company name and link
- Role with location/remote tags
- Industry
- Location
- Remote status
- Posted date

## Recent Changes

- Added [Local] and [Remote] tags to job titles
- Implemented scheduled runs at 9:30 AM Denver time
- Added systemd service for automated running
- Moved Discord token to .env file
- Added support for multiple job configurations
- Improved logging and error handling
- Added job deduplication
- Added blacklist company support

## Contributing

Feel free to submit issues and enhancement requests!

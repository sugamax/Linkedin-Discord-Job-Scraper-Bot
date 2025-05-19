#!/bin/bash

# Remove existing virtual environment if it exists
rm -rf venv

# Copy service file to systemd directory
sudo cp job-hunter.service /etc/systemd/system/

# Reload systemd to recognize new service
sudo systemctl daemon-reload

# Enable the service to start on boot
sudo systemctl enable job-hunter

# Start the service
sudo systemctl start job-hunter

# Check status
echo "Service status:"
sudo systemctl status job-hunter

echo "To view logs:"
echo "sudo journalctl -u job-hunter -f"
echo "Or check the log files in the bot directory:"
echo "bot.log and bot.error.log" 
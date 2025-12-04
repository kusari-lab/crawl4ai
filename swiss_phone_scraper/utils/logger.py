"""Structured logging for the phone scraping system."""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


class ScraperLogger:
    """Structured logger for scraping operations."""
    
    def __init__(self, log_file: Optional[str] = None):
        """
        Initialize logger.
        
        Args:
            log_file: Path to log file (optional)
        """
        self.logger = logging.getLogger('swiss_phone_scraper')
        self.logger.setLevel(logging.INFO)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_format = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_format)
        self.logger.addHandler(console_handler)
        
        # File handler
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)
            file_format = logging.Formatter(
                '%(asctime)s - %(levelname)s - %(name)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(file_format)
            self.logger.addHandler(file_handler)
    
    def info(self, message: str):
        """Log info message."""
        self.logger.info(message)
    
    def error(self, message: str, exc_info=False):
        """Log error message."""
        self.logger.error(message, exc_info=exc_info)
    
    def warning(self, message: str):
        """Log warning message."""
        self.logger.warning(message)
    
    def debug(self, message: str):
        """Log debug message."""
        self.logger.debug(message)
    
    def log_business_attempt(self, business_name: str, source: str, success: bool, 
                            phone: Optional[str] = None, error: Optional[str] = None):
        """
        Log business scraping attempt.
        
        Args:
            business_name: Name of business
            source: Source name
            success: Whether attempt was successful
            phone: Phone number if found
            error: Error message if failed
        """
        status = "SUCCESS" if success else "FAILED"
        message = f"[{status}] {business_name} | Source: {source}"
        if phone:
            message += f" | Phone: {phone}"
        if error:
            message += f" | Error: {error}"
        self.info(message)


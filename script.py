import asyncio
from itertools import tee
import logging
import os
import time
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime
import re
from urllib.parse import urlparse

from proxy_lite import Runner, RunnerConfig
from proxy_lite.logger import logger

# Configure enhanced logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'newsletter_registrations_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'),
        logging.StreamHandler()
    ]
)

@dataclass
class ProcessingResult:
    url: str
    success: bool
    error_message: Optional[str] = None
    form_found: bool = False
    submission_confirmed: bool = False
    processing_time: float = 0.0

class NewsletterSignupBot:
    def __init__(self, urls_file: str = "url.csv"):
        self.urls_file = urls_file
        self.processed_urls_file = "processed_urls.txt"
        self.completed_urls_file = "completed_urls.txt"
        self.last_index_file = "last_index.txt"
        self.urls = self.get_urls_from_file()
        self.config = self.create_proxy_config()
        self.semaphore = asyncio.Semaphore(1)  # Limit to 1 concurrent task

    def get_urls_from_file(self) -> List[str]:
        """Load URLs from file with error handling"""
        urls = []
        try:
            with open(self.urls_file, 'r') as f:
                for line in f:
                    url = line.strip()
                    if url and not url.startswith('#'):  # Skip comments
                        urls.append(url)
            logger.info(f"Loaded {len(urls)} URLs from {self.urls_file}")
        except FileNotFoundError:
            logger.error(f"Error: File not found at {self.urls_file}")
            raise
        return urls

    def create_proxy_config(self) -> RunnerConfig:
        """Create optimized proxy-lite configuration for form handling"""
        config_dict = {
            "environment": {
                "name": "webbrowser",
                "annotate_image": True,
                "screenshot_delay": 2.0,  # Reduced delay for faster processing
                "viewport_width": 1280,
                "viewport_height": 1920,
                "include_poi_text": True,
                "homepage": "https://www.google.com",
                "keep_original_image": True,
                "headless": True,  # Use headless mode to avoid profile conflicts
                "include_html": True,  # Include HTML for better form detection
            },
            "solver": {
                "name": "simple",
                "agent": {
                    "name": "proxy_lite",
                    "client": {
                        "name": "convergence",
                        "model_id": "convergence-ai/proxy-lite-3b",
                        "api_base": "https://convergence-ai-demo-api.hf.space/v1",
                    },
                },
            },
            "max_steps": 50,  # Increased for complex forms
            "task_timeout": 900,  # 5 minutes per URL
            "action_timeout": 20,  # 1 minute per action
            "environment_timeout": 30,  # 30 seconds for environment response
            "logger_level": "INFO",
            "save_every_step": True,
        }
        return RunnerConfig.from_dict(config_dict)

    def generate_email_from_url(self, url: str) -> str:
        """Generate email in format: news-{domain}@plugilo.news"""
        try:
            # Parse the URL to extract the domain
            parsed = urlparse(url if url.startswith(('http://', 'https://')) else f'http://{url}')
            domain = parsed.netloc
            
            # Always define clean_url at the start
            clean_url = url.replace('http://', '').replace('https://', '').split('/')[0]
            domain = clean_url
            # Remove 'www.' prefix if present
            if domain.startswith('www.'):
                domain = domain[4:]
                clean_url = domain
            # If domain is empty, try to extract from the original URL
            if not domain:
                # Handle cases where URL might just be a domain without protocol
                clean_url = url.replace('http://', '').replace('https://', '').split('/')[0]
                if clean_url.startswith('www.'):
                    clean_url = clean_url[4:]
                domain = clean_url
            return f"news-{clean_url}@plugilo.news"
        except Exception as e:
            logger.warning(f"Error parsing URL {url}: {e}. Using fallback method.")
            clean_url = url.replace('http://', '').replace('https://', '').split('/')[0]
            if clean_url.startswith('www.'):
                clean_url = clean_url[4:]
            return f"news-{clean_url}@plugilo.news"

    def get_start_index(self) -> int:
        """Get the starting index from last run"""
        try:
            if os.path.exists(self.last_index_file):
                with open(self.last_index_file, 'r') as f:
                    return int(f.read().strip())
        except (FileNotFoundError, ValueError):
            pass
        return 0

    def save_progress(self, index: int):
        """Save current progress"""
        with open(self.last_index_file, 'w') as f:
            f.write(str(index))

    def is_url_processed(self, url: str) -> bool:
        """Check if URL was already processed"""
        if os.path.exists(self.processed_urls_file):
            with open(self.processed_urls_file, 'r') as f:
                processed = f.read().splitlines()
                return url in processed
        return False

    def mark_url_processed(self, url: str, result: ProcessingResult):
        """Mark URL as processed and log result"""
        # Add to processed URLs
        with open(self.processed_urls_file, 'a') as f:
            f.write(f"{url}\n")
        
        # Add to completed URLs if successful
        if result.success and result.submission_confirmed:
            with open(self.completed_urls_file, 'a') as f:
                f.write(f"{url}\n")
        
        # Log detailed result
        status = "SUCCESS" if result.success else "FAILED"
        logger.info(f"[{status}] {url} - Form: {result.form_found}, "
                   f"Submitted: {result.submission_confirmed}, "
                   f"Time: {result.processing_time:.1f}s")
        
        if result.error_message:
            logger.error(f"Error for {url}: {result.error_message}")

    def create_enhanced_prompt(self, url: str, captcha_failures: int = 0) -> str:
        """Create a comprehensive prompt for form detection and filling"""
        email = self.generate_email_from_url(url)
        
        prompt = f"""
Visit the URL: {url}

Your task is to find and successfully submit a newsletter signup, contact, or subscription form. Follow these steps carefully:

STEP 1 - FORM DETECTION:
Search thoroughly for forms containing any of these keywords (in any language):
- Newsletter, subscribe, subscription, abonnieren, anmelden
- Contact, kontakt, kontaktformular, contact form
- Email signup, email list, mailing list
- Register, registration, registrierung
- Stay updated, get updates, news updates

STEP 2 - FORM ANALYSIS:
Before filling, analyze the form to identify:
- Required fields (marked with * or "required")
- Email field
- Name fields (first name, last name, full name)
- Any checkboxes for consent/privacy policy
- Submit button location

STEP 3 - FORM FILLING:
Fill the form with this exact information:
- First Name: Max
- Last Name: Plugilo  
- Full Name: Max Plugilo
- Email: {email}
- Any other text fields: Use appropriate placeholder text
c
STEP 4 - CONSENT AND SUBMISSION:
- Check ALL required checkboxes (privacy policy, terms, consent, etc.)
- Look for GDPR consent checkboxes and check them
- Click the submit/send/register button
- Wait for confirmation or success message

STEP 5 - VERIFICATION:
After submission, look for:
- Success messages
- Confirmation pages
- "Thank you" messages
- Email verification notices
- Any indication the form was successfully submitted

IMPORTANT REQUIREMENTS:
- Be persistent - try multiple approaches if the first attempt fails
- Handle pop-ups, cookie banners, or overlays that might block the form
- If you encounter a captcha, attempt to solve it
- Wait for page loads between actions
- If no form is found after thorough searching, clearly state "NO FORM FOUND"
- If form submission fails, try alternative submit buttons or methods

{"AFTER 5 CAPTCHA FAILURES, SKIP TO THE NEXT PAGE" if captcha_failures >= 5 else ""}

Report your final status clearly at the end.
"""
        return prompt
    
    async def process_url(self, url: str, captcha_failures=0) -> ProcessingResult:
        """Process a single URL with enhanced error handling"""
        start_time = time.time()
        result = ProcessingResult(url=url, success=False)
        
        try:
            email = self.generate_email_from_url(url)
            if not url.startswith(('http://', 'https://')):
                if not url.startswith('www.'):
                    url = 'www.' + url
                url = 'http://' + url
            logger.info(f"Processing: {url} with email: {email}")

            # Create runner instance
            runner = Runner(config=self.config)

            # Create enhanced prompt
            prompt = self.create_enhanced_prompt(url, captcha_failures)

            # Run the task
            run_result = await runner.run(prompt)
            
            # Analyze the result
            result.processing_time = time.time() - start_time
            
            if run_result.complete:
                result_text = run_result.result.lower()
                
                # Check for form detection
                if any(keyword in result_text for keyword in [
                    "form found", "newsletter", "subscribe", "contact", "email"
                ]):
                    result.form_found = True
                
                # Check for successful submission
                if any(keyword in result_text for keyword in [
                    "success", "submitted", "thank you", "confirmation",
                    "registered", "subscribed", "sent"
                ]):
                    result.submission_confirmed = True
                    result.success = True
                elif "no form found" in result_text:
                    result.success = True  # Successfully determined no form exists
                    result.error_message = "No newsletter form found on page"
                else:
                    result.error_message = "Form found but submission unclear"
            else:
                result.error_message = "Task did not complete within timeout"

        except asyncio.TimeoutError:
            result.error_message = "Timeout during processing"
            result.processing_time = time.time() - start_time
        except Exception as e:
            result.error_message = f"Unexpected error: {str(e)}"
            result.processing_time = time.time() - start_time
            logger.exception(f"Error processing {url}")

        return result
    
    async def process_url_with_semaphore(self, url: str, captcha_failures=0) -> ProcessingResult:
        """Process a single URL with semaphore control"""
        async with self.semaphore:
            return await self.process_url(url, captcha_failures)

    async def run_batch(self, start_index: int = 0, batch_size: int = 1):
        """Run newsletter signup for a batch of URLs sequentially (single concurrency)"""
        logger.info(f"Starting batch processing from index {start_index}")
        
        successful_submissions = 0
        total_processed = 0
        
        for i in range(start_index + 1, len(self.urls)):
            url = self.urls[i]
            # Skip if already processed
            if self.is_url_processed(url):
                logger.info(f"Skipping already processed URL: {url}")
                continue
            try:
                result = await self.process_url_with_semaphore(url, 0)
                # Mark as processed and log result
                self.mark_url_processed(url, result)
                # Update counters
                total_processed += 1
                if result.success and result.submission_confirmed:
                    successful_submissions += 1
                # Save progress
                self.save_progress(i + 1)
                # Log progress
                logger.info(f"Progress: {i + 1}/{len(self.urls)} URLs processed. "
                           f"Successful submissions: {successful_submissions}")
            except Exception as e:
                logger.exception(f"Error processing {url}")
                continue
        logger.info(f"Batch complete. Total processed: {total_processed}, "
                   f"Successful submissions: {successful_submissions}")
        return successful_submissions, total_processed

async def main():
    """Main execution function"""
    bot = NewsletterSignupBot()
    
    # Get starting index
    start_index = bot.get_start_index()
    
    logger.info(f"Newsletter signup bot starting from index {start_index}")
    logger.info(f"Total URLs to process: {len(bot.urls)}")
    
    try:
        successful, total = await bot.run_batch(start_index)
        logger.info(f"Final results: {successful} successful submissions out of {total} processed URLs")
    except Exception as e:
        logger.error(f"Fatal error in main execution: {e}")
        raise

if __name__ == "__main__":
    # Run with retry logic for robustness
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            asyncio.run(main())
            break  # Success, exit retry loop
        except Exception as e:
            retry_count += 1
            logger.error(f"Execution failed (attempt {retry_count}/{max_retries}): {e}")
            if retry_count < max_retries:
                logger.info(f"Retrying in 10 seconds...")
                time.sleep(10)
            else:
                logger.error("Max retries exceeded. Exiting.")
                raise
import sqlite3
import requests
import time
import logging
from datetime import datetime
import pytz

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Define the EDT timezone
eastern = pytz.timezone('US/Eastern')

def repost_failed_transcripts():
    conn = sqlite3.connect('transcriptions.db')
    cursor = conn.cursor()

    try:
        # Fetch transcripts with unsuccessful response codes or NULL response codes
        cursor.execute('''
            SELECT id, timestamp, transcript
            FROM transcriptions
            WHERE id > 90000 AND (responsecode IS NOT NULL AND responsecode < 200 OR responsecode >= 300)
        ''')
        
        failed_transcripts = cursor.fetchall()
        
        if not failed_transcripts:
            logger.info("No failed transcripts found.")
            return

        logger.info(f"Found {len(failed_transcripts)} failed transcripts. Attempting to repost...")

        for transcript in failed_transcripts:
            id, timestamp, text = transcript
            
            post_url = "https://lkwd.agency/transcription"
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "ScannerStream0.5"
            }
            data = {
                "timestamp": timestamp,
                "text": transcript
            }
            
            try:
                response = requests.post(post_url, headers=headers, json=data, timeout=10)
                response.raise_for_status()
                
                # Update the database with the new response code
                cursor.execute('''
                    UPDATE transcriptions
                    SET responsecode = ?
                    WHERE id = ?
                ''', (response.status_code, id))
                conn.commit()
                
                logger.info(f"Successfully reposted transcript ID {id}: {response.status_code}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to repost transcript ID {id}: {str(e)}")
                if hasattr(e, 'response') and e.response is not None:
                    logger.error(f"Response content: {e.response.content}")
                    # Update the database with the error response code if available
                    if e.response.status_code:
                        cursor.execute('''
                            UPDATE transcriptions
                            SET responsecode = ?
                            WHERE id = ?
                        ''', (e.response.status_code, id))
                        conn.commit()
            
            # Add a small delay between posts to avoid overwhelming the server
            time.sleep(0.1)

    except sqlite3.Error as e:
        logger.error(f"Database error: {str(e)}")
    finally:
        conn.close()

if __name__ == "__main__":
    repost_failed_transcripts()

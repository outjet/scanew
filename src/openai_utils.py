# openai_utils.py

import requests
from flask import current_app
import json

def call_openai_api(payload):
    """Calls the OpenAI API and returns the response data."""
    api_key = current_app.config.get('OPENAI_API_KEY')
    if not api_key:
        current_app.logger.error("OpenAI API key is not set in the configuration")
        raise ValueError("OpenAI API key is missing")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"Error in OpenAI API call: {e}", exc_info=True)
        raise

def get_unit_status_from_openai(dispatch_text):
    """Analyzes dispatch text and returns unit status information."""
    prompt = f"""
Analyze the following dispatch traffic and provide a JSON output of the current status and location of all units mentioned.

For each unit, provide:
1. Unit number (as the key)
2. Type (police or fire)
3. Current status (e.g., dispatched, on scene, clear, unknown)
4. Detail of call
5. Current or last known location
6. Time of last update

Use the following JSON format:

{{
    "UnitNumber": {{
        "type": "Type",
        "status": "Status",
        "location": "Location",
        "detail": "Detail",
        "last_update": "Timestamp"
    }}
}}

Dispatch traffic:
{dispatch_text}

Respond only with the JSON output, no other text.
"""

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are a dispatch analyzer. Provide output in the exact JSON format specified."},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        response_data = call_openai_api(payload)
        content = response_data['choices'][0]['message']['content']

        # Remove code block markers if present
        if content.startswith("```json"):
            content = content.strip('```json').strip('```')

        unit_data = json.loads(content)
        return unit_data

    except json.JSONDecodeError as e:
        current_app.logger.error(f"Failed to parse OpenAI response as JSON: {e}")
        current_app.logger.error(f"Raw content: {content}")
        return {"error": f"Failed to parse unit data. Raw content: {content[:500]}..."}
    except Exception as e:
        current_app.logger.error(f"Error in get_unit_status_from_openai: {e}", exc_info=True)
        return {"error": f"An unexpected error occurred: {e}"}

# openai_utils.py

import requests
from flask import current_app
import json

def _redact_api_key(api_key):
    if not api_key:
        return ""
    if len(api_key) <= 14:
        return f"{api_key[:2]}...{api_key[-2:]}"
    return f"{api_key[:10]}...{api_key[-4:]}"

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
        response_body = ""
        if getattr(e, "response", None) is not None:
            try:
                response_body = e.response.text[:2000]
            except Exception:
                response_body = "<unavailable>"
        current_app.logger.error(
            f"Error in OpenAI API call: {e}; response_body={response_body}",
            exc_info=True,
        )
        raise

def call_openai_responses(payload):
    """Calls the OpenAI Responses API and returns the response data."""
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
            "https://api.openai.com/v1/responses",
            headers=headers,
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        response_body = ""
        if getattr(e, "response", None) is not None:
            try:
                response_body = e.response.text[:2000]
            except Exception:
                response_body = "<unavailable>"
        current_app.logger.error(
            f"Error in OpenAI Responses API call: {e}; response_body={response_body}",
            exc_info=True,
        )
        raise

def extract_response_text(response_data):
    """Extracts text content from a Responses API result."""
    if not response_data:
        return ""
    if isinstance(response_data, dict) and response_data.get("output_text"):
        output_text = response_data.get("output_text", "")
        if isinstance(output_text, str):
            return output_text
        if isinstance(output_text, list):
            return "\n".join(str(part) for part in output_text if part)
    output = response_data.get("output", []) if isinstance(response_data, dict) else []
    for item in output:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text:
                return text
            if isinstance(text, dict):
                value = text.get("value") or text.get("text")
                if isinstance(value, str) and value:
                    return value
            if content.get("type") in {"output_text", "text"}:
                value = content.get("value")
                if isinstance(value, str) and value:
                    return value
    return ""

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

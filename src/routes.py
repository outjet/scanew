# routes.py

from flask import (
    Blueprint, render_template, request, jsonify,
    current_app, abort, Response, stream_with_context, url_for,
    send_from_directory
)
from flask_login import login_required, current_user
dispatch_bp = Blueprint(
    'dispatch',
    __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/static'
)
from .models import Transcription, DailyBlotter
# TODO: The following imports are for modules that were not found in the 'dispatch' directory.
# from utils import role_required, sanitize_input, convert_to_eastern
# from tasks import generate_daily_blotter
from .openai_utils import call_openai_api, get_unit_status_from_openai
from sqlalchemy import func
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json, time
from .extensions import db
from redis import Redis
from .config import RECORDINGS_DIR

# TODO: Placeholder function because the original `convert_to_eastern` was in a missing `utils.py` file.
# This implementation does not perform the timezone conversion.
def convert_to_eastern(timestamp_str):
    try:
        return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None

@dispatch_bp.route('/', methods=['GET', 'POST'])
@login_required
# @role_required('dispatch', 'admin') # TODO: Commented out due to missing `utils.py`
def view_transcriptions():
    try:
        current_app.logger.debug("Entered the /dispatch route")

        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 100, type=int)
        search_query = request.form.get('search_query', '').strip() if request.method == 'POST' else request.args.get('search_query', '').strip()

        current_app.logger.debug(f"Page: {page}, Per Page: {per_page}, Search Query: {search_query}")

        query = Transcription.query
        if search_query:
            query = query.filter(Transcription.transcript.ilike(f"%{search_query}%"))

        total_transcriptions = query.count()
        current_app.logger.debug(f"Total transcriptions: {total_transcriptions}")

        pagination = query.order_by(func.datetime(Transcription.timestamp).desc()).paginate(page=page, per_page=per_page, error_out=False)
        transcriptions = pagination.items
        total_pages = pagination.pages

        current_app.logger.debug(f"Total Pages: {total_pages}")

        formatted_transcriptions = []
        for transcription in transcriptions:
            timestamp_str = transcription.timestamp  # String from DB
            timestamp = convert_to_eastern(timestamp_str)

            formatted_transcriptions.append({
                'id': transcription.id,
                'timestamp': timestamp.isoformat() if timestamp else timestamp_str,
                'formatted_timestamp': timestamp.strftime('%a %d-%b %H:%M:%S') if timestamp else timestamp_str,
                'transcript': transcription.transcript,
                'wav_filename': transcription.wav_filename
            })

        current_app.logger.debug("Successfully formatted transcriptions")

        # Calculate pagination range
        page_range_start = max(1, page - 2)
        page_range_end = min(total_pages, page + 2) + 1

        return render_template('dispatch.html',
                               transcriptions=formatted_transcriptions,
                               page=page,
                               total_pages=total_pages,
                               per_page=per_page,
                               search_query=search_query,
                               page_range_start=page_range_start,
                               page_range_end=page_range_end,
                               stream_url=url_for('dispatch.stream'))

    except Exception as e:
        current_app.logger.error(f"Error fetching transcriptions: {e}", exc_info=True)
        abort(500, description="An internal error occurred.")

@dispatch_bp.route('/fetch_new_transcriptions')
@login_required
# @role_required('dispatch', 'user') # TODO: Commented out due to missing `utils.py`
def fetch_new_transcriptions():
    try:
        last_timestamp = request.args.get('last_timestamp')
        query = Transcription.query

        if last_timestamp:
            try:
                # Ensure last_timestamp is a string in ISO format
                # No need to convert to datetime since both are strings
                query = query.filter(Transcription.timestamp > last_timestamp)
            except ValueError as e:
                current_app.logger.error(f"Invalid timestamp format: {e}")
                abort(400, description="Invalid timestamp format.")

        new_transcriptions = query.order_by(Transcription.timestamp.desc()).limit(10).all()

        formatted_transcriptions = []
        for transcription in new_transcriptions:
            timestamp = convert_to_eastern(transcription.timestamp)
            formatted_transcriptions.append({
                'id': transcription.id,
                'timestamp': timestamp.isoformat() if timestamp else transcription.timestamp,
                'formatted_timestamp': timestamp.strftime('%a %d-%b %H:%M:%S') if timestamp else transcription.timestamp,
                'transcript': transcription.transcript,
                'wav_filename': transcription.wav_filename
            })

        return jsonify(formatted_transcriptions)
    except Exception as e:
        current_app.logger.error(f"Error in fetch_new_transcriptions: {e}", exc_info=True)
        abort(500, description="An internal error occurred.")
        
@dispatch_bp.route('/blotter')
@login_required
# @role_required('admin','user')
def blotter():
    try:
        hours = int(request.args.get('hours', 2))
        start_time = datetime.utcnow() - timedelta(hours=hours)
        start_time_iso = start_time.isoformat()

        transcripts = Transcription.query.filter(
            Transcription.timestamp > start_time_iso,
            func.length(Transcription.transcript) > 25
        ).order_by(Transcription.timestamp.asc()).all()

        combined_text = "\n".join(
            f"[{t.timestamp}] {t.transcript}" for t in transcripts
        )

        blotter_file_path = current_app.config.get('BLOTTER_FILE_PATH','blotter.txt')
        with open(blotter_file_path,'r') as f:
            file_prompt = f.read().strip()

        blotter_prompt = (
            f"The following are dispatch transcripts from the last {hours} hours. "
            + file_prompt
        )

        payload = {
            "model": "gpt-4.1-nano-2025-04-14",
            "messages": [
                {"role":"system", "content":blotter_prompt},
                {"role":"user",   "content":combined_text}
            ]
        }

        current_app.logger.info(
            f"[BLOTTER] Sending {len(combined_text)} chars "
            f"from {len(transcripts)} transcripts"
        )
        current_app.logger.debug(
            f"[BLOTTER] Prompt payload: {json.dumps(payload)[:2000]}..."
        )

        response_data = call_openai_api(payload)
        summary_text = response_data['choices'][0]['message']['content'].strip()

        # strip code fences if any
        if summary_text.startswith('```json'):
            summary_text = summary_text.replace('```json','').replace('```','').strip()

        try:
            summary_data = json.loads(summary_text)
            top_events = summary_data.get('events', [])
            if not top_events:
                top_events = [{
                    "time":"N/A",
                    "nature":"Summary",
                    "summary":"No significant events to report or unexpected data format.",
                    "unitsDispatched":[]
                }]
        except json.JSONDecodeError as e:
            current_app.logger.error(f"Failed to parse JSON: {e}")
            top_events = [{
                "time":"N/A",
                "nature":"Summary",
                "summary":"No significant events to report or unexpected data format.",
                "unitsDispatched":[]
            }]

        # return rendered HTML fragment
        return render_template('blotter_fragment.html', events=top_events)

    except Exception as e:
        current_app.logger.error(f"Error in blotter: {e}", exc_info=True)
        abort(500, description="An internal error occurred.")

@dispatch_bp.route('/daily_blotter')
@dispatch_bp.route('/daily_blotter/<date>')
@login_required
# @role_required('admin') # TODO: Commented out due to missing `utils.py`
def daily_blotter(date=None):
    try:
        if date is None:
            # Get the most recent blotter
            blotter_entry = DailyBlotter.query.order_by(DailyBlotter.date.desc()).first()
        else:
            blotter_entry = DailyBlotter.query.filter_by(date=date).first()

        if blotter_entry:
            date = blotter_entry.date.strftime('%Y-%m-%d')
            events = json.loads(blotter_entry.content)
        else:
            date = datetime.utcnow().date().isoformat()
            events = []

        # Get all dates for which blotters exist
        dates_with_data = [entry.date.strftime('%Y-%m-%d') for entry in DailyBlotter.query.order_by(DailyBlotter.date.desc()).all()]

        return render_template('daily_blotter.html', date=date, events=events, dates_with_data=dates_with_data)
    except Exception as e:
        current_app.logger.error(f"Error in daily_blotter route: {e}", exc_info=True)
        abort(500, description="An internal error occurred.")

@dispatch_bp.route('/unit_locations')
@login_required
# @role_required('dispatch', 'admin') # TODO: Commented out due to missing `utils.py`
def unit_locations():
    try:
        # Get the current time in US Eastern
        eastern = ZoneInfo("US/Eastern")
        one_hour_ago = datetime.now(eastern) - timedelta(hours=1)
        one_hour_ago_iso = one_hour_ago.isoformat()
        current_app.logger.debug(f"Timestamp filter (one hour ago): {one_hour_ago_iso}")

        transcripts = Transcription.query.filter(
            Transcription.timestamp > one_hour_ago_iso
        ).order_by(Transcription.timestamp.asc()).all()

        # Log the retrieved transcripts
        current_app.logger.debug(f"Retrieved {len(transcripts)} transcripts: {transcripts}")

        combined_text = "\n".join([f"[{t.timestamp}] {t.transcript}" for t in transcripts])
        current_app.logger.debug(f"Combined text for API call: {combined_text}")

        unit_data = get_unit_status_from_openai(combined_text)

        if "error" in unit_data:
            current_app.logger.error(f"Error in get_unit_status_from_openai: {unit_data['error']}")
            abort(500, description=unit_data['error'])
        else:
            current_app.logger.debug(f"Successfully processed unit locations. Found {len(unit_data)} units.")
            return jsonify(unit_data)
    except Exception as e:
        current_app.logger.error(f"Error in unit_locations: {e}", exc_info=True)
        abort(500, description="An internal error occurred.")

@dispatch_bp.route('/transcription', methods=['POST'])
def add_transcription():
    try:
        data = request.json
        timestamp_str = data.get('timestamp')
        wav_filename = data.get('wav_filename')
        transcript = data.get('transcript')
        url = data.get('url')
        text = data.get('text')

        # Validate the incoming data
        if not all([timestamp_str, transcript or text]):
            return jsonify({'error': 'Missing data'}), 400

        transcript = transcript or text
        if len(transcript) > 10000:  # Adjust this limit as needed
            return jsonify({'error': 'Text too long'}), 400

        # Convert the timestamp to US Eastern time
        timestamp_dt = convert_to_eastern(timestamp_str)
        if not timestamp_dt:
            return jsonify({'error': 'Invalid timestamp format'}), 400

        # Create a new Transcription object and add it to the session
        if not wav_filename and url:
            wav_filename = url.split('/')[-1]

        new_transcription = Transcription(
            timestamp=timestamp_dt.isoformat(),  # Store as ISO string
            wav_filename=wav_filename,
            transcript=transcript
        )
        db.session.add(new_transcription)
        db.session.commit()

        # Format the transcription to send to Redis
        formatted_transcription = {
            'id': new_transcription.id,
            'timestamp': timestamp_dt.isoformat(),
            'wav_filename': wav_filename,
            'transcript': transcript,
            'formatted_timestamp': timestamp_dt.strftime('%a %d-%b %H:%M:%S'),
            'text': transcript,
            'url': f"/recordings/{wav_filename}" if wav_filename else None
        }

        # Publish the new transcription to Redis
        r = Redis.from_url(current_app.config['REDIS_URL'])
        r.publish('sse_channel', json.dumps(formatted_transcription))

        # current_app.logger.info(f"Transcription added and published to Redis: {new_transcription.timestamp}, {wav_filename}")
        return jsonify({'message': 'Transcription added successfully'}), 201

    except Exception as e:
        current_app.logger.error(f"Error in add_transcription: {str(e)}", exc_info=True)
        db.session.rollback()  # Roll back any changes on error
        return jsonify({'error': 'Unexpected error'}), 500

@dispatch_bp.route('/streamers')
def stream():
    try:
        return Response(
            stream_with_context(event_stream()),
            mimetype='text/event-stream',
            content_type='text/event-stream'
        )
    except Exception as e:
        current_app.logger.error(f"Error in stream route: {str(e)}")
        return "Error", 500

def event_stream():
    try:
        redis_client = Redis.from_url(current_app.config['REDIS_URL'])
        pubsub = redis_client.pubsub()
        pubsub.subscribe('sse_channel')
        last_keepalive = time.time()
        keepalive_interval = 15  # seconds

        for message in pubsub.listen():
            if message['type'] == 'message':
                yield f"data: {message['data'].decode('utf-8')}\n\n"

            # Send a comment every 15 seconds to keep the connection alive
            if time.time() - last_keepalive > keepalive_interval:
                yield ": keep-alive\n\n"
                last_keepalive = time.time()
    except Exception as e:
        current_app.logger.error(f"Error in event stream: {e}", exc_info=True)
        # Optionally, yield an error message or close the stream
        yield "data: {\"error\": \"Internal Server Error\"}\n\n"


@dispatch_bp.route('/recordings/<path:filename>')
def recordings(filename):
    try:
        return send_from_directory(str(RECORDINGS_DIR), filename, as_attachment=False)
    except FileNotFoundError:
        abort(404)

@dispatch_bp.route('/transcription_context/<int:transcription_id>')
@login_required
# @role_required('dispatch', 'admin') # TODO: Commented out due to missing `utils.py`
def transcription_context(transcription_id):
    try:
        # Get the target transcription
        target_transcription = Transcription.query.get(transcription_id)
        if not target_transcription:
            current_app.logger.error(f"Transcription not found: {transcription_id}")
            abort(404, description="Transcription not found")

        # Use efficient SQL queries to get context transcriptions
        # Get 29 transcriptions before (earlier in time)
        earlier_transcriptions = Transcription.query.filter(
            Transcription.timestamp < target_transcription.timestamp
        ).order_by(Transcription.timestamp.desc()).limit(29).all()
        
        # Get 70 transcriptions after (later in time)
        later_transcriptions = Transcription.query.filter(
            Transcription.timestamp > target_transcription.timestamp
        ).order_by(Transcription.timestamp.asc()).limit(70).all()
        
        # Combine and sort all transcriptions chronologically
        all_context_transcriptions = list(reversed(earlier_transcriptions)) + [target_transcription] + later_transcriptions
        
        # Format the transcriptions
        formatted_transcriptions = []
        for transcription in all_context_transcriptions:
            timestamp_str = transcription.timestamp
            timestamp = convert_to_eastern(timestamp_str)

            formatted_transcriptions.append({
                'id': transcription.id,
                'timestamp': timestamp.isoformat() if timestamp else timestamp_str,
                'formatted_timestamp': timestamp.strftime('%a %d-%b %H:%M:%S') if timestamp else timestamp_str,
                'transcript': transcription.transcript,
                'wav_filename': transcription.wav_filename,
                'is_target': transcription.id == transcription_id
            })

        current_app.logger.info(f"Context loaded: {len(earlier_transcriptions)} earlier, 1 target, {len(later_transcriptions)} later transcriptions")

        return render_template('context.html',
                               transcriptions=formatted_transcriptions,
                               target_id=transcription_id,
                               target_text=target_transcription.transcript)

    except Exception as e:
        current_app.logger.error(f"Error in transcription_context: {e}", exc_info=True)
        abort(500, description="An internal error occurred.")

@dispatch_bp.route('/edit_transcription', methods=['POST'])
@login_required
# @role_required('admin') # TODO: Commented out due to missing `utils.py`
def edit_transcription():
    try:
        data = request.get_json()
        current_app.logger.debug(f"Received edit request: {data}")
        
        transcription_id = data.get('id')
        new_transcript = data.get('transcript')

        if not transcription_id or new_transcript is None:
            current_app.logger.error("Missing transcription ID or new transcript")
            return jsonify({'success': False, 'error': 'Missing transcription ID or new transcript'}), 400

        # Use SQLAlchemy to query the Transcription
        transcription = Transcription.query.get(transcription_id)
        if not transcription:
            current_app.logger.error(f"Transcription not found: {transcription_id}")
            return jsonify({'success': False, 'error': 'Transcription not found'}), 404

        # Update the transcription text
        transcription.transcript = new_transcript
        db.session.commit()

        current_app.logger.info(f"Transcription {transcription_id} updated successfully")
        return jsonify({'success': True})
    except Exception as e:
        current_app.logger.error(f"Error in edit_transcription: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal Server Error'}), 500

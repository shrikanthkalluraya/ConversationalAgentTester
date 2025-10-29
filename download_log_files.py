from google.cloud import logging
from datetime import datetime, timedelta
import pytz
import json
import os
from collections import defaultdict

def get_session_logs_for_today(project_id, location, agent_id, output_dir="session_logs"):
    """
    Get all session logs for a Dialogflow CX agent for today.
    Creates a separate file for each session with complete conversation history.
    
    Args:
        project_id: GCP project ID
        location: Agent location (e.g., 'us-central1')
        agent_id: Dialogflow CX agent ID
        output_dir: Directory to save session log files
    """
    # Initialize logging client
    logging_client = logging.Client(project=project_id)
    
    # Calculate today's date range (midnight to now in UTC)
    # Adjust timezone as needed
    now = datetime.now(pytz.UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    print(f"Fetching session logs for agent: {agent_id}")
    print(f"Location: {location}")
    print(f"Date range: {today_start} to {now}")
    print(f"Output directory: {output_dir}")
    print("-" * 80)
    
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    # Build the filter for Dialogflow CX logs
    # This captures all conversation interactions
    filter_str = f'''
    resource.type="dialogflow.googleapis.com/Agent"
    resource.labels.location="{location}"
    timestamp >= "{today_start.isoformat()}"
    (jsonPayload.queryResult:* OR jsonPayload.responseMessages:*)
    '''
    
    # If you want to filter by specific agent_id, add this line to filter:
    # resource.labels.agent_id="{agent_id}"
    
    # Dictionary to store logs grouped by session
    sessions_data = defaultdict(list)
    session_metadata = {}
    
    print("Querying Cloud Logging for session data...")
    
    try:
        # List log entries (increase max_results as needed)
        entry_count = 0
        for entry in logging_client.list_entries(
            filter_=filter_str, 
            page_size=1000,
            max_results=10000  # Adjust based on your needs
        ):
            entry_count += 1
            
            # Extract session information from the log entry
            session_id = None
            session_path = None
            
            # Try to get session from payload
            if hasattr(entry, 'payload') and isinstance(entry.payload, dict):
                session_path = entry.payload.get('session', '')
                
                if session_path:
                    # Extract session ID from full path
                    # Format: projects/{project}/locations/{location}/agents/{agent}/sessions/{session_id}
                    parts = session_path.split('/')
                    if 'sessions' in parts:
                        idx = parts.index('sessions')
                        if idx + 1 < len(parts):
                            session_id = parts[idx + 1].split(':')[0]  # Remove any additional identifiers
            
            # Also check resource labels
            if not session_id and hasattr(entry, 'resource') and entry.resource.labels:
                session_id = entry.resource.labels.get('session_id')
            
            if session_id:
                # Parse the log entry
                log_data = {
                    'timestamp': entry.timestamp.isoformat() if entry.timestamp else None,
                    'severity': entry.severity,
                    'session_path': session_path,
                }
                
                # Extract relevant payload data
                if hasattr(entry, 'payload') and isinstance(entry.payload, dict):
                    payload = entry.payload
                    
                    # Extract query and response information
                    log_data['query_text'] = payload.get('queryResult', {}).get('text')
                    log_data['language_code'] = payload.get('queryResult', {}).get('languageCode')
                    
                    # Extract intent information
                    intent = payload.get('queryResult', {}).get('intent', {})
                    log_data['intent_name'] = intent.get('displayName')
                    log_data['intent_confidence'] = payload.get('queryResult', {}).get('intentDetectionConfidence')
                    
                    # Extract parameters
                    log_data['parameters'] = payload.get('queryResult', {}).get('parameters')
                    
                    # Extract response messages
                    response_messages = payload.get('queryResult', {}).get('responseMessages', [])
                    log_data['response_messages'] = []
                    
                    for msg in response_messages:
                        if 'text' in msg:
                            log_data['response_messages'].append({
                                'type': 'text',
                                'text': msg['text'].get('text', [])
                            })
                        elif 'payload' in msg:
                            log_data['response_messages'].append({
                                'type': 'payload',
                                'payload': msg['payload']
                            })
                    
                    # Extract current page
                    log_data['current_page'] = payload.get('queryResult', {}).get('currentPage', {}).get('displayName')
                    
                    # Extract match type
                    log_data['match_type'] = payload.get('queryResult', {}).get('match', {}).get('matchType')
                    
                    # Store full payload for reference
                    log_data['full_payload'] = payload
                
                # Add to sessions data
                sessions_data[session_id].append(log_data)
                
                # Track session metadata (first and last interaction times)
                if session_id not in session_metadata:
                    session_metadata[session_id] = {
                        'session_id': session_id,
                        'session_path': session_path,
                        'start_time': entry.timestamp,
                        'end_time': entry.timestamp,
                        'interaction_count': 0
                    }
                else:
                    # Update start time if this entry is earlier
                    if entry.timestamp < session_metadata[session_id]['start_time']:
                        session_metadata[session_id]['start_time'] = entry.timestamp
                    # Update end time if this entry is later
                    if entry.timestamp > session_metadata[session_id]['end_time']:
                        session_metadata[session_id]['end_time'] = entry.timestamp
                
                session_metadata[session_id]['interaction_count'] += 1
            
            if entry_count % 100 == 0:
                print(f"Processed {entry_count} log entries...")
        
        print(f"\nTotal log entries processed: {entry_count}")
        print(f"Unique sessions found: {len(sessions_data)}")
        print("-" * 80)
        
        # Save each session to a separate file
        for session_id, logs in sessions_data.items():
            # Sort logs by timestamp
            sorted_logs = sorted(logs, key=lambda x: x['timestamp'] if x['timestamp'] else '')
            
            metadata = session_metadata[session_id]
            
            # Calculate session duration
            duration = (metadata['end_time'] - metadata['start_time']).total_seconds()
            
            # Create session summary
            session_summary = {
                'session_metadata': {
                    'session_id': session_id,
                    'session_path': metadata['session_path'],
                    'start_time': metadata['start_time'].isoformat(),
                    'end_time': metadata['end_time'].isoformat(),
                    'duration_seconds': duration,
                    'total_interactions': metadata['interaction_count'],
                    'agent_id': agent_id,
                    'location': location
                },
                'conversation_logs': sorted_logs
            }
            
            # Create filename with timestamp
            start_time_str = metadata['start_time'].strftime('%Y%m%d_%H%M%S')
            filename = f"{output_dir}/session_{session_id}_{start_time_str}.json"
            
            # Save to file
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(session_summary, f, indent=2, ensure_ascii=False, default=str)
            
            print(f"✓ Saved: {filename}")
            print(f"  Session ID: {session_id}")
            print(f"  Start: {metadata['start_time'].strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  End: {metadata['end_time'].strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  Duration: {duration:.1f}s")
            print(f"  Interactions: {metadata['interaction_count']}")
            print()
        
        # Create summary file
        summary_filename = f"{output_dir}/sessions_summary.json"
        summary_data = {
            'query_info': {
                'project_id': project_id,
                'agent_id': agent_id,
                'location': location,
                'query_date': today_start.isoformat(),
                'query_executed_at': now.isoformat()
            },
            'summary': {
                'total_sessions': len(sessions_data),
                'total_log_entries': entry_count,
                'sessions': [
                    {
                        'session_id': sid,
                        'start_time': meta['start_time'].isoformat(),
                        'end_time': meta['end_time'].isoformat(),
                        'duration_seconds': (meta['end_time'] - meta['start_time']).total_seconds(),
                        'interactions': meta['interaction_count']
                    }
                    for sid, meta in session_metadata.items()
                ]
            }
        }
        
        with open(summary_filename, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, indent=2, ensure_ascii=False, default=str)
        
        print("=" * 80)
        print(f"✓ Summary saved: {summary_filename}")
        print(f"✓ Total sessions exported: {len(sessions_data)}")
        print(f"✓ All files saved to: {output_dir}/")
        
        return sessions_data, session_metadata
    
    except Exception as e:
        print(f"Error querying logs: {e}")
        import traceback
        traceback.print_exc()
        return {}, {}


def filter_sessions_by_criteria(output_dir, min_duration=None, max_duration=None, 
                                min_interactions=None, session_ids=None):
    """
    Filter and copy session files based on criteria.
    
    Args:
        output_dir: Directory containing session files
        min_duration: Minimum session duration in seconds
        max_duration: Maximum session duration in seconds
        min_interactions: Minimum number of interactions
        session_ids: List of specific session IDs to extract
    """
    filtered_dir = f"{output_dir}/filtered"
    os.makedirs(filtered_dir, exist_ok=True)
    
    import shutil
    
    # Read all session files
    for filename in os.listdir(output_dir):
        if filename.startswith('session_') and filename.endswith('.json'):
            filepath = os.path.join(output_dir, filename)
            
            with open(filepath, 'r') as f:
                session_data = json.load(f)
            
            metadata = session_data['session_metadata']
            
            # Apply filters
            include = True
            
            if session_ids and metadata['session_id'] not in session_ids:
                include = False
            
            if min_duration and metadata['duration_seconds'] < min_duration:
                include = False
            
            if max_duration and metadata['duration_seconds'] > max_duration:
                include = False
            
            if min_interactions and metadata['total_interactions'] < min_interactions:
                include = False
            
            if include:
                shutil.copy2(filepath, filtered_dir)
                print(f"Copied: {filename}")
    
    print(f"\nFiltered sessions saved to: {filtered_dir}/")


if __name__ == "__main__":
    # Configuration
    PROJECT_ID = "your-project-id"
    LOCATION = "us-central1"  # e.g., us-central1, europe-west1, global
    AGENT_ID = "your-agent-id"
    OUTPUT_DIR = "session_logs"
    
    # Get all session logs for today
    print("Starting Dialogflow CX Session Log Export")
    print("=" * 80)
    
    sessions_data, session_metadata = get_session_logs_for_today(
        project_id=PROJECT_ID,
        location=LOCATION,
        agent_id=AGENT_ID,
        output_dir=OUTPUT_DIR
    )
    
    # Optional: Filter sessions by criteria
    # Uncomment and customize as needed
    # print("\nFiltering sessions...")
    # filter_sessions_by_criteria(
    #     output_dir=OUTPUT_DIR,
    #     min_duration=10,  # At least 10 seconds
    #     min_interactions=3  # At least 3 interactions
    # )
    
    # Optional: Filter by specific session IDs
    # specific_sessions = ['session-id-1', 'session-id-2']
    # filter_sessions_by_criteria(
    #     output_dir=OUTPUT_DIR,
    #     session_ids=specific_sessions
    # )

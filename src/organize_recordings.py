import os
import datetime
from pathlib import Path

def organize_recordings_by_week():
    """
    Organizes files in the 'recordings' directory into subfolders by week.
    """
    base_dir = Path(__file__).resolve().parent.parent
    recordings_dir = base_dir / "recordings"

    if not recordings_dir.is_dir():
        print(f"Directory not found: {recordings_dir}")
        return

    print(f"Scanning {recordings_dir}...")

    # Use list(recordings_dir.iterdir()) to avoid issues with iterator invalidation
    for item in list(recordings_dir.iterdir()):
        if item.is_file():
            try:
                mod_time = item.stat().st_mtime
                mod_date = datetime.datetime.fromtimestamp(mod_time)
                
                # Format: YYYY-Week-WW
                week_folder_name = mod_date.strftime("%Y-Week-%U")
                
                week_dir = recordings_dir / week_folder_name
                week_dir.mkdir(exist_ok=True)
                
                new_path = week_dir / item.name
                item.rename(new_path)
                print(f"Moved {item.name} to {week_dir.name}")

            except Exception as e:
                print(f"Could not process {item.name}: {e}")

if __name__ == "__main__":
    organize_recordings_by_week()

import os

output_file = "all_python_code.txt"
excluded_dirs = {".venv", "__pycache__", ".git", ".mypy_cache"}
this_script = os.path.basename(__file__)

with open(output_file, "w", encoding="utf-8") as outfile:
    for foldername, subdirs, filenames in os.walk("."):
        if any(part in excluded_dirs for part in foldername.split(os.sep)):
            continue
        for filename in filenames:
            if filename.endswith(".py") and filename != this_script:
                filepath = os.path.join(foldername, filename)
                outfile.write(f"\n\n# --- {filepath} ---\n\n")
                with open(filepath, "r", encoding="utf-8", errors="ignore") as infile:
                    outfile.write(infile.read())

print(f"Done! Code exported to {output_file}")
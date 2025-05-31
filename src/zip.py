import os

# Directory to scan
project_dir = 'src'

# Output file
output_file = 'py_files_list.txt'

# Collect all files based on criteria
files_to_include = []
for root, dirs, files in os.walk(project_dir):
    for file in files:
        if (file.endswith('.py') and file != 'zip.py') or (file == 'config.ini') or (file.endswith('.txt') and file != 'py_files_list.txt'):
            full_path = os.path.join(root, file)
            files_to_include.append(full_path)

# Write to the output file
with open(output_file, 'w') as f:
    for file_path in files_to_include:
        f.write(f"File: {file_path}\n")
        with open(file_path, 'r') as file_content:
            f.write(file_content.read())
        f.write('\n' + '-'*40 + '\n')  # Separator between files

print(f"List of selected files and their contents written to {output_file}")
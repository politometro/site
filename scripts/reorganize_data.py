import os
import shutil
import re

workspace = "."
data_dir = os.path.join(workspace, "data")

# Create data directory if it doesn't exist
os.makedirs(data_dir, exist_ok=True)

# List of folders to move directly to data/
folders_to_move = [
    "Açores", "Declaração de Princípios", "Europeias", "IL", "Legislativas", 
    "Madeira", "Orçamentos de Estado"
]

for folder in folders_to_move:
    src_path = os.path.join(workspace, folder)
    dst_path = os.path.join(data_dir, folder)
    if os.path.exists(src_path) and src_path != dst_path:
        print(f"Moving folder {folder} to data/{folder}...")
        try:
            shutil.move(src_path, dst_path)
        except Exception as e:
            print(f"Error moving {folder}: {e}")

# Files in root to move to data/
files_to_move = [
    "70-MEDIDAS-PARA-REERGUER-PORTUGAL-CHEGA.pdf",
    "Constituição.pdf"
]

for file in files_to_move:
    src_path = os.path.join(workspace, file)
    dst_path = os.path.join(data_dir, file)
    if os.path.exists(src_path):
        print(f"Moving file {file} to data/{file}...")
        try:
            shutil.move(src_path, dst_path)
        except Exception as e:
            print(f"Error moving file {file}: {e}")

# Reorganize pending files
pending_dir = os.path.join(workspace, "Programas políticos pendentes")
if os.path.exists(pending_dir):
    print("Reorganizing files from 'Programas políticos pendentes'...")
    pending_files = [f for f in os.listdir(pending_dir) if f.endswith(".pdf")]
    
    for filename in pending_files:
        src_file = os.path.join(pending_dir, filename)
        
        # Determine year
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", filename)
        year = year_match.group(1) if year_match else None
        
        if not year:
            print(f"  Warning: Could not determine year for {filename}, leaving in pending folder.")
            continue
            
        # Determine category and destination
        lower_name = filename.lower()
        if "açores" in lower_name:
            dest_subdir = os.path.join(data_dir, "Açores", f"Açores {year}")
        elif "madeira" in lower_name:
            dest_subdir = os.path.join(data_dir, "Madeira", f"Madeira {year}")
        elif "europeias" in lower_name:
            dest_subdir = os.path.join(data_dir, "Europeias", f"Europeias {year}")
        elif "princípios" in lower_name:
            dest_subdir = os.path.join(data_dir, "Declaração de Princípios")
        else:
            dest_subdir = os.path.join(data_dir, "Legislativas", f"Legislativas {year}")
            
        os.makedirs(dest_subdir, exist_ok=True)
        dst_file = os.path.join(dest_subdir, filename)
        
        print(f"  Moving {filename} -> {os.path.relpath(dst_file, workspace)}")
        try:
            shutil.move(src_file, dst_file)
        except Exception as e:
            print(f"  Error moving {filename}: {e}")
            
    # Clean up empty pending dir
    try:
        if not os.listdir(pending_dir):
            os.rmdir(pending_dir)
            print("Removed empty 'Programas políticos pendentes' folder.")
    except Exception as e:
        print(f"Error removing pending folder: {e}")

print("Data reorganization complete!")

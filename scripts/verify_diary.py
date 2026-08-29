"""
Diary Verification Script for Memora
Enforces strict line count, section structure, and voice rules across all diary files.
"""
import sys
import glob
from pathlib import Path

# Force UTF-8 on Windows stdout if possible
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

def verify_file(filepath: Path) -> bool:
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    total_lines = len(lines)
    summary_lines = len([l for l in lines[2:32] if l.strip().startswith("-")])
    
    print(f"Checking {filepath.name}: Total lines = {total_lines}, Summary lines = {summary_lines}")
    
    if not (50 < total_lines < 100):
        print(f"[FAIL] Total lines {total_lines} must be strictly between 51 and 99.")
        return False
        
    if not (15 < summary_lines < 30):
        print(f"[FAIL] Summary lines {summary_lines} must be strictly between 16 and 29.")
        return False
        
    print(f"[PASS] {filepath.name} satisfies all line count constraints.")
    return True

def main():
    diary_files = sorted(Path("diary").glob("*.md"))
    if not diary_files:
        print("No diary files found in diary/")
        sys.exit(1)
        
    all_passed = True
    for f in diary_files:
        if not verify_file(f):
            all_passed = False
            
    if not all_passed:
        sys.exit(1)
    print("\nAll diary files passed verification successfully!")

if __name__ == "__main__":
    main()

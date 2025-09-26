import os
import shutil

def clean_directories(base_path="results"):
    """
    Removes all files and subdirectories from specified target directories
    within the base results path.
    """
    dirs_to_clean = [
        "compounded",
        "formulations",
        "plots",
        "summaries"
    ]

    print("This script will delete all contents from the following directories:")
    for d in dirs_to_clean:
        print(f"- {os.path.join(base_path, d)}")

    confirm = input("\nAre you sure you want to proceed? (y/n): ")
    if confirm.lower() != 'y':
        print("Operation cancelled.")
        return

    print("\nCleaning directories...")
    for dir_name in dirs_to_clean:
        target_dir = os.path.join(base_path, dir_name)
        if not os.path.isdir(target_dir):
            print(f"Skipping '{target_dir}' (does not exist or not a directory).")
            continue

        for item_name in os.listdir(target_dir):
            item_path = os.path.join(target_dir, item_name)
            try:
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.unlink(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                print(f"  - Removed {item_path}")
            except Exception as e:
                print(f"Failed to delete {item_path}. Reason: {e}")
    print("\nCleanup complete.")

if __name__ == "__main__":
    clean_directories()
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_DIR = Path(__file__).parent
CTFD_ADMIN_TOKEN = os.getenv('CTFD_ADMIN_TOKEN')
CTFD_HOST = os.getenv('CTFD_HOST', 'http://localhost:8000')
CTFD_STORAGE = Path(os.getenv('CTFD_STORAGE', REPO_DIR / 'ctfd_storage.json'))

"""
init_db.py — Run this once to create all database tables.
Usage: python scripts/init_db.py
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.database import init_db

if __name__ == "__main__":
    print("Initialising database...")
    init_db()
    print("All tables created successfully.")
    print("Database file: wassce_mentor.db")

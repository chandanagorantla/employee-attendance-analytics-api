
import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

uri = os.getenv("MONGO_URI")
db_name = os.getenv("MONGO_DB", "attendance_db")

if not uri:
    print("ERROR: MONGO_URI is missing. Check your .env file.")
else:
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=10000)
        client.admin.command("ping")
        print("SUCCESS: Connected to MongoDB Atlas!")
        print("Database configured:", db_name)
        client.close()
    except Exception as exc:
        print("CONNECTION FAILED:", type(exc).__name__)
        print(str(exc))

import os
import certifi
import asyncio
import json
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "edumanage")
USE_MOCK_DB = os.getenv("USE_MOCK_DB", "true").lower() in {"1", "true", "yes"}
MOCK_DB_FILE = Path(__file__).with_name("mock_db.json")
FALLBACK_MOCK_DB_FILE = Path(os.getenv("MOCK_DB_FALLBACK_FILE", r"C:\tmp\edumanage_mock_db.json"))

class MockCollection:
    def __init__(self, name, data=None, on_change=None):
        self.name = name
        self.data = data or []
        self.on_change = on_change

    async def find_one(self, filter, projection=None):
        for item in self.data:
            if self._matches(item, filter):
                return item
        return None

    def find(self, filter=None, projection=None):
        if not filter:
            return MockCursor(list(self.data))
        return MockCursor([item for item in self.data if self._matches(item, filter)])

    async def insert_one(self, document):
        from bson import ObjectId
        if "_id" not in document:
            document["_id"] = ObjectId()
        self.data.append(document)
        self._changed()
        class Result:
            def __init__(self, id): self.inserted_id = id
        return Result(document["_id"])

    async def count_documents(self, filter):
        if not filter:
            return len(self.data)
        return len([item for item in self.data if self._matches(item, filter)])
    
    async def update_one(self, filter, update):
        class Result:
            def __init__(self, count): self.modified_count = count
        for item in self.data:
            if self._matches(item, filter):
                if "$set" in update:
                    item.update(update["$set"])
                self._changed()
                return Result(1)
        return Result(0)
    
    async def delete_one(self, filter):
        class Result:
            def __init__(self, count): self.deleted_count = count
        for index, item in enumerate(self.data):
            if self._matches(item, filter):
                self.data.pop(index)
                self._changed()
                return Result(1)
        return Result(0)

    async def aggregate(self, pipeline):
        return MockCursor([])

    def _matches(self, item, filter):
        for key, value in (filter or {}).items():
            item_value = item.get(key)
            if str(item_value) != str(value):
                return False
        return True

    def _changed(self):
        if self.on_change:
            try:
                self.on_change()
            except OSError as exc:
                print(f"Warning: mock database changes could not be saved: {exc}")

class MockCursor:
    def __init__(self, data):
        self.data = data
        self.index = 0
    
    def sort(self, key, direction=1): return self
    def limit(self, n): return self
    
    def __aiter__(self): return self
    async def __anext__(self):
        if self.index >= len(self.data):
            raise StopAsyncIteration
        val = self.data[self.index]
        self.index += 1
        return val
    
    async def to_list(self, length):
        return self.data[:length]

class MockDB:
    def __init__(self):
        self.collections = {}
        saved_data = self._load()
        if saved_data:
            for name, items in saved_data.items():
                self.collections[name] = MockCollection(name, items, self._save)
            return

        self.collections["users"] = MockCollection("users", [
            {
                "name": "System Admin",
                "email": "admin@college.com",
                "password": "$2b$12$7XxIic6ysS.AdWpo/hStYObsNAFv3DczX8jzG8fPon3ASANmvlwJy", # password123
                "role": "Admin"
            },
            {
                "name": "Demo Staff",
                "email": "staff@college.com",
                "password": "$2b$12$7XxIic6ysS.AdWpo/hStYObsNAFv3DczX8jzG8fPon3ASANmvlwJy", # password123
                "role": "Staff"
            },
            {
                "name": "Demo Student",
                "email": "student@college.com",
                "password": "$2b$12$7XxIic6ysS.AdWpo/hStYObsNAFv3DczX8jzG8fPon3ASANmvlwJy", # password123
                "role": "Student"
            }
        ], self._save)
        self.collections["admissions"] = MockCollection("admissions", [], self._save)
        self.collections["notifications"] = MockCollection("notifications", [], self._save)
        self.collections["subjects"] = MockCollection("subjects", [], self._save)
        self.collections["schedules"] = MockCollection("schedules", [], self._save)
        self._save()

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = MockCollection(name, on_change=self._save)
        return self.collections[name]

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return self[name]
    
    async def list_collection_names(self):
        return list(self.collections.keys())

    def _load(self):
        for db_file in (FALLBACK_MOCK_DB_FILE, MOCK_DB_FILE):
            if not db_file.exists():
                continue
            try:
                return json.loads(db_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
        return None

    def _save(self):
        serializable = {
            name: collection.data
            for name, collection in self.collections.items()
        }
        payload = json.dumps(serializable, default=str, indent=2)
        try:
            MOCK_DB_FILE.write_text(payload, encoding="utf-8")
        except OSError:
            FALLBACK_MOCK_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
            FALLBACK_MOCK_DB_FILE.write_text(payload, encoding="utf-8")

# Real Client
real_client = AsyncIOMotorClient(
    MONGO_URI,
    tlsCAFile=certifi.where(),
    tlsAllowInvalidCertificates=True,
    tlsAllowInvalidHostnames=True,
    serverSelectionTimeoutMS=2000, # Fast timeout for fallback
)
real_db = real_client[DB_NAME]
mock_db = MockDB()

_db_mode = "real"

def get_db():
    if USE_MOCK_DB:
        return mock_db
    return real_db

def get_mock_db():
    return mock_db

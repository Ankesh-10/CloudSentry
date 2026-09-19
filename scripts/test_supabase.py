import os
import asyncio
from dotenv import load_dotenv
from supabase import create_client, Client

async def test_connection():
    load_dotenv()
    
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY")
    
    if not url or not key:
        print("Missing SUPABASE_URL or SUPABASE_ANON_KEY")
        return
        
    print(f"Connecting to Supabase at {url}...")
    try:
        supabase: Client = create_client(url, key)
        
        # Test query to system_config table
        # Since we use anon key and RLS is enabled for authenticated users, 
        # this will likely return an empty list [], but it proves the connection works.
        response = supabase.table("system_config").select("*").execute()
        
        print("Connection successful!")
        print(f"Data returned (RLS applied): {response.data}")
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_connection())

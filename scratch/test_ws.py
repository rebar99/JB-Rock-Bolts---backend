import asyncio
import websockets
import json

async def test_ws():
    # Login as deepak
    import requests
    res = requests.post("http://127.0.0.1:8000/api/users/login", json={"email": "deepak@jbrocks.com", "password": "password"})
    if res.status_code != 200:
        print("Login failed:", res.json())
        return
    token = res.json()["access_token"]
    print("Logged in, token:", token)
    
    # Connect ws
    ws_url = f"ws://127.0.0.1:8000/api/users/ws/auth?token={token}"
    async with websockets.connect(ws_url) as ws:
        print("Connected to WS!")
        
        # Now try to login AGAIN from another thread/async task
        async def second_login():
            print("Second login attempt...")
            # We use requests which is blocking, so we'll run it in thread
            res2 = requests.post("http://127.0.0.1:8000/api/users/login", json={"email": "deepak@jbrocks.com", "password": "password"})
            print("Second login response:", res2.json())
            
        asyncio.create_task(second_login())
        
        # Wait for WS message
        msg = await ws.recv()
        print("WS Message received:", msg)
        
        # Approve it
        data = json.loads(msg)
        req_id = data["request_id"]
        res3 = requests.post("http://127.0.0.1:8000/api/users/approve-login", json={"request_id": req_id, "action": "approve"}, headers={"Authorization": f"Bearer {token}"})
        print("Approve response:", res3.json())
        
        # wait a bit
        await asyncio.sleep(2)

asyncio.run(test_ws())

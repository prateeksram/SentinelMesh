import asyncio
import io
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import server as host
from photobooth import PhotoBooth


def test_jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "#3EC7F4").save(output, format="JPEG")
    return output.getvalue()


JPEG = test_jpeg()


class PhotoBoothStoreTests(unittest.TestCase):
    def test_expiration_removes_photos_and_rotates_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            booth = PhotoBooth(Path(directory), ttl_seconds=10)
            token = booth.join_token
            photo = booth.add_photo(
                JPEG,
                label="Goal reaction",
                kick=1,
                result="goal",
                score="1-0",
            )
            self.assertTrue((Path(directory) / photo.filename).exists())

            self.assertTrue(booth.cleanup(now=booth.expires_at))
            self.assertNotEqual(token, booth.join_token)
            self.assertEqual(booth.snapshot()["photoCount"], 0)
            self.assertFalse((Path(directory) / photo.filename).exists())


class PhotoBoothHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        host.booth = PhotoBooth(Path(self.temp.name), ttl_seconds=900)
        host.game = host.Game(host.Desk())
        self.client = TestClient(TestServer(host.make_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        if host.game.task and not host.game.task.done():
            host.game.task.cancel()
            await host.game.task
        await self.client.close()
        self.temp.cleanup()

    async def test_qr_upload_gallery_and_protected_download(self):
        config_response = await self.client.get("/api/booth")
        self.assertEqual(config_response.status, 200)
        config = await config_response.json()
        self.assertIn(host.booth.join_token, config["joinUrl"])
        self.assertEqual(config["captureToken"], host.booth.capture_token)

        qr_response = await self.client.get("/api/booth/qr.png")
        self.assertEqual(qr_response.status, 200)
        self.assertTrue((await qr_response.read()).startswith(b"\x89PNG"))

        denied = await self.client.post("/api/booth/photos", data=JPEG)
        self.assertEqual(denied.status, 403)

        upload = await self.client.post(
            "/api/booth/photos?kind=reaction&kick=2&result=goal&score=2-0",
            data=JPEG,
            headers={"Content-Type": "image/jpeg", "X-Capture-Token": host.booth.capture_token},
        )
        self.assertEqual(upload.status, 201)
        photo = await upload.json()

        denied_gallery = await self.client.get("/api/booth/gallery?token=wrong")
        self.assertEqual(denied_gallery.status, 403)
        gallery = await self.client.get(f"/api/booth/gallery?token={host.booth.join_token}")
        self.assertEqual((await gallery.json())["photoCount"], 1)

        denied_photo = await self.client.get(f"/api/booth/photos/{photo['id']}?token=wrong")
        self.assertEqual(denied_photo.status, 403)
        download = await self.client.get(
            f"/api/booth/photos/{photo['id']}?token={host.booth.join_token}&download=1",
        )
        self.assertEqual(download.status, 200)
        self.assertIn("attachment", download.headers["Content-Disposition"])
        self.assertEqual(await download.read(), JPEG)

    async def test_joined_phone_can_start_when_striker_controller_is_connected(self):
        phone = await self.client.ws_connect("/ws")
        guest = await self.client.ws_connect("/ws")
        await phone.send_json({"type": "hello", "client": "phone"})
        await guest.send_json({"type": "hello", "client": "guest", "token": host.booth.join_token})

        async def receive_until_ready():
            for _ in range(8):
                message = await guest.receive_json(timeout=2)
                if message.get("connected", {}).get("phone") and message.get("connected", {}).get("guest"):
                    return message
            self.fail("Phone and QR guest did not both appear in game state")

        await receive_until_ready()
        await guest.send_json({"type": "start"})
        for _ in range(8):
            state = await guest.receive_json(timeout=2)
            if state.get("phase") != "lobby":
                break
        self.assertEqual(state["phase"], "announce")
        await phone.close()
        await guest.close()


if __name__ == "__main__":
    unittest.main()

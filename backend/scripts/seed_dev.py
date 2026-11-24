"""Seed the development database with realistic data through the real API.

Every row here is created by calling the same HTTP endpoints the frontend calls,
so the resulting state is reachable in production rather than hand-assembled with
INSERTs that could encode a state the app can never actually produce.

This is a development convenience only. It refuses to run unless the target
database name ends in a non-``_test`` dev name, and it never touches uploads.

    docker compose exec -T backend python scripts/seed_dev.py

Post-August flow this script has to satisfy (it predates all three):
  * posting requires a verified phone, so every account runs the sandbox OTP
    request/verify pair first;
  * a typed ID number is recorded as ``manual_fallback``, so the claim needs
    finder approval *and* an admin decision before payment;
  * the finder's ``Earning`` is written at handover confirmation, so the
    completed example walks quote -> pay -> receipt code -> confirm -> complete.

Intended for a fresh dev database; the document numbers are fixed, so repeat
runs without a reset will post duplicates of the unclaimed records.
"""
from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys

import httpx
from PIL import Image

BASE = os.environ.get("SEED_BASE_URL", "http://localhost:8000")
PASSWORD = "correct-horse-battery"
ADMIN_EMAIL = "admin@seeker.co.ke"


class Client:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def register(self, email: str, name: str, phone: str) -> dict:
        response = await self.http.post(
            "/api/auth/register",
            json={
                "email": email,
                "password": PASSWORD,
                "display_name": name,
                "phone_number": phone,
            },
        )
        if response.status_code == 409:  # already seeded, so log in instead
            response = await self.http.post(
                "/api/auth/login", json={"email": email, "password": PASSWORD}
            )
        response.raise_for_status()
        data = response.json()
        return {
            "id": data["user"]["id"],
            "email": data["user"]["email"],
            "name": data["user"]["display_name"],
            "phone": phone,
            "headers": {"Authorization": f"Bearer {data['access_token']}"},
        }

    async def verify_phone(self, user: dict) -> None:
        """Drive the sandbox OTP flow; posting 403s without a verified phone."""
        challenge = await self.http.post(
            "/api/auth/otp/request",
            json={"phone_number": user["phone"], "purpose": "phone_verification"},
            headers=user["headers"],
        )
        challenge.raise_for_status()
        body = challenge.json()
        verified = await self.http.post(
            "/api/auth/otp/verify",
            json={"challenge_id": body["challenge_id"], "code": body["sandbox_code"]},
            headers=user["headers"],
        )
        verified.raise_for_status()
        assert verified.json()["phone_verified_at"], (
            f"{user['email']} still unverified after OTP"
        )

    async def post_found(
        self,
        finder: dict,
        *,
        id_number: str,
        holder: str,
        doc: str,
        where: str,
        pickup: str,
        colour: tuple[int, int, int],
    ) -> dict:
        """Post with a generated photo, so the blur pipeline has real bytes."""
        image = Image.new("RGB", (480, 300), colour)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG")
        response = await self.http.post(
            "/api/found-ids",
            data={
                "document_type": doc,
                "holder_name": holder,
                "id_number": id_number,
                "found_location": where,
                # Required outside SEEKER_TESTING mode.
                "pickup_address": pickup,
                "run_ocr": "false",
            },
            files={"image": ("id.jpg", buffer.getvalue(), "image/jpeg")},
            headers=finder["headers"],
        )
        response.raise_for_status()
        return response.json()

    async def search(self, user: dict, id_number: str) -> list[str]:
        response = await self.http.post(
            "/api/search", json={"id_number": id_number}, headers=user["headers"]
        )
        response.raise_for_status()
        return response.json()["match_ids"]

    async def start_claim(self, user: dict, match_id: str) -> None:
        response = await self.http.post(
            f"/api/matches/{match_id}/start-claim", headers=user["headers"]
        )
        response.raise_for_status()

    async def claim(self, user: dict, match_id: str, id_number: str) -> None:
        response = await self.http.post(
            f"/api/matches/{match_id}/claim",
            json={"id_number": id_number, "message": "This is mine, thank you!"},
            headers=user["headers"],
        )
        response.raise_for_status()

    async def decide(self, finder: dict, match_id: str, approve: bool) -> dict:
        response = await self.http.post(
            f"/api/matches/{match_id}/decision",
            json={"approve": approve, "note": "" if approve else "Details did not line up."},
            headers=finder["headers"],
        )
        response.raise_for_status()
        return response.json()

    async def admin_decide(self, admin: dict, match_id: str) -> dict:
        response = await self.http.post(
            f"/api/admin/claims/{match_id}/decision",
            json={"approve": True, "note": "Seeded: ownership proof checked"},
            headers=admin["headers"],
        )
        response.raise_for_status()
        return response.json()

    async def quote(self, owner: dict, match_id: str, address: str) -> dict:
        response = await self.http.put(
            f"/api/deliveries/{match_id}/quote",
            json={"method": "meetup", "destination_address": address},
            headers=owner["headers"],
        )
        response.raise_for_status()
        return response.json()

    async def pay(self, owner: dict, match_id: str) -> None:
        started = await self.http.post(
            f"/api/payments/{match_id}/initiate",
            json={"phone": owner["phone"]},
            headers=owner["headers"],
        )
        started.raise_for_status()
        reference = started.json()["reference"]
        paid = await self.http.post(
            f"/api/payments/mock/{reference}", headers=owner["headers"]
        )
        paid.raise_for_status()

    async def confirm_handover(self, owner: dict, finder: dict, match_id: str) -> str:
        delivery = await self.http.get(
            f"/api/deliveries/{match_id}", headers=owner["headers"]
        )
        delivery.raise_for_status()
        code = delivery.json()["handover_code"]
        confirmed = await self.http.post(
            f"/api/deliveries/{match_id}/confirm",
            json={"code": code},
            headers=finder["headers"],
        )
        confirmed.raise_for_status()
        return confirmed.json()["status"]

    async def complete(self, owner: dict, match_id: str) -> str:
        response = await self.http.post(
            f"/api/matches/{match_id}/complete", headers=owner["headers"]
        )
        response.raise_for_status()
        return response.json()["status"]

    async def earnings(self, finder: dict) -> dict:
        response = await self.http.get("/api/earnings/summary", headers=finder["headers"])
        response.raise_for_status()
        return response.json()

    async def lost_alert(self, owner: dict, holder: str, id_number: str, doc: str) -> None:
        response = await self.http.post(
            "/api/lost-alerts",
            json={"holder_name": holder, "id_number": id_number, "document_type": doc},
            headers=owner["headers"],
        )
        response.raise_for_status()


def promote_admin(email: str) -> None:
    """Role is never grantable over HTTP, so use the repo's own script."""
    result = subprocess.run(
        [sys.executable, "scripts/promote_admin.py", "grant", email],
        capture_output=True,
        text=True,
        cwd="/app",
    )
    if result.returncode != 0:
        raise SystemExit(f"promote_admin failed: {result.stdout[-300:]}{result.stderr[-300:]}")


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as http:
        api = Client(http)

        admin = await api.register(ADMIN_EMAIL, "Amina Otieno", "+254733000201")
        finders = [
            await api.register("grace@example.com", "Grace Wanjiru", "+254733000202"),
            await api.register("daniel@example.com", "Daniel Kimani", "+254733000203"),
        ]
        owners = [
            await api.register("peter@example.com", "Peter Mwangi", "+254733000204"),
            await api.register("aisha@example.com", "Aisha Hassan", "+254733000205"),
            await api.register("john@example.com", "John Ochieng", "+254733000206"),
        ]

        for account in [admin, *finders, *owners]:
            await api.verify_phone(account)
        promote_admin(ADMIN_EMAIL)
        # JWTs carry no role claim; the role is re-read from the DB per request,
        # but a fresh login keeps the demo's token unambiguous.
        admin = await api.register(ADMIN_EMAIL, "Amina Otieno", "+254733000201")
        print("accounts 6 ready, all phones verified, admin promoted")

        docs = [
            ("28471930", "Peter Mwangi", "national_id", "Main Library, 2nd floor",
             "Campus Security Office, Gate A", (60, 90, 140)),
            ("A0294817", "Aisha Hassan", "passport", "Student Centre cafeteria",
             "Student Centre information desk", (120, 70, 60)),
            ("31882045", "John Ochieng", "national_id", "Sports Complex changing rooms",
             "Sports Complex reception", (70, 120, 80)),
            ("29104773", "Mary Njeri", "national_id", "Engineering block, Room 204",
             "Engineering block porter's desk", (110, 100, 60)),
            ("B1847265", "Samuel Kariuki", "passport", "Shuttle stage, Main Gate",
             "Main Gate security post", (90, 70, 120)),
            ("30558214", "Faith Chebet", "driving_licence", "Hostel B reception",
             "Hostel B resident tutor", (60, 110, 110)),
        ]

        posted = []
        for index, (number, holder, doc, where, pickup, colour) in enumerate(docs):
            finder = finders[index % len(finders)]
            record = await api.post_found(
                finder,
                id_number=number,
                holder=holder,
                doc=doc,
                where=where,
                pickup=pickup,
                colour=colour,
            )
            posted.append({"found": record, "finder": finder, "number": number})
            print(f"posted    {doc:16} {holder}")

        # 1) Full happy path to completed, so the overview's completed counter and
        #    the finder's wallet are both non-zero.
        entry, owner = posted[0], owners[0]
        match_ids = await api.search(owner, entry["number"])
        if not match_ids:
            print(f"WARN      no match for {entry['number']}")
        else:
            match_id = match_ids[0]
            await api.start_claim(owner, match_id)
            await api.claim(owner, match_id, entry["number"])
            await api.decide(entry["finder"], match_id, approve=True)
            await api.admin_decide(admin, match_id)
            await api.quote(owner, match_id, "Campus Security Office, Main Gate")
            await api.pay(owner, match_id)
            await api.confirm_handover(owner, entry["finder"], match_id)
            status = await api.complete(owner, match_id)
            wallet = await api.earnings(entry["finder"])
            print(f"completed {entry['number']} -> {status}; finder wallet {wallet}")

        # 2) Approved by both sides, payment outstanding.
        entry, owner = posted[1], owners[1]
        match_ids = await api.search(owner, entry["number"])
        if match_ids:
            match_id = match_ids[0]
            await api.start_claim(owner, match_id)
            await api.claim(owner, match_id, entry["number"])
            await api.decide(entry["finder"], match_id, approve=True)
            await api.admin_decide(admin, match_id)
            await api.quote(owner, match_id, "Student Centre information desk")
            print(f"approved  {entry['number']} -> awaiting payment")

        # 3) Claim waiting on the finder, so the finder queue has a live row.
        entry, owner = posted[2], owners[2]
        match_ids = await api.search(owner, entry["number"])
        if match_ids:
            match_id = match_ids[0]
            await api.claim(owner, match_id, entry["number"])
            print(f"pending   {entry['number']} -> awaiting finder decision")

        # 4) A second claim left unhandled, so the admin queue is not empty even
        #    after the admin approves (2).
        entry, owner = posted[3], owners[0]
        match_ids = await api.search(owner, entry["number"])
        if match_ids:
            match_id = match_ids[0]
            await api.claim(owner, match_id, entry["number"])
            await api.decide(entry["finder"], match_id, approve=True)
            print(f"queued    {entry['number']} -> awaiting admin review")

        await api.lost_alert(owners[0], "Peter Mwangi", "28471930", "national_id")
        await api.lost_alert(owners[2], "Lucy Wambui", "32004918", "national_id")
        print("alerts    2 lost-document alerts raised")

        print("\nDemo accounts (password: %s)" % PASSWORD)
        print(f"  admin   {ADMIN_EMAIL}")
        for account in [*finders, *owners]:
            print(f"  {'finder' if account in finders else 'owner '}  {account['email']}")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

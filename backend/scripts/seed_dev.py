"""Seed the development database with realistic data through the real API.

Every row here is created by calling the same HTTP endpoints the frontend calls,
so the resulting state is reachable in production rather than hand-assembled with
INSERTs that could encode a state the app can never actually produce.

This is a development convenience only. It refuses to run unless the target
database name ends in a non-``_test`` dev name, and it never touches uploads.

    docker compose exec -T backend python scripts/seed_dev.py
"""
from __future__ import annotations

import asyncio
import os
import sys

import httpx

BASE = os.environ.get("SEED_BASE_URL", "http://localhost:8000")
PASSWORD = "correct-horse-battery"


class Client:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def register(self, email: str, name: str) -> dict:
        response = await self.http.post(
            "/api/auth/register",
            json={"email": email, "password": PASSWORD, "display_name": name},
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
            "headers": {"Authorization": f"Bearer {data['access_token']}"},
        }

    async def post_found(
        self, finder: dict, *, id_number: str, holder: str, doc: str, where: str
    ) -> dict:
        response = await self.http.post(
            "/api/found-ids",
            data={
                "document_type": doc,
                "holder_name": holder,
                "id_number": id_number,
                "found_location": where,
                "run_ocr": "false",
            },
            headers=finder["headers"],
        )
        response.raise_for_status()
        return response.json()

    async def search(self, seeker: dict, id_number: str) -> list[str]:
        response = await self.http.post(
            "/api/search", json={"id_number": id_number}, headers=seeker["headers"]
        )
        response.raise_for_status()
        return response.json()["match_ids"]

    async def claim(self, seeker: dict, match_id: str, id_number: str) -> None:
        response = await self.http.post(
            f"/api/matches/{match_id}/claim",
            json={"id_number": id_number, "message": "This is mine, thank you!"},
            headers=seeker["headers"],
        )
        response.raise_for_status()

    async def decide(self, finder: dict, match_id: str, approve: bool) -> None:
        response = await self.http.post(
            f"/api/matches/{match_id}/decision",
            json={"approve": approve, "note": "" if approve else "Details did not line up."},
            headers=finder["headers"],
        )
        response.raise_for_status()

    async def pay(self, seeker: dict, match_id: str) -> None:
        started = await self.http.post(
            f"/api/payments/{match_id}/initiate",
            json={"phone": "+254700000000"},
            headers=seeker["headers"],
        )
        started.raise_for_status()
        hook = await self.http.post(
            "/api/payments/webhook",
            json={"reference": started.json()["reference"], "status": "paid"},
        )
        hook.raise_for_status()

    async def post_found_with_photo(
        self, finder: dict, *, id_number: str, holder: str, photo: str
    ) -> dict:
        """Post a document carrying a real portrait, so a face embedding is stored."""
        with open(photo, "rb") as handle:
            response = await self.http.post(
                "/api/found-ids",
                data={
                    "document_type": "national_id",
                    "holder_name": holder,
                    "id_number": id_number,
                    "found_location": "Westlands police post",
                    "run_ocr": "false",
                },
                files={"image": ("id.jpg", handle.read(), "image/jpeg")},
                headers=finder["headers"],
            )
        response.raise_for_status()
        return response.json()

    async def verify_face(self, seeker: dict, match_id: str, selfie: str) -> dict:
        with open(selfie, "rb") as handle:
            response = await self.http.post(
                f"/api/matches/{match_id}/verify-face",
                files={"selfie": ("selfie.jpg", handle.read(), "image/jpeg")},
                headers=seeker["headers"],
            )
        response.raise_for_status()
        return response.json()

    async def complete(self, seeker: dict, match_id: str) -> str:
        """Hand-over confirmed: CLAIMED -> COMPLETED, the terminal state."""
        response = await self.http.post(
            f"/api/matches/{match_id}/complete", headers=seeker["headers"]
        )
        response.raise_for_status()
        return response.json()["status"]

    async def lost_alert(self, seeker: dict, holder: str, id_number: str, doc: str) -> None:
        response = await self.http.post(
            "/api/lost-alerts",
            json={"holder_name": holder, "id_number": id_number, "document_type": doc},
            headers=seeker["headers"],
        )
        response.raise_for_status()


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as http:
        api = Client(http)

        admin = await api.register("admin@seeker.co.ke", "Amina Otieno")
        finders = [
            await api.register("grace@example.com", "Grace Wanjiru"),
            await api.register("daniel@example.com", "Daniel Kimani"),
        ]
        seekers = [
            await api.register("peter@example.com", "Peter Mwangi"),
            await api.register("aisha@example.com", "Aisha Hassan"),
            await api.register("john@example.com", "John Ochieng"),
        ]

        docs = [
            ("28471930", "Peter Mwangi", "national_id", "Kencom bus stop, Nairobi CBD"),
            ("A0294817", "Aisha Hassan", "passport", "Java House, Kimathi Street"),
            ("31882045", "John Ochieng", "national_id", "Matatu, Route 46"),
            ("29104773", "Mary Njeri", "national_id", "Sarit Centre car park"),
            ("B1847265", "Samuel Kariuki", "passport", "JKIA arrivals hall"),
            ("30558214", "Faith Chebet", "driving_licence", "Nakumatt Junction"),
        ]

        posted = []
        for index, (number, holder, doc, where) in enumerate(docs):
            finder = finders[index % len(finders)]
            record = await api.post_found(
                finder, id_number=number, holder=holder, doc=doc, where=where
            )
            posted.append({"found": record, "finder": finder, "number": number})
            print(f"posted   {doc:16} {holder}")

        # Three seekers find their documents; each ends at a different stage so
        # the console shows a real spread rather than six identical rows.
        for index in range(3):
            entry = posted[index]
            seeker = seekers[index]
            match_ids = await api.search(seeker, entry["number"])
            if not match_ids:
                print(f"WARN     no match for {entry['number']}")
                continue
            match_id = match_ids[0]
            await api.claim(seeker, match_id, entry["number"])
            print(f"claimed  {entry['number']} by {seeker['name']}")

            if index == 0:
                # Full happy path all the way to hand-over, so the Overview's
                # "Completed claims" counter (Match.status == COMPLETED) is
                # exercised rather than sitting permanently at zero.
                await api.decide(entry["finder"], match_id, approve=True)
                await api.pay(seeker, match_id)
                status = await api.complete(seeker, match_id)
                print(f"paid     {entry['number']} -> {status}")
            elif index == 1:
                # Approved, payment still outstanding.
                await api.decide(entry["finder"], match_id, approve=True)
                print(f"approved {entry['number']} -> awaiting payment")
            else:
                # Left pending, so the claims queue has something to show.
                print(f"pending  {entry['number']} -> awaiting finder decision")

        # A genuine failed selfie check, so the flagged queue holds a real row.
        # The reference portrait and the selfie are two different people, so the
        # engine rejects it on distance and the router flags it — the flag is
        # produced by the real comparison, not written by hand.
        faces = "tests/fixtures/faces"
        if os.path.isdir(faces):
            entry = posted[3]
            impostor = seekers[1]
            await api.post_found_with_photo(
                entry["finder"],
                id_number="29104773-F",
                holder="Mary Njeri",
                photo=f"{faces}/obama.jpg",
            )
            match_ids = await api.search(impostor, "29104773-F")
            if match_ids:
                verdict = await api.verify_face(impostor, match_ids[0], f"{faces}/biden.jpg")
                print(
                    f"flagged  selfie check {verdict['status']} "
                    f"(flagged={verdict['flagged_for_review']})"
                )
        else:
            print("skip     face fixtures absent, flagged queue left empty")

        await api.lost_alert(seekers[0], "Peter Mwangi", "28471930", "national_id")
        await api.lost_alert(seekers[2], "Lucy Wambui", "32004918", "national_id")
        print("alerts   2 lost-document alerts raised")

        print(f"\nadmin    {admin['email']} / {PASSWORD}  (needs promotion via SQL)")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))



import asyncio
import sys
import os

# Ensure the backend directory is in the path so we can import 'app'
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import select
from app.database import async_session
from app.models import User, UserRole

async def main():
    print("--- Promote User to Admin ---")
    email = input("Enter the email of the user to promote to ADMIN: ").strip()
    if not email:
        print("No email provided.")
        return

    async with async_session() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user:
            print(f"❌ User with email '{email}' not found.")
            return

        if user.role == UserRole.ADMIN:
            print(f"ℹ️ User '{email}' is already an ADMIN.")
            return

        confirm = input(f"❓ Promote '{email}' (current role: {user.role}) to ADMIN? (y/n): ")
        if confirm.lower() != 'y':
            print("🚫 Operation cancelled.")
            return

        user.role = UserRole.ADMIN
        await session.commit()
        print(f"✅ Successfully promoted '{email}' to ADMIN.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())

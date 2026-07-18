from sqlalchemy import text
from app.core.database import SessionLocal

def main():
    with SessionLocal() as db:
        try:
            db.execute(text("ALTER TABLE companies ADD COLUMN latest_kundli_score INTEGER"))
            print("Added latest_kundli_score")
        except Exception as e:
            print(e)
            
        try:
            db.execute(text("ALTER TABLE companies ADD COLUMN previous_kundli_score INTEGER"))
            print("Added previous_kundli_score")
        except Exception as e:
            print(e)
            
        try:
            db.execute(text("ALTER TABLE companies ADD COLUMN last_analyzed_at TIMESTAMP"))
            print("Added last_analyzed_at")
        except Exception as e:
            print(e)
            
        db.commit()

if __name__ == "__main__":
    main()

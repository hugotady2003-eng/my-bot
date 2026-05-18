from bot import init_db, check_feeds

if __name__ == "__main__":
    conn = init_db()
    check_feeds(conn)
    conn.close()
    print("✅ Passe terminée.")
